import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { EssaySummary, Issue } from "../api/types";
import EssayListPage from "./EssayListPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getIssue: vi.fn(),
      listIssueEssays: vi.fn(),
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
    created_at: "2026-09-07T00:00:00+00:00",
    proofread_at: null,
  },
];

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
});
