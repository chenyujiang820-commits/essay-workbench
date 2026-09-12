/**
 * 学生成长档案（v1.3 / FR-06）：单个学生的全部已定稿作文 + 统计 + 个人文集导出。
 *
 * 三条口径纪律：
 * 1. 列表顺序、星级、上榜次数一律后端算好下发，前端不再重算 —— 档案页、家长页、文集 PDF
 *    必须是同一套先后关系，否则学校和家里看到的两份档案会互相矛盾。
 * 2. 只有已定稿作文进档案（后端已过滤），所以本页不出现「待校对」状态与相关操作。
 * 3. 平均分为 null 时显示「—」而不是 0：0 会被家长读成「孩子考了 0 分」。
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { PortfolioData, PortfolioEntry, TemplateInfo } from "../api/types";
import Stars from "../components/Stars";
import TemplatePicker from "../components/TemplatePicker";
import { formatDate } from "../lib/date";
import { triggerDownload } from "../lib/download";

/** 文集排序：档案默认按期号倒序（最新写的在最前），其余取值与整册共用同一实现。 */
type PortfolioOrder = "issue_no" | "student_no" | "name" | "score";

/** 个人文集下载文件名：与后端 Content-Disposition 的默认名保持一致，不在两处给出不同名。 */
export function portfolioFileName(studentName: string): string {
  return studentName + "的作文成长档案.pdf";
}

/** 平均分展示：null（该生一篇都没评分）显示「—」，不显示 0。 */
export function formatAvgScore(avgScore: number | null | undefined): string {
  if (avgScore === null || avgScore === undefined) {
    return "—";
  }
  return String(avgScore);
}

function StatCard({ label, value, testId }: { label: string; value: ReactNode; testId: string }) {
  return (
    <div
      data-testid={testId}
      className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-sm"
    >
      <p className="text-xs text-slate-500">{label}</p>
      <div className="mt-1 text-lg font-semibold text-slate-900">{value}</div>
    </div>
  );
}

export default function PortfolioPage() {
  const { studentId } = useParams();
  const navigate = useNavigate();
  const numericStudentId = Number(studentId);

  const [data, setData] = useState<PortfolioData | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [template, setTemplate] = useState("elegant");
  const [order, setOrder] = useState<PortfolioOrder>("issue_no");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [exportError, setExportError] = useState("");
  const [openComments, setOpenComments] = useState<Record<number, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [portfolio, templateList] = await Promise.all([
        api.fetchPortfolio(numericStudentId),
        api.listTemplates(),
      ]);
      setData(portfolio);
      setTemplates(templateList);
      setTemplate((previous) =>
        templateList.some((item) => item.key === previous)
          ? previous
          : templateList[0]?.key ?? previous,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "成长档案加载失败");
    } finally {
      setLoading(false);
    }
  }, [numericStudentId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleExport(): Promise<void> {
    setBusy(true);
    setExportError("");
    try {
      const blob = await api.exportPortfolio(numericStudentId, template, order);
      triggerDownload(blob, portfolioFileName(data?.student.name ?? "学生"));
    } catch (err) {
      // 后端对「没有任何定稿作文」返回 400，文案（含学生姓名）必须原样显示给老师。
      setExportError(err instanceof ApiError ? err.message : "导出失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  function toggleComment(essayId: number): void {
    setOpenComments((previous) => ({ ...previous, [essayId]: !previous[essayId] }));
  }

  const entries: PortfolioEntry[] = data?.entries ?? [];
  const canExport = entries.length > 0;

  return (
    <main className="mx-auto max-w-4xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-slate-900">
          成长档案
          {data ? <span className="ml-2">{data.student.name}</span> : null}
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          {data ? (
            <>
              {data.student.student_no}
              {data.class_name ? <span className="ml-2">{data.class_name}</span> : null}
              {data.stats.last_issue_no !== null ? (
                <span className="ml-2">最近第 {data.stats.last_issue_no} 期</span>
              ) : null}
            </>
          ) : (
            <span>只收录已校对定稿的作文</span>
          )}
        </p>
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => navigate("/students")}
          data-testid="back-students"
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
        >
          学生名单
        </button>
        <button
          type="button"
          onClick={() => void load()}
          data-testid="reload-portfolio"
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
        >
          刷新
        </button>
      </div>
      </header>

      {loading ? <p className="mt-4 text-sm text-slate-500">加载中…</p> : null}
      {error ? (
        <p
          data-testid="portfolio-error"
          className="mt-4 rounded-md bg-rose-50 px-4 py-2 text-sm text-rose-600"
        >
          {error}
        </p>
      ) : null}

      {data ? (
        <>
          <section className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <StatCard testId="stat-essay-count" label="定稿作文" value={data.stats.essay_count} />
            <StatCard testId="stat-selected-count" label="入选精选" value={data.stats.selected_count} />
            <StatCard testId="stat-honoured-count" label="上榜次数" value={data.stats.honoured_count} />
            <StatCard
              testId="stat-avg-score"
              label="平均分"
              value={
                <span data-testid="avg-score">
                  {formatAvgScore(data.stats.avg_score)}
                  {data.stats.avg_score === null ? null : <span className="text-xs font-normal text-slate-500"> 分</span>}
                </span>
              }
            />
            <StatCard
              testId="stat-top-stars"
              label="最高星级"
              value={
                data.stats.top_stars > 0 ? (
                  <Stars stars={data.stats.top_stars} />
                ) : (
                  <span>—</span>
                )
              }
            />
            <StatCard testId="stat-issue-count" label="参与期数" value={data.stats.issue_count} />
          </section>

          <section className="mt-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-sm font-medium text-slate-700">导出个人文集 PDF</p>
            <div className="mt-2">
              <TemplatePicker
                templates={templates}
                value={template}
                onChange={setTemplate}
                disabled={busy || !canExport}
              />
            </div>
            <label
              className="mt-4 block text-sm font-medium text-slate-700"
              htmlFor="portfolio-order"
            >
              排序
            </label>
            <select
              id="portfolio-order"
              data-testid="portfolio-order"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500 sm:max-w-xs"
              value={order}
              disabled={busy || !canExport}
              onChange={(event) => setOrder(event.target.value as PortfolioOrder)}
            >
              <option value="issue_no">按期号（新→旧）</option>
              <option value="student_no">按学号</option>
              <option value="name">按姓名</option>
              <option value="score">按佳作序</option>
            </select>
            <button
              type="button"
              data-testid="export-portfolio"
              disabled={!canExport || busy}
              onClick={() => void handleExport()}
              className="mt-4 w-full rounded-md bg-slate-900 px-4 py-2.5 text-base font-medium text-white disabled:opacity-50"
            >
              {busy ? "处理中…" : "导出个人文集 PDF"}
            </button>
            {canExport ? (
              <p className="mt-2 text-xs text-emerald-600">
                共 {entries.length} 篇已定稿作文可导出。
              </p>
            ) : (
              <p data-testid="portfolio-empty" className="mt-2 text-xs text-amber-600">
                这位同学还没有任何已定稿作文：先在校对页完成定稿，档案与文集才会成型。
              </p>
            )}
            {exportError ? (
              <p
                data-testid="export-error"
                className="mt-2 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-600"
              >
                {exportError}
              </p>
            ) : null}
          </section>

          <section className="mt-6">
            <h2 className="text-sm font-medium text-slate-700">
              作文列表
              <span className="ml-2 text-xs font-normal text-slate-400">按期号由新到旧</span>
            </h2>
            {entries.length === 0 ? (
              <p className="mt-3 rounded-md bg-slate-100 px-4 py-3 text-sm text-slate-500">
                档案还是空的。等这篇作文校对定稿之后，它会带着分数、星级和评语出现在这里。
              </p>
            ) : (
              <ul className="mt-3 flex flex-col gap-2">
                {entries.map((entry) => {
                  const open = Boolean(openComments[entry.essay_id]);
                  return (
                    <li
                      key={entry.essay_id}
                      data-testid={"portfolio-entry-" + entry.essay_id}
                      className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="shrink-0 rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                          第 {entry.issue_no} 期
                        </span>
                        <span className="shrink-0 text-xs text-slate-400">
                          {formatDate(entry.week_start_date)}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-base font-medium text-slate-900">
                          {entry.title || "未命名"}
                        </span>
                        <Stars stars={entry.stars} score={entry.score} />
                        {entry.score === null ? (
                          <span className="shrink-0 text-xs text-slate-400">未评分</span>
                        ) : null}
                        {entry.selected ? (
                          <span className="shrink-0 rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                            本期精选
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 text-xs text-slate-400">
                        原片 {entry.photo_count} 张
                        {entry.proofread_at ? " · 定稿于 " + formatDate(entry.proofread_at) : ""}
                      </p>
                      {entry.teacher_comment ? (
                        <>
                          <button
                            type="button"
                            data-testid={"comment-toggle-" + entry.essay_id}
                            aria-expanded={open}
                            onClick={() => toggleComment(entry.essay_id)}
                            className="mt-2 text-xs text-slate-500 underline"
                          >
                            {open ? "收起评语" : "查看评语"}
                          </button>
                          {open ? (
                            <p
                              data-testid={"comment-" + entry.essay_id}
                              className="mt-2 whitespace-pre-wrap rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700"
                            >
                              {entry.teacher_comment}
                            </p>
                          ) : null}
                        </>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <p className="mt-6 text-xs text-slate-400">档案数据更新于 {data.generated_at}</p>
        </>
      ) : null}
    </main>
  );
}
