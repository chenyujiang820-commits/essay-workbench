/**
 * 讲评投屏：书式左右两栏逐篇展示，课堂讲评表彰用。
 *
 * 版式（真机智慧黑板 1920x1080 反馈后重做）：
 * * 抬头第一行**标题**、第二行**作者**（＋精选徽标），正文在其下方；
 * * 正文按**栏宽**测量后贪心分栏，相邻两栏配成一屏 —— 像翻书一样左一栏右一栏；
 * * 字号三档（小 22 / 中 28 / 大 36 px），默认「中」：实测一篇 323 字作文一屏放得下；
 * * 视口 < 1180px（投影 1024、平板竖屏）自动退回单栏，避免 11 字/行的窄栏；
 * * 单个超长段按字数切栏，切不动了再缩字号，任何视口下都不裁内容。
 *
 * 交互：
 * * 键盘 ←/→（上一/下一屏）、空格（下一屏）、Esc（退出）；触屏左右滑动；
 * * 翻页按钮的禁用与否由**全局位置**决定（见 hasPrevPage / hasNextPage）：
 *   用篇内屏号判定会让每篇最后一屏的按钮变灰，触屏翻不到下一篇（真机 GAP-08）；
 * * 底部进度：篇数少用进度点，篇数多自动切换为文字进度，避免溢出；
 * * 可切「纯净模式」隐藏工具栏。
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { PresentData } from "../api/types";
import Pager from "../components/Pager";
import { formatParagraphsForDisplay } from "../lib/textLayout";

/** 无标题作文的兜底文案（FR-11：区别于误导性的「无题」，与成册模板同口径）。 */
export const UNTITLED_LABEL = "未命名";

/** 单段文本在测量容器中的位置（相对容器顶边）。 */
export interface ScreenBox {
  top: number;
  bottom: number;
}

/** 无正文时的占位屏文案（保住「不丢内容」保证：宁可显示占位，也不渲染空白页）。 */
export const PLACEHOLDER_TEXT = "（正文待补）";

/** 正文行高：PRD 要求投屏行距 >= 1.8，肉眼远读才不累。 */
export const BODY_LINE_HEIGHT = 1.8;

/** 正文段样式：段间距用 em（跟着字号缩放），首行缩进两字。 */
const BODY_PARAGRAPH_CLASS = "mb-[0.9em] break-words indent-8";

/**
 * 两栏书式排布的最小视口宽度。
 *
 * 低于它（投影 1024、笔记本半屏、平板竖屏）就退回单栏：真机量过 1152px 视口下
 * 两栏的每栏只有 11 字/行，比单栏更难读。智慧黑板 1920 走两栏。
 */
export const TWO_COLUMN_MIN_WIDTH = 1180;

/** 字号档位（正文基准字号 px）：默认「中」= 28px，实测 1920x1080 一屏约 900 字。 */
export const FONT_SIZE_PX = { sm: 22, md: 28, lg: 36 } as const;

export type FontSizeTier = keyof typeof FONT_SIZE_PX;

export const DEFAULT_FONT_TIER: FontSizeTier = "md";

/** 工具栏字号按钮的顺序与文案。 */
export const FONT_TIERS: ReadonlyArray<{ tier: FontSizeTier; label: string }> = [
  { tier: "sm", label: "小" },
  { tier: "md", label: "中" },
  { tier: "lg", label: "大" },
];

/** 纯函数：按视口宽度决定栏数（>= 阈值为两栏书式，否则单栏）。 */
export function computeColumnCount(viewportWidth: number): number {
  return viewportWidth >= TWO_COLUMN_MIN_WIDTH ? 2 : 1;
}

/** 一篇作文的排版结果：各屏（每屏 1~2 栏，每栏一串段落）+ 各屏字号缩放。 */
export interface ScreenLayout {
  screens: string[][][];
  scales: number[];
  /** 本次分栏是否为「两栏均分」（见 paginateColumns）；单栏或不均衡时为 false。 */
  balanced: boolean;
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

/** 一栏的内容：段落序列 + 未缩放时的实际内容高度（px）。 */
export interface ColumnContent {
  paragraphs: string[];
  height: number;
}

/** 单个超长段最多切成几栏：再多就交给字号缩放，不能无限切。 */
export const MAX_COLUMN_PAGES = 8;

/**
 * 纯函数：内容超高时按比例缩小字号（下限 0.4），保证一屏文字完整可见、不被裁掉。
 * 与 computeScreenScales 同一口径，只是作用在「一栏」而不是「一屏」。
 */
export function shrinkToFit(height: number, contentHeight: number): number {
  if (contentHeight <= 0 || contentHeight <= height) {
    return 1;
  }
  return Math.max(0.4, Math.min(1, height / contentHeight));
}

/**
 * 纯函数：把「一个段落就把整栏撑爆」的栏按字数切成多栏。
 *
 * 真机正文只有 2~3 段（v1.2 rev.4 起后端已把 OCR 硬换行合并回自然段），
 * 不按字数切栏的话，一段就占满一栏甚至一屏，正是「一页只放得下几个字」的另一半原因。
 * 只含单段且超高才切；多段栏交给 paginateColumns 的贪心分栏，保持 A1「不丢内容」保证。
 */
export function splitOversizedColumn(column: ColumnContent, height: number): ColumnContent[] {
  if (column.paragraphs.length !== 1 || column.height <= height || height <= 0) {
    return [column];
  }
  const paragraph = column.paragraphs[0];
  if (paragraph.length < 2) {
    return [column];
  }
  const pages = Math.min(MAX_COLUMN_PAGES, Math.ceil(column.height / height));
  const perPage = Math.max(1, Math.floor(paragraph.length / pages));
  const pieces: ColumnContent[] = [];
  for (let start = 0; start < paragraph.length; start += perPage) {
    const text = paragraph.slice(start, start + perPage);
    pieces.push({
      paragraphs: [text],
      height: (column.height * text.length) / paragraph.length,
    });
  }
  return pieces;
}

/**
 * 纯函数：段落 → 栏 → 屏。
 *
 * 先用 computeScreens 按「一栏的高度」贪心分栏（boxes 必须是**栏宽**下的测量结果，
 * 否则换行数会算错），再把相邻两栏配成一屏 —— 书式左右两栏即由此而来。
 * 每屏的字号缩放取该屏最高栏的收缩比，保证任何视口下都不裁内容。
 */
function columnsFrom(
  paragraphs: string[],
  boxes: ScreenBox[],
  packHeight: number,
): ColumnContent[] {
  const columns: ColumnContent[] = [];
  let cursor = 0;
  for (const group of computeScreens(paragraphs, boxes, packHeight)) {
    const firstTop = boxes[cursor]?.top ?? 0;
    const lastBottom = boxes[cursor + group.length - 1]?.bottom ?? 0;
    columns.push(
      ...splitOversizedColumn({ paragraphs: group, height: lastBottom - firstTop }, packHeight),
    );
    cursor += group.length;
  }
  return columns;
}

export function paginateColumns(
  paragraphs: string[],
  boxes: ScreenBox[],
  height: number,
  columnCount: number,
): ScreenLayout {
  const size = Math.max(1, columnCount);
  let columns = columnsFrom(paragraphs, boxes, height);
  let packHeight = height;
  let balanced = false;

  // 一屏装得下、却没装满（真机上 308 字那篇只占了左半栏，右半块黑板发白）时，
  // 二分出「仍然装得进 size 栏」的最小栏高，让两栏尽量均分 —— 等价于 CSS column-fill: balance。
  // 下界取「最高的那一段」，保证均分不会反过来把字号压小；栏数一多就判不可行，A1 优先于好看。
  if (size > 1 && Number.isFinite(height) && columns.length > 0 && columns.length < size) {
    const tallestParagraph = boxes.reduce(
      (max, box) => Math.max(max, box.bottom - box.top),
      0,
    );
    if (tallestParagraph > 0 && tallestParagraph <= height) {
      let low = Math.max(1, Math.ceil(tallestParagraph));
      let high = Math.floor(height);
      while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (columnsFrom(paragraphs, boxes, middle).length <= size) {
          high = middle;
        } else {
          low = middle + 1;
        }
      }
      const candidate = columnsFrom(paragraphs, boxes, low);
      if (candidate.length <= size) {
        columns = candidate;
        packHeight = low;
        balanced = true;
      }
    }
  }
  const screens: string[][][] = [];
  const scales: number[] = [];
  for (let start = 0; start < columns.length; start += size) {
    const group = columns.slice(start, start + size);
    screens.push(group.map((column) => column.paragraphs));
    const tallest = group.reduce((max, column) => Math.max(max, column.height), 0);
    scales.push(shrinkToFit(packHeight, tallest));
  }
  if (screens.length === 0) {
    screens.push([[PLACEHOLDER_TEXT]]);
    scales.push(1);
  }
  return { screens, scales, balanced };
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
  const [order, setOrder] = useState("student_no");
  const [directoryOpen, setDirectoryOpen] = useState(true);
  const [fontSize, setFontSize] = useState<FontSizeTier>(DEFAULT_FONT_TIER);
  /** 视口宽度：决定两栏还是单栏（真机智慧黑板 1920，笔记本/投影 1024 退单栏）。 */
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);

  /** 每篇作文的排版缓存，key 含视口宽度与字号档（变了必须重排）。 */
  const layoutsRef = useRef<Map<string, ScreenLayout>>(new Map());
  /** 触发重新测量的计数器（视口尺寸变化时 +1）。 */
  const [measureTick, setMeasureTick] = useState(0);
  /** 当前篇的排版结果；null 表示处于测量阶段。 */
  const [layout, setLayout] = useState<ScreenLayout | null>(null);
  const measureRef = useRef<HTMLDivElement | null>(null);
  /** 正文区容器：它的高度才是每栏的可用高度（抬头已排除在外）。 */
  const bodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .fetchPresent(numericIssueId, order)
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
  }, [numericIssueId, order]);

  // 视口尺寸变化（换投影仪 / 旋转屏幕 / 拖窗口）→ 记宽度并清缓存重排。
  useEffect(() => {
    const onResize = (): void => {
      layoutsRef.current.clear();
      setViewportWidth(window.innerWidth);
      setScreenIndex(0);
      setMeasureTick((tick) => tick + 1);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const items = useMemo(() => data?.items ?? [], [data]);
  const essayCount = items.length;
  const safeEssayIndex = Math.min(essayIndex, Math.max(essayCount - 1, 0));
  const currentItem = items[safeEssayIndex] ?? null;
  const paragraphs = useMemo(
    () =>
      formatParagraphsForDisplay(
        currentItem && currentItem.paragraphs.length > 0
          ? currentItem.paragraphs
          : [PLACEHOLDER_TEXT],
      ),
    [currentItem],
  );

  const columnCount = computeColumnCount(viewportWidth);
  const baseFontPx = FONT_SIZE_PX[fontSize];
  const layoutKey = safeEssayIndex + ":" + viewportWidth + ":" + fontSize;

  // 篇目切换、换字号、换视口或重新测量时：优先用缓存，否则进入测量阶段。
  useEffect(() => {
    setLayout(layoutsRef.current.get(layoutKey) ?? null);
  }, [layoutKey, measureTick, paragraphs, columnCount]);

  // 测量阶段：全部段落已按栏宽渲染，按实际高度切成栏 → 两栏配成一屏 → 算缩放。
  useLayoutEffect(() => {
    if (layout !== null || loading || error || !currentItem) {
      return;
    }
    const measureBox = measureRef.current;
    const groupBox = bodyRef.current;
    if (!measureBox || !groupBox) {
      // 数据与 loading 可能分属两次提交：此刻测量容器还没挂上，
      // 直接 return 会永久停在测量态（正文再也不渲染）。借用重测计数器再来一次。
      setMeasureTick((tick) => tick + 1);
      return;
    }
    const boxTop = measureBox.getBoundingClientRect().top;
    // bottom 计入段间距：rect.bottom 不含 margin-bottom，漏算会让末段被裁掉。
    const boxes: ScreenBox[] = Array.from(
      measureBox.querySelectorAll<HTMLElement>("[data-paragraph]"),
    ).map((el) => {
      const rect = el.getBoundingClientRect();
      const marginBottom = Number.parseFloat(window.getComputedStyle(el).marginBottom) || 0;
      return { top: rect.top - boxTop, bottom: rect.bottom - boxTop + marginBottom };
    });
    // 容器无有效高度（测试环境）时退化成不切栏，避免按假高度乱排。
    const height = groupBox.clientHeight > 0 ? groupBox.clientHeight : Number.MAX_SAFE_INTEGER;
    const computed = paginateColumns(paragraphs, boxes, height, columnCount);
    layoutsRef.current.set(layoutKey, computed);
    setLayout(computed);
  }, [layout, paragraphs, layoutKey, columnCount, loading, error, currentItem]);

  const screenCount = layout?.screens.length ?? 1;
  const safeScreenIndex = Math.min(screenIndex, screenCount - 1);
  const currentScale = layout?.scales[safeScreenIndex] ?? 1;
  const columns = layout?.screens[safeScreenIndex] ?? [];

  /**
   * 跨篇能力必须由**全局位置**决定：篇内屏号 index/total 判 disabled 会让每篇最后一屏
   * 的按钮变灰，触屏（智慧黑板 / 手机）就此翻不过去（真机反馈 GAP-08）。
   */
  const hasPrevPage = safeEssayIndex > 0 || safeScreenIndex > 0;
  const hasNextPage = safeEssayIndex < essayCount - 1 || safeScreenIndex < screenCount - 1;

  const goPrev = useCallback(() => {
    if (screenIndex > 0) {
      setScreenIndex(screenIndex - 1);
      return;
    }
    if (essayIndex > 0) {
      const prev = essayIndex - 1;
      setEssayIndex(prev);
      // 上一屏屏数未知时先指向「最后一屏」，测量完成后由 safeScreenIndex 收敛。
      const cached = layoutsRef.current.get(prev + ":" + viewportWidth + ":" + fontSize);
      setScreenIndex(cached ? cached.screens.length - 1 : Number.MAX_SAFE_INTEGER);
    }
  }, [screenIndex, essayIndex, viewportWidth, fontSize]);

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

  const goPreviousEssay = useCallback(() => {
    if (essayIndex > 0) {
      setEssayIndex((index) => index - 1);
      setScreenIndex(0);
    }
  }, [essayIndex]);

  const goNextEssay = useCallback(() => {
    if (essayIndex < essayCount - 1) {
      setEssayIndex((index) => index + 1);
      setScreenIndex(0);
    }
  }, [essayIndex, essayCount]);

  const exit = useCallback(() => navigate("/issues/" + issueId + "/essays"), [navigate, issueId]);

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
  if (essayCount === 0) {
    return (
      <main className="p-10 text-lg text-slate-500">
        该期暂无作文，无法投屏。
        <button type="button" onClick={exit} className="ml-2 underline">
          返回看板
        </button>
      </main>
    );
  }

  const progressText = (safeEssayIndex + 1) + " / " + essayCount;
  // 顶栏把「为什么篇数比拍的对不上」说清楚（GAP-14）：未定稿的进轮播但标初稿，完全没文字的才排除。
  const draftCount = data?.draft_count ?? 0;
  const excludedCount = data?.excluded_no_text ?? 0;
  const columnBoxClass = columnCount === 2 ? "w-[calc(50%-1.5rem)]" : "w-full";
  const bodyClass = columnCount === 2 ? "grid grid-cols-2 gap-12" : "";

  return (
    <main className="relative flex h-screen w-full flex-col bg-slate-50 text-slate-900">
      {!pure ? (
        <header className="flex items-center justify-between border-b border-slate-200 px-8 py-3">
          <div>
            <p className="text-xl font-semibold">{data?.class_name}</p>
            <p className="text-sm text-slate-500">
              第 {data?.issue_no} 期 · {progressText}
              {draftCount > 0 ? (
                <span data-testid="present-draft-count">{
                  ` · ${draftCount} 篇未定稿（黑板上显示的是识别初稿）`
                }</span>
              ) : null}
              {excludedCount > 0 ? (
                <span data-testid="present-excluded-count" className="text-orange-600">{
                  ` · 另有 ${excludedCount} 篇暂无文字，未进轮播`
                }</span>
              ) : null}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1 text-sm text-slate-500 lg:hidden">
              作文
              <select
                data-testid="present-mobile-directory"
                value={safeEssayIndex}
                onChange={(event) => {
                  setEssayIndex(Number(event.target.value));
                  setScreenIndex(0);
                }}
                className="max-w-32 rounded border border-slate-300 bg-white px-2 py-1 text-sm text-slate-700"
              >
                {items.map((item, index) => (
                  <option key={`${item.student_no}-${index}`} value={index}>
                    {(item.name || "未命名学生") + " · " + (item.title || UNTITLED_LABEL)}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              data-testid="present-directory-toggle"
              onClick={() => setDirectoryOpen((open) => !open)}
              className="hidden rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700 lg:inline"
            >
              {directoryOpen ? "收起目录" : "打开目录"}
            </button>
            <button
              type="button"
              data-testid="present-prev-essay"
              disabled={safeEssayIndex <= 0}
              onClick={goPreviousEssay}
              className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700 disabled:opacity-40"
            >
              上一篇
            </button>
            <button
              type="button"
              data-testid="present-next-essay"
              disabled={safeEssayIndex >= essayCount - 1}
              onClick={goNextEssay}
              className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700 disabled:opacity-40"
            >
              下一篇
            </button>
            <label className="hidden items-center gap-1 text-sm text-slate-500 lg:flex">
              目录排序
              <select
                data-testid="present-order"
                value={order}
                onChange={(event) => setOrder(event.target.value)}
                className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-700"
              >
                <option value="student_no">按学号</option>
                <option value="selected_score">精选优先</option>
              </select>
            </label>
            <span className="mr-1 text-sm text-slate-400">字号</span>
            {FONT_TIERS.map((tier) => (
              <button
                key={tier.tier}
                type="button"
                data-testid={"present-font-" + tier.tier}
                aria-label={"字号" + tier.label}
                onClick={() => {
                  layoutsRef.current.clear();
                  setFontSize(tier.tier);
                  setScreenIndex(0);
                  setMeasureTick((tick) => tick + 1);
                }}
                className={
                  tier.tier === fontSize
                    ? "rounded-md border border-slate-800 bg-slate-800 px-3 py-1 text-sm text-white"
                    : "rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700"
                }
              >
                {tier.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setPure(true)}
              className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700"
            >
              纯净模式
            </button>
            <button
              type="button"
              onClick={exit}
              className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700"
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

      <div className="flex min-h-0 flex-1">
        {!pure && directoryOpen ? (
          <aside data-testid="present-directory" className="hidden w-64 shrink-0 overflow-y-auto border-r border-slate-200 bg-white px-3 py-4 lg:block">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-sm font-semibold text-slate-700">作文目录</p>
              <span className="text-xs text-slate-400">{essayCount} 篇</span>
            </div>
            <ol className="space-y-1">
              {items.map((item, index) => (
                <li key={`${item.student_no}-${index}`}>
                  <button
                    type="button"
                    data-testid={`present-directory-item-${index}`}
                    aria-current={index === safeEssayIndex ? "true" : undefined}
                    onClick={() => {
                      setEssayIndex(index);
                      setScreenIndex(0);
                    }}
                    className={`w-full rounded-md px-2 py-2 text-left text-sm ${index === safeEssayIndex ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"}`}
                  >
                    <span className="block truncate">{item.name || "未命名学生"}</span>
                    <span className={`block truncate text-xs ${index === safeEssayIndex ? "text-slate-300" : "text-slate-400"}`}>
                      {item.title || UNTITLED_LABEL}{item.is_selected ? " · 精选" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          </aside>
        ) : null}
        <Pager
          index={safeScreenIndex}
          total={screenCount}
          hasPrevPage={hasPrevPage}
          hasNextPage={hasNextPage}
          onPrev={goPrev}
          onNext={goNext}
          onExit={exit}
          progressText={progressText}
          showControls={!pure}
        >
          <article
            data-testid="present-slide"
            className="flex h-full w-full flex-col px-10 py-6"
          >
          <header data-testid="present-header" className="shrink-0 text-center">
            <h1
              data-testid="present-title"
              className="break-words text-[2.5rem] font-bold leading-tight"
            >
              {currentItem?.title || UNTITLED_LABEL}
            </h1>
            <p className="mt-2 text-xl leading-7 text-slate-600">
              <span data-testid="present-name">{currentItem?.name}</span>
              {currentItem?.is_selected ? (
                <span
                  data-testid="present-selected"
                  className="ml-3 rounded-full bg-amber-400 px-3 py-0.5 text-sm font-medium text-slate-900"
                >
                  精选
                </span>
              ) : null}
              {currentItem?.is_draft ? (
                <span
                  data-testid="present-draft"
                  className="ml-3 rounded-full bg-orange-100 px-3 py-0.5 text-sm font-medium text-orange-700"
                >
                  未定稿 · 识别初稿
                </span>
              ) : null}
              {safeScreenIndex > 0 ? (
                <span data-testid="present-continuation" className="ml-3 text-slate-400">
                  （续）
                </span>
              ) : null}
            </p>
          </header>

          <div ref={bodyRef} className="relative min-h-0 flex-1">
            <div className="mx-auto h-full w-full max-w-[1600px] text-left">
              {layout === null ? (
                <div ref={measureRef} aria-hidden="true" className="invisible">
                  <div
                    className={columnBoxClass}
                    style={{ fontSize: baseFontPx + "px", lineHeight: BODY_LINE_HEIGHT }}
                  >
                    {paragraphs.map((paragraph, position) => (
                      <p key={position} data-paragraph className={BODY_PARAGRAPH_CLASS}>
                        {paragraph}
                      </p>
                    ))}
                  </div>
                </div>
              ) : (
                <div
                  data-testid="present-body"
                  className={bodyClass}
                  style={{
                    fontSize: Math.round(baseFontPx * currentScale) + "px",
                    lineHeight: BODY_LINE_HEIGHT,
                  }}
                >
                  {columns.map((column, columnIndex) => (
                    <div key={columnIndex} data-testid="present-column" className="min-w-0">
                      {column.map((paragraph, position) => (
                        <p key={position} className={BODY_PARAGRAPH_CLASS}>
                          {paragraph}
                        </p>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          </article>
        </Pager>
      </div>

      {!pure ? (
        <footer className="flex items-center justify-center gap-3 py-2">
          {essayCount <= 12 ? (
            items.map((item, position) => (
              <span
                key={item.student_no + "-" + position}
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
          <span data-testid="present-screen-progress" className="text-sm text-slate-400">
            第 {safeScreenIndex + 1} / {screenCount} 屏 · 共 {essayCount} 篇
          </span>
        </footer>
      ) : null}
    </main>
  );
}
