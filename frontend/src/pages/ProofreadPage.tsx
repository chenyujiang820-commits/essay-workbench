/**
 * 校对环核心：桌面左右分栏（左原片 / 右 diff + 可编辑定稿），移动端上下堆叠可切换。
 *
 * * 顶部：整篇低置信横幅（low_confidence=1）+ 恒定操作提示条；
 * * 点击右侧存疑段落 → 高亮并切换到对应序号的左侧原片；
 * * 「保存并定稿」PATCH（final_text 非空 + proofread=true）后返回看板；
 * * 有未保存修改时离开页面需二次确认。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBlocker, useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { EssayDetail, Photo } from "../api/types";
import DiffText, { composeInitialText } from "../components/DiffText";
import PhotoViewer from "../components/PhotoViewer";

const EMPTY_PHOTOS: Photo[] = [];

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

export default function ProofreadPage() {
  const { essayId } = useParams();
  const navigate = useNavigate();
  const numericEssayId = Number(essayId);

  const [essay, setEssay] = useState<EssayDetail | null>(null);
  const [value, setValue] = useState("");
  const [initialText, setInitialText] = useState("");
  const [activeSeq, setActiveSeq] = useState(1);
  const [pane, setPane] = useState<"photo" | "text">("photo");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

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
      setActiveSeq(detail.photos[0]?.seq ?? 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "作文加载失败");
    } finally {
      setLoading(false);
    }
  }, [numericEssayId]);

  useEffect(() => {
    void load();
  }, [load]);

  const photos = essay?.photos ?? EMPTY_PHOTOS;
  const photoUrls = usePhotoUrls(photos);
  const activePhoto = useMemo(
    () => photos.find((photo) => photo.seq === activeSeq) ?? photos[0] ?? null,
    [photos, activeSeq],
  );

  const dirty = value !== initialText;

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

  async function handleSave(): Promise<void> {
    if (!value.trim()) {
      setError("定稿文字不能为空");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const saved = await api.updateEssay(numericEssayId, {
        final_text: value,
        proofread: true,
      });
      setEssay(saved);
      setInitialText(value);
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
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            重试
          </button>
          <button
            type="button"
            onClick={() => navigate("/")}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            返回首页
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex h-dvh max-w-7xl flex-col px-3 py-4 sm:px-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
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
            onClick={handleBack}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            ← 看板
          </button>
        </div>
          <h1 className="text-lg font-semibold text-slate-900">
            逐句校对 · {essay.student_name ?? `#${essay.student_id}`}
          </h1>
          {dirty ? <span className="text-xs text-amber-600">未保存</span> : null}
        </div>
        <button
          type="button"
          disabled={saving}
          onClick={handleSave}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {saving ? "保存中…" : "保存并定稿"}
        </button>
      </header>

      {essay.low_confidence === 1 ? (
        <p className="mt-3 rounded-md bg-amber-100 px-4 py-2 text-sm text-amber-800">
          本篇识别置信度偏低，请重点核对全部存疑处后再定稿。
        </p>
      ) : null}

      <p className="mt-3 rounded-md bg-slate-100 px-4 py-2 text-xs text-slate-500">
        左侧为原片（可缩放），右侧上方编辑定稿文字，下方「识别对照」黄色为引擎存疑处；点击存疑处可定位对应原片。核对后点击「保存并定稿」。
      </p>

      {error ? (
        <p className="mt-3 rounded-md bg-rose-50 px-4 py-2 text-sm text-rose-600">{error}</p>
      ) : null}

      {saved ? (
        <p className="mt-3 rounded-md bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
          已定稿保存成功，正在返回看板…
        </p>
      ) : null}

      <div className="mt-3 flex gap-2 lg:hidden">
        <button
          type="button"
          onClick={() => setPane("photo")}
          className={`flex-1 rounded-md px-3 py-2 text-sm ${
            pane === "photo" ? "bg-slate-900 text-white" : "border border-slate-300 text-slate-700"
          }`}
        >
          原片
        </button>
        <button
          type="button"
          onClick={() => setPane("text")}
          className={`flex-1 rounded-md px-3 py-2 text-sm ${
            pane === "text" ? "bg-slate-900 text-white" : "border border-slate-300 text-slate-700"
          }`}
        >
          文字
        </button>
      </div>

      <div className="mt-3 grid min-h-0 flex-1 gap-4 lg:grid-cols-2">
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
          <div className="min-h-[240px] flex-1">
            <PhotoViewer
              url={activePhoto ? photoUrls[activePhoto.id] ?? null : null}
              seq={activePhoto?.seq ?? 1}
            />
          </div>
        </section>

        <section className={`min-h-0 ${pane === "text" ? "block" : "hidden"} lg:block`}>
          <DiffText
            photos={photos}
            value={value}
            onChange={setValue}
            onSelectPhoto={setActiveSeq}
            disabled={saving}
          />
        </section>
      </div>
    </main>
  );
}
