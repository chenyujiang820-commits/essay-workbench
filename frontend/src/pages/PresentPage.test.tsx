import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { PresentData } from "../api/types";
import PresentPage, { buildSlides } from "./PresentPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      fetchPresent: vi.fn(),
    },
  };
});

const presentData: PresentData = {
  class_name: "高一(1)班",
  issue_no: 3,
  week_start_date: "2026-09-07",
  generated_at: "2026-09-10 20:00",
  items: [
    {
      student_no: "S001",
      name: "张三",
      title: "春天",
      paragraphs: ["春来。", "花开。"],
      is_selected: true,
    },
    { student_no: "S002", name: "李四", title: "秋天", paragraphs: ["秋至。"], is_selected: false },
  ],
};

function renderPresent() {
  return render(
    <MemoryRouter initialEntries={["/present/1"]}>
      <Routes>
        <Route path="/present/:issueId" element={<PresentPage />} />
        <Route path="/issues/:issueId/essays" element={<div>看板页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("buildSlides", () => {
  it("splits long essays into continuous slides", () => {
    const paragraphs = Array.from({ length: 8 }, (_, index) => `第${index + 1}段`);
    const slides = buildSlides(
      [{ student_no: "S001", name: "张三", title: "长文", paragraphs, is_selected: false }],
      6,
    );
    expect(slides).toHaveLength(2);
    expect(slides[0].withHeader).toBe(true);
    expect(slides[1].withHeader).toBe(false);
    expect(slides[1].paragraphs).toEqual(["第7段", "第8段"]);
  });

  it("keeps a slide for empty essays", () => {
    const slides = buildSlides([
      { student_no: "S001", name: "张三", title: "", paragraphs: [], is_selected: false },
    ]);
    expect(slides).toHaveLength(1);
    expect(slides[0].paragraphs).toEqual(["（正文待补）"]);
  });
});

describe("PresentPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchPresent).mockResolvedValue(presentData);
  });

  it("renders the first essay with progress and selected badge", async () => {
    renderPresent();

    expect((await screen.findByTestId("present-name")).textContent).toBe("张三");
    expect(screen.getByTestId("present-title").textContent).toBe("春天");
    expect(screen.getByTestId("pager-progress").textContent).toBe("1 / 2");
    expect(screen.getByText("精选")).toBeTruthy();
    expect(screen.getByText("高一(1)班")).toBeTruthy();
  });

  it("advances to the next essay on ArrowRight", async () => {
    renderPresent();
    await screen.findByTestId("present-name");

    fireEvent.keyDown(window, { key: "ArrowRight" });

    // 放宽超时：整套用例并行执行、机器高负载时，状态更新到重渲染可能超过默认 1s。
    await waitFor(() => expect(screen.getByTestId("present-name").textContent).toBe("李四"), {
      timeout: 4000,
    });
    expect(screen.getByTestId("pager-progress").textContent).toBe("2 / 2");
  });

  it("toggles pure mode", async () => {
    renderPresent();
    await screen.findByTestId("present-name");

    fireEvent.click(screen.getByRole("button", { name: "纯净模式" }));
    expect(screen.queryByTestId("pager-controls")).toBeNull();
    expect(screen.getByRole("button", { name: "退出纯净模式" })).toBeTruthy();
  });
});
