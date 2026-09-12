import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "../api/client";
import type { BookItem, ShareView } from "../api/types";
import SharePage, { GONE_FALLBACK } from "./SharePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: { ...actual.api, fetchShareView: vi.fn(), fetchSharePreview: vi.fn() },
  };
});

function item(name: string, title: string, patch: Partial<BookItem> = {}): BookItem {
  return {
    student_no: "",
    name,
    title,
    paragraphs: ["第一段正文。", "第二段正文。"],
    is_selected: false,
    stars: 0,
    score: null,
    comment: "",
    ...patch,
  };
}

function view(overrides: Partial<ShareView> = {}): ShareView {
  return {
    class_name: "高一(1)班",
    issue_no: 5,
    week_start_date: "2026-09-07",
    generated_at: "2026-09-12 09:00",
    expires_at: "2026-09-26T08:00:00+00:00",
    scope: "issue",
    student_name: null,
    items: [item("张三", "玉兰", { stars: 4, score: 88, comment: "观察细致。" })],
    ...overrides,
  };
}

function renderPage(token: string = "tk-1"): void {
  render(
    <MemoryRouter initialEntries={["/share/" + token]}>
      <Routes>
        <Route path="/share/:token" element={<SharePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("SharePage（家长只读页）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders essays read-only with stars but without scores", async () => {
    vi.mocked(api.fetchShareView).mockResolvedValue(view());
    renderPage();

    expect((await screen.findByTestId("share-title")).textContent).toContain("第 5 期作文集");
    expect((await screen.findByTestId("share-item")).textContent).toContain("玉兰");
    expect((await screen.findByTestId("share-item")).textContent).toContain("第一段正文。");
    expect((await screen.findByTestId("share-item")).textContent).toContain("观察细致。");
    expect(screen.getAllByTestId("stars")[0].getAttribute("data-stars")).toBe("4");
    expect((await screen.findByTestId("share-footer")).textContent).toContain("2026-09-26");
  });

  it("never leaks scores or student numbers to the parent page", async () => {
    vi.mocked(api.fetchShareView).mockResolvedValue(
      view({
        items: [item("张三", "玉兰", { stars: 4, score: 88, student_no: "S001", comment: "评语内容" })],
      }),
    );
    renderPage();
    await screen.findByTestId("share-item");

    const text = document.body.textContent ?? "";
    expect(text).not.toContain("88");
    expect(text).not.toContain(" 分");
    expect(text).not.toContain("S001");
  });

  it("shows the student name only for a per-student link", async () => {
    vi.mocked(api.fetchShareView).mockResolvedValue(
      view({ scope: "student", student_name: "张三" }),
    );
    renderPage();

    expect((await screen.findByTestId("share-title")).textContent).toBe("张三 的作文");
  });

  it("renders a dead link as a bare notice with no essay data at all", async () => {
    vi.mocked(api.fetchShareView).mockRejectedValue(
      new ApiError("链接已失效或不存在，请向老师重新获取", 410, 410),
    );
    renderPage("gone");

    expect((await screen.findByTestId("share-gone")).textContent).toContain("链接已失效或不存在");
    const text = document.body.textContent ?? "";
    for (const secret of ["张三", "玉兰", "第一段正文。", "观察细致。", "高一(1)班"]) {
      expect(text).not.toContain(secret);
    }
  });

  it("falls back to a local notice when the server message is empty", async () => {
    vi.mocked(api.fetchShareView).mockRejectedValue(new ApiError("", 410, 410));
    renderPage();

    expect((await screen.findByTestId("share-gone")).textContent).toBe(GONE_FALLBACK);
  });

  it("keeps a network failure retryable instead of pretending the link expired", async () => {
    vi.mocked(api.fetchShareView).mockRejectedValue(new ApiError("网络异常", 500, 500));
    renderPage();

    expect((await screen.findByTestId("share-error")).textContent).toContain("网络异常");
    expect(screen.queryByTestId("share-gone")).toBeNull();
  });

  it("does not call the API when the url carries no token", async () => {
    // 地址栏只剩 /share/（家长复制时被截断）：没有令牌就不该去猜数据。
    render(
      <MemoryRouter initialEntries={["/share"]}>
        <Routes>
          <Route path="/share/:token?" element={<SharePage />} />
          <Route path="*" element={<div data-testid="fell-through" />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("share-gone")).toBeTruthy();
    expect(api.fetchShareView).not.toHaveBeenCalled();
  });

  it("does not swallow urls with extra segments", async () => {
    // /share/abc/def 不是合法令牌地址（多半是复制链接时把后面的路径也带上了）：
    // 让它落到 catch-all 去重定向，而不是把 abc 当令牌猜一份数据给家长看。
    render(
      <MemoryRouter initialEntries={["/share/abc/def"]}>
        <Routes>
          <Route path="/share/:token?" element={<SharePage />} />
          <Route path="*" element={<div data-testid="fell-through" />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("fell-through")).toBeTruthy();
    expect(api.fetchShareView).not.toHaveBeenCalled();
  });

  it("loads the printable version from the share preview endpoint", async () => {
    vi.mocked(api.fetchShareView).mockResolvedValue(view());
    vi.mocked(api.fetchSharePreview).mockResolvedValue("<html><body>printable</body></html>");
    renderPage();

    const button = await screen.findByTestId("share-print");
    fireEvent.click(button);
    await waitFor(() => expect(api.fetchSharePreview).toHaveBeenCalledWith("tk-1"));
    await waitFor(() => expect(screen.getByTestId("preview-frame")).toBeTruthy());
  });
});
