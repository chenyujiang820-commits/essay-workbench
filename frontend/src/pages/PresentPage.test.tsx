import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { PresentData } from "../api/types";
import PresentPage, {
  computeScreenScales,
  computeScreens,
  type ScreenBox,
} from "./PresentPage";

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

describe("computeScreens", () => {
  // 构造 n 段、每段高 100、间隔 20 的测量盒。
  function boxes(n: number): ScreenBox[] {
    return Array.from({ length: n }, (_, index) => ({
      top: index * 120,
      bottom: index * 120 + 100,
    }));
  }

  it("splits paragraphs so each screen fits the container height", () => {
    const paragraphs = Array.from({ length: 8 }, (_, index) => `第${index + 1}段`);
    // 容器高 460：首段 top=0，第 4 段 bottom=460 恰好放得下，第 5 段放不下。
    const screens = computeScreens(paragraphs, boxes(8), 460);
    expect(screens).toHaveLength(2);
    expect(screens[0]).toEqual(["第1段", "第2段", "第3段", "第4段"]);
    expect(screens[1]).toEqual(["第5段", "第6段", "第7段", "第8段"]);
  });

  it("keeps an oversized single paragraph on its own screen instead of dropping it", () => {
    const screens = computeScreens(["很长很长的一段"], [{ top: 0, bottom: 9999 }], 100);
    expect(screens).toHaveLength(1);
    expect(screens[0]).toEqual(["很长很长的一段"]);
  });

  it("keeps a placeholder screen for empty essays", () => {
    const screens = computeScreens([], [], 500);
    expect(screens).toHaveLength(1);
    expect(screens[0]).toEqual(["（正文待补）"]);
  });

  it("never merges paragraphs that would overflow the container", () => {
    const paragraphs = ["一", "二", "三"];
    // 每段高度递增错开，验证贪心边界。
    const boxes: ScreenBox[] = [
      { top: 0, bottom: 200 },
      { top: 200, bottom: 500 },
      { top: 500, bottom: 620 },
    ];
    const screens = computeScreens(paragraphs, boxes, 500);
    expect(screens).toEqual([["一", "二"], ["三"]]);
  });

  it("reserves header space for the title screen and the continuation label", () => {
    const paragraphs = ["一", "二", "三", "四"];
    // 每段 100 高、间隔 20；容器 620。
    const boxes: ScreenBox[] = boxes_(4);
    // 不预留：620 能塞 5 段 → 全在一屏。
    expect(computeScreens(paragraphs, boxes, 620)).toHaveLength(1);
    // 首屏预留 300（姓名/标题抬头）+ 续屏预留 60：首屏只放 2 段（第 3 段 340 > 预算 320），续屏 2 段。
    const screens = computeScreens(paragraphs, boxes, 620, 300, 60);
    expect(screens).toEqual([["一", "二"], ["三", "四"]]);
  });
});

describe("computeScreenScales", () => {
  it("returns scale 1 when content fits the available height", () => {
    const screens = [["一", "二"], ["三"]];
    const boxes: ScreenBox[] = [
      { top: 0, bottom: 100 },
      { top: 120, bottom: 220 },
      { top: 240, bottom: 340 },
    ];
    expect(computeScreenScales(screens, boxes, 800, 200, 50)).toEqual([1, 1]);
  });

  it("shrinks oversized screens proportionally with a 0.4 floor", () => {
    // 单屏内容高 1000，可用 500 → 0.5。
    const screens = [["长段落"]];
    const boxes: ScreenBox[] = [{ top: 0, bottom: 1000 }];
    expect(computeScreenScales(screens, boxes, 500)).toEqual([0.5]);

    // 内容高 4000，可用 500 → 原比 0.125，被下限抬到 0.4。
    const huge: ScreenBox[] = [{ top: 0, bottom: 4000 }];
    expect(computeScreenScales([["超长段落"]], huge, 500)).toEqual([0.4]);
  });

  it("applies the continuation reserve to non-first screens", () => {
    const screens = [["一"], ["二"]];
    const boxes: ScreenBox[] = [
      { top: 0, bottom: 500 },
      { top: 500, bottom: 1100 },
    ];
    // 首屏可用 600（放得下 500 → 1）；续屏可用 1000-800=200，内容 600 → 0.333 低于下限，取 0.4。
    const scales = computeScreenScales(screens, boxes, 1000, 400, 800);
    expect(scales[0]).toBe(1);
    expect(scales[1]).toBe(0.4);
  });
});

// 与外层 boxes() 等价的构造器（describe 内不可提升使用，故在此重复一份小工具）。
function boxes_(n: number): ScreenBox[] {
  return Array.from({ length: n }, (_, index) => ({
    top: index * 120,
    bottom: index * 120 + 100,
  }));
}

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
