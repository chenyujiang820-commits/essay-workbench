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
 *
 * 兜底（fallback）模式：全篇都没有 diff（真机高置信稿最常见）时，面板不再是一句空壳提示，
 * 而是改渲染「识别原文对照」—— 用主引擎原文逐句比对定稿，标注未识别字与被改动/删减的
 * 句子（见 buildOcrCompare）。兜底存疑点不计入 listSuspectKeys，因此不会触发定稿前的确认框。
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

/**
 * 「识别原文对照」（fallback 模式）：全篇都没有 diff 时，面板不再只给一句空壳提示，
 * 而是把主引擎原文与当前定稿逐句比对，标出可疑处。
 *
 * 计数口径（渲染与徽标共用 buildOcrCompare / listFallbackSuspectKeys，不会两头不一致）：
 * * 按 `seq` 升序取各张 `engine1_text`（null / 纯空白跳过该张），**先按换行切行、行内再用
 * splitSentences 逐句切开**（rev.4：只按句末标点切会把页眉与后面的长句并成一个单元，
 * 老师删掉页眉后整句被连坐标红 —— 真机反馈「框画得有点麻烦」）；
 * * 句中含未识别占位符（`?` `？` `□` `▯` 及包起来的 `【?】` `〔?〕`）时，只把占位符本身
 *   标成「未识别」，整句不再重复标注 —— 与定稿的差异通常正是这些字造成的，重复画框只会更吵；
 * * 句中无占位符、但归一化（去掉所有空白）后不在定稿里时，整句标成「已修改/删减」；
 * * 纯标点碎句（`【?】` 断句后剩下的「。」等）不参与比对，避免噪声。
 *
 * 边界：fallback 存疑点**不**并入 listSuspectKeys。后者驱动 ProofreadPage 定稿前的
 * FR-09「还有 N 处存疑未看」确认框，混进来会让每篇高置信稿都弹一次框，等于制造新摩擦。
 */

/** 未识别占位符：方括号形式优先匹配，避免 `【?】` 被拆成裸 `?` 后留下孤立括号。 */
const UNKNOWN_MARK_SOURCE = "[\u3010\u3014][?\uFF1F\u25A1\u25AF][\u3011\u3015]|[?\uFF1F\u25A1\u25AF]";

/** 实义字符：汉字 / 字母 / 数字。用于跳过纯标点碎句。 */
const SUBSTANTIVE_SOURCE = "[\u4E00-\u9FFF0-9A-Za-z]";

const OCR_UNKNOWN_TITLE = "主引擎未能识别这个字，点击定位原片核对";
const OCR_CHANGED_TITLE =
  "这一行/句没有出现在当前定稿里（多为页眉、表头或被改写的句子），点击定位原片核对";

/** 全篇是否有任何一张照片带引擎 diff：决定面板走 diff 还是识别原文对照（两路径互斥）。 */
export function hasAnyDiff(photos: Photo[]): boolean {
  return photos.some((photo) => (photo.diff_json?.length ?? 0) > 0);
}

/**
 * 当前生效的存疑清单：有 diff 用引擎 diff，全篇无 diff 用识别原文对照。
 *
 * 面板徽标与 ProofreadPage 的移动端页签计数共用此函数，避免两处各算一套（GAP-12）。
 */
export function listActiveSuspectKeys(photos: Photo[], value: string): string[] {
  return hasAnyDiff(photos) ? listSuspectKeys(photos) : listFallbackSuspectKeys(photos, value);
}

/** fallback 存疑类别：`unknown` = 未识别字，`changed` = 定稿中已不存在的句子。 */
export type OcrSuspectKind = "unknown" | "changed";

export interface OcrComparePart {
  text: string;
  /** 存疑处的 key；普通文本为 null。 */
  key: string | null;
  kind: OcrSuspectKind | null;
  title: string;
}

export interface OcrCompareLine {
  seq: number;
  parts: OcrComparePart[];
}

/** fallback 存疑处 key：`photoSeq:ocr:unitIndex`，与既有 `${seq}:${index}` 命名空间不冲突。 */
export function ocrSuspectKey(seq: number, unitIndex: number): string {
  return `${seq}:ocr:${unitIndex}`;
}

/** 比对用归一化：去掉所有空白（含全角空格），只保留文字本身。 */
function normalizeForCompare(text: string): string {
  return text.replace(/\s+/g, "");
}

/** 找出文本中所有未识别占位符的位置（按出现顺序）。 */
function listUnknownMarks(text: string): Array<{ start: number; end: number }> {
  const pattern = new RegExp(UNKNOWN_MARK_SOURCE, "g");
  const marks: Array<{ start: number; end: number }> = [];
  let match = pattern.exec(text);
  while (match !== null) {
    if (match[0].length > 0) {
      marks.push({ start: match.index, end: match.index + match[0].length });
    }
    match = pattern.exec(text);
  }
  return marks;
}

function plainPart(text: string): OcrComparePart {
  return { text, key: null, kind: null, title: "" };
}

function suspectPart(text: string, seq: number, unitIndex: number, kind: OcrSuspectKind): OcrComparePart {
  return {
    text,
    key: ocrSuspectKey(seq, unitIndex),
    kind,
    title: kind === "unknown" ? OCR_UNKNOWN_TITLE : OCR_CHANGED_TITLE,
  };
}

/** 逐句比对识别原文与定稿，产出「识别原文对照」的行与单元（无 diff 时使用）。 */
export function buildOcrCompare(photos: Photo[], value: string): OcrCompareLine[] {
  const finalText = normalizeForCompare(value);
  const substantive = new RegExp(SUBSTANTIVE_SOURCE);
  const lines: OcrCompareLine[] = [];
  for (const photo of [...photos].sort((a, b) => a.seq - b.seq)) {
    const recognized = photo.engine1_text ?? "";
    if (recognized.trim() === "") {
      continue;
    }
    const parts: OcrComparePart[] = [];
    let unitIndex = 0;
    // 先按行切、行内再按句切：OCR 原文的换行是天然单元，只按句末标点切会把
    // 「页眉三行 + 第一个长句」并成一个单元（它们中间没有句号），于是老师删掉页眉后
    // 整句被连坐标红 —— 真机反馈「框画得有点麻烦」正是这个（GAP-13）。
    const rawLines = recognized.split(/\r?\n/);
    rawLines.forEach((rawLine, linePosition) => {
      if (linePosition > 0) {
        // 保留换行，面板仍是 whitespace-pre-wrap 的原始行貌，不会糊成一整段。
        parts.push(plainPart("\n"));
      }
      if (rawLine.trim() === "") {
        return;
      }
      for (const sentence of splitSentences(rawLine)) {
        const marks = listUnknownMarks(sentence);
        if (marks.length > 0) {
          let cursor = 0;
          for (const mark of marks) {
            if (mark.start > cursor) {
              parts.push(plainPart(sentence.slice(cursor, mark.start)));
            }
            parts.push(suspectPart(sentence.slice(mark.start, mark.end), photo.seq, unitIndex, "unknown"));
            unitIndex += 1;
            cursor = mark.end;
          }
          if (cursor < sentence.length) {
            parts.push(plainPart(sentence.slice(cursor)));
          }
          continue;
        }
        const normalized = normalizeForCompare(sentence);
        const changed = substantive.test(normalized) && !finalText.includes(normalized);
        parts.push(changed ? suspectPart(sentence, photo.seq, unitIndex, "changed") : plainPart(sentence));
        if (changed) {
          unitIndex += 1;
        }
      }
    });
    lines.push({ seq: photo.seq, parts });
  }
  return lines;
}

/** fallback 模式下的全篇存疑清单（按照片 seq → 句序），与面板渲染同源。 */
export function listFallbackSuspectKeys(photos: Photo[], value: string): string[] {
  const keys: string[] = [];
  for (const line of buildOcrCompare(photos, value)) {
    for (const part of line.parts) {
      if (part.key !== null) {
        keys.push(part.key);
      }
    }
  }
  return keys;
}

/** fallback 存疑点的视觉态：只有虚线下划线 + 文字色，不画底色和边框（移动端版面很紧）。 */
const OCR_UNKNOWN_CLASS =
  "rounded px-0.5 text-amber-800 underline decoration-dotted decoration-amber-600 underline-offset-4";
const OCR_CHANGED_CLASS =
  "rounded px-0.5 text-rose-700 underline decoration-dotted decoration-rose-500 underline-offset-4";

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
  comparison?: "inline" | "hidden";
}

export interface DiffComparePanelProps {
  photos: Photo[];
  value: string;
  onChange?: (value: string) => void;
  onSelectPhoto?: (seq: number) => void;
  viewedSuspects?: ReadonlySet<string>;
  onSuspectView?: (key: string) => void;
  disabled?: boolean;
}

export function DiffComparePanel({
  photos,
  value,
  onChange,
  onSelectPhoto,
  viewedSuspects,
  onSuspectView,
  disabled = false,
}: DiffComparePanelProps) {
  const hasDiff = hasAnyDiff(photos);
  /** 对照面板展开态：初始恒为展开（不做 localStorage 持久化，进入页面即复位）。 */
  const [panelOpen, setPanelOpen] = useState(true);
  /** 桌面端「放大」态：对照面板吃满整列、定稿框压成一条，用于通读长对照。 */
  const [compareFocused, setCompareFocused] = useState(false);
  const viewed = viewedSuspects ?? EMPTY_VIEWED;

  const suspectKeys = useMemo(() => listSuspectKeys(photos), [photos]);
  /** fallback（识别原文对照）：仅在全篇都没有 diff 时启用，与 diff 渲染路径互斥。 */
  const ocrLines = useMemo<OcrCompareLine[]>(
    () => (hasDiff ? [] : buildOcrCompare(photos, value)),
    [hasDiff, photos, value],
  );
  // 徽标里的 N 直接取 listFallbackSuspectKeys，与面板渲染同一口径，不会两处算得不一样。
  const fallbackKeys = useMemo(
    () => (hasDiff ? [] : listFallbackSuspectKeys(photos, value)),
    [hasDiff, photos, value],
  );
  const activeKeys = hasDiff ? suspectKeys : fallbackKeys;
  const viewedCount = useMemo(
    () => activeKeys.filter((key) => viewed.has(key)).length,
    [activeKeys, viewed],
  );

  // 对照内容规模提示：面板内部滚动，不告诉老师还有多少行，就等于「显示不齐全」。
  const compareCount = hasDiff
    ? photos.reduce((acc, photo) => acc + (photo.diff_json?.length ?? 0), 0)
    : ocrLines.length;
  const compareUnit = hasDiff ? "段" : "行";

  return (
      <div
        data-testid="diff-details"
        style={
          compareFocused
            ? {
                position: "fixed",
                inset: "1rem",
                height: "calc(100vh - 2rem)",
                zIndex: 50,
                boxShadow: "0 20px 40px rgba(15, 23, 42, 0.24)",
              }
            : undefined
        }
        className={`flex min-h-0 flex-col rounded-lg border border-slate-200 bg-slate-50 ${
          compareFocused
            ? "h-full"
            : panelOpen
              ? "min-h-[180px] lg:h-[42%] lg:min-h-[190px] lg:shrink-0"
              : "h-auto min-h-0 lg:shrink-0"
        }`}
      >
        <div className="flex shrink-0 flex-wrap items-center gap-2 px-4 py-2 text-sm font-medium text-slate-600">
          <button
            type="button"
            data-testid="diff-toggle"
            aria-expanded={panelOpen}
            aria-controls="diff-annotated"
            className="cursor-pointer select-none"
            onClick={() => setPanelOpen((open) => !open)}
          >
            <span aria-hidden="true" className="mr-1">
              {panelOpen ? "▾" : "▸"}
            </span>
            识别对照（存疑高亮）
          </button>
          {activeKeys.length > 0 ? (
            <span
              data-testid="suspect-counter"
              className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
            >
              存疑 {activeKeys.length} 处 · 已查看 {viewedCount} 处
            </span>
          ) : (
            <span
              data-testid="suspect-counter"
              className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700"
            >
              无存疑
            </span>
          )}
          <span className="ml-auto flex items-center gap-2 text-xs font-normal text-slate-500">
            {compareCount > 0 ? (
              <span data-testid="compare-count">
                共 {compareCount} {compareUnit}
              </span>
            ) : null}
            <button
              type="button"
              data-testid="diff-focus"
              aria-pressed={compareFocused}
              className="hidden cursor-pointer rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700 hover:bg-white lg:inline"
              onClick={() => setCompareFocused((focused) => !focused)}
            >
              {compareFocused ? "还原" : "放大"}
            </button>
          </span>
        </div>
        <section
          id="diff-annotated"
          data-testid="diff-annotated"
          hidden={!panelOpen}
          className="min-h-0 flex-1 overflow-auto border-t border-slate-200 bg-white p-4 leading-8"
        >
          {onChange ? (
            <label className="mb-3 block text-xs font-medium text-slate-500">
              在识别对照中修改校对草稿（会同步到定稿文字）
              <textarea
                data-testid="comparison-editor"
                value={value}
                disabled={disabled}
                onChange={(event) => onChange(event.target.value)}
                className="mt-1 min-h-[110px] w-full resize-y rounded-md border border-slate-300 p-3 text-base leading-7 text-slate-900 outline-none focus:border-slate-500 disabled:bg-slate-50"
              />
            </label>
          ) : null}
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
          ) : ocrLines.length === 0 ? (
            <p className="text-sm text-slate-500">
              暂无 diff 数据（本篇识别置信度较高、复核引擎未介入，或复核未返回文本），
              也没有可用的主引擎识别文本，请直接对照左侧原片核对并在上方编辑。
            </p>
          ) : activeKeys.length === 0 ? (
            <p data-testid="ocr-compare-clean" className="text-sm leading-6 text-emerald-700">
              识别原文与定稿一致，未发现存疑字。
            </p>
          ) : (
            <div data-testid="ocr-compare" className="flex flex-col gap-2">
              <p className="text-xs leading-5 text-slate-400">
                识别原文对照（复核引擎未介入）：
                <span className={OCR_UNKNOWN_CLASS}>橙色虚线</span>为未识别字、
                <span className={OCR_CHANGED_CLASS}>红色虚线</span>为定稿中已不存在的句子；点标注可定位原片。
              </p>
              {ocrLines.map((line) => (
                <p
                  key={`ocr-line-${line.seq}`}
                  data-testid={`ocr-line-${line.seq}`}
                  className="whitespace-pre-wrap break-words text-base leading-8 text-slate-800"
                >
                  {photos.length > 1 ? (
                    <span className="mr-1 text-xs text-slate-400">第 {line.seq} 张</span>
                  ) : null}
                  {line.parts.map((part, partIndex) => {
                    if (part.key === null) {
                      return <span key={partIndex}>{part.text}</span>;
                    }
                    const key = part.key;
                    const seen = viewed.has(key);
                    const markClass = part.kind === "unknown" ? OCR_UNKNOWN_CLASS : OCR_CHANGED_CLASS;
                    return (
                      <span
                        key={key}
                        data-suspect={part.kind === "unknown" ? "ocr-unknown" : "ocr-changed"}
                        data-suspect-key={key}
                        data-viewed={seen ? "1" : undefined}
                        data-photo-seq={line.seq}
                        title={seen ? `${part.title}（已查看，可再次点开定位原片）` : part.title}
                        role="button"
                        tabIndex={0}
                        aria-pressed={seen}
                        className={`${seen ? VIEWED_CLASS : markClass} cursor-pointer`}
                        onClick={() => {
                          onSuspectView?.(key);
                          onSelectPhoto?.(line.seq);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            onSuspectView?.(key);
                            onSelectPhoto?.(line.seq);
                          }
                        }}
                      >
                        {part.text}
                      </span>
                    );
                  })}
                </p>
              ))}
            </div>
          )}
        </section>
      </div>
  );
}

export default function DiffText({
  photos,
  value,
  onChange,
  onSelectPhoto,
  viewedSuspects,
  onSuspectView,
  disabled = false,
  comparison = "inline",
}: DiffTextProps) {
  return (
    <div className="flex min-h-0 flex-col gap-1 lg:h-full">
      <label className="flex min-h-0 flex-1 flex-col gap-1 text-sm font-medium text-slate-700">
        定稿文字
        <textarea
          data-testid="final-text"
          className="min-h-[160px] lg:min-h-[180px] w-full flex-1 resize-none rounded-lg border border-slate-300 p-3 text-base leading-7 outline-none focus:border-slate-500 disabled:bg-slate-50"
          value={value}
          disabled={disabled}
          placeholder="在此核对并编辑定稿文字"
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
      {comparison === "inline" ? (
      <DiffComparePanel
          photos={photos}
          value={value}
          onChange={onChange}
          onSelectPhoto={onSelectPhoto}
          viewedSuspects={viewedSuspects}
          onSuspectView={onSuspectView}
          disabled={disabled}
        />
      ) : null}
    </div>
  );
}
