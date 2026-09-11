/**
 * 成册预览容器：A4 版面（794px 宽）按容器宽度等比缩放，
 * 手机等窄屏不再需要 iframe 内横向滚动。
 */

import { useEffect, useRef, useState } from "react";

/** A4 纵向预览的设计宽度（px）。 */
export const PREVIEW_DESIGN_WIDTH = 794;

/** 纯函数：容器宽度 → 预览缩放比（最小 0.25，最大 1）。 */
export function computePreviewScale(containerWidth: number): number {
  if (containerWidth <= 0) {
    return 1;
  }
  return Math.max(0.25, Math.min(1, containerWidth / PREVIEW_DESIGN_WIDTH));
}

const PREVIEW_HEIGHT = "72vh";

export default function PreviewFrame({ html }: { html: string }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const innerRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const inner = innerRef.current;
    if (!inner) {
      return;
    }
    const update = (): void => setScale(computePreviewScale(inner.clientWidth));
    update();
    // ResizeObserver 在 jsdom 不可用，退化为仅初始化一次。
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(update);
    observer.observe(inner);
    return () => observer.disconnect();
  }, [html]);

  return (
    <div
      ref={wrapRef}
      data-testid="preview-frame"
      className="mt-3 overflow-hidden rounded-md border border-slate-200 bg-slate-50"
      style={{ height: PREVIEW_HEIGHT }}
    >
      <div ref={innerRef} className="h-full w-full p-2">
        <iframe
          title="成册预览"
          data-testid="book-preview"
          srcDoc={html}
          className="block border-0 bg-white shadow-sm"
          style={{
            width: PREVIEW_DESIGN_WIDTH,
            height: `calc(${PREVIEW_HEIGHT} / ${scale})`,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
          }}
        />
      </div>
    </div>
  );
}
