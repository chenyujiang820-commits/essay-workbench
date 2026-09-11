import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "../api/client";
import type { Issue } from "../api/types";
import IssuePage from "./IssuePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listIssues: vi.fn(),
      createIssue: vi.fn(),
      deleteIssue: vi.fn(),
    },
  };
});

function issueOf(id: number, no: number, count: number): Issue {
  return {
    id,
    issue_no: no,
    week_start_date: "2026-09-07",
    created_at: "2026-09-07T00:00:00+00:00",
    essay_count: count,
  };
}

function renderIssuePage() {
  const router = createMemoryRouter(
    [
      { path: "/", element: <IssuePage /> },
      { path: "/issues/:issueId/essays", element: <div>看板页</div> },
      { path: "/issues/:issueId/upload", element: <div>上传页</div> },
    ],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("IssuePage 删除期数", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listIssues).mockResolvedValue([
      issueOf(1, 1, 2),
      issueOf(2, 2, 0),
    ]);
  });

  it("requires a second confirmation dialog before deleting", async () => {
    renderIssuePage();
    await screen.findByText("第 1 期");

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);

    // 有作文的期：确认文案必须明示将删除作文数
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0][0]).toContain("2 篇作文");

    // 取消 → 未发起删除请求，期数仍在
    expect(api.deleteIssue).not.toHaveBeenCalled();
    expect(screen.getByText("第 1 期")).toBeTruthy();

    // 再点删除并确认 → 调用 deleteIssue(1, true)
    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);
    await waitFor(() => expect(api.deleteIssue).toHaveBeenCalledWith(1, true));
    confirmSpy.mockRestore();
  });

  it("confirms without essay count wording for an empty issue and deletes it", async () => {
    renderIssuePage();
    await screen.findByText("第 2 期");

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    // 第 2 期（0 篇）是第二个删除按钮
    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[1]);
    await waitFor(() => expect(api.deleteIssue).toHaveBeenCalledWith(2, true));
    // 空期不提示作文数
    expect(confirmSpy.mock.calls[0][0]).not.toContain("篇作文");
    confirmSpy.mockRestore();
  });

  it("shows an error message when deletion fails", async () => {
    vi.mocked(api.deleteIssue).mockRejectedValue(
      new (await import("../api/client")).ApiError("删除失败，请重试", 500, 500),
    );
    renderIssuePage();
    await screen.findByText("第 1 期");

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);
    await waitFor(() => expect(screen.getByText("删除失败，请重试")).toBeTruthy());
    confirmSpy.mockRestore();
  });

});

describe("IssuePage 编辑期数（v1.2 OPT-02）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listIssues).mockResolvedValue([issueOf(1, 1, 2)]);
  });

  it("opens an inline form prefilled with the current issue_no and week", async () => {
    renderIssuePage();
    await screen.findByText("第 1 期");

    fireEvent.click(screen.getByTestId("edit-issue"));
    const no = (await screen.findByTestId("edit-issue-no")) as HTMLInputElement;
    const week = (await screen.findByTestId("edit-week")) as HTMLInputElement;
    expect(no.value).toBe("1");
    expect(week.value).toBe("2026-09-07");
  });

  it("sends PATCH with both fields and refreshes the list on success", async () => {
    const update = vi
      .spyOn(api, "updateIssue")
      .mockResolvedValue(issueOf(1, 9, 2));
    renderIssuePage();
    await screen.findByText("第 1 期");

    fireEvent.click(screen.getByTestId("edit-issue"));
    fireEvent.change(await screen.findByTestId("edit-issue-no"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { issue_no: 9, week_start_date: "2026-09-07" }));
    await waitFor(() => expect(api.listIssues).toHaveBeenCalledTimes(2));
    update.mockRestore();
  });

  it("surfaces the backend message when the issue number collides", async () => {
    const update = vi
      .spyOn(api, "updateIssue")
      .mockRejectedValue(new ApiError("期号 1 已存在", 409, 409));
    renderIssuePage();
    await screen.findByText("第 1 期");

    fireEvent.click(screen.getByTestId("edit-issue"));
    await screen.findByTestId("edit-issue-no");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(screen.getByText("期号 1 已存在")).toBeTruthy());
    update.mockRestore();
  });

  it("rejects an illegal issue number locally without calling the API", async () => {
    const update = vi.spyOn(api, "updateIssue");
    renderIssuePage();
    await screen.findByText("第 1 期");

    fireEvent.click(screen.getByTestId("edit-issue"));
    fireEvent.change(await screen.findByTestId("edit-issue-no"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(screen.getByText(/合法的期号/)).toBeTruthy());
    expect(update).not.toHaveBeenCalled();
    update.mockRestore();
  });
});
