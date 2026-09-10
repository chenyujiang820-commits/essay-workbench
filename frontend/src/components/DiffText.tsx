/**
 * DiffText：把双引擎字符级 diff 段落渲染为「存疑高亮 + 可编辑定稿文字」。
 *
 * 规则（与后端 docs/architecture.md 第 7 节 diff 约定一致）：
 * * `equal` 段落正常显示；
 * * `replace` / `delete` / `insert` 三类为「存疑」段落，黄底（insert 用玫红底）高亮；
 * * 存疑段落的 `title` 展示复核引擎（engine2）候选文本 `text_b`；
 * * 点击存疑段落 → 回调其所属照片的 `seq`，用于高亮对应原片。
 *
 * 展示文本：`equal/replace/delete` 用主引擎文本 `text_a`；`insert` 主引擎无字符，
 * 故展示复核引擎候选 `text_b`（否则插入点不可见、不可点）。
 */

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
  disabled?: boolean;
}

export default function DiffText({
  photos,
  value,
  onChange,
  onSelectPhoto,
  disabled = false,
}: DiffTextProps) {
  const hasDiff = photos.some((photo) => (photo.diff_json?.length ?? 0) > 0);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <section
        data-testid="diff-annotated"
        className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 bg-white p-4 leading-8"
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
                  return (
                    <span
                      key={view.index}
                      data-suspect={view.type}
                      data-photo-seq={photo.seq}
                      title={view.title}
                      role="button"
                      tabIndex={0}
                      className={`${view.variant === "suspect-b" ? "diff-suspect-b" : "diff-suspect"} cursor-pointer`}
                      onClick={() => onSelectPhoto?.(photo.seq)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelectPhoto?.(photo.seq);
                        }
                      }}
                    >
                      {view.text || "◻"}
                    </span>
                  );
                })}
              </p>
            </div>
          ))
        ) : (
          <p className="text-sm text-slate-500">
            暂无 diff 数据（复核引擎可能已降级），请直接对照左侧原片核对并在下方编辑。
          </p>
        )}
      </section>

      <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
        定稿文字
        <textarea
          data-testid="final-text"
          className="min-h-[160px] w-full resize-y rounded-lg border border-slate-300 p-3 text-base leading-7 outline-none focus:border-slate-500 disabled:bg-slate-50"
          value={value}
          disabled={disabled}
          placeholder="在此核对并编辑定稿文字"
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    </div>
  );
}
