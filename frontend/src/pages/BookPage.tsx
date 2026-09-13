/**
 * 成册预览与导出：五套模板切换（所见即所得 iframe 预览）、四种排序、
 * 校对铁律禁用（未全定稿不可导出）、整册 PDF 与单篇版式下载。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { EssaySummary, ExportOrder, Issue, TemplateInfo } from "../api/types";
import PreviewFrame from "../components/PreviewFrame";
import TemplatePicker from "../components/TemplatePicker";
import { useClassName } from "../lib/classMeta";
import { bookFileName, singleFileName, triggerDownload } from "../lib/download";
import { formatDate } from "../lib/date";

type PreviewState = "empty" | "loading" | "ready" | "error";

export function sortEssaySummaries(essays: EssaySummary[], order: ExportOrder): EssaySummary[] {
  return [...essays].sort((left, right) => {
    const compareStudentNo = (left.student_no ?? "").localeCompare(
      right.student_no ?? "",
      "zh-CN",
      { numeric: true, sensitivity: "base" },
    );
    if (order === "name") {
      return (left.student_name ?? "").localeCompare(right.student_name ?? "", "zh-CN") || compareStudentNo || left.id - right.id;
    }
    if (order === "score") {
      return (right.score ?? Number.NEGATIVE_INFINITY) - (left.score ?? Number.NEGATIVE_INFINITY) || compareStudentNo || (left.student_name ?? "").localeCompare(right.student_name ?? "", "zh-CN") || left.id - right.id;
    }
    if (order === "selected_score") {
      return right.selected - left.selected || (right.score ?? Number.NEGATIVE_INFINITY) - (left.score ?? Number.NEGATIVE_INFINITY) || compareStudentNo || (left.student_name ?? "").localeCompare(right.student_name ?? "", "zh-CN") || left.id - right.id;
    }
    return compareStudentNo || left.id - right.id;
  });
}

export default function BookPage() {
  const { issueId } = useParams();
  const navigate = useNavigate();
  const numericIssueId = Number(issueId);

  const [issue, setIssue] = useState<Issue | null>(null);
  const [essays, setEssays] = useState<EssaySummary[]>([]);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [template, setTemplate] = useState("elegant");
  const [order, setOrder] = useState<ExportOrder>("student_no");
  const [preview, setPreview] = useState("");
  const [previewState, setPreviewState] = useState<PreviewState>("loading");
  const [previewRetry, setPreviewRetry] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const className = useClassName();

  const pendingCount = essays.filter((essay) => essay.status !== "proofread").length;
  const canExport = essays.length > 0 && pendingCount === 0;
  const orderedEssays = useMemo(() => sortEssaySummaries(essays, order), [essays, order]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [issueData, list, templateList] = await Promise.all([
        api.getIssue(numericIssueId),
        api.listIssueEssays(numericIssueId),
        api.listTemplates(),
      ]);
      setIssue(issueData);
      setEssays(list);
      setTemplates(templateList);
      setTemplate((previous) =>
        templateList.some((item) => item.key === previous) ? previous : templateList[0]?.key ?? previous,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "成册页加载失败");
    } finally {
      setLoading(false);
    }
  }, [numericIssueId]);

  useEffect(() => {
    void load();
  }, [load]);

  // 预览：模板 / 排序变化即刷新（与 PDF 同一套模板）；带 loading / 失败重试状态。
  useEffect(() => {
    if (essays.length === 0) {
      setPreview("");
      setPreviewState("empty");
      return;
    }
    let cancelled = false;
    setPreviewState("loading");
    api
      .fetchExportPreview(numericIssueId, { template, order })
      .then((html) => {
        if (!cancelled) {
          setPreview(html);
          setPreviewState("ready");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPreview("");
          setPreviewState("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [numericIssueId, template, order, essays.length, previewRetry]);

  async function handleExportBook(): Promise<void> {
    setBusy(true);
    setError("");
    try {
      const blob = await api.exportBook(numericIssueId, { template, order });
      triggerDownload(blob, bookFileName(issue?.issue_no ?? numericIssueId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "导出失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function handleExportSingle(essay: EssaySummary): Promise<void> {
    setBusy(true);
    setError("");
    try {
      const blob = await api.exportSingle(numericIssueId, essay.id, template);
      triggerDownload(
        blob,
        singleFileName(issue?.issue_no ?? numericIssueId, essay.student_name ?? "", essay.title),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "单篇导出失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          成册与导出{issue ? ` · 第 ${issue.issue_no} 期` : ""}
          {className ? (
            <span className="ml-2 text-sm font-normal text-slate-500">{className}</span>
          ) : null}
        </h1>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => navigate("/")}
            data-testid="back-home"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            期数列表
          </button>
          <button
            type="button"
            onClick={() => navigate(`/issues/${issueId}/essays`)}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            ← 看板
          </button>
        </div>
      </header>

      {error ? (
        <p className="mt-4 rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-600">{error}</p>
      ) : null}

      <section className="mt-5 grid gap-5 lg:grid-cols-[320px_1fr]">
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-sm font-medium text-slate-700">模板</p>
            <div className="mt-2">
              <TemplatePicker
                templates={templates}
                value={template}
                onChange={setTemplate}
                disabled={busy}
              />
            </div>

            <label className="mt-4 block text-sm font-medium text-slate-700" htmlFor="order">
              排序
            </label>
            <select
              id="order"
              data-testid="order-select"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
              value={order}
              onChange={(event) => setOrder(event.target.value as ExportOrder)}
            >
              <option value="student_no">按学号</option>
              <option value="name">按姓名</option>
              <option value="score">按评分</option>
              <option value="selected_score">精选优先（精选内按评分）</option>
            </select>
            <p className="mt-1 text-xs text-slate-400">按评分排序时分数高的在前，未评分的排最后（同分按学号）。</p>

            <button
              type="button"
              data-testid="export-book"
              disabled={!canExport || busy}
              onClick={handleExportBook}
              className="mt-4 w-full rounded-md bg-slate-900 px-4 py-2.5 text-base font-medium text-white disabled:opacity-50"
            >
              {busy ? "处理中…" : "导出整册 PDF"}
            </button>
            {!canExport ? (
              <p data-testid="export-hint" className="mt-2 text-xs text-amber-600">
                {essays.length === 0
                  ? "本期暂无作文，无法成册。"
                  : `还有 ${pendingCount} 篇未校对定稿，暂时无法成册。`}
              </p>
            ) : (
              <p className="mt-2 text-xs text-emerald-600">已全部定稿，可导出（共 {essays.length} 篇）。</p>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-sm font-medium text-slate-700">单篇版式（打印张贴）</p>
            {essays.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400">暂无作文</p>
            ) : (
              <ul data-testid="single-layout-list" className="mt-2 flex max-h-[55vh] flex-col gap-2 overflow-y-auto pr-1">
                {orderedEssays.map((essay) => (
                  <li key={essay.id} className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm text-slate-700">
                      {essay.student_name ?? `#${essay.student_id}`}
                      {essay.title ? ` · ${essay.title}` : ""}
                    </span>
                    <button
                      type="button"
                      disabled={essay.status !== "proofread" || busy}
                      onClick={() => handleExportSingle(essay)}
                      className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-700 disabled:opacity-40"
                    >
                      单篇 PDF
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-slate-700">预览（A4）</p>
            {issue ? (
              <p className="text-xs text-slate-400">周一起 {formatDate(issue.week_start_date)}</p>
            ) : null}
          </div>
          {loading ? (
            <p className="mt-3 text-sm text-slate-500">加载中…</p>
          ) : previewState === "loading" ? (
            <div
              data-testid="preview-loading"
              className="mt-3 flex h-[70vh] items-center justify-center rounded-md border border-slate-200 bg-slate-50 text-sm text-slate-500"
            >
              预览生成中…
            </div>
          ) : previewState === "error" ? (
            <div className="mt-3 flex h-[70vh] flex-col items-center justify-center gap-3 rounded-md border border-slate-200 bg-slate-50 text-sm text-slate-500">
              预览加载失败，请重试。
              <button
                type="button"
                data-testid="preview-retry"
                onClick={() => setPreviewRetry((tick) => tick + 1)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
              >
                重试
              </button>
            </div>
          ) : preview ? (
            <PreviewFrame html={preview} />
          ) : (
            <p className="mt-3 text-sm text-slate-500">暂无可预览内容。</p>
          )}
        </div>
      </section>
    </main>
  );
}
