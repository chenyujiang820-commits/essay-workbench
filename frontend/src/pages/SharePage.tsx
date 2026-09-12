/**
 * 家长只读页（v1.3 / FR-07）：免鉴权、只读、移动端单列。
 *
 * 四条纪律：
 * 1. 令牌无效一律走同一个失效态：后端对「不存在 / 已撤销 / 已过期」返回同一句 410 文案，
 *    本页只回显那句话、不区分原因 —— 区分原因等于替外人枚举哪些链接存在过。
 * 2. 失效态的 DOM 里不得出现任何学生、篇目或正文内容：本页在拿到数据之前就失败，
 *    所以只要不缓存上一次的 data 就成立，测试把这条钉死。
 * 3. 只出星级不出分数：分数是校内评价口径，家长看到的与 PDF 家长页保持同一判据。
 * 4. 不显示学号：后端已把 student_no 抹成空串，本页也不从别处拼回来。
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { ShareView } from "../api/types";
import PreviewFrame from "../components/PreviewFrame";
import Stars from "../components/Stars";
import { formatDate } from "../lib/date";

/** HTTP 410：链接不存在 / 已撤销 / 已过期，后端刻意不给细分原因。 */
export const GONE_STATUS = 410;

/** 兜底文案：连不上后端（或后端没带 message）时也要给家长一句人话。 */
export const GONE_FALLBACK = "链接已失效或不存在，请向老师重新获取";

/** 后端把 student_no 抹空，这里再兜一层：空白就不显示分隔点，避免名字后面挂个孤零零的 ·。 */

export default function SharePage() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<ShareView | null>(null);
  const [loading, setLoading] = useState(true);
  const [goneMessage, setGoneMessage] = useState("");
  const [error, setError] = useState("");
  const [printHtml, setPrintHtml] = useState("");
  const [printBusy, setPrintBusy] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    // 没有 token（链接被截断）也按失效处理：不给空白页，也不给报错术语。
    if (!token) {
      setLoading(false);
      setGoneMessage(GONE_FALLBACK);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const view = await api.fetchShareView(token);
      setData(view);
      setGoneMessage("");
    } catch (err) {
      if (err instanceof ApiError && err.status === GONE_STATUS) {
        setData(null);
        setGoneMessage(err.message || GONE_FALLBACK);
      } else {
        setError(err instanceof ApiError ? err.message : "打开失败，请检查网络后重试");
      }
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handlePrint(): Promise<void> {
    if (!token) {
      return;
    }
    setPrintBusy(true);
    setError("");
    try {
      // 打印版直接复用成册那套模板：家里打出来的和教室里发的是同一张纸。
      setPrintHtml(await api.fetchSharePreview(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "打印版加载失败");
    } finally {
      setPrintBusy(false);
    }
  }

  if (loading) {
    return (
      <main className="mx-auto max-w-xl px-4 py-10 text-sm text-slate-500" data-testid="share-loading">
        正在加载作文…
      </main>
    );
  }

  if (goneMessage) {
    return (
      <main className="mx-auto max-w-xl px-5 py-16 text-center">
        <h1 className="text-lg font-semibold text-slate-900">这份作文打不开</h1>
        <p data-testid="share-gone" className="mt-3 text-sm leading-6 text-slate-600">
          {goneMessage}
        </p>
        <button
          type="button"
          data-testid="share-retry"
          onClick={() => void load()}
          className="mt-6 rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-700"
        >
          重新打开
        </button>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="mx-auto max-w-xl px-5 py-10">
        <p data-testid="share-error" className="rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-600">
          {error || "作文没能加载出来"}
        </p>
        <button
          type="button"
          data-testid="share-retry"
          onClick={() => void load()}
          className="mt-4 rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-700"
        >
          重试
        </button>
      </main>
    );
  }

  const items = data.items ?? [];
  const heading =
    data.scope === "student"
      ? (data.student_name ?? "") + " 的作文"
      : "第 " + data.issue_no + " 期作文集";

  return (
    <main className="mx-auto max-w-xl px-4 pb-12 pt-6 sm:px-6">
      <header className="border-b border-slate-200 pb-4">
        <p className="text-xs text-slate-500">{data.class_name}</p>
        <h1 data-testid="share-title" className="mt-1 text-xl font-semibold text-slate-900">{heading}</h1>
        <p className="mt-1 text-sm text-slate-500">
          周一起 {formatDate(data.week_start_date)} · 共 {items.length} 篇
        </p>
      </header>

      {error ? (
        <p data-testid="share-error" className="mt-4 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-600">
          {error}
        </p>
      ) : null}

      {items.length === 0 ? (
        <p data-testid="share-empty" className="mt-6 rounded-md bg-slate-100 px-4 py-3 text-sm text-slate-500">
          这一期暂时没有可以展示的作文，请等老师定稿后再打开链接。
        </p>
      ) : (
        <ul className="mt-5 flex flex-col gap-4">
          {items.map((item) => (
            <li
              key={item.name + "-" + item.title}
              data-testid="share-item"
              className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
            >
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="min-w-0 flex-1 text-base font-semibold text-slate-900">
                  {item.title || "未命名"}
                </h2>
                {/* 只出星不出分：Stars 不传 score 就不会渲染分数 */}
                <Stars stars={item.stars ?? 0} />
                {item.is_selected ? (
                  <span className="shrink-0 rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                    本期精选
                  </span>
                ) : null}
              </div>
              {/* 家长页永远不出现学号：后端已抹一次，这里不二次信任，避免"改了后端忘了前端" */}
      <p className="mt-1 text-sm text-slate-500">{item.name}</p>
              <div className="mt-3 flex flex-col gap-2 text-[15px] leading-7 text-slate-800">
                {(item.paragraphs ?? []).map((paragraph, index) => (
                  <p key={item.title + "-" + index}>{paragraph}</p>
                ))}
              </div>
              {item.comment ? (
                <p
                  data-testid="share-comment"
                  className="mt-3 whitespace-pre-wrap rounded-lg bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-600"
                >
                  老师的话：{item.comment}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-4">
        <p data-testid="share-footer" className="text-sm text-slate-600">
          本页有效期至 {formatDate(data.expires_at)}，过期后需要老师重新生成。
        </p>
        <button
          type="button"
          data-testid="share-print"
          onClick={() => void handlePrint()}
          disabled={printBusy}
          className="mt-3 w-full rounded-md bg-slate-900 px-4 py-2.5 text-base font-medium text-white disabled:opacity-50"
        >
          {printBusy ? "加载中…" : "打印 / 存为 PDF"}
        </button>
        {printHtml ? (
          <>
            <p className="mt-3 text-xs text-slate-500">
              下面是打印版：手机浏览器菜单里的「打印」，或电脑上按 Ctrl+P 即可。
            </p>
            <PreviewFrame html={printHtml} />
          </>
        ) : null}
      </section>

      <p className="mt-4 text-xs text-slate-400">生成于 {data.generated_at} · 由老师分享，内容仅供家长查看</p>
    </main>
  );
}
