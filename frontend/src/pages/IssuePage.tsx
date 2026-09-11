/** 期数列表 + 新建一期；点进入对应期的状态看板。 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { Issue } from "../api/types";
import { formatDate, mondayIso } from "../lib/date";
import { useAppStore } from "../store";

export default function IssuePage() {
  const navigate = useNavigate();
  const setCurrentIssueId = useAppStore((state) => state.setCurrentIssueId);
  const logout = useAppStore((state) => state.logout);

  const [issues, setIssues] = useState<Issue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [issueNo, setIssueNo] = useState("");
  const [weekStart, setWeekStart] = useState(mondayIso());
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setIssues(await api.listIssues());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "期数加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const parsed = Number(issueNo);
    if (!Number.isInteger(parsed) || parsed < 1) {
      setError("请输入合法的期号（≥1 的整数）");
      return;
    }
    setCreating(true);
    setError("");
    setNotice("");
    try {
      await api.createIssue(parsed, weekStart);
      setIssueNo("");
      setNotice(`第 ${parsed} 期创建成功，可在下方进入。`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "新建失败，请重试");
    } finally {
      setCreating(false);
    }
  }

  function openIssue(issue: Issue): void {
    setCurrentIssueId(issue.id);
    navigate(`/issues/${issue.id}/essays`);
  }

  function handleLogout(): void {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900 sm:text-2xl">班级作文工作台</h1>
        <button
          type="button"
          onClick={handleLogout}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600"
        >
          退出登录
        </button>
      </header>

      <form
        onSubmit={handleCreate}
        className="mt-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
      >
        <h2 className="text-base font-medium text-slate-800">新建一期</h2>
        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex-1 text-sm font-medium text-slate-700">
            期号
            <input
              type="number"
              min={1}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
              value={issueNo}
              onChange={(event) => setIssueNo(event.target.value)}
              placeholder="如 3"
              required
            />
          </label>
          <label className="flex-1 text-sm font-medium text-slate-700">
            周一日期
            <input
              type="date"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
              value={weekStart}
              onChange={(event) => setWeekStart(event.target.value)}
              required
            />
          </label>
          <button
            type="submit"
            disabled={creating}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {creating ? "创建中…" : "创建"}
          </button>
        </div>
      </form>

      {notice ? (
        <p className="mt-4 rounded-md bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{notice}</p>
      ) : null}

      {error ? (
        <p className="mt-4 rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-600">{error}</p>
      ) : null}

      <section className="mt-6">
        <h2 className="text-base font-medium text-slate-800">全部期数</h2>
        {loading ? (
          <p className="mt-3 text-sm text-slate-500">加载中…</p>
        ) : issues.length === 0 ? (
          <p className="mt-3 rounded-md bg-slate-100 px-4 py-3 text-sm text-slate-500">
            还没有任何期数，先在上方新建一期吧。
          </p>
        ) : (
          <ul className="mt-3 flex flex-col gap-3">
            {issues.map((issue) => (
              <li
                key={issue.id}
                className="flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
              >
                <button
                  type="button"
                  onClick={() => openIssue(issue)}
                  className="text-left"
                >
                  <p className="text-base font-semibold text-slate-900">第 {issue.issue_no} 期</p>
                  <p className="mt-1 text-sm text-slate-500">
                    周一起 {formatDate(issue.week_start_date)} · 共 {issue.essay_count} 篇
                  </p>
                </button>
                <div className="flex shrink-0 gap-2">
                  <button
                    type="button"
                    onClick={() => navigate(`/issues/${issue.id}/upload`)}
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
                  >
                    拍照上传
                  </button>
                  <button
                    type="button"
                    onClick={() => openIssue(issue)}
                    className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white"
                  >
                    进入看板
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
