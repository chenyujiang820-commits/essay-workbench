/**
 * 讲评投屏：横版大字号逐篇展示，课堂表彰用。
 *
 * * 顶部：班级名 + 第 N 期 + 进度「3 / 45」；
 * * 中央：学生姓名（大号）+ 标题（特大号）+ 正文（≥28px，行高 1.8）；
 * * 正文按容器实际高度测量后贪心切分成多屏；单段过高的屏自动缩小字号兜底，
 *   任何视口下都不裁切内容；
 * * 键盘 ←/→（上一/下一屏）、空格（下一屏）、Esc（退出）；触屏左右滑动；
 * * 底部进度：篇数少用进度点，篇数多自动切换为文字进度，避免溢出；
 * * 精选篇目醒目徽标；可切「纯净模式」隐藏工具栏。
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { PresentData } from "../api/types";
import Pager from "../components/Pager";

/** 单段文本在测量容器中的位置（相对容器顶边）。 */
export interface ScreenBox {
  top: number;
  bottom: number;
}

/** 一篇作文的切屏结果：各屏段落 + 各屏字号缩放。 */
export interface ScreenLayout {
  screens: string[][];
  scales: number[];
}

/**
 * 纯函数：按容器高度把段落贪心切分成多屏。
 * boxes[i] 为第 i 段渲染后的 [top, bottom]（相对测量容器）；height 为容器可用高度。
 * 首屏会显示姓名/标题抬头、续屏显示「（续）」标签，分别用两个预留值从预算中扣除。
 * 规则：同屏内最后一段的 bottom 与首段 top 之差不超过可用高度；单段超高时独立成屏。
 */
export function computeScreens(
  paragraphs: string[],
  boxes: ScreenBox[],
  height: number,
  firstScreenReserve = 0,
  continuationReserve = 0,
): string[][] {
  if (paragraphs.length === 0) {
    return [["（正文待补）"]];
  }
  const screens: string[][] = [];
  let start = 0;
  let isFirstScreen = true;
  while (start < paragraphs.length) {
    const available = height - (isFirstScreen ? firstScreenReserve : continuationReserve);
    const baseline = boxes[start]?.top ?? 0;
    let end = start;
    while (
      end < paragraphs.length &&
      (boxes[end]?.bottom ?? Number.MAX_SAFE_INTEGER) - baseline <= available
    ) {
      end += 1;
    }
    if (end === start) {
      end = start + 1; // 单段超出容器高度也要占一屏，不能丢内容
    }
    screens.push(paragraphs.slice(start, end));
    start = end;
    isFirstScreen = false;
  }
  return screens;
}

/**
 * 纯函数：按各屏实际内容高度计算字号缩放。
 * 内容超高（如手机窄屏单段过长）时按比例缩小字号（下限 0.4），保证整屏文字可见。
 */
export function computeScreenScales(
  screens: string[][],
  boxes: ScreenBox[],
  height: number,
  firstScreenReserve = 0,
  continuationReserve = 0,
): number[] {
  let startIndex = 0;
  return screens.map((screen, screenNo) => {
    const start = startIndex;
    startIndex += screen.length;
    const available = height - (screenNo === 0 ? firstScreenReserve : continuationReserve);
    const firstTop = boxes[start]?.top ?? 0;
    const lastBottom = boxes[start + screen.length - 1]?.bottom ?? 0;
    const contentHeight = lastBottom - firstTop;
    if (contentHeight <= 0 || contentHeight <= available) {
      return 1;
    }
    return Math.max(0.4, Math.min(1, available / contentHeight));
  });
}

export default function PresentPage() {
  const { issueId } = useParams();
  const navigate = useNavigate();
  const numericIssueId = Number(issueId);

  const [data, setData] = useState<PresentData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [essayIndex, setEssayIndex] = useState(0);
  const [screenIndex, setScreenIndex] = useState(0);
  const [pure, setPure] = useState(false);

  /** 每篇作文的切屏结果缓存（按篇索引），来回翻页不重测。 */
  const layoutsRef = useRef<Map<number, ScreenLayout>>(new Map());
  /** 触发重新测量的计数器（视口尺寸变化时 +1）。 */
  const [measureTick, setMeasureTick] = useState(0);
  /** 当前篇的切屏结果；null 表示处于测量阶段。 */
  const [layout, setLayout] = useState<ScreenLayout | null>(null);
  const measureRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .fetchPresent(numericIssueId)
      .then((payload) => {
        if (!cancelled) {
          layoutsRef.current.clear();
          setData(payload);
          setEssayIndex(0);
          setScreenIndex(0);
          setLayout(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "投屏数据加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [numericIssueId]);

  // 视口尺寸变化（换投影仪 / 旋转屏幕）→ 清缓存重新切屏。
  useEffect(() => {
    const onResize = (): void => {
      layoutsRef.current.clear();
      setScreenIndex(0);
      setMeasureTick((tick) => tick + 1);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const items = useMemo(() => data?.items ?? [], [data]);
  const essayCount = items.length;
  const safeEssayIndex = Math.min(essayIndex, Math.max(essayCount - 1, 0));
  const currentItem = items[safeEssayIndex] ?? null;
  const paragraphs = useMemo(
    () =>
      currentItem && currentItem.paragraphs.length > 0 ? currentItem.paragraphs : ["（正文待补）"],
    [currentItem],
  );

  // 篇目切换或重新测量时：优先用缓存，否则进入测量阶段。
  useEffect(() => {
    setLayout(layoutsRef.current.get(safeEssayIndex) ?? null);
  }, [safeEssayIndex, measureTick, paragraphs]);

  // 测量阶段：全部段落已渲染，按实际高度贪心切分 + 计算字号缩放。
  useLayoutEffect(() => {
    if (layout !== null) {
      return;
    }
    const box = measureRef.current;
    if (!box) {
      return;
    }
    const boxTop = box.getBoundingClientRect().top;
    const boxes: ScreenBox[] = Array.from(
      box.querySelectorAll<HTMLElement>("[data-paragraph]"),
    ).map((el) => {
      const rect = el.getBoundingClientRect();
      return { top: rect.top - boxTop, bottom: rect.bottom - boxTop };
    });
    // 容器无有效高度（测试环境）时退化为单屏，避免按假高度乱切。
    const height = box.clientHeight > 0 ? box.clientHeight : Number.MAX_SAFE_INTEGER;
    // 首屏姓名/标题抬头与续屏「（续）」标签的垂直预留（含安全边距）。
    const firstReserve = 208;
    const continuationReserve = 48;
    const screens = computeScreens(paragraphs, boxes, height, firstReserve, continuationReserve);
    const scales = computeScreenScales(
      screens,
      boxes,
      height,
      firstReserve,
      continuationReserve,
    );
    const computed: ScreenLayout = { screens, scales };
    layoutsRef.current.set(safeEssayIndex, computed);
    setLayout(computed);
  }, [layout, paragraphs, safeEssayIndex]);

  const screenCount = layout?.screens.length ?? 1;
  const safeScreenIndex = Math.min(screenIndex, screenCount - 1);
  const currentScale = layout?.scales[safeScreenIndex] ?? 1;

  const goPrev = useCallback(() => {
    if (screenIndex > 0) {
      setScreenIndex(screenIndex - 1);
      return;
    }
    if (essayIndex > 0) {
      const prev = essayIndex - 1;
      setEssayIndex(prev);
      // 上一屏屏数未知时先指向「最后一屏」，测量完成后由 safeScreenIndex 收敛。
      const cached = layoutsRef.current.get(prev);
      setScreenIndex(cached ? cached.screens.length - 1 : Number.MAX_SAFE_INTEGER);
    }
  }, [screenIndex, essayIndex]);

  const goNext = useCallback(() => {
    if (screenIndex < screenCount - 1) {
      setScreenIndex(screenIndex + 1);
      return;
    }
    if (essayIndex < essayCount - 1) {
      setEssayIndex(essayIndex + 1);
      setScreenIndex(0);
    }
  }, [screenIndex, screenCount, essayIndex, essayCount]);

  const exit = useCallback(() => navigate(`/issues/${issueId}/essays`), [navigate, issueId]);

  if (loading) {
    return <main className="p-10 text-lg text-slate-500">加载中…</main>;
  }
  if (error) {
    return (
      <main className="p-10">
        <p className="rounded-md bg-rose-50 px-4 py-3 text-lg text-rose-600">{error}</p>
        <button type="button" onClick={exit} className="mt-4 text-slate-500 underline">
          返回看板
        </button>
      </main>
    );
  }
  if (!data || !currentItem) {
    return (
      <main className="p-10 text-lg text-slate-500">
        该期暂无作文，无法投屏。
        <button type="button" onClick={exit} className="ml-2 underline">
          返回看板
        </button>
      </main>
    );
  }

  const progressText = `${safeEssayIndex + 1} / ${essayCount}`;

  return (
    <main className="relative flex h-screen w-full flex-col bg-slate-50 text-slate-900">
      {!pure ? (
        <header className="flex items-center justify-between border-b border-slate-200 px-8 py-4">
          <div>
            <p className="text-2xl font-semibold">{data.class_name}</p>
            <p className="text-lg text-slate-500">
              第 {data.issue_no} 期 · {progressText}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setPure(true)}
              className="rounded-md border border-slate-300 px-4 py-2 text-slate-700"
            >
              纯净模式
            </button>
            <button
              type="button"
              onClick={exit}
              className="rounded-md border border-slate-300 px-4 py-2 text-slate-700"
            >
              退出
            </button>
          </div>
        </header>
      ) : (
        <button
          type="button"
          aria-label="退出纯净模式"
          onClick={() => setPure(false)}
          className="absolute right-4 top-4 z-10 rounded-md border border-slate-300 bg-white/90 px-3 py-1 text-sm text-slate-700 shadow-sm"
        >
          退出纯净模式
        </button>
      )}

      <Pager
        index={safeScreenIndex}
        total={screenCount}
        onPrev={goPrev}
        onNext={goNext}
        onExit={exit}
        progressText={progressText}
        showControls={!pure}
      >
        <div className="relative h-full w-full">
          <div ref={measureRef} className="absolute inset-0 overflow-hidden">
            {layout === null ? (
              <div
                aria-hidden="true"
                className="invisible mx-auto h-full w-full max-w-6xl px-10 text-left text-[2rem] leading-[1.8]"
              >
                {paragraphs.map((paragraph, position) => (
                  <p key={position} data-paragraph className="mb-5 indent-8">
                    {paragraph}
                  </p>
                ))}
              </div>
            ) : (
              <article
                data-testid="present-slide"
                className="flex h-full flex-col items-center justify-center px-10 text-center"
              >
                {safeScreenIndex === 0 ? (
                  <>
                    <div className="mb-3 flex items-center gap-4">
                      <span data-testid="present-name" className="text-present font-semibold">
                        {currentItem.name}
                      </span>
                      {currentItem.is_selected ? (
                        <span className="rounded-full bg-amber-400 px-4 py-1 text-2xl font-medium text-slate-900">
                          精选
                        </span>
                      ) : null}
                    </div>
                    <h1 data-testid="present-title" className="mb-8 text-present-lg font-bold">
                      {currentItem.title || "无题"}
                    </h1>
                  </>
                ) : (
                  <p className="mb-4 text-lg text-slate-400">{currentItem.name} ·（续）</p>
                )}
                <div
                  className="w-full max-w-6xl overflow-hidden text-left text-[2rem] leading-[1.8]"
                  style={currentScale < 1 ? { fontSize: `${2 * currentScale}rem` } : undefined}
                >
                  {(layout.screens[safeScreenIndex] ?? []).map((paragraph, position) => (
                    <p key={position} className="mb-5 indent-8">
                      {paragraph}
                    </p>
                  ))}
                </div>
              </article>
            )}
          </div>
        </div>
      </Pager>

      {!pure ? (
        <footer className="flex items-center justify-center gap-2 py-3">
          {essayCount <= 12 ? (
            items.map((item, position) => (
              <span
                key={`${item.student_no}-${position}`}
                aria-hidden="true"
                className={
                  position === safeEssayIndex
                    ? "h-2 w-8 rounded-full bg-slate-800"
                    : "h-2 w-2 rounded-full bg-slate-300"
                }
              />
            ))
          ) : (
            <span className="text-sm text-slate-500">
              第 {safeEssayIndex + 1} 篇 · 共 {essayCount} 篇
            </span>
          )}
        </footer>
      ) : null}
    </main>
  );
}
