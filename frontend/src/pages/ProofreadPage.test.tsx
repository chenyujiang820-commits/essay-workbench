import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { DiffSegment, EssayDetail, Photo } from "../api/types";
import ProofreadPage from "./ProofreadPage";

// 隔离 react-zoom-pan-pinch（其依赖 jsdom 不支持的 ResizeObserver 测量）。
vi.mock("../components/PhotoViewer", () => ({
  default: () => <div data-testid="photo-viewer" />,
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getEssay: vi.fn(),
      updateEssay: vi.fn(),
      fetchPhotoBlob: vi.fn(),
      retryEssay: vi.fn(),
    },
  };
});

const detail: EssayDetail = {
  id: 3,
  issue_id: 1,
  student_id: 7,
  student_name: "张三",
  title: "",
  status: "review",
  low_confidence: 1,
  photo_count: 1,
  low_resolution_count: 0,
  created_at: "2026-09-07T00:00:00+00:00",
  proofread_at: null,
  final_text: "",
  teacher_comment: null,
  score: null,
  stars: 0,
  selected: 0,
  photos: [
    {
      id: 11,
      seq: 1,
      file_path: "photos/1.jpg",
      width: null,
      height: null,
      low_resolution: 0,
      engine1_text: "春天",
      engine2_text: "春天",
      diff_json: [{ type: "equal", text_a: "春天", text_b: "春天" }],
    },
    {
      id: 12,
      seq: 2,
      file_path: "photos/2.jpg",
      width: null,
      height: null,
      low_resolution: 0,
      engine1_text: "校园",
      engine2_text: "学校",
      diff_json: [{ type: "replace", text_a: "校园", text_b: "学校" }],
    },
  ],
  task: null,
};

function renderProofread() {
  // 数据路由（createMemoryRouter）：ProofreadPage 的 useBlocker 依赖 data router 上下文。
  const router = createMemoryRouter(
    [
      { path: "/essays/:essayId/proofread", element: <ProofreadPage /> },
      { path: "/issues/:issueId/essays", element: <div>看板页</div> },
    ],
    { initialEntries: ["/essays/3/proofread"] },
  );
  return render(<RouterProvider router={router} />);
}

/** 造一张原片夹具（默认高画质、无 diff）。 */
function photoWith(seq: number, diff: DiffSegment[] | null, engine1: string): Photo {
  return {
    id: 10 + seq,
    seq,
    file_path: `photos/${seq}.jpg`,
    width: null,
    height: null,
    low_resolution: 0,
    engine1_text: engine1,
    engine2_text: engine1,
    diff_json: diff,
  };
}

/** 在默认稿面上按需覆盖字段。 */
function essayWith(overrides: Partial<EssayDetail>): EssayDetail {
  return { ...detail, ...overrides };
}

/** 渲染指定稿面。 */
function renderEssay(overrides: Partial<EssayDetail>) {
  vi.mocked(api.getEssay).mockResolvedValue(essayWith(overrides));
  return renderProofread();
}

/** 当前页面上的存疑处 key（按渲染顺序）。 */
function suspectKeys(): string[] {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-suspect-key]")).map(
    (span) => span.dataset.suspectKey ?? "",
  );
}

/** 模拟老师逐一点看存疑处（点前 count 个）。 */
function viewSuspects(count: number): void {
  const spans = Array.from(document.querySelectorAll<HTMLElement>("[data-suspect-key]"));
  if (spans.length < count) {
    throw new Error(`夹具只有 ${spans.length} 处存疑，无法点看 ${count} 处`);
  }
  for (let index = 0; index < count; index += 1) {
    fireEvent.click(spans[index]);
  }
}

describe("ProofreadPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:mock"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
    vi.mocked(api.fetchPhotoBlob).mockResolvedValue(
      new Blob([new Uint8Array([1])], { type: "image/jpeg" }),
    );
    vi.mocked(api.getEssay).mockResolvedValue(detail);
    // PATCH 默认回一份「已定稿」详情，避免个别用例未显式打桩时拿到 undefined。
    vi.mocked(api.updateEssay).mockResolvedValue({ ...detail, status: "proofread" });
  });

  it("shows the low-confidence banner and seeds the final text from diff", async () => {
    renderProofread();
    await screen.findByTestId("final-text");

    expect(screen.getByText(/本篇识别置信度偏低/)).toBeTruthy();
    const textarea = screen.getByTestId("final-text") as HTMLTextAreaElement;
    expect(textarea.value).toBe("春天\n校园");
  });

  it("switches the highlighted photo when a suspect segment is clicked", async () => {
    renderProofread();
    await screen.findByText("校园");

    const secondTab = screen.getByRole("button", { name: "第 2 张" });
    expect(secondTab.className).not.toContain("bg-slate-900");

    fireEvent.click(screen.getByText("校园"));
    await waitFor(() => expect(secondTab.className).toContain("bg-slate-900"));
  });

  it("asks for confirmation before leaving with unsaved changes", async () => {
    renderProofread();
    const textarea = (await screen.findByTestId("final-text")) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "改过的定稿文字" } });
    await waitFor(() => expect(screen.getByText("未保存")).toBeTruthy());

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "← 看板" }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("看板页")).toBeNull();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "← 看板" }));
    await waitFor(() => expect(screen.getByText("看板页")).toBeTruthy());

    confirmSpy.mockRestore();
  });

  it("blocks in-app navigation (browser back / swipe-back) with unsaved changes", async () => {
    const router = createMemoryRouter(
      [
        { path: "/essays/:essayId/proofread", element: <ProofreadPage /> },
        { path: "/issues/:issueId/essays", element: <div>看板页</div> },
      ],
      { initialEntries: ["/essays/3/proofread"] },
    );
    render(<RouterProvider router={router} />);
    const textarea = (await screen.findByTestId("final-text")) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "改过的定稿文字" } });
    await waitFor(() => expect(screen.getByText("未保存")).toBeTruthy());

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    router.navigate("/issues/1/essays");
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("看板页")).toBeNull();

    confirmSpy.mockReturnValue(true);
    router.navigate("/issues/1/essays");
    await waitFor(() => expect(screen.getByText("看板页")).toBeTruthy());

    confirmSpy.mockRestore();
  });

  it("saves the proofread text and returns to the board", async () => {
    vi.mocked(api.updateEssay).mockResolvedValue({ ...detail, status: "proofread" });
    renderProofread();
    const textarea = (await screen.findByTestId("final-text")) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "最终定稿" } });

    // 先把唯一的存疑处点看掉，避免触发 FR-09 的存疑清点确认。
    viewSuspects(1);
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));

    await waitFor(() =>
      expect(api.updateEssay).toHaveBeenCalledWith(3, {
        final_text: "最终定稿",
        proofread: true,
        title: "",
      }),
    );
    // 保存成功先有可见反馈，再自动返回看板。
    expect(await screen.findByText("已定稿保存成功，正在返回看板…")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("看板页")).toBeTruthy(), { timeout: 4000 });
  });

  // ---------------------------------------------------------------- W1 GAP-01
  it("keeps the PRD caution banner always visible outside any collapsible block", async () => {
    renderProofread();
    await screen.findByTestId("final-text");

    const banner = screen.getByTestId("ai-warning");
    // 文案逐字对齐 PRD v1.2 FR-09（含全角引号与句末句号）。
    expect(banner.textContent).toBe("AI 可能会把同学的错字“改对”，请逐句以原片为准。");
    // 是正文文本而非 title 提示；不在 details 里；无关闭入口；不是屏幕阅读器专用。
    expect(banner.getAttribute("title")).toBeNull();
    expect(banner.closest("details")).toBeNull();
    expect(banner.querySelector("button")).toBeNull();
    expect(banner.className).not.toMatch(/\b(sr-only|hidden|invisible|lg:hidden|max-lg:hidden|sr-only)\b/);
    // 挂在 main 直接子层（移动端的 hidden 只作用于原片/文字分栏），375 视口同样可见。
    expect(banner.parentElement?.tagName).toBe("MAIN");
    // 字号不小于正文说明行。
    expect(banner.className).toContain("text-sm");
  });

  // ------------------------------------------------------------------- W2 FR-11
  it("sends the edited title together with the final text when saving", async () => {
    vi.mocked(api.updateEssay).mockResolvedValue({ ...detail, status: "proofread", title: "春天来了" });
    renderProofread();

    const titleInput = (await screen.findByTestId("essay-title")) as HTMLInputElement;
    expect(titleInput.value).toBe(""); // 初值取 essay.title
    expect(titleInput.maxLength).toBe(200);

    fireEvent.change(titleInput, { target: { value: "春天来了" } });
    viewSuspects(1); // 先清掉存疑清点提醒，聚焦标题断言
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));

    await waitFor(() =>
      expect(api.updateEssay).toHaveBeenCalledWith(3, {
        final_text: "春天\n校园",
        proofread: true,
        title: "春天来了",
      }),
    );
  });

  it("treats a title-only edit as unsaved and blocks leaving", async () => {
    renderProofread();
    const titleInput = (await screen.findByTestId("essay-title")) as HTMLInputElement;
    fireEvent.change(titleInput, { target: { value: "只改了标题" } });
    expect(await screen.findByText("未保存")).toBeTruthy();

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "← 看板" }));
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("看板页")).toBeNull();
    confirmSpy.mockRestore();
  });

  // ------------------------------------------------------------------- W3 GAP-05
  const failedTask = {
    id: 9,
    step: "engine1",
    retry_count: 1,
    error: "engine1 timeout after 3 retries: HTTP 504 from upstream gateway",
    updated_at: "2026-09-11T10:00:00+00:00",
  };

  it("offers 重新识别 instead of the finalize button for a failed essay", async () => {
    renderEssay({ status: "failed", photos: [], task: failedTask });

    const notice = await screen.findByTestId("failed-notice");
    expect(notice.textContent).toContain("engine1 timeout after 3 retries");
    expect(screen.getByTestId("failed-retry").textContent).toBe("重新识别");
    expect(screen.queryByRole("button", { name: "保存并定稿" })).toBeNull();
  });

  it("falls back to a generic hint when the failed task carries no error", async () => {
    renderEssay({ status: "failed", photos: [], task: null });

    expect(
      await screen.findByText("识别服务未返回具体原因，可直接重新排队识别。"),
    ).toBeTruthy();
  });

  it("re-queues a failed essay and reports 已重新排队识别", async () => {
    const queued = essayWith({ status: "recognizing", task: { ...failedTask, step: "queued" } });
    vi.mocked(api.retryEssay).mockResolvedValue({
      essay_id: 3,
      status: "recognizing",
      photo_count: 2,
      task: null,
    });
    vi.mocked(api.getEssay).mockResolvedValue(essayWith({ status: "failed", photos: [], task: failedTask }));
    renderProofread();
    await screen.findByTestId("failed-retry");

    // 重新拉取后后端已把状态推进到 recognizing。
    vi.mocked(api.getEssay).mockResolvedValue(queued);
    fireEvent.click(screen.getByTestId("failed-retry"));

    expect(await screen.findByText("已重新排队识别", {}, { timeout: 4000 })).toBeTruthy();
    expect(api.retryEssay).toHaveBeenCalledWith(3);
    expect(await screen.findByText(/逐句校对 · 张三/)).toBeTruthy();
  });

  // ------------------------------------------------------------------- W4 FR-09
  const twoSuspects = [
    photoWith(1, [{ type: "equal", text_a: "春天", text_b: "春天" }], "春天"),
    photoWith(2, [{ type: "replace", text_a: "校园", text_b: "学校" }], "校园"),
    photoWith(3, [{ type: "insert", text_a: "", text_b: "很美" }], ""),
  ];

  it("prompts once before finalizing while suspects remain unviewed", async () => {
    renderEssay({ photos: twoSuspects, low_confidence: 0 });
    await screen.findByTestId("final-text");
    expect(suspectKeys()).toEqual(["2:0", "3:0"]);

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));
    expect(confirmSpy).toHaveBeenCalledWith("本篇还有 2 处存疑未逐一点看，确认已核对？");
    expect(api.updateEssay).not.toHaveBeenCalled();
    confirmSpy.mockRestore();

    viewSuspects(1);
    const approveSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));
    await waitFor(() => expect(approveSpy).toHaveBeenCalledWith("本篇还有 1 处存疑未逐一点看，确认已核对？"));
    await waitFor(() => expect(api.updateEssay).toHaveBeenCalled());
    approveSpy.mockRestore();
  });

  it("does not prompt when every suspect has been viewed", async () => {
    renderEssay({ photos: twoSuspects, low_confidence: 0 });
    await screen.findByTestId("final-text");
    viewSuspects(2);

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));
    expect(confirmSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(api.updateEssay).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  it("does not prompt on save when the essay has no suspects", async () => {
    renderEssay({
      photos: [photoWith(1, [{ type: "equal", text_a: "春天", text_b: "春天" }], "春天")],
      low_confidence: 0,
    });
    await screen.findByTestId("final-text");
    expect(suspectKeys()).toEqual([]);

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));
    expect(confirmSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(api.updateEssay).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  // OPT-01：存疑清点必须按**句**计数，否则一段两句的稿子点一下就变成「已核对」。
  it("counts suspect sentences rather than suspect paragraphs in the audit", async () => {
    renderEssay({
      photos: [
        photoWith(
          1,
          [
            {
              type: "replace",
              text_a: "春天来了。校园也热闹了！",
              text_b: "春天到了。学校也热闹了！",
            },
          ],
          "春天来了。校园也热闹了！",
        ),
      ],
      low_confidence: 0,
    });
    await screen.findByTestId("final-text");
    expect(suspectKeys()).toEqual(["1:0", "1:0:1"]);

    viewSuspects(1);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));
    expect(confirmSpy).toHaveBeenCalledWith("本篇还有 1 处存疑未逐一点看，确认已核对？");
    expect(api.updateEssay).not.toHaveBeenCalled();
    confirmSpy.mockRestore();

    viewSuspects(2);
    const approveSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));
    await waitFor(() => expect(api.updateEssay).toHaveBeenCalled());
    expect(approveSpy).not.toHaveBeenCalled();
    approveSpy.mockRestore();
  });

  it("keeps the diff panel expanded on every entry without persisting anything", async () => {    const { unmount } = renderEssay({ photos: twoSuspects, low_confidence: 0 });
    await screen.findByTestId("final-text");
    expect((screen.getByTestId("diff-details") as HTMLDetailsElement).open).toBe(true);
    expect(screen.getByTestId("suspect-counter").textContent).toContain("存疑 2 处");
    unmount();

    renderEssay({ photos: twoSuspects, low_confidence: 0 });
    await screen.findByTestId("final-text");
    expect((screen.getByTestId("diff-details") as HTMLDetailsElement).open).toBe(true);
    // 折叠状态与已查看集合都不落 localStorage（每次进入复位）。
    expect(window.localStorage.length).toBe(0);
  });

  // ---------------------------------------------------------------------- F3
  // 高置信稿（真机最常见）：全篇没有 diff，面板改走「识别原文对照」，
  // 但绝不因此触发 FR-09 的定稿前确认框。
  const noDiffPhotos = [
    photoWith(1, null, "春天来了。"),
    photoWith(2, null, "三圈了，之后【?】胡老师他们跑完了"),
  ];

  it("无 diff 时面板改渲染识别原文对照，而不是空壳提示", async () => {
    renderEssay({ photos: noDiffPhotos, low_confidence: 0 });
    await screen.findByTestId("final-text");

    expect(screen.getByTestId("ocr-compare").textContent).toContain("三圈了，之后");
    expect(screen.queryByText(/暂无 diff 数据/)).toBeNull();
    expect(suspectKeys()).toEqual(["2:ocr:0"]);
    expect(screen.getByTestId("suspect-counter").textContent).toContain("存疑 1 处");
  });

  it("点识别原文标注可定位原片，且 fallback 存疑点不触发定稿确认框", async () => {
    renderEssay({ photos: noDiffPhotos, low_confidence: 0 });
    await screen.findByTestId("final-text");

    const secondTab = screen.getByRole("button", { name: "第 2 张" });
    expect(secondTab.className).not.toContain("bg-slate-900");
    const mark = document.querySelector<HTMLElement>('[data-suspect-key="2:ocr:0"]');
    expect(mark).toBeTruthy();
    fireEvent.click(mark as HTMLElement);
    await waitFor(() => expect(secondTab.className).toContain("bg-slate-900"));

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));
    expect(confirmSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(api.updateEssay).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  // ---------------------------------------------------------------------- GAP-12
  // 真机反馈「手机端点开文章，识别对照看不见」：根因不在数据，而在版式 ——
  // 默认停在「原片」页 + 整页不可滚。故页签默认值、计数、跨页跳转都要有测试锁住。
  it("默认停在「文字」页，页签带存疑数，老师第一眼就能看到识别对照", async () => {
    renderEssay({ photos: noDiffPhotos, low_confidence: 0 });
    await screen.findByTestId("final-text");

    expect(screen.getByTestId("pane-text").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("pane-photo").getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTestId("pane-text").textContent).toContain("存疑 1");
  });

  it("点存疑处会连页签一起切到原片：手机端原片在另一个页签，只改 seq 等于没反应", async () => {
    renderEssay({ photos: noDiffPhotos, low_confidence: 0 });
    await screen.findByTestId("final-text");

    const mark = document.querySelector<HTMLElement>('[data-suspect-key="2:ocr:0"]');
    expect(mark).toBeTruthy();
    fireEvent.click(mark as HTMLElement);
    await waitFor(() =>
      expect(screen.getByTestId("pane-photo").getAttribute("aria-pressed")).toBe("true"),
    );
    expect(screen.getByTestId("pane-text").getAttribute("aria-pressed")).toBe("false");
    // 定位到的那张原片同时高亮。
    expect(screen.getByRole("button", { name: "第 2 张" }).className).toContain("bg-slate-900");
  });

  it("全篇无存疑时页签只剩「文字」，面板给出一致结论而不是空壳", async () => {
    renderEssay({ photos: [photoWith(1, null, "春天来了。")], low_confidence: 0 });
    await screen.findByTestId("ocr-compare-clean");

    expect(screen.getByTestId("pane-text").textContent).toBe("文字");
    expect(screen.getByTestId("ocr-compare-clean").textContent).toContain("未发现存疑字");
  });
});
