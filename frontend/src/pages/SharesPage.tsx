/**
 * 家长分享链接管理（v1.3 / FR-07，老师侧，挂在某一期下）。
 *
 * 三条设计约束：
 * 1. 复制给家长的是绝对地址：后端只知道自己域名下的相对路径 ``/share/<token>``，
 *    绝对 URL 必须在前端按 ``window.location.origin`` 现拼，否则内网 IP 与公网域名必有一处是错的。
 * 2. 列表不删行：撤销是后端置标记，老师要能看见「我发过又撤了哪几条」，所以撤销后重拉列表。
 * 3. 状态（有效/已过期/已撤销）由 `revoked` 与 `expires_at` 与当前时间现算，判据与后端
 *    `app/share.py:is_active` 对齐 —— 时间戳解析不了时按已过期处理，宁可拒绝也不放宽。
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { Issue, ShareLink, Student } from "../api/types";
import { formatDate } from "../lib/date";

/** 与后端 app/share.py 一致的天数区间：越界由后端返回 422，这里先挡一次省一次往返。 */
export const MIN_SHARE_DAYS = 1;
export const MAX_SHARE_DAYS = 90;
export const DEFAULT_SHARE_DAYS = 14;

/** 链接状态：`revoked` 优先于过期，因为老师主动撤销时想知道的是「我撤了」。 */
export type ShareStatus = "active" | "expired" | "revoked";

export function shareStatusOf(link: ShareLink, now: number = Date.now()): ShareStatus {
  if (Number(link.revoked) > 0) {
    return "revoked";
  }
  const expiresAt = Date.parse(link.expires_at);
  if (Number.isNaN(expiresAt) || expiresAt <= now) {
    return "expired";
  }
  return "active";
}

const STATUS_TEXT: Record<ShareStatus, string> = {
  active: "有效",
  expired: "已过期",
  revoked: "已撤销",
};

const STATUS_CLASS: Record<ShareStatus, string> = {
  active: "bg-emerald-50 text-emerald-700",
  expired: "bg-slate-100 text-slate-500",
  revoked: "bg-rose-50 text-rose-600",
};

/**
 * 写剪贴板。
 *
 * `navigator.clipboard` 只在安全上下文可用：部署在内网用 http 访问时它根本不存在，
 * 因此退化成 execCommand。两条路都不通时返回 false，由调用方给出「长按手动复制」的提示，
 * 而不是静默失败让老师以为已经复制好了。
 */
export async function writeClipboard(text: string): Promise<boolean> {
  const clipboard = navigator.clipboard;
  if (clipboard && typeof clipboard.writeText === "function") {
    await clipboard.writeText(text);
    return true;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

export default function SharesPage() {
  const { issueId } = useParams();
  const navigate = useNavigate();
  const numericIssueId = Number(issueId);

  const [issue, setIssue] = useState<Issue | null>(null);
  const [students, setStudents] = useState<Student[]>([]);
  const [links, setLinks] = useState<ShareLink[]>([]);
  const [days, setDays] = useState(String(DEFAULT_SHARE_DAYS));
  const [studentId, setStudentId] = useState("");
  const [label, setLabel] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copiedToken, setCopiedToken] = useState("");
  const [clipboardHint, setClipboardHint] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [issueData, list, studentList] = await Promise.all([
        api.getIssue(numericIssueId),
        api.listShares(numericIssueId),
        api.listStudents(),
      ]);
      setIssue(issueData);
      setLinks(list);
      setStudents(studentList);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "分享链接加载失败");
    } finally {
      setLoading(false);
    }
  }, [numericIssueId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const parsedDays = Number(days);
    if (
      !Number.isInteger(parsedDays) ||
      parsedDays < MIN_SHARE_DAYS ||
      parsedDays > MAX_SHARE_DAYS
    ) {
      setError("有效期天数必须是 " + MIN_SHARE_DAYS + "~" + MAX_SHARE_DAYS + " 之间的整数");
      return;
    }
    setBusy(true);
    setError("");
    setClipboardHint("");
    try {
      const created = await api.createShare(numericIssueId, {
        days: parsedDays,
        // 留空 = 整期范围：后端把 student_id 为 null 解释成「这一期全部已定稿作文」。
        student_id: studentId ? Number(studentId) : null,
        label: label.trim(),
      });
      setLabel("");
      // 后端建完返回最终态，但仍重拉一次列表，保证排序/篇数与老师侧视图同源。
      setLinks(await api.listShares(numericIssueId));
      setCopiedToken("");
      setError("已生成分享链接：" + (created.scope === "student" ? created.student_name ?? "" : "整期") + " · 共 " + created.essay_count + " 篇");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "生成分享链接失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function handleCopy(link: ShareLink): Promise<void> {
    // 相对路径发给家长打不开，必须拼上当前域名（见文件头注释）。
    const absoluteUrl = window.location.origin + link.url;
    let ok = false;
    try {
      ok = await writeClipboard(absoluteUrl);
    } catch {
      ok = false;
    }
    if (ok) {
      setCopiedToken(link.token);
      setClipboardHint("");
      return;
    }
    setCopiedToken("");
    setClipboardHint("这个浏览器不允许一键复制（多为非 HTTPS 访问），请点地址框全选后长按/右键复制。");
  }

  async function handleRevoke(link: ShareLink): Promise<void> {
    if (!window.confirm("撤销后家长打开原链接会立即失效。确定撤销这条分享吗？")) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.revokeShare(link.token);
      setLinks(await api.listShares(numericIssueId));
      setCopiedToken("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "撤销失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate("/")}
            data-testid="back-home"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            期数列表
          </button>
          <h1 className="text-lg font-semibold text-slate-900">
            家长分享{issue ? " · 第 " + issue.issue_no + " 期" : ""}
          </h1>
        </div>
        <button
          type="button"
          data-testid="open-book"
          onClick={() => navigate("/issues/" + numericIssueId + "/book")}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
        >
          成册页
        </button>
      </header>

      {issue ? (
        <p className="mt-2 text-sm text-slate-500">
          周一起 {formatDate(issue.week_start_date)}
        </p>
      ) : null}

      <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <p className="text-sm font-medium text-slate-700">新建分享链接</p>
        <form
          onSubmit={handleCreate}
          data-testid="share-form"
          className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-end"
        >
          <label className="text-sm font-medium text-slate-700">
            有效期（天）
            <input
              type="number"
              data-testid="share-days"
              min={MIN_SHARE_DAYS}
              max={MAX_SHARE_DAYS}
              className="mt-1 w-28 rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
              value={days}
              onChange={(event) => setDays(event.target.value)}
              required
            />
          </label>
          <label className="flex-1 text-sm font-medium text-slate-700">
            范围
            <select
              data-testid="share-scope"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
              value={studentId}
              onChange={(event) => setStudentId(event.target.value)}
            >
              <option value="">整期（全部已定稿作文）</option>
              {students.map((student) => (
                <option key={student.id} value={String(student.id)}>
                  仅 {student.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex-1 text-sm font-medium text-slate-700">
            备注（不发家长，只给自己看）
            <input
              type="text"
              data-testid="share-label"
              placeholder="如 三班家长群 / 单独发给某位家长"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </label>
          <button
            type="submit"
            data-testid="create-share"
            disabled={busy}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {busy ? "处理中…" : "生成链接"}
          </button>
        </form>
      </section>

      {error ? (
        <p data-testid="share-notice" className="mt-3 rounded-md bg-amber-50 px-4 py-2 text-sm text-amber-700">
          {error}
        </p>
      ) : null}
      {clipboardHint ? (
        <p data-testid="clipboard-hint" className="mt-3 rounded-md bg-amber-50 px-4 py-2 text-sm text-amber-700">
          {clipboardHint}
        </p>
      ) : null}

      <section className="mt-5">
        {loading ? (
          <p className="text-sm text-slate-500">加载中…</p>
        ) : links.length === 0 ? (
          <p data-testid="shares-empty" className="rounded-md bg-slate-100 px-4 py-3 text-sm text-slate-500">
            还没有发过分享链接。生成一条发到家长群，家长打开即可看到本期全部已定稿作文，不需要登录。
          </p>
        ) : (
          <>
            <p className="text-sm text-slate-500">
              共 <span className="font-semibold text-slate-900">{links.length}</span> 条（含已撤销/已过期）
            </p>
            <ul className="mt-3 flex flex-col gap-3">
              {links.map((link) => {
                const status = shareStatusOf(link);
                return (
                  <li
                    key={link.token}
                    data-testid="share-row"
                    className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        data-testid="share-status"
                        className={
                          "rounded px-2 py-0.5 text-xs font-medium " + STATUS_CLASS[status]
                        }
                      >
                        {STATUS_TEXT[status]}
                      </span>
                      <span className="text-sm font-medium text-slate-900">
                        {link.scope === "student"
                          ? "仅 " + (link.student_name ?? "该学生") + " · 第 " + link.issue_no + " 期"
                          : "整期 · 第 " + link.issue_no + " 期"}
                      </span>
                      <span className="text-xs text-slate-400">共 {link.essay_count} 篇</span>
                      {link.label ? (
                        <span data-testid="share-row-label" className="text-xs text-slate-500">
                          {link.label}
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 text-xs text-slate-400">
                      有效期至 {formatDate(link.expires_at)}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {/* 地址框既是复制目标，也是剪贴板不可用时的兜底：值就是发出去的绝对地址 */}
                      <input
                        type="text"
                        readOnly
                        data-testid="share-url"
                        value={window.location.origin + link.url}
                        onFocus={(event) => event.target.select()}
                        className="min-w-0 flex-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs text-slate-600"
                      />
                      <button
                        type="button"
                        data-testid="copy-share"
                        onClick={() => void handleCopy(link)}
                        className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1.5 text-xs text-slate-700"
                      >
                        {copiedToken === link.token ? "已复制" : "复制地址"}
                      </button>
                      <a
                        href={link.url}
                        target="_blank"
                        rel="noreferrer"
                        data-testid="open-share"
                        className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1.5 text-xs text-slate-700"
                      >
                        在家长端打开
                      </a>
                      <button
                        type="button"
                        data-testid="revoke-share"
                        disabled={status === "revoked" || busy}
                        onClick={() => void handleRevoke(link)}
                        className="shrink-0 rounded-md border border-rose-200 px-2.5 py-1.5 text-xs text-rose-600 disabled:opacity-40"
                      >
                        撤销
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </section>
    </main>
  );
}
