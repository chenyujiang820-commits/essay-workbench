import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { EssaySummary, Issue, TemplateInfo } from "../api/types";
import BookPage from "./BookPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getIssue: vi.fn(),
      listIssueEssays: vi.fn(),
      listTemplates: vi.fn(),
      fetchExportPreview: vi.fn(),
      exportBook: vi.fn(),
    },
  };
});

const issue: Issue = {
  id: 1,
  issue_no: 3,
  week_start_date: "2026-09-07",
  created_at: "2026-09-07T00:00:00+00:00",
  essay_count: 2,
};

const templates: TemplateInfo[] = [
  { key: "elegant", name: "清雅", description: "素净排版" },
  { key: "playful", name: "灵动", description: "活泼配色" },
  { key: "formal", name: "端庄", description: "公文风格" },
];

function essay(id: number, status: string, name: string, title: string): EssaySummary {
  return {
    id,
    issue_id: 1,
    student_id: id,
    student_name: name,
    title,
    status,
    low_confidence: 0,
    created_at: "2026-09-07T00:00:00+00:00",
    proofread_at: null,
  };
}

function renderBook() {
  return render(
    <MemoryRouter initialEntries={["/issues/1/book"]}>
      <Routes>
        <Route path="/issues/:issueId/book" element={<BookPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BookPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getIssue).mockResolvedValue(issue);
    vi.mocked(api.listTemplates).mockResolvedValue(templates);
    vi.mocked(api.fetchExportPreview).mockResolvedValue("<html><body>预览</body></html>");
  });

  it("disables export and shows the pending hint when not all proofread", async () => {
    vi.mocked(api.listIssueEssays).mockResolvedValue([
      essay(1, "proofread", "张三", "春天"),
      essay(2, "review", "李四", "秋天"),
    ]);
    renderBook();

    const button = (await screen.findByTestId("export-book")) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(true));
    expect(screen.getByTestId("export-hint").textContent).toContain("还有 1 篇未校对定稿");
  });

  it("enables export once every essay is proofread", async () => {
    vi.mocked(api.listIssueEssays).mockResolvedValue([
      essay(1, "proofread", "张三", "春天"),
      essay(2, "proofread", "李四", "秋天"),
    ]);
    renderBook();

    const button = (await screen.findByTestId("export-book")) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    expect(screen.queryByTestId("export-hint")).toBeNull();
  });

  it("greys out the score order option (二期)", async () => {
    vi.mocked(api.listIssueEssays).mockResolvedValue([essay(1, "proofread", "张三", "春天")]);
    renderBook();

    const select = (await screen.findByTestId("order-select")) as HTMLSelectElement;
    const scoreOption = Array.from(select.options).find((option) => option.value === "score");
    expect(scoreOption).toBeTruthy();
    expect(scoreOption?.disabled).toBe(true);
  });

  it("refreshes the preview when the template changes", async () => {
    vi.mocked(api.listIssueEssays).mockResolvedValue([essay(1, "proofread", "张三", "春天")]);
    renderBook();

    await screen.findByTestId("export-book");
    await waitFor(() =>
      expect(api.fetchExportPreview).toHaveBeenCalledWith(1, {
        template: "elegant",
        order: "student_no",
      }),
    );

    vi.mocked(api.fetchExportPreview).mockClear();
    fireEvent.click(screen.getByRole("radio", { name: "灵动" }));

    await waitFor(() =>
      expect(api.fetchExportPreview).toHaveBeenCalledWith(1, {
        template: "playful",
        order: "student_no",
      }),
    );
  });
});
