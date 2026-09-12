import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "../api/client";
import type { Issue, ShareLink, Student } from "../api/types";
import SharesPage, { shareStatusOf, writeClipboard } from "./SharesPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getIssue: vi.fn(),
      listStudents: vi.fn(),
      listShares: vi.fn(),
      createShare: vi.fn(),
      revokeShare: vi.fn(),
    },
  };
});

const issue: Issue = {
  id: 5,
  issue_no: 9,
  week_start_date: "2026-09-07",
  created_at: "2026-09-07T00:00:00+00:00",
  essay_count: 3,
};

const students: Student[] = [{ id: 7, student_no: "S007", name: "张三", active: 1 }];

function link(overrides: Partial<ShareLink> = {}): ShareLink {
  return {
    token: "tk-active",
    url: "/share/tk-active",
    issue_id: 5,
    issue_no: 9,
    student_id: null,
    student_name: null,
    scope: "issue",
    created_at: "2026-09-12T08:00:00+00:00",
    expires_at: "2099-01-01T00:00:00+00:00",
    revoked: 0,
    label: "",
    essay_count: 3,
    ...overrides,
  };
}

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/issues/5/shares"]}>
      <Routes>
        <Route path="/issues/:issueId/shares" element={<SharesPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("shareStatusOf", () => {
  it("把 revoked 排在过期之前", () => {
    expect(shareStatusOf(link())).toBe("active");
    expect(shareStatusOf(link({ revoked: 1 }))).toBe("revoked");
    expect(
      shareStatusOf(
        link({ expires_at: "2020-01-01T00:00:00+00:00", revoked: 1 }),
        Date.parse("2026-01-01T00:00:00+00:00"),
      ),
    ).toBe("revoked");
  });

  it("时间戳解析不了时按已过期处理（宁可拒绝也不放宽）", () => {
    expect(shareStatusOf(link({ expires_at: "2020-01-01T00:00:00+00:00" }))).toBe("expired");
    expect(shareStatusOf(link({ expires_at: "不是时间" }))).toBe("expired");
  });
});

describe("writeClipboard", () => {
  it("两条路都不通时返回 false，由界面给出手动复制提示", async () => {
    // jsdom 没有 navigator.clipboard，document.execCommand 也未实现 → 这里必须走到 false 分支
    expect(await writeClipboard("http://x/share/tk")).toBe(false);
  });
});

describe("SharesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getIssue).mockResolvedValue(issue);
    vi.mocked(api.listStudents).mockResolvedValue(students);
    vi.mocked(api.listShares).mockResolvedValue([link(), link({ token: "tk-revoked", url: "/share/tk-revoked", revoked: 1 })]);
  });

  it("列表同时给出有效与已撤销两条，并显示绝对地址", async () => {
    renderPage();

    const rows = await screen.findAllByTestId("share-row");
    expect(rows).toHaveLength(2);
    const url = screen.getAllByTestId("share-url")[0] as HTMLInputElement;
    expect(url.value).toBe(window.location.origin + "/share/tk-active");
    expect(url.value.startsWith("http")).toBe(true);
    expect(screen.getAllByTestId("share-status").map((node) => node.textContent)).toEqual([
      expect.stringContaining("有效"),
      expect.stringContaining("已撤销"),
    ]);
  });

  it("复制的是当前域名拼出来的绝对地址", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderPage();

    const buttons = await screen.findAllByTestId("copy-share");
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toBe(window.location.origin + "/share/tk-active");
    expect(screen.queryByTestId("clipboard-hint")).toBeNull();
  });

  it("非安全上下文一键复制失败时给出手动复制提示", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    renderPage();

    fireEvent.click((await screen.findAllByTestId("copy-share"))[0]);
    expect((await screen.findByTestId("clipboard-hint")).textContent).toContain("长按");
  });

  it("整期建链：天数留默认、student_id 传 null，建成后重拉列表", async () => {
    vi.mocked(api.createShare).mockResolvedValue(link({ token: "tk-new", url: "/share/tk-new" }));
    renderPage();

    fireEvent.change(screen.getByTestId("share-label"), { target: { value: "  三班家长群  " } });
    fireEvent.submit(screen.getByTestId("share-form"));
    await waitFor(() => expect(api.createShare).toHaveBeenCalled());
    expect(api.createShare).toHaveBeenCalledWith(5, { days: 14, student_id: null, label: "三班家长群" });
    // 建完重拉一次：加载 1 次 + 建链后 1 次（顺序与总数都以接口回读为准，不做本地乐观插入）
    await waitFor(() => expect(api.listShares).toHaveBeenCalledTimes(2));
  });

  it("限定单个学生建链时 student_id 是数字", async () => {
    vi.mocked(api.createShare).mockResolvedValue(link({ scope: "student", student_id: 7, student_name: "张三" }));
    renderPage();

    // 学生下拉的选项要等 listStudents 回来才有值，否则 select 会把 "7" 归零
    await screen.findByRole("option", { name: /张三/ });
    fireEvent.change(screen.getByTestId("share-scope"), { target: { value: "7" } });
    fireEvent.submit(screen.getByTestId("share-form"));
    await waitFor(() => expect(api.createShare).toHaveBeenCalled());
    expect(vi.mocked(api.createShare).mock.calls[0][1].student_id).toBe(7);
  });

  it("天数越界在前端就挡住，不发请求", async () => {
    renderPage();

    fireEvent.change(screen.getByTestId("share-days"), { target: { value: "999" } });
    fireEvent.submit(screen.getByTestId("share-form"));

    expect((await screen.findByTestId("share-notice")).textContent).toContain("必须是");
    expect(api.createShare).not.toHaveBeenCalled();
  });

  it("撤销走后端置标记后重拉列表，行不消失", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.mocked(api.revokeShare).mockResolvedValue({ revoked: 1 });
    // 首次列表给"有效"，撤销后回读给"已撤销"：行不删，只换状态（PRD v1.3 FR-07）
    vi.mocked(api.listShares).mockReset();
    vi.mocked(api.listShares).mockResolvedValueOnce([link()]);
    vi.mocked(api.listShares).mockResolvedValue([link({ revoked: 1 })]);
    renderPage();

    await screen.findByTestId("copy-share");
    fireEvent.click((await screen.findAllByTestId("revoke-share"))[0]);
    await waitFor(() => expect(api.revokeShare).toHaveBeenCalledWith("tk-active"));
    await waitFor(() => expect(screen.getAllByTestId("share-row")).toHaveLength(1));
    expect(screen.getByTestId("share-status").textContent).toContain("已撤销");
    vi.unstubAllGlobals();
  });

  it("后端拒绝建链时原样显示文案", async () => {
    vi.mocked(api.createShare).mockRejectedValue(new ApiError("本期还没有已定稿作文", 400, 400));
    renderPage();

    fireEvent.submit(screen.getByTestId("share-form"));
    expect((await screen.findByTestId("share-notice")).textContent).toContain("本期还没有已定稿作文");
  });
});
