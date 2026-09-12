/**
 * RankingPage（三榜表彰页，v1.3 / FR-04）回归测试。
 *
 * 守的产品约束：
 * 1. 星级一律显示后端下发的 stars，前端不按分数换算 —— 与 PDF、家长页、投屏同源。
 * 2. 星级榜不显示名次与分数：弱化竞争是产品约束（PRD v1.3 §9），不是样式选择。
 * 3. 「被配置关闭」与「本期没人上榜」必须在界面上可区分，老师不能靠猜。
 * 4. 海报导出失败只在原地提示，不能把已经加载出来的榜单清空。
 * 5. 榜单只读：本页不提供任何改分/改精选的入口。
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "../api/client";
import type { ProgressRow, RankingData, StarRow, WorkRow } from "../api/types";
import { triggerDownload } from "../lib/download";
import RankingPage, {
  BOARD_TITLES,
  EMPTY_TEXT,
  TOP_HIGHLIGHT,
  VISIBILITY_TEXT,
  deltaText,
  formatScore,
  highlightClass,
  progressRows,
  starRows,
  thresholdHint,
  workRows,
} from "./RankingPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      fetchRanking: vi.fn(),
      exportPoster: vi.fn(),
    },
  };
});

/** jsdom 没有 URL.createObjectURL，真实下载会炸；这里整层换掉，只断言调用参数。 */
vi.mock("../lib/download", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/download")>();
  return { ...actual, triggerDownload: vi.fn() };
});

function workRow(overrides: Partial<WorkRow> = {}): WorkRow {
  return {
    rank: 1,
    essay_id: 101,
    student_id: 7,
    student_no: "S007",
    name: "张三",
    title: "玉兰",
    score: 92,
    stars: 4,
    selected: true,
    ...overrides,
  };
}

function progressRow(overrides: Partial<ProgressRow> = {}): ProgressRow {
  return {
    ...workRow({ essay_id: 102, student_id: 8, student_no: "S008", name: "李四", title: "巷口", score: 85, stars: 3, selected: false }),
    rank: 1,
    previous_score: 78,
    previous_issue_no: 8,
    delta: 7,
    ...overrides,
  };
}

function starRow(overrides: Partial<StarRow> = {}): StarRow {
  return {
    student_id: 7,
    student_no: "S007",
    name: "张三",
    title: "玉兰",
    stars: 4,
    ...overrides,
  };
}

function rankingData(overrides: Partial<RankingData> = {}): RankingData {
  return {
    issue_id: 5,
    issue_no: 9,
    class_name: "高一(1)班",
    generated_at: "2026-09-12T09:00:00+08:00",
    thresholds: [60, 70, 80, 90],
    max_stars: 5,
    config: {
      work: { enabled: true, visibility: "public" },
      progress: { enabled: true, visibility: "teacher" },
      star: { enabled: true, visibility: "public" },
    },
    work: [workRow(), workRow({ essay_id: 102, rank: 2, name: "李四", title: "巷口", score: 88.5, stars: 3, selected: false })],
    progress: [progressRow()],
    star: [starRow(), starRow({ student_id: 8, student_no: "S008", name: "李四", title: "巷口", stars: 5 })],
    disabled: [],
    scored_count: 2,
    unscored_count: 0,
    ...overrides,
  };
}

function renderPage(issueId: string = "5"): void {
  render(
    <MemoryRouter initialEntries={["/issues/" + issueId + "/ranking"]}>
      <Routes>
        <Route path="/issues/:issueId/ranking" element={<RankingPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RankingPage（三榜表彰）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchRanking).mockResolvedValue(rankingData());
  });

  it("加载成功：标题带期号，评分说明只统计已评分篇数", async () => {
    renderPage();

    expect((await screen.findByTestId("ranking-title")).textContent).toContain("第 9 期");
    expect(screen.getByTestId("score-note").textContent).toBe("本期已评分 2 篇");
    expect(screen.getByTestId("ranking-thresholds").textContent).toContain("高一(1)班");
  });

  it("还有未评分作文时，说明里点名「未评分不进榜单」", async () => {
    vi.mocked(api.fetchRanking).mockResolvedValue(rankingData({ unscored_count: 2 }));
    renderPage();

    const note = (await screen.findByTestId("score-note")).textContent ?? "";
    expect(note).toContain("本期已评分 2 篇");
    expect(note).toContain("还有 2 篇未评分（未评分不进任何榜单）");
  });

  it("三榜同时渲染，佳作榜行内有分数与星级", async () => {
    renderPage();
    await screen.findByTestId("ranking-title");

    expect(screen.getByTestId("board-work")).toBeTruthy();
    expect(screen.getByTestId("board-progress")).toBeTruthy();
    expect(screen.getByTestId("board-star")).toBeTruthy();
    expect(within(screen.getByTestId("board-work")).getByText(BOARD_TITLES.work)).toBeTruthy();

    const row = within(screen.getByTestId("board-work")).getByTestId("ranking-row-work-101");
    expect(row.textContent).toContain("张三《玉兰》");
    expect(row.textContent).toContain("92 分");
    expect(within(row).getByTestId("stars").getAttribute("data-stars")).toBe("4");
    expect(within(row).getByTestId("ranking-badge").textContent).toBe("精选");
    expect(screen.getByTestId("board-work-visibility").textContent).toBe(VISIBILITY_TEXT.public);
    expect(screen.getByTestId("board-progress-visibility").textContent).toBe(VISIBILITY_TEXT.teacher);
  });

  it("星级榜不显示名次也不显示分数（PRD v1.3 §9）", async () => {
    renderPage();
    const board = await screen.findByTestId("board-star");

    const rows = within(board).getAllByTestId(/^ranking-row-star-/);
    expect(rows).toHaveLength(2);
    // 后端不下发 rank/score，前端也不许反推：整块里不该出现"分"字，也不该有名次列
    expect(board.textContent).not.toContain("分");
    for (const row of rows) {
      expect(row.querySelector("span.w-5")).toBeNull();
      expect(row.firstElementChild?.tagName).toBe("A");
      expect(within(row).getByTestId("stars")).toBeTruthy();
    }
    expect(within(board).queryAllByTestId("ranking-badge")).toHaveLength(0);
  });

  it("被配置关闭的榜显示「已按配置关闭」，而不是「没人上榜」", async () => {
    vi.mocked(api.fetchRanking).mockResolvedValue(rankingData({ disabled: ["progress"] }));
    renderPage();

    const hint = await screen.findByTestId("board-progress-disabled");
    expect(hint.textContent).toContain("已按配置关闭");
    expect(screen.queryByTestId("board-progress-empty")).toBeNull();
    // 关掉的榜连行都不排，避免老师以为榜上的孩子还在
    expect(screen.queryAllByTestId(/^ranking-row-progress-/)).toHaveLength(0);
    // 其他两榜照常
    expect(within(screen.getByTestId("board-work")).getByTestId("ranking-row-work-101")).toBeTruthy();
    expect(screen.queryByTestId("board-work-disabled")).toBeNull();
  });

  it("空榜与关闭是两回事：没有数据时给空榜文案", async () => {
    vi.mocked(api.fetchRanking).mockResolvedValue(rankingData({ work: [] }));
    renderPage();

    expect((await screen.findByTestId("board-work-empty")).textContent).toBe(EMPTY_TEXT.work);
    expect(screen.queryByTestId("board-work-disabled")).toBeNull();
  });

  it("榜单读取失败：原样回显后端文案并给出返回入口", async () => {
    vi.mocked(api.fetchRanking).mockRejectedValue(new ApiError("第 9 期不存在。", 404, 404));
    renderPage();

    expect((await screen.findByTestId("ranking-error")).textContent).toBe("第 9 期不存在。");
    expect(screen.getByTestId("ranking-back")).toBeTruthy();
    expect(screen.queryByTestId("board-work")).toBeNull();
  });

  it("海报导出成功：按期号请求并触发下载", async () => {
    const blob = new Blob(["x"]);
    vi.mocked(api.exportPoster).mockResolvedValue(blob);
    renderPage();
    await screen.findByTestId("ranking-title");

    fireEvent.click(screen.getByTestId("ranking-poster"));

    await waitFor(() => expect(api.exportPoster).toHaveBeenCalledWith(5));
    await waitFor(() => expect(triggerDownload).toHaveBeenCalledTimes(1));
    expect(vi.mocked(triggerDownload).mock.calls[0][0]).toBe(blob);
    expect(vi.mocked(triggerDownload).mock.calls[0][1]).toContain("本周精选");
    expect(screen.queryByTestId("poster-error")).toBeNull();
  });

  it("海报导出失败：原地报错但不清空已加载的榜单", async () => {
    vi.mocked(api.exportPoster).mockRejectedValue(
      new ApiError("本期还没有精选作文，先在看板上勾选佳作。", 409, 409),
    );
    renderPage();
    await screen.findByTestId("board-work");

    fireEvent.click(screen.getByTestId("ranking-poster"));

    expect((await screen.findByTestId("poster-error")).textContent).toBe(
      "本期还没有精选作文，先在看板上勾选佳作。",
    );
    expect(screen.getByTestId("board-work")).toBeTruthy();
    expect(screen.getByTestId("ranking-row-work-101")).toBeTruthy();
    expect(screen.queryByTestId("ranking-error")).toBeNull();
  });

  it("地址里的期号不可用时不发请求，直接给人话错误", async () => {
    renderPage("abc");

    expect((await screen.findByTestId("ranking-error")).textContent).toContain("期号不可用");
    expect(api.fetchRanking).not.toHaveBeenCalled();
    expect(screen.getByTestId("ranking-back")).toBeTruthy();
  });
});

describe("榜单纯函数", () => {
  it("分数显示：整数不带小数点，小数留一位，null 用破折号", () => {
    expect(formatScore(92)).toBe("92");
    expect(formatScore(92.5)).toBe("92.5");
    expect(formatScore(null)).toBe("—");
    expect(formatScore(undefined)).toBe("—");
  });

  it("涨跌写法带上期期号，0 显示持平", () => {
    expect(deltaText(5, 3)).toContain("+5");
    expect(deltaText(5, 3)).toContain("第 3 期");
    expect(deltaText(-5, 3)).toContain("−");
    expect(deltaText(-5, 3)).not.toContain("+");
    expect(deltaText(0, 3)).toContain("持平");
  });

  it("阈值提示逐级铺开，未配置时说明本期不评星", () => {
    const hint = thresholdHint([95, 90, 85, 80], 5);
    expect(hint).toContain("95 分及以上 1 星");
    expect(hint).toContain("至 5 星");
    expect(thresholdHint([], 5)).toContain("本期不评星");
  });

  it("前三名高亮，星级榜的 null 名次不高亮", () => {
    expect(highlightClass(3)).not.toBe("");
    expect(highlightClass(TOP_HIGHLIGHT)).toBe(highlightClass(1));
    expect(highlightClass(4)).toBe("");
    expect(highlightClass(0)).toBe("");
    expect(highlightClass(null)).toBe("");
  });

  it("行模型：佳作榜给分数，星级榜名次与附加信息恒为 null", () => {
    const data = rankingData();
    expect(workRows(data).map((row) => row.extra)).toEqual(["92 分", "88.5 分"]);
    expect(workRows(data)[1].href).toBe("/essays/102/proofread");
    expect(starRows(data).map((row) => row.rank)).toEqual([null, null]);
    expect(starRows(data).map((row) => row.extra)).toEqual([null, null]);
    expect(starRows(data)[0].href).toBe("/students/7/portfolio");
    expect(starRows(data).map((row) => row.stars)).toEqual([4, 5]);
  });

  it("进步榜的附加信息是「较第 N 期」涨跌，而不是本期分数", () => {
    const row = progressRows(rankingData())[0];
    expect(row.extra).toBe(deltaText(7, 8));
    expect(row.extra).toContain("较第 8 期 +7");
    expect(row.rank).toBe(1);
  });
});
