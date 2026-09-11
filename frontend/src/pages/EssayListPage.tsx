/**
 * 作文状态看板：按状态分组展示全班进度；识别中的作文每 3s 轮询一次详情，
 * 状态离开「识别中」即停止该篇轮询；组件卸载时停止全部轮询。
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { EssayDetail, EssaySummary, Issue } from "../api/types";
import { isRecognizing } from "../api/types";
import StatusBadge from "../components/StatusBadge";
import { groupEssays, studentCounts } from "../lib/board";
import { useAppStore } from "../store";

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

  // 卸载即停全部轮询。
  useEffect(() => () => stopAllPolling(), [stopAllPolling]);

  const groups = groupEssays(essays);
  const counts = studentCounts(essays);

  return (
    <main className="mx-auto max-w-4xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          状态看板{issue ? ` · 第 ${issue.issue_no} 期` : ""}
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
            onClick={() => void load()}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            立即刷新
          </button>
        </div>
      </header>

      {error ? (
        <p className="mt-4 rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-600">{error}</p>
      ) : null}

      <section className="mt-5 rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600 shadow-sm">
        <p>
          共 <span className="font-semibold text-slate-900">{essays.length}</span> 篇 ·
          <span className="font-semibold text-slate-900"> {counts.length}</span> 名学生
          <span className="ml-2 text-xs text-slate-400">识别中作文每 3 秒自动刷新</span>
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
                    <li key={essay.id}>
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
                        {essay.low_confidence === 1 ? (
                          <span className="mt-1 block text-xs text-amber-600">整篇置信度偏低</span>
                        ) : null}
                      </button>
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
