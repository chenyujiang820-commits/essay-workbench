/**
 * 作文状态看板：按状态分组展示全班进度；识别中的作文每 3s 轮询一次详情，
 * 状态离开「识别中」即停止该篇轮询；组件卸载时停止全部轮询。
 *
 * v1.3 起这里还是「本期精选」的唯一勾选入口（FR-05）与二期表奖/分享的导航起点。
 * 精选刻意做成**整期覆盖式**：勾完点「保存精选」才落库，中途关页面不留半成品。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { EssayDetail, EssaySummary, Issue } from "../api/types";
import { isRecognizing } from "../api/types";
import StatusBadge from "../components/StatusBadge";
import Stars from "../components/Stars";
import { useClassName } from "../lib/classMeta";
import { groupEssays, studentCounts } from "../lib/board";
import { useAppStore } from "../store";

/** 每期精选上限，与后端 app/ranking.py:MAX_SELECTED_PER_ISSUE 同值。 */
export const SELECTION_LIMIT = 10;

/** 建议预勾篇数，与后端 DEFAULT_SELECTED_SUGGEST 同值：只是省点击，不等于自动保存。 */
export const SELECTION_SUGGEST = 5;

/** 勾选态初值：以后端下发的 selected 为准，前端不另存一份真相。 */
export function pickedFromList(list: EssaySummary[]): number[] {
  return list.filter((essay) => Number(essay.selected) === 1).map((essay) => essay.id);
}

/** 有没有分：null 与 undefined 都算未评分，和后端 ``score is None`` 同判据。 */
function hasScore(essay: EssaySummary): boolean {
  return essay.score !== null && essay.score !== undefined;
}

/** 已定稿且有分 —— 精选建议、评分进度、三榜共用这一条判据，避免三处口径分叉。 */
function scoredProofread(list: EssaySummary[]): EssaySummary[] {
  return list.filter((essay) => essay.status === "proofread" && hasScore(essay));
}

/** 分数降序，同分再按姓名升序：建议结果必须可复现，否则刷新一次勾选就跳。 */
function byScoreThenName(left: EssaySummary, right: EssaySummary): number {
  const gap = Number(right.score) - Number(left.score);
  if (gap !== 0) {
    return gap;
  }
  return String(left.student_name ?? "").localeCompare(
    String(right.student_name ?? ""),
    "zh-Hans-CN",
  );
}

/**
 * 评分进度。只数已定稿的稿子，未定稿连「未评分」都不算 ——
 * 判据要和后端 GET /ranking 的 scored_count 一致，否则看板与表彰页对不上。
 */
export function scoreProgress(list: EssaySummary[]): { scored: number; unscored: number } {
  const proofread = list.filter((essay) => essay.status === "proofread");
  const scored = proofread.filter(hasScore);
  return { scored: scored.length, unscored: proofread.length - scored.length };
}

/** 建议预勾：已定稿且有分里按分数降序取前 count 篇，老师照样能改。 */
export function suggestPicked(list: EssaySummary[], count = SELECTION_SUGGEST): number[] {
  return scoredProofread(list)
    .sort(byScoreThenName)
    .slice(0, count)
    .map((essay) => essay.id);
}


export default function EssayListPage() {
  const { issueId } = useParams();
  const navigate = useNavigate();
  const numericIssueId = Number(issueId);

  const startPolling = useAppStore((state) => state.startPolling);
  const stopPolling = useAppStore((state) => state.stopPolling);
  const stopAllPolling = useAppStore((state) => state.stopAllPolling);
  const setCurrentIssueId = useAppStore((state) => state.setCurrentIssueId);

  const [issue, setIssue] = useState<Issue | null>(null);
  const [essays, setEssays] = useState<EssaySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryingId, setRetryingId] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  // 精选勾选态只活在内存里：点「保存精选」才落库，任何一次 load 都按后端回读值重置。
  const [picked, setPicked] = useState<number[]>([]);
  const [picking, setPicking] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const [selectionNotice, setSelectionNotice] = useState("");

  const applyDetail = useCallback((detail: EssayDetail) => {
    setEssays((previous) =>
      previous.map((essay) => (essay.id === detail.id ? { ...essay, ...detail } : essay)),
    );
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [issueData, list] = await Promise.all([
        api.getIssue(numericIssueId),
        api.listIssueEssays(numericIssueId),
      ]);
      setIssue(issueData);
      setEssays(list);
      setPicked(pickedFromList(list));
      setCurrentIssueId(numericIssueId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "看板加载失败");
    } finally {
      setLoading(false);
    }
  }, [numericIssueId, setCurrentIssueId]);

  useEffect(() => {
    void load();
  }, [load]);

  // 为识别中的作文登记轮询（store 去重，重复渲染不会产生多个定时器）。
  useEffect(() => {
    for (const essay of essays) {
      if (!isRecognizing(essay.status)) {
        continue;
      }
      startPolling(essay.id, async () => {
        try {
          const detail = await api.getEssay(essay.id);
          applyDetail(detail);
          if (!isRecognizing(detail.status)) {
            stopPolling(essay.id);
          }
        } catch {
          stopPolling(essay.id); // 轮询异常则停止，避免无限重试
        }
      });
    }
  }, [essays, startPolling, stopPolling, applyDetail]);

  /** AC-6 / FR-10：失败稿在看板上直接重跑，不必先点进校对页找入口。 */
  async function handleRetry(essay: EssaySummary): Promise<void> {
    setRetryingId(essay.id);
    setError("");
    setNotice("");
    try {
      const result = await api.retryEssay(essay.id);
      const status = result.status || "recognizing";
      setEssays((previous) =>
        previous.map((item) => (item.id === essay.id ? { ...item, status } : item)),
      );
      setNotice((essay.student_name ?? "#" + String(essay.student_id)) + " 已重新排队识别");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "重新识别失败，请稍后再试");
    } finally {
      setRetryingId(null);
    }
  }

  /** 勾选/取消一篇：本地只改内存，越界由「保存精选」前的检查挡一次。 */
  function togglePicked(essayId: number): void {
    setSelectionError("");
    setPicked((previous) =>
      previous.includes(essayId)
        ? previous.filter((id) => id !== essayId)
        : [...previous, essayId],
    );
  }

  /** 「按分数建议」不自动保存：省点击而已，老师改完仍要点「保存精选」。 */
  function handleSuggest(): void {
    setPicked(suggestPicked(essays));
    setSelectionError("");
    setSelectionNotice("");
  }

  /**
   * 覆盖式提交精选（PRD v1.3 Q28：一次交整个集合，不逐篇开关）。
   *
   * 成功后按后端回读的 selected_ids 重绘，**不做乐观更新** —— 失败时勾选必须看起来没变；
   * 失败还顺手重拉一次，因为原因很可能是别的窗口改了数据，得让老师看到真实状态。
   */
  async function handleSaveSelection(): Promise<void> {
    setPicking(true);
    setSelectionError("");
    setSelectionNotice("");
    try {
      if (picked.length > SELECTION_LIMIT) {
        setSelectionError(
          `本期精选最多 ${SELECTION_LIMIT} 篇，当前已勾 ${picked.length} 篇，请先取消一部分。`,
        );
        return;
      }
      const result = await api.setSelection(numericIssueId, picked);
      const chosen = new Set(result.selected_ids);
      setEssays((previous) =>
        previous.map((essay) => ({ ...essay, selected: chosen.has(essay.id) ? 1 : 0 })),
      );
      setPicked(result.selected_ids);
      setSelectionNotice(`精选已保存：本期共 ${result.selected_ids.length} 篇。`);
    } catch (err) {
      setSelectionError(err instanceof ApiError ? err.message : "精选保存失败，请重试");
      await load();
    } finally {
      setPicking(false);
    }
  }

  // 卸载即停全部轮询。
  useEffect(() => () => stopAllPolling(), [stopAllPolling]);

  const groups = groupEssays(essays);
  const counts = studentCounts(essays);
  const className = useClassName();
  // 画质偏低的篇目：原片不达标会直接放大「编字」风险，看板层面就要看得见。
  const lowResEssays = essays.filter((essay) => (essay.low_resolution_count ?? 0) > 0);
  // 评分进度与表彰页同一判据，勾完精选就地刷新，不必跳去表彰页才知道还漏了几篇没打分。
  const progress = scoreProgress(essays);
  // 「有没有改动」要能识别取消勾选，所以比集合而不是比长度。
  const dirtySelection = useMemo(() => {
    const saved = new Set(pickedFromList(essays));
    return (
      picked.length !== saved.size || picked.some((id) => !saved.has(id))
    );
  }, [essays, picked]);

  return (
    <main className="mx-auto max-w-4xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          状态看板{issue ? ` · 第 ${issue.issue_no} 期` : ""}
          {className ? (
            <span className="ml-2 text-sm font-normal text-slate-500">{className}</span>
          ) : null}
        </h1>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => navigate("/")}
            data-testid="back-home"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            ← 期数列表
          </button>
          <button
            type="button"
            onClick={() => navigate(`/issues/${issueId}/upload`)}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            拍照上传
          </button>
          <button
            type="button"
            onClick={() => navigate(`/issues/${issueId}/book`)}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            成册导出
          </button>
          <button
            type="button"
            onClick={() => navigate(`/present/${issueId}`)}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            讲评投屏
          </button>
          <button
            type="button"
            onClick={() => navigate(`/issues/${issueId}/ranking`)}
            data-testid="board-ranking"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            表彰榜单
          </button>
          <button
            type="button"
            onClick={() => navigate(`/issues/${issueId}/shares`)}
            data-testid="board-shares"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            家长分享
          </button>
          <button
            type="button"
            onClick={() => void load()}
            data-testid="board-refresh"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            立即刷新
          </button>
        </div>
      </header>

      {error ? (
        <p className="mt-4 rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-600">{error}</p>
      ) : null}

      {notice ? (
        <p
          data-testid="board-retry-notice"
          className="mt-4 rounded-md bg-emerald-50 px-4 py-3 text-sm text-emerald-700"
        >
          {notice}
        </p>
      ) : null}

      <section className="mt-5 rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600 shadow-sm">
        <p>
          共 <span className="font-semibold text-slate-900">{essays.length}</span> 篇 ·
          <span className="font-semibold text-slate-900"> {counts.length}</span> 名学生
          <span className="ml-2 text-xs text-slate-400">识别中作文每 3 秒自动刷新</span>
        {lowResEssays.length > 0 ? (
          <span data-testid="board-low-res-summary" className="ml-2 text-xs text-amber-600">
            {lowResEssays.length} 篇原片画质偏低
          </span>
        ) : null}
        </p>
        {counts.length > 0 ? (
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            {counts.map((item) => (
              <li key={item.studentId}>
                {item.name} · {item.count} 篇
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section
        data-testid="selection-panel"
        className="mt-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
      >
        <h2 className="flex flex-wrap items-center justify-between gap-2 text-sm font-medium text-slate-700">
          <span>本期精选</span>
          <span data-testid="selection-limit" className="text-xs text-slate-400">
            {`已勾 ${picked.length} / ${SELECTION_LIMIT} 篇`}
          </span>
        </h2>
        <p data-testid="selection-note" className="mt-1 text-xs leading-5 text-slate-400">
          精选只影响「本周精选」海报与佳作榜上的标记，不改正文；未定稿的稿子不能勾选。
          整期一次提交，勾完要点「保存精选」才生效。
        </p>
        {progress.unscored > 0 ? (
          <p
            data-testid="board-score-progress"
            className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700"
          >
            {`本期已定稿 ${progress.scored + progress.unscored} 篇，其中 ${progress.unscored} 篇还没有评分；未评分不进任何榜单。`}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="selection-suggest"
            onClick={handleSuggest}
            disabled={picking}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 disabled:opacity-50"
          >
            {`按分数建议 ${SELECTION_SUGGEST} 篇`}
          </button>
          <button
            type="button"
            data-testid="submit-selection"
            onClick={() => void handleSaveSelection()}
            disabled={picking || !dirtySelection}
            className="rounded-md bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {picking ? "保存中…" : "保存精选"}
          </button>
        </div>
        {selectionError ? (
          <p
            data-testid="selection-error"
            className="mt-2 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-600"
          >
            {selectionError}
          </p>
        ) : null}
        {selectionNotice ? (
          <p
            data-testid="selection-notice"
            className="mt-2 rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
          >
            {selectionNotice}
          </p>
        ) : null}
      </section>

      {loading ? (
        <p className="mt-4 text-sm text-slate-500">加载中…</p>
      ) : essays.length === 0 ? (
        <p className="mt-4 rounded-md bg-slate-100 px-4 py-3 text-sm text-slate-500">
          本期还没有作文，点击「拍照上传」开始。
        </p>
      ) : (
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          {groups.map((group) => (
            <section key={group.key} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="flex items-center justify-between text-sm font-medium text-slate-700">
                {group.title}
                <span className="text-xs text-slate-400">{group.essays.length} 篇</span>
              </h2>
              {group.essays.length === 0 ? (
                <p className="mt-2 text-xs text-slate-400">暂无</p>
              ) : (
                <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {group.essays.map((essay) => (
                    <li key={essay.id} className="flex flex-col gap-1">
                      <label
                        title={
                          essay.status === "proofread"
                            ? "勾进本期精选（整期最多 10 篇，点保存才生效）"
                            : "未定稿不能设为精选"
                        }
                        className={
                          "flex items-center gap-1.5 text-xs " +
                          (essay.status === "proofread" ? "text-slate-600" : "text-slate-300")
                        }
                      >
                        <input
                          type="checkbox"
                          data-testid={`select-essay-${essay.id}`}
                          checked={picked.includes(essay.id)}
                          disabled={essay.status !== "proofread" || picking}
                          onChange={() => togglePicked(essay.id)}
                          className="h-4 w-4 accent-emerald-600"
                        />
                        <span>精选</span>
                        {Number(essay.selected) === 1 ? (
                          <span
                            data-testid={`selection-saved-${essay.id}`}
                            className="text-emerald-700"
                          >
                            已存
                          </span>
                        ) : null}
                      </label>
                      <button
                        type="button"
                        onClick={() => navigate(`/essays/${essay.id}/proofread`)}
                        className="w-full rounded-lg border border-slate-200 px-2.5 py-2 text-left hover:border-slate-400"
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium text-slate-900">
                            {essay.student_name ?? `#${essay.student_id}`}
                          </span>
                          <StatusBadge status={essay.status} />
                        </span>
                        {essay.stars > 0 ? (
                          <Stars
                            stars={essay.stars}
                            score={null}
                            label={`${essay.student_name ?? ""} 本期 ${essay.stars} 星`}
                          />
                        ) : null}
                        {essay.low_confidence === 1 ? (
                          <span className="mt-1 block text-xs text-amber-600">整篇置信度偏低</span>
                        ) : null}
                        {(essay.low_resolution_count ?? 0) > 0 ? (
                          <span
                            data-testid="essay-low-res"
                            title="有原片短边不足 600 或长边不足 800 像素，建议重拍"
                            className="mt-1 block text-xs text-amber-600"
                          >
                            画质偏低 {essay.low_resolution_count} 张
                          </span>
                        ) : null}
                      </button>
                      {essay.status === "failed" ? (
                        <button
                          type="button"
                          data-testid={"board-retry-" + String(essay.id)}
                          disabled={retryingId !== null}
                          onClick={() => void handleRetry(essay)}
                          className="w-full rounded-md bg-rose-700 px-2 py-1 text-xs font-medium text-white disabled:opacity-60"
                        >
                          {retryingId === essay.id ? "排队中…" : "重新识别"}
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
