/**
 * 讲评投屏：横版大字号逐篇展示，课堂表彰用。
 *
 * * 顶部：班级名 + 第 N 期 + 进度「3 / 45」；
 * * 中央：学生姓名（大号）+ 标题（特大号）+ 正文（≥28px，行高 1.8）；
 * * 正文过长按段落自动切分成多屏，翻页连续；
 * * 键盘 ←/→（上一/下一页）、空格（下一页）、Esc（退出）；触屏左右滑动；
 * * 底部进度点；精选篇目醒目徽标；可切「纯净模式」隐藏工具栏。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { BookItem, PresentData } from "../api/types";
import Pager from "../components/Pager";

/** 每屏最多容纳的段落数（超出则切分为多屏）。 */
export const PARAGRAPHS_PER_SCREEN = 6;

export interface Slide {
  essayIndex: number;
  item: BookItem;
  paragraphs: string[];
  withHeader: boolean;
}

/** 把逐篇作文按段落切分为连续幻灯片（首屏带标题/姓名抬头）。 */
export function buildSlides(items: BookItem[], perScreen: number = PARAGRAPHS_PER_SCREEN): Slide[] {
  const slides: Slide[] = [];
  items.forEach((item, essayIndex) => {
    const paragraphs = item.paragraphs.length > 0 ? item.paragraphs : ["（正文待补）"];
    for (let start = 0; start < paragraphs.length; start += perScreen) {
      slides.push({
        essayIndex,
        item,
        paragraphs: paragraphs.slice(start, start + perScreen),
        withHeader: start === 0,
      });
    }
  });
  return slides;
}

export default function PresentPage() {
  const { issueId } = useParams();
  const navigate = useNavigate();
  const numericIssueId = Number(issueId);

  const [data, setData] = useState<PresentData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);
  const [pure, setPure] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .fetchPresent(numericIssueId)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setIndex(0);
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

  const items = useMemo(() => data?.items ?? [], [data]);
  const slides = useMemo(() => buildSlides(items), [items]);
  const totalSlides = slides.length;
  const safeIndex = Math.min(index, Math.max(totalSlides - 1, 0));
  const current = slides[safeIndex] ?? null;

  const goPrev = useCallback(() => setIndex((value) => Math.max(0, value - 1)), []);
  const goNext = useCallback(
    () => setIndex((value) => Math.min(totalSlides - 1, value + 1)),
    [totalSlides],
  );
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
  if (!data || !current) {
    return (
      <main className="p-10 text-lg text-slate-500">
        该期暂无作文，无法投屏。
        <button type="button" onClick={exit} className="ml-2 underline">
          返回看板
        </button>
      </main>
    );
  }

  const progressText = `${current.essayIndex + 1} / ${items.length}`;

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
          className="absolute right-4 top-4 z-10 rounded-md bg-slate-900/10 px-3 py-1 text-sm text-slate-600"
        >
          退出纯净模式
        </button>
      )}

      <Pager
        index={safeIndex}
        total={totalSlides}
        onPrev={goPrev}
        onNext={goNext}
        onExit={exit}
        progressText={progressText}
        showControls={!pure}
      >
        <article
          data-testid="present-slide"
          className="flex h-full flex-col items-center justify-center px-10 text-center"
        >
          {current.withHeader ? (
            <>
              <div className="mb-3 flex items-center gap-4">
                <span data-testid="present-name" className="text-present font-semibold">
                  {current.item.name}
                </span>
                {current.item.is_selected ? (
                  <span className="rounded-full bg-amber-400 px-4 py-1 text-2xl font-medium text-slate-900">
                    精选
                  </span>
                ) : null}
              </div>
              <h1 data-testid="present-title" className="mb-8 text-present-lg font-bold">
                {current.item.title || "无题"}
              </h1>
            </>
          ) : (
            <p className="mb-4 text-lg text-slate-400">
              {current.item.name} · （续）
            </p>
          )}
          <div className="max-h-[68vh] w-full max-w-6xl overflow-hidden text-left text-[2rem] leading-[1.8]">
            {current.paragraphs.map((paragraph, position) => (
              <p key={position} className="mb-5 indent-8">
                {paragraph}
              </p>
            ))}
          </div>
        </article>
      </Pager>

      {!pure ? (
        <footer className="flex items-center justify-center gap-2 py-3">
          {items.map((item, position) => (
            <span
              key={`${item.student_no}-${position}`}
              aria-hidden="true"
              className={
                position === current.essayIndex
                  ? "h-2 w-8 rounded-full bg-slate-800"
                  : "h-2 w-2 rounded-full bg-slate-300"
              }
            />
          ))}
        </footer>
      ) : null}
    </main>
  );
}
