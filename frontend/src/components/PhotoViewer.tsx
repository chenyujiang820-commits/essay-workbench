/** 原片查看：react-zoom-pan-pinch 手势缩放（手机双指 / 电脑滚轮）。 */

import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";

export interface PhotoViewerProps {
  /** 原片 objectURL；为空表示尚未加载完成。 */
  url: string | null;
  /** 照片序号（用于无障碍文案）。 */
  seq: number;
  /** 该原片画质低于手写识别门槛（后端 photo.low_resolution=1）→ 角标提示。 */
  lowResolution?: boolean;
}

export default function PhotoViewer({ url, seq, lowResolution = false }: PhotoViewerProps) {
  return (
    <div className="relative h-full min-h-[240px] w-full overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
      {lowResolution ? (
        <p
          data-testid="photo-low-resolution"
          className="absolute bottom-2 left-2 z-10 max-w-[calc(100%-1rem)] break-words rounded bg-amber-200/95 px-2 py-1 text-xs font-medium text-amber-900"
        >
          第 {seq} 张画质偏低，看不清就先放大再看
        </p>
      ) : null}
      {url ? (
        <TransformWrapper minScale={0.5} maxScale={6} doubleClick={{ disabled: false }}>
          {({ zoomIn, zoomOut, resetTransform }) => (
            <>
              <div className="absolute right-2 top-2 z-10 flex gap-1">
                <button
                  type="button"
                  aria-label="放大"
                  className="rounded bg-white/90 px-2 py-1 text-sm shadow"
                  onClick={() => zoomIn()}
                >
                  ＋
                </button>
                <button
                  type="button"
                  aria-label="缩小"
                  className="rounded bg-white/90 px-2 py-1 text-sm shadow"
                  onClick={() => zoomOut()}
                >
                  －
                </button>
                <button
                  type="button"
                  aria-label="复位"
                  className="rounded bg-white/90 px-2 py-1 text-sm shadow"
                  onClick={() => resetTransform()}
                >
                  复位
                </button>
              </div>
              <TransformComponent
                wrapperStyle={{ width: "100%", height: "100%" }}
                contentStyle={{ width: "100%", height: "100%" }}
              >
                <img
                  src={url}
                  alt={`第 ${seq} 张原片`}
                  className="h-full w-full object-contain"
                  draggable={false}
                />
              </TransformComponent>
            </>
          )}
        </TransformWrapper>
      ) : (
        <div className="flex h-full items-center justify-center text-sm text-slate-400">
          原片加载中…
        </div>
      )}
    </div>
  );
}
