/**
 * 三榜表彰（v1.3 / FR-04，老师侧）。
 *
 * 四条约束：
 * 1. 星级一律显示后端下发的 stars，前端不再按分数换算 —— 与 PDF、家长页、投屏同源。
 * 2. 星级榜不显示名次与分数（弱化竞争是产品约束，不是样式选择），后端本就不下发这两个字段。
 * 3. 被配置关闭的榜显示「已按配置关闭」而不是「没人上榜」，两者老师必须能区分。
 * 4. 榜单只读：分数与精选都在看板和校对页改，本页不提供任何写入口。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { BoardConfig, BoardKey, RankingData } from "../api/types";
import Stars from "../components/Stars";
import { triggerDownload } from "../lib/download";

export const BOARD_TITLES: Record<BoardKey, string> = {
  work: "佳作榜",
  progress: "进步榜",
  star: "星级榜",
};

/** 前三名高亮：表彰页要一眼看见本周被点名的孩子。 */
export const TOP_HIGHLIGHT = 3;

/** 名次为 null（星级榜）不参与高亮，用无穷大兜底而不是塞一个假的 0。 */
export function highlightClass(rank: number | null): string {
  return rank !== null && rank > 0 && rank <= TOP_HIGHLIGHT ? "bg-amber-50/70" : "";
}

/** 分数显示：整数不带小数点，小数保留一位；null 一律破折号，不显示 0 分。 */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(Number(score))) {
    return "—";
  }
  return Number(score).toFixed(1).replace(/[.]0$/, "");
}

export function thresholdHint(thresholds: number[], maxStars: number): string {
  if (!thresholds || thresholds.length === 0) {
    return "星级未配置阈值，本期不评星。";
  }
  const parts = thresholds.map((value, index) => value + " 分及以上 " + (index + 1) + " 星");
  const extra = maxStars - thresholds.length;
  if (extra > 0) {
    parts.push("再高 " + extra + " 档至 " + maxStars + " 星");
  }
  return "星级阈值：" + parts.join("、") + "。";
}

export const VISIBILITY_TEXT: Record<BoardConfig["visibility"], string> = {
  teacher: "仅老师可见",
  public: "家长可见",
};

/** 进步榜的涨跌写法：0 显示「持平」，并且始终带上上期期号，老师要对得上比的是哪一期。 */
export function deltaText(delta: number, previousIssueNo: number): string {
  if (delta > 0) {
    return "较第 " + previousIssueNo + " 期 +" + formatScore(delta);
  }
  if (delta < 0) {
    return "较第 " + previousIssueNo + " 期 −" + formatScore(Math.abs(delta));
  }
  return "较第 " + previousIssueNo + " 期 持平";
}

/**
 * 三榜共用一种行模型：字段来源不同，但渲染骨架相同。
 *
 * 星级榜的 rank / extra 恒为 null —— 后端不下发，前端也不许凭 score 反推。
 */
export interface BoardRow {
  key: string;
  rank: number | null;
  main: string;
  href: string;
  extra: string | null;
  stars: number;
  badge: string | null;
  /** 行内无障碍标签：屏幕阅读器不会念星星符号。 */
  label: string;
}

/** 标题兜底用「未命名」：空标题是「还没填」，不是学生真写了篇无题作文（同 FR-11 口径）。 */
function displayName(name: string | null, title: string): string {
  return (name ?? "未命名学生") + "《" + (title.trim() || "未命名") + "》";
}

export function workRows(data: RankingData): BoardRow[] {
  return data.work.map((row) => {
    const main = displayName(row.name, row.title);
    return {
      key: "work-" + row.essay_id,
      rank: row.rank,
      main,
      href: "/essays/" + row.essay_id + "/proofread",
      extra: formatScore(row.score) + " 分",
      stars: row.stars,
      badge: row.selected ? "精选" : null,
      label: main + " " + row.stars + " 星 " + formatScore(row.score) + " 分",
    };
  });
}

export function progressRows(data: RankingData): BoardRow[] {
  return data.progress.map((row) => {
    const main = displayName(row.name, row.title);
    return {
      key: "progress-" + row.essay_id,
      rank: row.rank,
      main,
      href: "/essays/" + row.essay_id + "/proofread",
      extra: deltaText(row.delta, row.previous_issue_no),
      stars: row.stars,
      badge: row.selected ? "精选" : null,
      label: main + " " + row.stars + " 星",
    };
  });
}

/** 星级榜只有学生与篇名，点进去是成长档案（这里没有 essay_id，也不该有分数可看）。 */
export function starRows(data: RankingData): BoardRow[] {
  return data.star.map((row, index) => {
    const main = displayName(row.name, row.title);
    return {
      key: "star-" + row.student_id + "-" + index,
      rank: null,
      main,
      href: "/students/" + row.student_id + "/portfolio",
      extra: null,
      stars: row.stars,
      badge: null,
      label: main + " " + row.stars + " 星",
    };
  });
}

export const EMPTY_TEXT: Record<BoardKey, string> = {
  work: "本期还没有可上榜的评分，先去校对页给作文打分。",
  progress: "没有可比的上一期：进步榜要求同一个学生上期也有评分。",
  star: "还没有达到一星的作文。",
};

const GHOST_BUTTON =
  "rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50";

interface BoardSectionProps {
  boardKey: BoardKey;
  rows: BoardRow[];
  disabled: boolean;
  visibility: BoardConfig["visibility"];
}

function BoardSection({ boardKey, rows, disabled, visibility, projection = false }: BoardSectionProps & { projection?: boolean }) {
  return (
    <section
      data-testid={"board-" + boardKey}
      className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
    >
      <h2 className="flex items-center justify-between gap-2 text-sm font-medium text-slate-700">
        <span>{BOARD_TITLES[boardKey]}</span>
        <span data-testid={"board-" + boardKey + "-visibility"} className="text-xs text-slate-400">
          {VISIBILITY_TEXT[visibility] ?? VISIBILITY_TEXT.teacher}
        </span>
      </h2>
      {disabled ? (
        <p
          data-testid={"board-" + boardKey + "-disabled"}
          className="mt-2 text-xs text-slate-400"
        >
          此榜已按配置关闭，不是没有人选上。
        </p>
      ) : rows.length === 0 ? (
        <p data-testid={"board-" + boardKey + "-empty"} className="mt-2 text-xs text-slate-400">
          {EMPTY_TEXT[boardKey]}
        </p>
      ) : (
        <ol className="mt-2 divide-y divide-slate-100">
          {rows.map((row) => (
            <li
              key={row.key}
              data-testid={"ranking-row-" + row.key}
              className={"flex flex-wrap items-center gap-2 py-2 text-sm " + highlightClass(row.rank)}
            >
              {row.rank === null ? null : (
                <span className="w-5 shrink-0 text-xs font-semibold text-slate-400">{row.rank}</span>
              )}
              <Link
                to={row.href}
                className="min-w-0 flex-1 truncate text-slate-900 hover:underline"
                title={row.main}
              >
                {projection ? row.main.replace(/《.*》$/, "") : row.main}
              </Link>
              {row.badge ? (
                <span
                  data-testid="ranking-badge"
                  className="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-700"
                >
                  {row.badge}
                </span>
              ) : null}
              {row.extra ? (
                <span className="shrink-0 text-xs text-slate-500">{row.extra}</span>
              ) : null}
              <Stars stars={row.stars} score={null} label={row.label} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function RankingPage() {
  const { issueId } = useParams();
  const numericIssueId = Number(issueId);
  const validId = Number.isInteger(numericIssueId) && numericIssueId > 0;

  const [data, setData] = useState<RankingData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [posterBusy, setPosterBusy] = useState(false);
  const [posterError, setPosterError] = useState("");
  const [projection, setProjection] = useState(false);

  const load = useCallback(async () => {
    if (!validId) {
      setError("地址里的期号不可用，请从状态看板进入。");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      setData(await api.fetchRanking(numericIssueId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "榜单读取失败，请刷新重试。");
    } finally {
      setLoading(false);
    }
  }, [numericIssueId, validId]);

  useEffect(() => {
    void load();
  }, [load]);

  /** 海报是本周精选的 A4 单页（发家长群）：失败只在原地提示，不动已加载的榜单。 */
  async function handlePoster(): Promise<void> {
    if (!data) {
      return;
    }
    setPosterBusy(true);
    setPosterError("");
    try {
      const blob = await api.exportPoster(data.issue_id);
      triggerDownload(blob, "第" + data.issue_no + "期本周精选.pdf");
    } catch (err) {
      setPosterError(err instanceof ApiError ? err.message : "海报导出失败，请稍后重试。");
    } finally {
      setPosterBusy(false);
    }
  }

  if (loading) {
    return (
      <main
        data-testid="ranking-loading"
        className="mx-auto max-w-3xl px-4 py-10 text-sm text-slate-500"
      >
        榜单计算中…
      </main>
    );
  }

  if (!data) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-10">
        <p data-testid="ranking-error" className="text-sm text-rose-600">
          {error}
        </p>
        <Link to="/" data-testid="ranking-back" className={GHOST_BUTTON + " mt-4 inline-block"}>
          返回状态看板
        </Link>
      </main>
    );
  }

  const boards: Array<{ boardKey: BoardKey; rows: BoardRow[]; wide?: boolean }> = [
    { boardKey: "work", rows: workRows(data) },
    { boardKey: "progress", rows: progressRows(data) },
    { boardKey: "star", rows: starRows(data), wide: true },
  ];

  return (
    <main className="mx-auto min-h-dvh w-full max-w-6xl px-3 py-4 text-slate-900 sm:px-4 lg:max-w-7xl">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 data-testid="ranking-title" className="text-xl font-semibold">
            {"第 " + data.issue_no + " 期 · 本周表彰"}
          </h1>
          <p data-testid="ranking-thresholds" className="mt-1 text-sm text-slate-500">
            {data.class_name + " · " + thresholdHint(data.thresholds, data.max_stars)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={"/issues/" + data.issue_id + "/essays"}
            data-testid="ranking-back"
            className={GHOST_BUTTON}
          >
            返回看板
          </Link>
          <Link
            to={"/issues/" + data.issue_id + "/shares"}
            data-testid="ranking-shares"
            className={GHOST_BUTTON}
          >
            家长分享
          </Link>
          <button
            type="button"
            data-testid="ranking-poster"
            disabled={posterBusy}
            onClick={() => void handlePoster()}
            className={GHOST_BUTTON + " bg-slate-900 text-white hover:bg-slate-700"}
          >
            {posterBusy ? "海报导出中…" : "导出本周精选海报"}
          </button>
          <button
            type="button"
            data-testid="ranking-projection"
            onClick={() => setProjection((value) => !value)}
            className={GHOST_BUTTON + (projection ? " bg-slate-900 text-white" : "")}
          >
            {projection ? "退出投影态" : "投影态"}
          </button>
        </div>
      </header>

      <p data-testid="score-note" className="mt-3 text-sm text-slate-500">
        {"本期已评分 " + data.scored_count + " 篇"}
        {data.unscored_count > 0
          ? "，还有 " + data.unscored_count + " 篇未评分（未评分不进任何榜单）"
          : ""}
      </p>

      {posterError ? (
        <p
          data-testid="poster-error"
          className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-600"
        >
          {posterError}
        </p>
      ) : null}

      <div className={"mt-4 grid gap-4 lg:grid-cols-2" + (projection ? " text-lg" : "")}>
        {boards.map((board) => (
          <div key={board.boardKey} className={board.wide ? "lg:col-span-2" : ""}>
            <BoardSection
              boardKey={board.boardKey}
              rows={board.rows}
              disabled={data.disabled.includes(board.boardKey)}
              visibility={data.config[board.boardKey]?.visibility ?? "teacher"}
              projection={projection}
            />
          </div>
        ))}
      </div>

      <p className="mt-4 text-xs text-slate-400">
        榜单由本期已定稿且已评分的作文算出，改分或改精选后回到看板重进即是最新结果。
      </p>
    </main>
  );
}
