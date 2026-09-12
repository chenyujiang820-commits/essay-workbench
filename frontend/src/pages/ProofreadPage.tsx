/**
 * 校对环核心：桌面左右分栏（左原片 / 右 diff + 可编辑定稿），移动端上下堆叠可切换。
 *
 * * 顶部：PRD 强制警示条（常显、不可关闭）+ 标题输入框 + 整篇低置信横幅；
 * * 点击右侧存疑段落 → 高亮并切换到对应序号的左侧原片，并记入「已查看」集合；
 * * 识别失败（status=failed）给专门提示 +「重新识别」入口（FR-10），此时不提供定稿按钮；
 * * 「保存并定稿」PATCH（final_text 非空 + title + proofread=true）后返回看板；
 *   仍有存疑处未点看时先给一次非阻断确认（FR-09）；
 * * 有未保存修改时离开页面需二次确认。
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useBlocker, useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { EssayDetail, Photo } from "../api/types";
import DiffText, {
  composeInitialText,
  listActiveSuspectKeys,
  listSuspectKeys,
} from "../components/DiffText";
import PhotoViewer from "../components/PhotoViewer";

const EMPTY_PHOTOS: Photo[] = [];

/** 移动端分栏键。 */
type PaneKey = "photo" | "text";

/** 移动端上下堆叠时的两个分栏（顺序即按钮顺序）。 */
const PANE_LABELS: ReadonlyArray<readonly [PaneKey, string]> = [
  ["photo", "原片"],
  ["text", "文字"],
];

/**
 * PRD v1.2 FR-09 强制警示文案（逐字对齐，不可改写、不可折叠、移动端不隐藏）。
 * 这是 v1.1 §9.2「编字风险三重防线」之一，缺文案即视为验收不通过（AC-2）。
 */
const AI_CAUTION_TEXT = "AI 可能会把同学的错字“改对”，请逐句以原片为准。";

/** 加载各原片的 objectURL，并在卸载 / 变化时回收，避免内存泄漏。 */
function usePhotoUrls(photos: Photo[]): Record<number, string> {
  const [urls, setUrls] = useState<Record<number, string>>({});

  useEffect(() => {
    if (photos.length === 0) {
      setUrls({});
      return;
    }
    let cancelled = false;
    const created: string[] = [];

    void (async () => {
      const next: Record<number, string> = {};
      for (const photo of photos) {
        try {
          const blob = await api.fetchPhotoBlob(photo.id);
          const url = URL.createObjectURL(blob);
          created.push(url);
          next[photo.id] = url;
        } catch {
          /* 单张原片加载失败不阻断整页 */
        }
      }
      if (cancelled) {
        created.forEach((url) => URL.revokeObjectURL(url));
        return;
      }
      setUrls(next);
    })();

    return () => {
      cancelled = true;
      created.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [photos]);

  return urls;
}

/**
 * OPT-01 / FR-09 存疑清点：持有本次会话内的「已点看」集合，并在仍有未点看存疑处时
 * 给出**一次**非阻断确认。集合不持久化（离开页面即随组件卸载复位）。
 */
function useSuspectReview(photos: Photo[]) {
  const [viewedSuspects, setViewedSuspects] = useState<Set<string>>(() => new Set<string>());
  const suspectKeys = useMemo(() => listSuspectKeys(photos), [photos]);

  const markViewed = useCallback((key: string) => {
    setViewedSuspects((prev) => (prev.has(key) ? prev : new Set(prev).add(key)));
  }, []);
  const resetViewed = useCallback(() => setViewedSuspects(new Set<string>()), []);

  const unviewedCount = suspectKeys.filter((key) => !viewedSuspects.has(key)).length;

  /** 返回 true 表示可以继续保存；false 表示用户选择留下继续核对。 */
  const confirmUnviewed = useCallback((): boolean => {
    if (unviewedCount === 0) {
      return true;
    }
    return window.confirm(`本篇还有 ${unviewedCount} 处存疑未逐一点看，确认已核对？`);
  }, [unviewedCount]);

  return { viewedSuspects, markViewed, resetViewed, confirmUnviewed };
}

/** 失败原因摘要：过长任务错误只取首 120 字，避免挤占校对版面。 */
function briefTaskError(error: string | null | undefined): string {
  const trimmed = (error ?? "").trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.length > 120 ? `${trimmed.slice(0, 120)}…` : trimmed;
}

/** 顶部提示条的统一样式（配色集中在此，避免各处复制 className）。 */
const NOTICE_TONES = {
  /** FR-09 强制警示：rose 底 + 描边，字号不小于正文说明行，移动端同样整行可见。 */
  caution: "border border-rose-300 bg-rose-100 font-medium leading-6 text-rose-800",
  warning: "bg-amber-100 text-amber-800",
  danger: "bg-rose-50 text-rose-600",
  success: "bg-emerald-50 text-emerald-700",
  hint: "bg-slate-100 text-slate-500",
} as const;

/** 页面内次要按钮的统一样式（返回、重试等）。 */
const GHOST_BUTTON = "rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700";

function Notice({
  tone,
  testId,
  children,
}: {
  tone: keyof typeof NOTICE_TONES;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <p
      data-testid={testId}
      className={`mt-3 rounded-md px-4 py-2 text-sm break-words ${NOTICE_TONES[tone]}`}
    >
      {children}
    </p>
  );
}

export default function ProofreadPage() {
  const { essayId } = useParams();
  const navigate = useNavigate();
  const numericEssayId = Number(essayId);

  const [essay, setEssay] = useState<EssayDetail | null>(null);
  const [value, setValue] = useState("");
  const [initialText, setInitialText] = useState("");
  const [title, setTitle] = useState("");
  const [initialTitle, setInitialTitle] = useState("");
  const [activeSeq, setActiveSeq] = useState(1);
  // 移动端默认落在「文字」页：定稿文字与识别对照都在这里，老师点开文章第一眼就能看到；
  // 桌面端两栏同时可见，此默认值不影响桌面。原片点一下页签即可切回（GAP-12）。
  const [pane, setPane] = useState<PaneKey>("text");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [retryNotice, setRetryNotice] = useState("");

  const review = useSuspectReview(essay?.photos ?? EMPTY_PHOTOS);
  // 仅取稳定引用的复位函数：避免 review 对象每次渲染新建导致 load 身份抖动（无限重加载）。
  const { resetViewed } = review;

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const detail = await api.getEssay(numericEssayId);
      setEssay(detail);
      const seed =
        detail.final_text && detail.final_text.trim()
          ? detail.final_text
          : composeInitialText(detail.photos);
      setValue(seed);
      setInitialText(seed);
      setTitle(detail.title ?? "");
      setInitialTitle(detail.title ?? "");
      // 重新载入即视为一次新的核对会话，存疑清点随之复位。
      resetViewed();
      setActiveSeq(detail.photos[0]?.seq ?? 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "作文加载失败");
    } finally {
      setLoading(false);
    }
  }, [numericEssayId, resetViewed]);

  useEffect(() => {
    void load();
  }, [load]);

  const photos = essay?.photos ?? EMPTY_PHOTOS;
  const photoUrls = usePhotoUrls(photos);
  const activePhoto = useMemo(
    () => photos.find((photo) => photo.seq === activeSeq) ?? photos[0] ?? null,
    [photos, activeSeq],
  );

  // 页签上的「存疑 N」与面板徽标同源（listActiveSuspectKeys）：移动端否则没人知道
  // 「文字」页里还有一张识别对照表要看的（GAP-12 真机反馈）。
  const compareCount = useMemo(
    () => listActiveSuspectKeys(photos, value).length,
    [photos, value],
  );

  /** 点存疑处 = 要看原片：移动端原片在另一个页签，必须连页签一起切过去才看得见。 */
  const focusPhoto = useCallback((seq: number) => {
    setActiveSeq(seq);
    setPane("photo");
  }, []);

  // 标题是一等字段（FR-11）：改标题未保存同样算「脏」。
  const dirty = value !== initialText || title !== initialTitle;
  const failed = essay?.status === "failed";
  const taskErrorBrief = briefTaskError(essay?.task?.error);

  // 保存成功后的返回跳转不算「未保存离开」（ref 即时读取，不受渲染时序影响）。
  const savedNavRef = useRef(false);

  // SPA 内路由离开（含浏览器返回键 / 手机手势返回）统一拦截确认。
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    return !savedNavRef.current && dirty && currentLocation.pathname !== nextLocation.pathname;
  });

  useEffect(() => {
    if (blocker.state !== "blocked") {
      return;
    }
    if (window.confirm("有未保存的修改，确定离开而不保存吗？")) {
      blocker.proceed();
    } else {
      blocker.reset();
    }
  }, [blocker]);

  // 关闭/刷新浏览器标签时的二次确认。
  useEffect(() => {
    if (!dirty) {
      return;
    }
    const handler = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
      event.returnValue = "有未保存的修改";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  function handleBack(): void {
    // 未保存确认由 useBlocker 统一处理（按钮与手势返回同一条路径）。
    navigate(`/issues/${essay?.issue_id ?? ""}/essays`);
  }

  /** FR-10 / GAP-05：识别失败稿一键重新排队识别。 */
  async function handleRetry(): Promise<void> {
    setRetrying(true);
    setError("");
    try {
      await api.retryEssay(numericEssayId);
      await load();
      setRetryNotice("已重新排队识别");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "重新识别失败，请稍后再试");
    } finally {
      setRetrying(false);
    }
  }

  async function handleSave(): Promise<void> {
    if (!value.trim()) {
      setError("定稿文字不能为空");
      return;
    }
    // FR-09：仍有存疑处未逐一点看时，先给一次非阻断确认；取消则留在本页继续核对。
    if (!review.confirmUnviewed()) {
      return;
    }
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const saved = await api.updateEssay(numericEssayId, {
        final_text: value,
        proofread: true,
        title,
      });
      setEssay(saved);
      setInitialText(value);
      setInitialTitle(title);
      savedNavRef.current = true;
      // 给一句可见的成功反馈，再返回看板。
      setSaved(true);
      window.setTimeout(() => navigate(`/issues/${saved.issue_id}/essays`), 600);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <main className="px-4 py-10 text-sm text-slate-500">加载中…</main>;
  }

  if (!essay) {
    return (
      <main className="px-4 py-10">
        <p className="rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-600">
          {error || "作文不存在"}
        </p>
        <div className="mt-4 flex gap-2">
          <button
            type="button"
            onClick={() => void load()}
            className={GHOST_BUTTON}
          >
            重试
          </button>
          <button
            type="button"
            onClick={() => navigate("/")}
            className={GHOST_BUTTON}
          >
            返回首页
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-7xl flex-col px-3 py-4 sm:px-4 lg:h-dvh">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => navigate("/")}
              data-testid="back-home"
              className={GHOST_BUTTON}
            >
              期数列表
            </button>
            <button type="button" onClick={handleBack} className={GHOST_BUTTON}>
              ← 看板
            </button>
          </div>
          <h1 className="min-w-0 truncate text-lg font-semibold text-slate-900">
            逐句校对 · {essay.student_name ?? `#${essay.student_id}`}
          </h1>
          {dirty ? <span className="shrink-0 text-xs text-amber-600">未保存</span> : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {failed ? (
            <button
              type="button"
              data-testid="failed-retry"
              disabled={retrying}
              onClick={() => void handleRetry()}
              className="rounded-md bg-rose-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {retrying ? "排队中…" : "重新识别"}
            </button>
          ) : (
            <button
              type="button"
              data-testid="save-proofread"
              disabled={saving}
              onClick={() => void handleSave()}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {saving ? "保存中…" : "保存并定稿"}
            </button>
          )}
        </div>
      </header>

      {/* FR-09 强制警示：常显、不可关闭、不进 details、移动端不隐藏（AC-2）。 */}
      <Notice tone="caution" testId="ai-warning">
        {AI_CAUTION_TEXT}
      </Notice>

      <label className="mt-3 flex flex-wrap items-center gap-2 text-sm font-medium text-slate-700">
        <span className="shrink-0">标题</span>
        <input
          data-testid="essay-title"
          type="text"
          value={title}
          maxLength={200}
          disabled={saving}
          placeholder="识别自动抽取，可修改；留空时成册与投屏显示「未命名」"
          onChange={(event) => setTitle(event.target.value)}
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-base outline-none focus:border-slate-500 disabled:bg-slate-50"
        />
      </label>

      {essay.low_confidence === 1 ? (
        <Notice tone="warning">本篇识别置信度偏低，请重点核对全部存疑处后再定稿。</Notice>
      ) : null}

      {failed ? (
        <div
          data-testid="failed-notice"
          className="mt-3 rounded-md border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-700"
        >
          <p className="font-medium">本篇识别失败，没有可校对的识别结果。</p>
          <p className="mt-1 break-words text-xs leading-5 text-rose-600">
            {taskErrorBrief || "识别服务未返回具体原因，可直接重新排队识别。"}
          </p>
          <p className="mt-1 text-xs leading-5 text-rose-500">
            排队完成后回看板刷新即可进入校对；期间本篇不可定稿。
          </p>
        </div>
      ) : null}

      {failed ? null : (
        <Notice tone="hint">
          <span className="hidden lg:inline">
            左侧为原片（可缩放），右侧上方填写标题与定稿文字，下方「识别对照」黄色为引擎存疑处；
            逐个点看存疑处可标记「已查看」并定位对应原片。核对后点击「保存并定稿」。
          </span>
          <span className="lg:hidden">
            当前是「文字」页：上方改定稿，下面「识别对照」标出未识别字（橙色）与被改动句（红色），
            点任意标注会跳到对应原片核对。核对完点「保存并定稿」。
          </span>
        </Notice>
      )}

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {retryNotice ? <Notice tone="success" testId="retry-notice">{retryNotice}</Notice> : null}

      {saved ? (
        <Notice tone="success">已定稿保存成功，正在返回看板…</Notice>
      ) : null}

      <div className="mt-3 flex gap-2 lg:hidden">
        {PANE_LABELS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            data-testid={"pane-" + key}
            aria-pressed={pane === key}
            onClick={() => setPane(key)}
            className={`flex-1 rounded-md px-3 py-2 text-sm ${
              pane === key ? "bg-slate-900 text-white" : "border border-slate-300 text-slate-700"
            }`}
          >
            {key === "text" && compareCount > 0 ? label + " · 存疑 " + compareCount : label}
          </button>
        ))}
      </div>

      {/* 手机端定稿框 + 识别对照远超一屏：整页滚动（见 styles.css 高度放开）才读得完；
          桌面端仍保持「一屏不滚」的工作台布局（GAP-12 真机反馈）。 */}
      <div className="mt-3 grid flex-1 gap-4 lg:min-h-0 lg:grid-cols-2 lg:overflow-hidden">
        <section className={`min-h-0 flex-col ${pane === "photo" ? "flex" : "hidden"} lg:flex`}>
          {photos.length > 1 ? (
            <div className="mb-2 flex flex-wrap gap-1">
              {photos.map((photo) => (
                <button
                  key={photo.id}
                  type="button"
                  onClick={() => setActiveSeq(photo.seq)}
                  className={`rounded-md px-2.5 py-1 text-xs ${
                    photo.seq === activePhoto?.seq
                      ? "bg-slate-900 text-white"
                      : "border border-slate-300 text-slate-700"
                  }`}
                >
                  第 {photo.seq} 张
                </button>
              ))}
            </div>
          ) : null}
          {/* 手机端给原片确定高度：PhotoViewer 内部是 h-full，父级无确定高度时缩放层会塌成 0。 */}
          <div className="min-h-[240px] flex-1 max-lg:h-[52vh] max-lg:flex-none">
            <PhotoViewer
              url={activePhoto ? photoUrls[activePhoto.id] ?? null : null}
              seq={activePhoto?.seq ?? 1}
              lowResolution={activePhoto?.low_resolution === 1}
            />
          </div>
        </section>

        <section className={`min-h-0 ${pane === "text" ? "block" : "hidden"} lg:block`}>
          <DiffText
            photos={photos}
            value={value}
            onChange={setValue}
            onSelectPhoto={focusPhoto}
            viewedSuspects={review.viewedSuspects}
            onSuspectView={review.markViewed}
            disabled={saving}
          />
        </section>
      </div>
    </main>
  );
}
