/**
 * Pager：投屏翻页容器。统一处理键盘（←/→ 上一/下一页、空格下一页、Esc 退出）
 * 与触屏左右滑动，并渲染底部进度与翻页按钮。
 */

import { useEffect, useRef } from "react";

export interface PagerProps {
  /** 当前页索引（从 0 开始）。 */
  index: number;
  /** 总页数。 */
  total: number;
  onPrev: () => void;
  onNext: () => void;
  onExit?: () => void;
  /** 自定义进度文案；缺省为「index+1 / total」。 */
  progressText?: string;
  /** 是否显示底部控制条（纯净模式可隐藏）。 */
  showControls?: boolean;
  children: React.ReactNode;
}

const SWIPE_THRESHOLD = 50;
const INTERACTIVE_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT", "BUTTON"]);

export default function Pager({
  index,
  total,
  onPrev,
  onNext,
  onExit,
  progressText,
  showControls = true,
  children,
}: PagerProps) {
  const touchStartX = useRef<number | null>(null);

  useEffect(() => {
    function handleKey(event: KeyboardEvent): void {
      const tag = (event.target as HTMLElement | null)?.tagName ?? "";
      const interactive = INTERACTIVE_TAGS.has(tag);

      if (event.key === "ArrowLeft") {
        event.preventDefault();
        onPrev();
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        onNext();
        return;
      }
      if (event.key === "Escape") {
        onExit?.();
        return;
      }
      if (event.key === " " && !interactive) {
        event.preventDefault();
        onNext();
      }
    }

    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onPrev, onNext, onExit]);

  function handleTouchStart(event: React.TouchEvent<HTMLDivElement>): void {
    touchStartX.current = event.touches[0]?.clientX ?? null;
  }

  function handleTouchEnd(event: React.TouchEvent<HTMLDivElement>): void {
    const start = touchStartX.current;
    touchStartX.current = null;
    if (start === null) {
      return;
    }
    const end = event.changedTouches[0]?.clientX ?? start;
    const delta = end - start;
    if (Math.abs(delta) < SWIPE_THRESHOLD) {
      return;
    }
    if (delta < 0) {
      onNext();
    } else {
      onPrev();
    }
  }

  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      data-testid="pager"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      <div className="min-h-0 flex-1">{children}</div>
      {showControls ? (
        <footer
          data-testid="pager-controls"
          className="flex items-center justify-between gap-4 px-6 py-3"
        >
          <button
            type="button"
            onClick={onPrev}
            disabled={index <= 0}
            className="rounded-md border border-slate-300 px-4 py-2 text-base text-slate-700 disabled:opacity-40"
          >
            ← 上一页
          </button>
          <span data-testid="pager-progress" className="text-base text-slate-500">
            {progressText ?? `${Math.min(index + 1, total)} / ${total}`}
          </span>
          <button
            type="button"
            onClick={onNext}
            disabled={index >= total - 1}
            className="rounded-md border border-slate-300 px-4 py-2 text-base text-slate-700 disabled:opacity-40"
          >
            下一页 →
          </button>
        </footer>
      ) : null}
    </div>
  );
}
