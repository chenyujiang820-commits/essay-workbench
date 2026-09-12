import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import type { EssaySummary, Issue, SelectionResult, UploadResult } from "../api/types";
import EssayListPage from "./EssayListPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getIssue: vi.fn(),
      listIssueEssays: vi.fn(),
      retryEssay: vi.fn(),
      setSelection: vi.fn(),
    },
  };
});

const issue: Issue = {
  id: 1,
  issue_no: 3,
  week_start_date: "2026-09-07",
  created_at: "2026-09-07T00:00:00+00:00",
  essay_count: 1,
};

const essays: EssaySummary[] = [
  {
    id: 1,
    issue_id: 1,
    student_id: 7,
    student_name: "张三",
    title: "春天",
    status: "proofread",
    low_confidence: 0,
    photo_count: 1,
    low_resolution_count: 0,
    created_at: "2026-09-07T00:00:00+00:00",
    proofread_at: null,
    teacher_comment: null,
    score: null,
    stars: 0,
    selected: 0,
  },
];

const failedEssay: EssaySummary = {
  ...essays[0],
  id: 2,
  student_id: 8,
  student_name: "李四",
  status: "failed",
};

const rerunAccepted: UploadResult = {
  essay_id: 2,
  status: "recognizing",
  photo_count: 1,
  task: null,
};

function renderBoard() {
  const router = createMemoryRouter(
    [
      { path: "/", element: <div>期数列表页</div> },
      { path: "/issues/:issueId/essays", element: <EssayListPage /> },
      { path: "/essays/:essayId/proofread", element: <div>校对页</div> },
    ],
    { initialEntries: ["/issues/1/essays"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("EssayListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getIssue).mockResolvedValue(issue);
    vi.mocked(api.listIssueEssays).mockResolvedValue(essays);
  });

  it("provides a direct link back to the issue list (home)", async () => {
    renderBoard();
    await screen.findByText("状态看板");

    fireEvent.click(screen.getByTestId("back-home"));
    await waitFor(() => expect(screen.getByText("期数列表页")).toBeTruthy());
  });

  it("shows polling hint and manual refresh button", async () => {
    renderBoard();
    await screen.findByText("张三");

    expect(screen.getByText(/每 3 秒自动刷新/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "立即刷新" })).toBeTruthy();
  });

  it("renders many students as a compact grid, not a tall single column", async () => {
    // 模拟 45 名学生全部已定稿
    const many: EssaySummary[] = Array.from({ length: 45 }, (_, i) => ({
      id: i + 1,
      issue_id: 1,
      student_id: 100 + i,
      student_name: `学生${String(i + 1).padStart(2, "0")}`,
      title: "",
      status: "proofread" as const,
      low_confidence: 0,
      photo_count: 1,
      low_resolution_count: 0,
      created_at: "2026-09-07T00:00:00+00:00",
      proofread_at: null,
      teacher_comment: null,
      score: null,
      stars: 0,
      selected: 0,
    }));
    vi.mocked(api.listIssueEssays).mockResolvedValue(many);

    const { container } = renderBoard();
    await screen.findByText("学生45");

    // 「已定稿」分组内的列表必须是多列网格（sm:grid-cols-3），不是竖排 flex-col
    const grids = Array.from(container.querySelectorAll("ul.grid"));
    const target = grids.find((ul) => ul.textContent?.includes("学生45"));
    expect(target).toBeTruthy();
    expect(target!.className).toContain("sm:grid-cols-3");
  });

  it("marks essays whose photos are below the resolution floor", async () => {
    vi.mocked(api.listIssueEssays).mockResolvedValue([
      { ...essays[0], photo_count: 3, low_resolution_count: 2 },
    ]);

    renderBoard();
    await screen.findByText("状态看板");

    expect(screen.getByTestId("essay-low-res").textContent).toContain("画质偏低 2 张");
    expect(screen.getByTestId("board-low-res-summary").textContent).toContain("1 篇原片画质偏低");
  });

  it("keeps the low-res hints hidden when every photo is sharp enough", async () => {
    renderBoard();
    await screen.findByText("状态看板");

    expect(screen.queryByTestId("essay-low-res")).toBeNull();
    expect(screen.queryByTestId("board-low-res-summary")).toBeNull();
  });

  // AC-6：失败稿的重跑入口必须在看板上可见，不必先点进校对页。
  it("offers a retry entry for failed essays on the board", async () => {
    vi.mocked(api.listIssueEssays).mockResolvedValue([failedEssay]);
    vi.mocked(api.retryEssay).mockResolvedValue(rerunAccepted);

    renderBoard();
    await screen.findByText("识别失败");
    expect(screen.getByTestId("board-retry-2").textContent).toContain("重新识别");

    fireEvent.click(screen.getByTestId("board-retry-2"));
    await waitFor(() => expect(api.retryEssay).toHaveBeenCalledWith(2));

    expect((await screen.findByTestId("board-retry-notice")).textContent).toContain(
      "李四 已重新排队识别",
    );
    // 受理成功后离开「失败」分组，入口随之消失，后续进度交给 3s 轮询。
    expect(screen.queryByTestId("board-retry-2")).toBeNull();
    expect(screen.getByText("李四")).toBeTruthy();
  });

  it("keeps the retry entry away from essays that did not fail", async () => {
    renderBoard();
    await screen.findByText("张三");

    expect(screen.queryByTestId("board-retry-1")).toBeNull();
  });

  it("keeps the retry entry and shows the message when the rerun is rejected", async () => {
    vi.mocked(api.listIssueEssays).mockResolvedValue([failedEssay]);
    vi.mocked(api.retryEssay).mockRejectedValue(new ApiError("该篇没有原片，无法识别", 400, 400));

    renderBoard();
    fireEvent.click(await screen.findByTestId("board-retry-2"));

    expect(await screen.findByText("该篇没有原片，无法识别")).toBeTruthy();
    expect(screen.queryByTestId("board-retry-2")).toBeTruthy();
    expect(screen.queryByTestId("board-retry-notice")).toBeNull();
  });
});


// ---------------------------------------------------------------------------
// FR-05 本期精选：勾选只改内存，「保存精选」覆盖式提交整期集合
// ---------------------------------------------------------------------------

const selIssue: Issue = {
  id: 1,
  issue_no: 4,
  week_start_date: "2026-09-07",
  created_at: "2026-09-07T00:00:00+00:00",
  essay_count: 10,
};

function mk(overrides: Partial<EssaySummary> = {}): EssaySummary {
  return {
    id: 11,
    issue_id: 1,
    student_id: 201,
    student_name: "张三",
    title: "春天",
    status: "proofread",
    low_confidence: 0,
    photo_count: 1,
    low_resolution_count: 0,
    created_at: "2026-09-07T00:00:00+00:00",
    proofread_at: null,
    teacher_comment: null,
    score: 90,
    stars: 0,
    selected: 0,
    ...overrides,
  };
}

function mkList(count: number, overrides: Partial<EssaySummary> = {}): EssaySummary[] {
  return Array.from({ length: count }, (_, index) =>
    mk({
      id: 11 + index,
      student_id: 201 + index,
      student_name: "学生" + String(index + 1),
      score: 90 - index,
      ...overrides,
    }),
  );
}

function mkResult(ids: number[], overrides: Partial<SelectionResult> = {}): SelectionResult {
  return {
    issue_id: 1,
    selected_ids: ids,
    limit: 10,
    suggested: 5,
    scored_count: ids.length,
    unscored_count: 0,
    ...overrides,
  };
}

function renderSelectionBoard(list: EssaySummary[]) {
  const router = createMemoryRouter(
    [
      { path: "/", element: <div>期数列表页</div> },
      { path: "/issues/:issueId/essays", element: <EssayListPage /> },
      { path: "/essays/:essayId/proofread", element: <div>校对页</div> },
      { path: "/issues/:issueId/ranking", element: <div>表彰榜页</div> },
      { path: "/issues/:issueId/shares", element: <div>分享管理页</div> },
    ],
    { initialEntries: ["/issues/1/essays"] },
  );
  return render(<RouterProvider router={router} />);
}

function checkBox(id: number): HTMLInputElement {
  return screen.getByTestId("select-essay-" + String(id)) as HTMLInputElement;
}

describe("EssayListPage 本期精选（FR-05）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getIssue).mockResolvedValue(selIssue);
    vi.mocked(api.listIssueEssays).mockResolvedValue(mkList(10));
  });

  it("renders the selection panel with the per-issue limit", async () => {
    renderSelectionBoard(mkList(10));

    expect((await screen.findByTestId("selection-panel")).textContent).toContain("本期精选");
    expect(screen.getByTestId("selection-limit").textContent).toContain("已勾 0 / 10 篇");
    expect(screen.getByTestId("selection-note").textContent).toContain("保存精选");
  });

  it("starts from the server-echoed selection and keeps submit disabled while untouched", async () => {
    renderSelectionBoard(mkList(10, { selected: 1 }));
    await screen.findByTestId("selection-panel");

    expect(checkBox(11).checked).toBe(true);
    expect(screen.getByTestId("selection-saved-11").textContent).toContain("已存");
    expect(screen.getByTestId("selection-limit").textContent).toContain("已勾 10 / 10 篇");
    expect(screen.getByTestId("submit-selection").disabled).toBe(true);
  });

  it("submits the whole set and repaints from the server echo, not from the click", async () => {
    vi.mocked(api.setSelection).mockResolvedValue(mkResult([12]));
    renderSelectionBoard(mkList(10));
    await screen.findByTestId("selection-panel");

    fireEvent.click(checkBox(11));
    fireEvent.click(checkBox(12));
    expect(screen.getByTestId("submit-selection").disabled).toBe(false);
    fireEvent.click(screen.getByTestId("submit-selection"));

    await waitFor(() => expect(api.setSelection).toHaveBeenCalledWith(1, [11, 12]));
    expect((await screen.findByTestId("selection-notice")).textContent).toContain("共 1 篇");
    expect(checkBox(12).checked).toBe(true);
    expect(checkBox(11).checked).toBe(false);
    expect(screen.getByTestId("selection-saved-12")).toBeTruthy();
    expect(screen.queryByTestId("selection-saved-11")).toBeNull();
  });

  it("surfaces the backend reason on failure, keeps the notice away and reloads", async () => {
    vi.mocked(api.setSelection).mockRejectedValue(new ApiError("精选里含未定稿作文", 400, 400));
    renderSelectionBoard(mkList(10));
    await screen.findByTestId("selection-panel");
    const loadsBefore = vi.mocked(api.listIssueEssays).mock.calls.length;

    fireEvent.click(checkBox(11));
    fireEvent.click(screen.getByTestId("submit-selection"));

    expect((await screen.findByTestId("selection-error")).textContent).toContain("精选里含未定稿作文");
    expect(screen.queryByTestId("selection-notice")).toBeNull();
    await waitFor(() =>
      expect(vi.mocked(api.listIssueEssays).mock.calls.length).toBe(loadsBefore + 1),
    );
    // 失败后勾选必须看起来没变：重拉按后端回读值重置
    expect(checkBox(11).checked).toBe(false);
  });

  it("blocks an over-cap set locally without calling the API", async () => {
    renderSelectionBoard(mkList(11));
    await screen.findByTestId("selection-panel");

    for (let id = 11; id <= 21; id += 1) {
      fireEvent.click(checkBox(id));
    }
    expect(screen.getByTestId("selection-limit").textContent).toContain("已勾 11 / 10 篇");

    fireEvent.click(screen.getByTestId("submit-selection"));
    expect((await screen.findByTestId("selection-error")).textContent).toContain("最多 10 篇");
    expect(api.setSelection).not.toHaveBeenCalled();
  });

  it("suggests the top five scored essays without saving them", async () => {
    const list = mkList(8);
    list[2] = { ...list[2], score: null }; // 13 号未评分：不进建议
    renderSelectionBoard(list);
    await screen.findByTestId("selection-panel");

    fireEvent.click(screen.getByTestId("selection-suggest"));
    await waitFor(() => expect(checkBox(11).checked).toBe(true));

    expect(checkBox(16).checked).toBe(true);
    expect(checkBox(13).checked).toBe(false);
    expect(checkBox(17).checked).toBe(false);
    expect(screen.getByTestId("selection-limit").textContent).toContain("已勾 5 / 10 篇");
    expect(api.setSelection).not.toHaveBeenCalled();
  });

  it("keeps the checkbox away from essays that are not finalized", async () => {
    renderSelectionBoard([mk({ id: 11, status: "review" }), mk({ id: 12 })]);
    await screen.findByTestId("selection-panel");

    expect(checkBox(11).disabled).toBe(true);
    expect(checkBox(12).disabled).toBe(false);
  });

  it("reports the scoring gap with the same rule as the three boards", async () => {
    renderSelectionBoard([
      mk({ id: 11, score: 92 }),
      mk({ id: 12, score: null }),
      mk({ id: 13, score: null }),
      mk({ id: 14, status: "review", score: null }),
    ]);

    const hint = await screen.findByTestId("board-score-progress");
    expect(hint.textContent).toContain("本期已定稿 3 篇");
    expect(hint.textContent).toContain("其中 2 篇还没有评分");
  });

  it("keeps the scoring hint away once every finalized essay is scored", async () => {
    renderSelectionBoard(mkList(3));
    await screen.findByTestId("selection-panel");

    expect(screen.queryByTestId("board-score-progress")).toBeNull();
  });

  it("links the board to the awards page and the share manager", async () => {
    renderSelectionBoard(mkList(2));
    await screen.findByTestId("board-ranking");

    fireEvent.click(screen.getByTestId("board-ranking"));
    expect(await screen.findByText("表彰榜页")).toBeTruthy();
  });

  it("opens the share manager from the board", async () => {
    renderSelectionBoard(mkList(2));
    await screen.findByTestId("board-shares");

    fireEvent.click(screen.getByTestId("board-shares"));
    expect(await screen.findByText("分享管理页")).toBeTruthy();
  });

  it("shows stars only when the backend sent a star count", async () => {
    renderSelectionBoard([mk({ id: 11, student_name: "张三", stars: 4 }), mk({ id: 12, student_name: "李四", stars: 0 })]);
    await screen.findByText("张三");

    expect(screen.getAllByTestId("stars")).toHaveLength(1);
  });
});
