import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { PresentData } from "../api/types";
import PresentPage, {
  computeColumnCount,
  computeScreenScales,
  computeScreens,
  paginateColumns,
  shrinkToFit,
  splitOversizedColumn,
  TWO_COLUMN_MIN_WIDTH,
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

  it("falls back to 未命名 when the essay has no title", async () => {
    vi.mocked(api.fetchPresent).mockResolvedValue({
      ...presentData,
      items: [{ ...presentData.items[0], title: "" }, presentData.items[1]],
    });
    renderPresent();

    // 与同文件其它用例一致：并行高负载时状态更新到重渲染可能超过默认 1s。
    const title = await screen.findByTestId("present-title", undefined, { timeout: 4000 });
    expect(title.textContent).toBe("未命名");
    // 旧的「无题」兜底不得再出现（FR-11 明确要求区分）。
    expect(screen.queryByText("无题")).toBeNull();
  });
});

describe("computeColumnCount / shrinkToFit", () => {
  it("窄视口退回单栏，避免 11 字/行的窄栏", () => {
    expect(computeColumnCount(1920)).toBe(2);
    expect(computeColumnCount(TWO_COLUMN_MIN_WIDTH)).toBe(2);
    expect(computeColumnCount(TWO_COLUMN_MIN_WIDTH - 1)).toBe(1);
    expect(computeColumnCount(1024)).toBe(1);
    expect(computeColumnCount(390)).toBe(1);
  });

  it("内容超高时按比例缩小，并有 0.4 下限", () => {
    expect(shrinkToFit(500, 400)).toBe(1);
    expect(shrinkToFit(500, 1000)).toBe(0.5);
    expect(shrinkToFit(500, 4000)).toBe(0.4);
    expect(shrinkToFit(500, 0)).toBe(1);
  });
});

describe("splitOversizedColumn", () => {
  it("多段栏与不超高的单段栏原样返回", () => {
    const two = { paragraphs: ["甲", "乙"], height: 900 };
    expect(splitOversizedColumn(two, 400)).toEqual([two]);
    const fits = { paragraphs: ["甲乙丙"], height: 380 };
    expect(splitOversizedColumn(fits, 400)).toEqual([fits]);
  });

  it("超高的单段按字数切成多栏，切完不丢字", () => {
    // 100 字、内容高 800、栏高 400 → 切成 2 栏，每栏 50 字、高 400
    const paragraph = "一二三四五六七八九十".repeat(10);
    expect(paragraph).toHaveLength(100);
    const pieces = splitOversizedColumn({ paragraphs: [paragraph], height: 800 }, 400);
    expect(pieces).toHaveLength(2);
    expect(pieces.map((piece) => piece.paragraphs.join(""))).toEqual([
      paragraph.slice(0, 50),
      paragraph.slice(50),
    ]);
    expect(pieces.reduce((sum, piece) => sum + piece.height, 0)).toBeLessThanOrEqual(800 + 1);
  });

  it("切栏页数封顶 8，异常超高的单段不会炸出几十栏", () => {
    const paragraph = "字".repeat(400);
    expect(splitOversizedColumn({ paragraphs: [paragraph], height: 8000 }, 100)).toHaveLength(8);
  });
});

describe("paginateColumns", () => {
  const paragraphs = ["一", "二", "三", "四", "五", "六"];
  // 每段高 100、段间距 20，容器高 250 → 每栏两段 → 3 栏 → 相邻两栏配成一屏
  const boxes: ScreenBox[] = paragraphs.map((_, index) => ({
    top: index * 120,
    bottom: index * 120 + 100,
  }));

  it("相邻两栏配成一屏（书式左右两栏）", () => {
    const layout = paginateColumns(paragraphs, boxes, 250, 2);
    expect(layout.screens).toEqual([
      [["一", "二"], ["三", "四"]],
      [["五", "六"]],
    ]);
    expect(layout.scales).toEqual([1, 1]);
  });

  it("columnCount=1 时一栏即一屏", () => {
    const layout = paginateColumns(paragraphs, boxes, 250, 1);
    expect(layout.screens.map((screen) => screen.length)).toEqual([1, 1, 1]);
  });

  it("单段超高先切栏再配对，长文也排满一屏", () => {
    const long = "长".repeat(200);
    // 内容高 760 / 栏高 200 → 切成 4 栏（每栏 50 字、高 190）→ 两栏配成 2 屏
    const layout = paginateColumns([long], [{ top: 0, bottom: 760 }], 200, 2);
    expect(layout.screens).toHaveLength(2);
    expect(layout.screens[0]).toHaveLength(2);
    const covered = layout.screens.flat(2).join("");
    expect(covered).toBe(long); // 不丢字、不重复
    expect(layout.scales).toEqual([1, 1]);
  });

  it("整篇远不满一屏时两栏均分，不留半块空白黑板", () => {
    // 两段各 100 高、栏可用 1000：不均分会全挤进左栏，右半块空白。
    const boxes: ScreenBox[] = [
      { top: 0, bottom: 100 },
      { top: 100, bottom: 200 },
    ];
    const layout = paginateColumns(["甲", "乙"], boxes, 1000, 2);
    expect(layout.balanced).toBe(true);
    expect(layout.screens).toEqual([[['甲'], ['乙']]]);
    expect(layout.scales).toEqual([1]);
  });

  it("单段已被切成两栏时不再均分，也不缩字号、不丢字", () => {
    const long = "字".repeat(400);
    const layout = paginateColumns([long], [{ top: 0, bottom: 2000 }], 1000, 2);
    expect(layout.balanced).toBe(false);
    expect(layout.screens).toHaveLength(1);
    expect(layout.screens[0]).toHaveLength(2);
    expect(layout.screens.flat(2).join("")).toBe(long);
    expect(layout.scales).toEqual([1]);
  });

  it("均分后段落仍不丢：两栏合起来等于全文", () => {
    const paras = ["甲段", "乙段", "丙段"];
    const boxes: ScreenBox[] = [
      { top: 0, bottom: 150 },
      { top: 200, bottom: 350 },
      { top: 400, bottom: 550 },
    ];
    const layout = paginateColumns(paras, boxes, 1000, 2);
    expect(layout.balanced).toBe(true);
    // 均分只重排栏高：不许多切出一屏，也不许把字号压小（下界=最高那一段）
    expect(layout.screens).toHaveLength(1);
    expect(layout.screens[0]).toHaveLength(2);
    expect(layout.scales).toEqual([1]);
    expect(layout.screens.flat(2)).toEqual(paras);
  });
  it("空正文给一屏占位，不产生空白页", () => {
    const layout = paginateColumns([], [], 500, 2);
    expect(layout.screens).toEqual([[["（正文待补）"]]]);
    expect(layout.scales).toEqual([1]);
  });
});

describe("书式两栏排版", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchPresent).mockResolvedValue(presentData);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /** 让 jsdom 量出真实高度：每段 segmentHeight 高、段间无额外 margin，正文区 containerHeight 高。 */
  function stubMeasuredLayout(segmentHeight: number, containerHeight: number): void {
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(containerHeight);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: Element,
    ) {
      const list = Array.from(document.querySelectorAll("[data-paragraph]"));
      const index = list.indexOf(this);
      const top = index < 0 ? 0 : index * segmentHeight;
      return { top, bottom: top + segmentHeight } as DOMRect;
    });
  }

  function setViewport(width: number): void {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(width);
  }

  it("标题在上、作者在下（抬头 DOM 顺序即用户要求）", async () => {
    setViewport(1920);
    stubMeasuredLayout(100, 1000);
    renderPresent();
    await screen.findByTestId("present-body");

    const title = screen.getByTestId("present-title");
    const name = screen.getByTestId("present-name");
    expect(title.textContent).toBe("春天");
    expect(name.textContent).toBe("张三");
    // eslint-disable-next-line no-bitwise
    expect(title.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // 抬头在正文之前
    // eslint-disable-next-line no-bitwise
    expect(
      title.compareDocumentPosition(screen.getByTestId("present-body")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("正文分左右两栏：右栏首段正是左栏末段的下一段", async () => {
    const six = {
      ...presentData,
      items: [
        {
          student_no: "S001",
          name: "张三",
          title: "春天",
          paragraphs: ["第1段", "第2段", "第3段", "第4段", "第5段", "第6段"],
          is_selected: false,
        },
        presentData.items[1],
      ],
    };
    vi.mocked(api.fetchPresent).mockResolvedValue(six);
    setViewport(1920);
    // 每段 100 高、正文区 250 高 → 每栏 2 段 → 3 栏 → 相邻两栏配成 2 屏
    stubMeasuredLayout(100, 250);
    renderPresent();
    await screen.findByTestId("present-body");

    const columns = screen.getAllByTestId("present-column");
    expect(columns).toHaveLength(2);
    expect(columns[0].textContent).toBe("第1段第2段");
    expect(columns[1].textContent).toBe("第3段第4段");
    expect(screen.getByTestId("present-screen-progress").textContent).toBe("第 1 / 2 屏 · 共 2 篇");
  });

  it("窄视口退回单栏（不产生 11 字/行的窄栏）", async () => {
    setViewport(1024);
    stubMeasuredLayout(100, 250);
    const six = {
      ...presentData,
      items: [
        {
          student_no: "S001",
          name: "张三",
          title: "春天",
          paragraphs: ["第1段", "第2段", "第3段", "第4段", "第5段", "第6段"],
          is_selected: false,
        },
        presentData.items[1],
      ],
    };
    vi.mocked(api.fetchPresent).mockResolvedValue(six);
    renderPresent();
    await screen.findByTestId("present-body");

    expect(screen.getAllByTestId("present-column")).toHaveLength(1);
    expect(screen.getByTestId("present-screen-progress").textContent).toBe("第 1 / 3 屏 · 共 2 篇");
  });

  it("字号档位可切，切换后按新基准字号重排", async () => {
    setViewport(1920);
    stubMeasuredLayout(100, 1000);
    renderPresent();
    await screen.findByTestId("present-body");

    expect((screen.getByTestId("present-body") as HTMLElement).style.fontSize).toBe("28px");
    fireEvent.click(screen.getByTestId("present-font-lg"));
    await waitFor(() =>
      expect((screen.getByTestId("present-body") as HTMLElement).style.fontSize).toBe("36px"),
    );
    fireEvent.click(screen.getByTestId("present-font-sm"));
    await waitFor(() =>
      expect((screen.getByTestId("present-body") as HTMLElement).style.fontSize).toBe("22px"),
    );
  });
});

describe("跨篇翻页回归（真机 GAP-08：只能看到一篇）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchPresent).mockResolvedValue(presentData);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function stubWideAndTall(): void {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(1920);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(250);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: Element,
    ) {
      const list = Array.from(document.querySelectorAll("[data-paragraph]"));
      const index = list.indexOf(this);
      const top = index < 0 ? 0 : index * 120;
      return { top, bottom: top + 100 } as DOMRect;
    });
  }

  function longEssay() {
    return {
      ...presentData,
      items: [
        {
          student_no: "S001",
          name: "张三",
          title: "春天",
          paragraphs: ["甲", "乙", "丙", "丁", "戊", "己"],
          is_selected: true,
        },
        presentData.items[1],
      ],
    };
  }

  it("本篇最后一屏的「下一页」仍可点，点下去进入下一篇", async () => {
    vi.mocked(api.fetchPresent).mockResolvedValue(longEssay());
    stubWideAndTall();
    renderPresent();
    await screen.findByTestId("present-body");

    const next = screen.getByRole("button", { name: "下一页 →" });
    expect(screen.getByTestId("present-screen-progress").textContent).toBe("第 1 / 2 屏 · 共 2 篇");
    expect(next.hasAttribute("disabled")).toBe(false);

    fireEvent.click(next);
    await waitFor(() =>
      expect(screen.getByTestId("present-screen-progress").textContent).toBe("第 2 / 2 屏 · 共 2 篇"),
    );
    // 缺陷现场：旧实现用篇内屏号判 disabled，走到这里按钮已经灰掉，触屏翻不过去。
    expect(next.hasAttribute("disabled")).toBe(false);

    fireEvent.click(next);
    await waitFor(() => expect(screen.getByTestId("present-name").textContent).toBe("李四"));
    expect(screen.getByTestId("pager-progress").textContent).toBe("2 / 2");
  });

  it("下一篇的「上一页」回到上一篇最后一屏", async () => {
    vi.mocked(api.fetchPresent).mockResolvedValue(longEssay());
    stubWideAndTall();
    renderPresent();
    await screen.findByTestId("present-body");

    fireEvent.click(screen.getByRole("button", { name: "下一页 →" }));
    await waitFor(() =>
      expect(screen.getByTestId("present-screen-progress").textContent).toBe("第 2 / 2 屏 · 共 2 篇"),
    );
    fireEvent.click(screen.getByRole("button", { name: "下一页 →" }));
    await waitFor(() => expect(screen.getByTestId("present-name").textContent).toBe("李四"));

    const prev = screen.getByRole("button", { name: "← 上一页" });
    expect(prev.hasAttribute("disabled")).toBe(false);
    fireEvent.click(prev);
    await waitFor(() => expect(screen.getByTestId("present-name").textContent).toBe("张三"));
    // 回到上一篇时指向它的最后一屏，而不是第 1 屏
    expect(screen.getByTestId("present-screen-progress").textContent).toBe("第 2 / 2 屏 · 共 2 篇");
  });

  it("只有全站最后一屏才禁用下一页", async () => {
    renderPresent();
    await screen.findByTestId("present-body");

    const next = screen.getByRole("button", { name: "下一页 →" });
    // jsdom 视口 1024 → 单栏；每篇各占一屏
    expect(screen.getByTestId("pager-progress").textContent).toBe("1 / 2");
    expect(next.hasAttribute("disabled")).toBe(false);
    fireEvent.click(next);
    await waitFor(() => expect(screen.getByTestId("present-name").textContent).toBe("李四"));
    expect(next.hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "← 上一页" }).hasAttribute("disabled")).toBe(false);
  });
});

describe("投屏列出全部有文字的稿件（真机 GAP-14：只能显示一篇）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchPresent).mockResolvedValue(presentData);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // 老师拍 3 篇、只定稿 1 篇：后端现在把未定稿的识别初稿也放进轮播，另有无文字的一篇单独计数。
  function mixedDraft() {
    return {
      ...presentData,
      draft_count: 1,
      excluded_no_text: 1,
      items: [
        { ...presentData.items[0], is_draft: false },
        { ...presentData.items[1], is_draft: true },
      ],
    } satisfies PresentData;
  }

  it("未定稿那篇挂「未定稿 · 识别初稿」徽标，定稿那篇不挂", async () => {
    vi.mocked(api.fetchPresent).mockResolvedValue(mixedDraft());
    renderPresent();
    await screen.findByTestId("present-name");

    expect(screen.queryByTestId("present-draft")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "下一页 →" }));
    await waitFor(() => expect(screen.getByTestId("present-name").textContent).toBe("李四"));

    expect(screen.getByTestId("present-draft").textContent).toContain("识别初稿");
  });

  it("顶栏说明未定稿篇数与被排除的无文字篇数", async () => {
    vi.mocked(api.fetchPresent).mockResolvedValue(mixedDraft());
    renderPresent();
    await screen.findByTestId("present-name");

    expect(screen.getByTestId("present-draft-count").textContent).toMatch(/1\s*篇未定稿/);
    expect(screen.getByTestId("present-excluded-count").textContent).toMatch(/1\s*篇暂无文字/);
  });

  it("全部定稿且无排除时不出现初稿提示", async () => {
    renderPresent();
    await screen.findByTestId("present-name");

    expect(screen.queryByTestId("present-draft")).toBeNull();
    expect(screen.queryByTestId("present-draft-count")).toBeNull();
    expect(screen.queryByTestId("present-excluded-count")).toBeNull();
  });
});
