/**
 * 期数列表 + 新建 / 编辑 / 删除一期；点进入对应期的状态看板。
 *
 * v1.2 OPT-02：后端一直有 PATCH /api/issues/{id}，前端却没有入口——老师改期号或
 * 周起始日只能"删了重建"，而删除会连带删掉该期作文与原片目录。这里补一个内联编辑表单。
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { Issue } from "../api/types";
import { useClassName } from "../lib/classMeta";
import { formatDate, mondayIso } from "../lib/date";
import { useAppStore } from "../store";

export default function IssuePage() {
  const navigate = useNavigate();
  const className = useClassName();
  const setCurrentIssueId = useAppStore((state) => state.setCurrentIssueId);
  const logout = useAppStore((state) => state.logout);

  const [issues, setIssues] = useState<Issue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [issueNo, setIssueNo] = useState("");
  const [weekStart, setWeekStart] = useState(mondayIso());
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");
  const [deleting, setDeleting] = useState(false);

  /** 当前展开编辑表单的期数 id；null 表示没有。 */
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editNo, setEditNo] = useState("");
  const [editWeek, setEditWeek] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

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

  /** 删除期数：弹框二次确认；有作文时文案明示将删除的作文数。 */
  async function handleDelete(issue: Issue): Promise<void> {
    const message =
      issue.essay_count > 0
        ? `确定删除第 ${issue.issue_no} 期吗？该期共有 ${issue.essay_count} 篇作文，删除后不可恢复。`
        : `确定删除第 ${issue.issue_no} 期吗？删除后不可恢复。`;
    if (!window.confirm(message)) {
      return;
    }
    setDeleting(true);
    setError("");
    setNotice("");
    try {
      await api.deleteIssue(issue.id, true);
      setNotice(`第 ${issue.issue_no} 期已删除。`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "删除失败，请重试");
    } finally {
      setDeleting(false);
    }
  }

  function startEdit(issue: Issue): void {
    setError("");
    setNotice("");
    setEditingId(issue.id);
    setEditNo(String(issue.issue_no));
    setEditWeek(issue.week_start_date);
  }

  /** 保存期号 / 周起始日：后端返回 400（日期非周一）或 409（期号冲突）时直接展示文案。 */
  async function handleSaveEdit(issue: Issue): Promise<void> {
    const parsed = Number(editNo);
    if (!Number.isInteger(parsed) || parsed < 1) {
      setError("请输入合法的期号（≥1 的整数）");
      return;
    }
    if (!editWeek) {
      setError("请选择周一日期");
      return;
    }
    setSavingEdit(true);
    setError("");
    try {
      await api.updateIssue(issue.id, { issue_no: parsed, week_start_date: editWeek });
      setNotice(`第 ${parsed} 期已更新。`);
      setEditingId(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "更新失败，请重试");
    } finally {
      setSavingEdit(false);
    }
  }

  function handleLogout(): void {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-10">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 sm:text-2xl">班级作文工作台</h1>
          {className ? <p className="mt-0.5 text-sm text-slate-500">{className}</p> : null}
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={() => navigate("/students")}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600"
          >
            学生名单
          </button>
          <button
            type="button"
            onClick={handleLogout}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600"
          >
            退出登录
          </button>
        </div>
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
                className="rounded-xl border border-slate-200 bg-white shadow-sm"
                data-testid={`issue-row-${issue.id}`}
              >
                <div className="flex items-center justify-between gap-3 p-4">
                  <button type="button" onClick={() => openIssue(issue)} className="text-left">
                    <p className="text-base font-semibold text-slate-900">第 {issue.issue_no} 期</p>
                    <p className="mt-1 text-sm text-slate-500">
                      周一起 {formatDate(issue.week_start_date)} · 共 {issue.essay_count} 篇
                    </p>
                  </button>
                  <div className="flex shrink-0 flex-wrap justify-end gap-2">
                    <button
                      type="button"
                      data-testid="edit-issue"
                      onClick={() => startEdit(issue)}
                      className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
                    >
                      编辑
                    </button>
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
                    <button
                      type="button"
                      disabled={deleting}
                      onClick={() => void handleDelete(issue)}
                      className="rounded-md border border-rose-200 px-3 py-1.5 text-sm text-rose-600 disabled:opacity-60"
                    >
                      删除
                    </button>
                  </div>
                </div>

                {editingId === issue.id ? (
                  <div className="border-t border-slate-100 px-4 py-3">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                      <label className="flex-1 text-sm font-medium text-slate-700">
                        期号
                        <input
                          type="number"
                          min={1}
                          data-testid="edit-issue-no"
                          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
                          value={editNo}
                          onChange={(event) => setEditNo(event.target.value)}
                          required
                        />
                      </label>
                      <label className="flex-1 text-sm font-medium text-slate-700">
                        周一日期
                        <input
                          type="date"
                          data-testid="edit-week"
                          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
                          value={editWeek}
                          onChange={(event) => setEditWeek(event.target.value)}
                          required
                        />
                      </label>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={savingEdit}
                          onClick={() => void handleSaveEdit(issue)}
                          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
                        >
                          {savingEdit ? "保存中…" : "保存"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingId(null)}
                          className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-700"
                        >
                          取消
                        </button>
                      </div>
                    </div>
                    <p className="mt-2 text-xs text-slate-400">
                      改期号不影响该期已上传的作文；期号与其他期重复会保存失败。
                    </p>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
