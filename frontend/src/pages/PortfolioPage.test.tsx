import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "../api/client";
import type { PortfolioData, PortfolioEntry, TemplateInfo } from "../api/types";
import { formatAvgScore, portfolioFileName } from "./PortfolioPage";
import PortfolioPage from "./PortfolioPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      fetchPortfolio: vi.fn(),
      listTemplates: vi.fn(),
      exportPortfolio: vi.fn(),
    },
  };
});

const templates: TemplateInfo[] = [
  { key: "elegant", name: "清雅", description: "素净排版" },
];

function entry(id: number, issueNo: number, title: string, patch: Partial<PortfolioEntry> = {}): PortfolioEntry {
  return {
    essay_id: id,
    issue_id: issueNo,
    issue_no: issueNo,
    week_start_date: "2026-09-07",
    title,
    score: null,
    stars: 0,
    selected: 0,
    teacher_comment: null,
    proofread_at: null,
    photo_count: 1,
    ...patch,
  };
}

function portfolioData(entries: PortfolioEntry[], avgScore: number | null): PortfolioData {
  return {
    student: { id: 7, student_no: "S007", name: "张三", active: 1 },
    stats: {
      essay_count: entries.length,
      selected_count: entries.filter((item) => item.selected).length,
      honoured_count: 2,
      top_stars: 4,
      avg_score: avgScore,
      issue_count: 3,
      last_issue_no: entries.length ? entries[0].issue_no : null,
    },
    entries,
    class_name: "高一(1)班",
    generated_at: "2026-09-12 10:00",
  };
}

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/students/7/portfolio"]}>
      <Routes>
        <Route path="/students/:studentId/portfolio" element={<PortfolioPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("PortfolioPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listTemplates).mockResolvedValue(templates);
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:portfolio"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
  });

  it("renders stats, entries newest-first and honours the backend order", async () => {
    vi.mocked(api.fetchPortfolio).mockResolvedValue(
      portfolioData([
        entry(11, 5, "玉兰", { score: 92, stars: 5, selected: 1, teacher_comment: "观察细致。" }),
        entry(10, 3, "一次错误", { score: 71, stars: 4 }),
      ], 81.5),
    );
    renderPage();

    expect((await screen.findByTestId("stat-essay-count")).textContent).toContain("2");
    expect(screen.getByTestId("stat-selected-count").textContent).toContain("1");
    expect(screen.getByTestId("stat-honoured-count").textContent).toContain("2");
    expect(screen.getByTestId("avg-score").textContent).toContain("81.5");
    const rows = screen.getAllByTestId(/^portfolio-entry-/);
    expect(rows[0].textContent).toContain("第 5 期");
    expect(rows[0].textContent).toContain("玉兰");
    expect(rows[0].textContent).toContain("本期精选");
    expect(rows[1].textContent).toContain("第 3 期");
    expect(rows[1].textContent).not.toContain("本期精选");
  });

  it("shows a dash instead of 0 when the student has no scores", async () => {
    vi.mocked(api.fetchPortfolio).mockResolvedValue(portfolioData([entry(12, 6, "无分之作")], null));
    renderPage();

    await screen.findByTestId("portfolio-entry-12");
    expect(screen.getByTestId("avg-score").textContent).toBe("—");
    expect(screen.getByTestId("avg-score").textContent).not.toContain("0");
    expect(formatAvgScore(null)).toBe("—");
  });

  it("keeps comments collapsed until the teacher opens them", async () => {
    vi.mocked(api.fetchPortfolio).mockResolvedValue(
      portfolioData([entry(13, 6, "旧稿", { teacher_comment: "开头太急。" })], 70),
    );
    renderPage();

    const toggle = await screen.findByTestId("comment-toggle-13");
    expect(screen.queryByTestId("comment-13")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByTestId("comment-13").textContent).toContain("开头太急。");
    fireEvent.click(screen.getByTestId("comment-toggle-13"));
    expect(screen.queryByTestId("comment-13")).toBeNull();
  });

  it("provides a direct return to the essay proofread page", async () => {
    vi.mocked(api.fetchPortfolio).mockResolvedValue(portfolioData([entry(15, 6, "回到校对")], 80));
    renderPage();

    expect(await screen.findByTestId("portfolio-proofread-15")).toBeTruthy();
  });

  it("exports with the picked template and order", async () => {
    vi.mocked(api.fetchPortfolio).mockResolvedValue(portfolioData([entry(14, 6, "可导出")], 88),
    );
    vi.mocked(api.exportPortfolio).mockResolvedValue(new Blob(["pdf"], { type: "application/pdf" }));
    renderPage();

    const button = (await screen.findByTestId("export-portfolio")) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(api.exportPortfolio).toHaveBeenCalledWith(7, "elegant", "issue_no"));
  });

  it("surfaces the backend message when there is nothing to export", async () => {
    vi.mocked(api.fetchPortfolio).mockResolvedValue(portfolioData([], null));
    vi.mocked(api.exportPortfolio).mockRejectedValue(new ApiError("该学生还没有已定稿作文", 400, 400));
    renderPage();

    const button = (await screen.findByTestId("export-portfolio")) as HTMLButtonElement;
    expect(screen.getByTestId("portfolio-empty").textContent).toContain("还没有任何已定稿作文");
    expect(button.disabled).toBe(true);
    expect(portfolioFileName("张三")).toBe("张三的作文成长档案.pdf");
  });

  it("keeps a broken portfolio link visible with a retry instead of an empty page", async () => {
    vi.mocked(api.fetchPortfolio).mockRejectedValue(new ApiError("学生不存在", 404, 404));
    renderPage();

    expect((await screen.findByTestId("portfolio-error")).textContent).toContain("学生不存在");
    expect(screen.queryByTestId("stat-essay-count")).toBeNull();
  });
});
