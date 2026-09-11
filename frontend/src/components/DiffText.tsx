/**
 * DiffText：把双引擎字符级 diff 段落渲染为「存疑高亮 + 可编辑定稿文字」。
 *
 * 规则（与后端 docs/architecture.md 第 7 节 diff 约定一致）：
 * * `equal` 段落正常显示；
 * * `replace` / `delete` / `insert` 三类为「存疑」段落，黄底（insert 用玫红底）高亮；
 * * 存疑段落的 `title` 展示复核引擎（engine2）候选文本 `text_b`；
 * * 点击存疑段落 → 回调其所属照片的 `seq`，用于高亮对应原片；同时上报该处 key，
 *   由页面记入「已查看」集合（OPT-01 存疑清点），已查看处改为描边态、仍可再次点击；
 * * 存疑段落**按句切分**（OPT-01 句级跳转）：一句一个可点单元，长段落不再是一整块，
 *   点击任意一句即定位其所属原片；单句段落不产生额外编号，key 与既有一致。
 * * 「识别对照」面板默认展开（每次挂载复位，不做任何持久化）。
 *
 * 展示文本：`equal/replace/delete` 用主引擎文本 `text_a`；`insert` 主引擎无字符，
 * 故展示复核引擎候选 `text_b`（否则插入点不可见、不可点）。
 */

import { useMemo, useState } from "react";

import type { DiffSegment, Photo } from "../api/types";

export type SegmentVariant = "plain" | "suspect" | "suspect-b";

export interface SegmentView {
  index: number;
  type: DiffSegment["type"];
  /** 用于展示的文本。 */
  text: string;
  suspect: boolean;
  variant: SegmentVariant;
  title: string;
}

/** 依据段落类型推导展示文本、是否存疑、高亮样式与 title。 */
export function describeSegment(segment: DiffSegment, index: number): SegmentView {
  if (segment.type === "equal") {
    return {
      index,
      type: segment.type,
      text: segment.text_a,
      suspect: false,
      variant: "plain",
      title: "",
    };
  }
  if (segment.type === "insert") {
    return {
      index,
      type: segment.type,
      text: segment.text_b,
      suspect: true,
      variant: "suspect-b",
      title: `复核引擎建议补充：${segment.text_b}`,
    };
  }
  if (segment.type === "delete") {
    return {
      index,
      type: segment.type,
      text: segment.text_a,
      suspect: true,
      variant: "suspect",
      title: "复核引擎未识别到此处，建议删除",
    };
  }
  return {
    index,
    type: segment.type,
    text: segment.text_a,
    suspect: true,
    variant: "suspect",
    title: `复核引擎候选：${segment.text_b}`,
  };
}

/**
 * 句末标点集合（OPT-01 句级切分）：中文句号/叹号/问号/分号 + 对应半角 + 省略号。
 * 用码位常量而不是字面量，避免源码里混排全角符号被编辑器或行尾处理改坏。
 */
const SENTENCE_ENDINGS = String.fromCharCode(0x3002, 0xff01, 0xff1f, 0x0021, 0x003f, 0xff1b, 0x003b, 0x2026);

/** 句末标点之后仍应归属于本句的收尾符号（右引号、右括号等）。 */
const TRAILING_CLOSERS = String.fromCharCode(
  0x201d, 0x2019, 0x0022, 0x0027, 0x300d, 0x300f, 0x3011, 0x300b, 0xff09, 0x0029,
);

/**
 * 按句切分一段文本，标点跟随前句。
 *
 * 只用于「存疑段落」的展示粒度，不改数据模型：`diff_json` 仍是引擎给出的段落序列，
 * 空白/无标点文本原样返回一格（插入类候选可能为空串，必须保持可点）。
 */
export function splitSentences(text: string): string[] {
  if (text.trim() === "") {
    return [text];
  }
  const parts: string[] = [];
  let current = "";
  let cursor = 0;
  while (cursor < text.length) {
    const char = text[cursor];
    cursor += 1;
    current += char;
    if (!SENTENCE_ENDINGS.includes(char)) {
      continue;
    }
    while (cursor < text.length && TRAILING_CLOSERS.includes(text[cursor])) {
      current += text[cursor];
      cursor += 1;
    }
    parts.push(current);
    current = "";
  }
  if (current.trim() === "") {
    if (parts.length > 0) {
      parts[parts.length - 1] += current;
    } else if (current !== "") {
      parts.push(current);
    }
  } else {
    parts.push(current);
  }
  return parts;
}

/**
 * 存疑处的会话内唯一 key：`photoSeq:segmentIndex[:sentenceIndex]`。
 * 只有一句时不带句序号 —— 与既有 key 口径、既有断言完全一致。
 */
export function suspectKey(seq: number, index: number, part = 0): string {
  const base = `${seq}:${index}`;
  return part === 0 ? base : `${base}:${part}`;
}

/**
 * 全篇存疑处清单（按照片序 → 段落序 → 句序），供定稿前「存疑清点」使用。
 * 与渲染共用 splitSentences，因此「存疑 N 处」的 N 是**句级**计数，不会两头不一致。
 */
export function listSuspectKeys(photos: Photo[]): string[] {
  const keys: string[] = [];
  for (const photo of photos) {
    const segments = photo.diff_json ?? [];
    for (let index = 0; index < segments.length; index += 1) {
      const view = describeSegment(segments[index], index);
      if (!view.suspect) {
        continue;
      }
      const units = splitSentences(view.text);
      for (let part = 0; part < units.length; part += 1) {
        keys.push(suspectKey(photo.seq, index, part));
      }
    }
  }
  return keys;
}

/** 已查看存疑处的视觉态：去掉高亮底色，改为描边 + 弱化文字色，保持可点击。 */
const VIEWED_CLASS =
  "rounded bg-white/70 px-0.5 text-slate-600 ring-1 ring-inset ring-slate-400";

/** 未提供「已查看」集合时的空集单例（避免每次渲染新建对象）。 */
const EMPTY_VIEWED: ReadonlySet<string> = new Set<string>();

/** 由各照片 diff（无 diff 时回退主引擎文本）拼出初始定稿文字。 */
export function composeInitialText(photos: Photo[]): string {
  return photos
    .map((photo) => {
      if (photo.diff_json && photo.diff_json.length > 0) {
        return photo.diff_json.map((segment) => segment.text_a).join("");
      }
      return photo.engine1_text ?? "";
    })
    .join("\n");
}

export interface DiffTextProps {
  photos: Photo[];
  /** 受控的定稿文字。 */
  value: string;
  onChange: (value: string) => void;
  /** 点击存疑段落时回调其所属照片 seq。 */
  onSelectPhoto?: (seq: number) => void;
  /** 本次会话内已被点看过的存疑处 key 集合。 */
  viewedSuspects?: ReadonlySet<string>;
  /** 存疑处被点击 / 回车时上报其 key。 */
  onSuspectView?: (key: string) => void;
  disabled?: boolean;
}

export default function DiffText({
  photos,
  value,
  onChange,
  onSelectPhoto,
  viewedSuspects,
  onSuspectView,
  disabled = false,
}: DiffTextProps) {
  const hasDiff = photos.some((photo) => (photo.diff_json?.length ?? 0) > 0);
  /** 对照面板展开态：初始恒为展开（不做 localStorage 持久化，进入页面即复位）。 */
  const [panelOpen, setPanelOpen] = useState(true);
  const viewed = viewedSuspects ?? EMPTY_VIEWED;

  const suspectKeys = useMemo(() => listSuspectKeys(photos), [photos]);
  const viewedCount = useMemo(
    () => suspectKeys.filter((key) => viewed.has(key)).length,
    [suspectKeys, viewed],
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <label className="flex min-h-0 flex-[3] flex-col gap-1 text-sm font-medium text-slate-700">
        定稿文字
        <textarea
          data-testid="final-text"
          className="min-h-[160px] w-full flex-1 resize-none rounded-lg border border-slate-300 p-3 text-base leading-7 outline-none focus:border-slate-500 disabled:bg-slate-50"
          value={value}
          disabled={disabled}
          placeholder="在此核对并编辑定稿文字"
          onChange={(event) => onChange(event.target.value)}
        />
      </label>

      <details
        data-testid="diff-details"
        className="flex min-h-0 flex-[2] flex-col rounded-lg border border-slate-200 bg-slate-50"
        open={panelOpen}
        onToggle={(event) => setPanelOpen(event.currentTarget.open)}
      >
        <summary className="shrink-0 cursor-pointer select-none px-4 py-2 text-sm font-medium text-slate-600">
          <span className="mr-2">识别对照（存疑高亮）</span>
          {suspectKeys.length > 0 ? (
            <span
              data-testid="suspect-counter"
              className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
            >
              存疑 {suspectKeys.length} 处 · 已查看 {viewedCount} 处
            </span>
          ) : (
            <span
              data-testid="suspect-counter"
              className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700"
            >
              无存疑
            </span>
          )}
        </summary>
        <section
          data-testid="diff-annotated"
          className="min-h-0 flex-1 overflow-auto border-t border-slate-200 bg-white p-4 leading-8"
        >
          {hasDiff ? (
            photos.map((photo) => (
              <div key={photo.id} className="mb-4 last:mb-0">
                <p className="mb-1 text-xs font-medium text-slate-400">第 {photo.seq} 张</p>
                <p className="whitespace-pre-wrap text-base text-slate-900">
                  {(photo.diff_json ?? []).map((segment, index) => {
                    const view = describeSegment(segment, index);
                    if (!view.suspect) {
                      return <span key={view.index}>{view.text}</span>;
                    }
                    const units = splitSentences(view.text);
                    return units.map((unit, part) => {
                      const key = suspectKey(photo.seq, index, part);
                      const seen = viewed.has(key);
                      const mark =
                        units.length > 1 ? "（第 " + String(part + 1) + "/" + String(units.length) + " 句）" : "";
                      return (
                        <span
                          key={key}
                          data-suspect={view.type}
                          data-suspect-key={key}
                          data-viewed={seen ? "1" : undefined}
                          data-photo-seq={photo.seq}
                          title={seen ? view.title + mark + "（已查看，可再次点开定位原片）" : view.title + mark}
                          role="button"
                          tabIndex={0}
                          aria-pressed={seen}
                          className={
                            seen
                              ? `${VIEWED_CLASS} cursor-pointer`
                              : `${view.variant === "suspect-b" ? "diff-suspect-b" : "diff-suspect"} cursor-pointer`
                          }
                          onClick={() => {
                            onSuspectView?.(key);
                            onSelectPhoto?.(photo.seq);
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              onSuspectView?.(key);
                              onSelectPhoto?.(photo.seq);
                            }
                          }}
                        >
                          {unit || "◻"}
                        </span>
                      );
                    });
                  })}
                </p>
              </div>
            ))
          ) : (
            <p className="text-sm text-slate-500">
              暂无 diff 数据（本篇识别置信度较高、复核引擎未介入，或复核未返回文本），
              请直接对照左侧原片核对并在上方编辑。
            </p>
          )}
        </section>
      </details>
    </div>
  );
}
