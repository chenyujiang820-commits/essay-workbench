/**
 * 校对环核心：桌面左右分栏（左原片 / 右 diff + 可编辑定稿），移动端上下堆叠可切换。
 *
 * * 顶部：PRD 强制警示条（常显、不可关闭）+ 标题输入框 + 整篇低置信横幅；
 * * 点击右侧存疑段落 → 高亮并切换到对应序号的左侧原片，并记入「已查看」集合；
 * * 识别失败（status=failed）给专门提示 +「重新识别」入口（FR-10），此时不提供定稿按钮；
 * * 「保存并定稿」PATCH（final_text 非空 + title + proofread=true）后返回看板；
 * 定稿文字区下方是二期新增的「评」区（评语 + 评分 + 星级预览）：
 *   「仅保存评语」只写评语、不推状态机，且本篇未定稿时绝不提交 score（后端会 400）；
 *   星级一律以提交返回的 EssayDetail.stars 重绘，本地阈值只用于打字时的即时预览；
 *   仍有存疑处未点看时先给一次非阻断确认（FR-09）；
 * * 有未保存修改时离开页面需二次确认。
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useBlocker, useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { EssayDetail, Photo } from "../api/types";
import DiffText, {
  DiffComparePanel,
  composeInitialText,
  listActiveSuspectKeys,
  listSuspectKeys,
} from "../components/DiffText";
import PhotoViewer from "../components/PhotoViewer";
import Stars from "../components/Stars";

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

/** 评语字数上限（与后端 app/ranking.py 的 MAX_COMMENT_LENGTH 同值）。 */
const COMMENT_MAX_LENGTH = 2000;

/** 评分区间（与后端 SCORE_MIN / SCORE_MAX 同值）。 */
const SCORE_MIN = 0;
const SCORE_MAX = 100;

/**
 * 星级本地近似：与后端默认阈值（60/70/80/90 → 1~5 星）对齐，只服务「边填边看」。
 *
 * 真口径在后端：EssayDetail.stars 按 app.yaml 实际配置算好下发，提交后一律按它重绘。
 * 老师改了阈值配置时这里不必同步，预览差一颗星可接受，落库差一颗星不可接受。
 */
const APPROX_STAR_THRESHOLDS: readonly number[] = [60, 70, 80, 90];

function approxStars(score: number | null | undefined): number {
  if (score === null || score === undefined) {
    return 0;
  }
  let stars = 1;
  for (const threshold of APPROX_STAR_THRESHOLDS) {
    if (score >= threshold) {
      stars += 1;
    }
  }
  return Math.min(stars, 5);
}

/**
 * 评分输入解析（三态）：空 → null（本次不改）；非法 → undefined（阻断提交）。
 */
function parseScoreInput(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < SCORE_MIN || value > SCORE_MAX) {
    return undefined;
  }
  return value;
}

/** 评分输入的显示值：null / undefined 都渲染成空串（留空 = 不评分）。 */
function scoreToInput(score: number | null | undefined): string {
  return score === null || score === undefined ? "" : String(score);
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
  // 「评」区：评语与评分各自记一份「已保存的初值」，只有改动过才进 payload（不传即不改）。
  const [comment, setComment] = useState("");
  const [initialComment, setInitialComment] = useState("");
  const [scoreText, setScoreText] = useState("");
  const [initialScoreText, setInitialScoreText] = useState("");
  const [commentNotice, setCommentNotice] = useState("");
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
  const [reviewFocused, setReviewFocused] = useState(false);

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
      setComment(detail.teacher_comment ?? "");
      setInitialComment(detail.teacher_comment ?? "");
      setScoreText(scoreToInput(detail.score));
      setInitialScoreText(scoreToInput(detail.score));
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

  // 标题是一等字段（FR-11）：改标题未保存同样算「脏」；v1.3 起评语与评分同一条口径。
  const dirty =
    value !== initialText ||
    title !== initialTitle ||
    comment !== initialComment ||
    scoreText !== initialScoreText;
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

  /** 提交后一律以后端返回值为真值重绘：星级与评分都不信本地推算。 */
  function applyServerDetail(result: EssayDetail): void {
    setEssay(result);
    setInitialComment(result.teacher_comment ?? "");
    setInitialScoreText(result.score === null || result.score === undefined ? "" : String(result.score));
  }

  /**
  * 组装 PATCH 载荷，严守后端的三态语义（不传 = 本次不改）。
  *
  * 铁律：后端是**先把状态翻成 proofread 再判评分门禁**，所以「本次请求会定稿」时可以
  * 一并带分；而本篇仍未定稿、又只是「仅保存评语」时**绝不**带 score —— 带了必 400，
  * 而且会把老师以为存下来的分数悄悄丢掉。selected 一律不在本页提交（精选是整期覆盖式
  * 操作，只在看板上做，见 EssayListPage）。
  */
  function buildPayload(finalize: boolean): Parameters<typeof api.updateEssay>[1] {
    const payload: Parameters<typeof api.updateEssay>[1] = {
      final_text: value,
      proofread: finalize,
      title,
    };
    if (comment !== initialComment) {
      payload.teacher_comment = comment;
    }
    const parsed = parseScoreInput(scoreText);
    if (typeof parsed === "number" && (finalize || essay?.status === "proofread")) {
      payload.score = parsed;
    }
    return payload;
  }

  /** finalize=true 走「保存并定稿」；false 走「仅保存评语」（只落评语，不推状态机）。 */
  async function submit(finalize: boolean): Promise<void> {
    if (!value.trim()) {
      setError("定稿文字不能为空");
      return;
    }
    const parsed = parseScoreInput(scoreText);
    // 只有 undefined 才是非法输入；null 表示「留空、本次不改分数」，必须放行。
    if (parsed === undefined) {
      setError("评分需是 0~100 的数字，留空表示不评分");
      return;
    }
    // FR-09：存疑清点只在**定稿**时拦一次；仅保存评语不改状态，不该弹核对框。
    if (finalize && !review.confirmUnviewed()) {
      return;
    }
    setSaving(true);
    setError("");
    setSaved(false);
    setCommentNotice("");
    try {
      const result = await api.updateEssay(numericEssayId, buildPayload(finalize));
      setInitialText(value);
      setInitialTitle(title);
      applyServerDetail(result);
      if (finalize) {
        savedNavRef.current = true;
        // 给一句可见的成功反馈，再返回看板。
        setSaved(true);
        window.setTimeout(() => navigate(`/issues/${result.issue_id}/essays`), 600);
      } else {
        // 只存评语不返回看板：老师往往还要接着改正文。
        setCommentNotice("评语已保存，本篇状态未改动。");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  async function handleSave(): Promise<void> {
    await submit(true);
  }

  async function handleSaveComment(): Promise<void> {
    await submit(false);
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

  // 星级预览：评分没动就用后端下发的 stars；动了才本地近似（提交成功后照样以后端返回值重绘）。
  const previewStars =
    scoreText === initialScoreText ? essay.stars : approxStars(parseScoreInput(scoreText));
  // 未定稿却填了分：不报错也不静默丢弃，而是明确告诉老师「这笔分数会随定稿一起提交」。
  const scoreLocked = essay.status !== "proofread" && typeof parseScoreInput(scoreText) === "number";

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

      {/* 桌面端把「原片」和「校对工作区」并排：右侧按定稿 → 识别对照排列，
          评语放在整页底部，避免把逐句校对动线拆成多个小区。 */}
      <div className="mt-3 grid flex-1 gap-4 lg:min-h-0 lg:grid-cols-[minmax(0,0.72fr)_minmax(0,1.28fr)] lg:grid-rows-[minmax(0,1fr)_auto] lg:overflow-hidden">
        <section className={`min-h-0 flex-col ${pane === "photo" ? "flex" : "hidden"} lg:col-start-1 lg:row-start-1 lg:flex`}>
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

        <section
          className={`min-h-0 flex-col gap-3 ${pane === "text" ? "flex" : "hidden"} lg:col-start-2 lg:row-span-2 lg:flex`}
        >
          <div className="min-h-0 flex-1">
            <DiffText
              photos={photos}
              value={value}
              onChange={setValue}
              onSelectPhoto={focusPhoto}
              viewedSuspects={review.viewedSuspects}
              onSuspectView={review.markViewed}
              disabled={saving}
              comparison="hidden"
            />
          </div>
          <DiffComparePanel
            photos={photos}
            value={value}
            onChange={setValue}
            onSelectPhoto={focusPhoto}
            viewedSuspects={review.viewedSuspects}
            onSuspectView={review.markViewed}
            disabled={saving}
          />
        </section>
      {/* v1.3「评」：评语 + 评分。精选勾选刻意不在此页（整期覆盖式，只在看板做）。
          识别失败稿没有可定稿的正文，整块不给入口。 */}
      {failed ? null : (
        <section
          data-testid="teacher-review"
          style={
            reviewFocused
              ? {
                  position: "fixed",
                  inset: "1rem",
                  zIndex: 50,
                  boxShadow: "0 20px 40px rgba(15, 23, 42, 0.24)",
                }
              : undefined
          }
          className={`mt-3 shrink-0 rounded-xl border border-slate-200 bg-white p-3 shadow-sm lg:col-start-1 lg:row-start-2 ${
            reviewFocused ? "overflow-auto" : ""
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="min-w-0 text-xs font-medium text-slate-500">
              老师评语 · {comment.length}/{COMMENT_MAX_LENGTH} 字（随成册与投屏一并展示）
            </span>
            <button
              type="button"
              data-testid="review-focus"
              aria-pressed={reviewFocused}
              onClick={() => setReviewFocused((focused) => !focused)}
              className="shrink-0 rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-50"
            >
              {reviewFocused ? "还原" : "放大"}
            </button>
          </div>
          <label className="mt-2 block text-sm text-slate-700">
              <textarea
                data-testid="teacher-comment"
                value={comment}
                rows={2}
                maxLength={COMMENT_MAX_LENGTH}
                disabled={saving}
                placeholder="不超过 2000 字；写这篇具体好在哪、下一步改哪里"
                onChange={(event) => setComment(event.target.value)}
                className="w-full resize-y rounded-md border border-slate-300 px-3 py-2 text-sm leading-6 outline-none focus:border-slate-500 disabled:bg-slate-50"
              />
          </label>

          <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
            <div className="w-36 shrink-0">
              <label className="block text-xs font-medium text-slate-500">
                评分 0~100
                <input
                  data-testid="score-input"
                  type="number"
                  min={SCORE_MIN}
                  max={SCORE_MAX}
                  step={1}
                  value={scoreText}
                  disabled={saving}
                  placeholder="留空不评"
                  onChange={(event) => setScoreText(event.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-slate-500 disabled:bg-slate-50"
                />
              </label>
              <span
                data-testid="star-preview"
                className="mt-1 flex items-center gap-1 text-xs text-slate-500"
              >
                <Stars stars={previewStars} />
                {previewStars > 0 ? "星级" : "未评分"}
              </span>
            </div>

            <div className="flex shrink-0 flex-col items-start gap-2">
              <button
                type="button"
                data-testid="save-comment"
                disabled={saving}
                onClick={() => void handleSaveComment()}
                className={GHOST_BUTTON}
              >
                {saving ? "保存中…" : "仅保存评语"}
              </button>
              {scoreLocked ? (
                <span
                  data-testid="score-locked"
                  className="max-w-[10rem] text-xs leading-4 text-amber-700"
                >
                  评分需先定稿
                </span>
              ) : null}
            </div>
          </div>

          {commentNotice ? (
            <p
              data-testid="comment-saved"
              className="mt-2 rounded-md bg-emerald-50 px-3 py-1.5 text-xs text-emerald-700"
            >
              {commentNotice}
            </p>
          ) : null}

          <p className="mt-2 text-xs leading-5 text-slate-400">
            评语留原样即不改动、清空后保存即删除；评分留空表示本次不改分数。
            {scoreLocked ? " 本篇尚未定稿，填好的分数会在点「保存并定稿」时一并提交。" : ""}
          </p>
        </section>
      )}
      </div>
    </main>
  );
}
