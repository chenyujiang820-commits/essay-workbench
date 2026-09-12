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
