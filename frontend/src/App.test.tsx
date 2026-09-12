import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { useAppStore } from "./store";

/**
 * 路由表的鉴权边界回归锁。
 *
 * 二期一次加了表彰榜 / 分享管理 / 成长档案三条路由，家长页又必须免鉴权：
 * 守卫少包一条就会把教师数据暴露给匿名访问，多包一条家长就打不开分享链接。
 * 这两种走法都只在真机上才会发现，所以在这里钉住。页面组件本身各自有测试，这里用替身。
 */
function stubPage(label: string) {
  return {
    default: function Stub() {
      return <div>{label}</div>;
    },
  };
}

vi.mock("./pages/BookPage", () => stubPage("作品集页"));
vi.mock("./pages/PortfolioPage", () => stubPage("成长档案页"));
vi.mock("./pages/RankingPage", () => stubPage("表彰榜页"));
vi.mock("./pages/SharePage", () => stubPage("家长分享页"));
vi.mock("./pages/SharesPage", () => stubPage("分享管理页"));
vi.mock("./pages/EssayListPage", () => stubPage("状态看板页"));
vi.mock("./pages/IssuePage", () => stubPage("期数列表页"));
vi.mock("./pages/LoginPage", () => stubPage("登录页"));
vi.mock("./pages/PresentPage", () => stubPage("投屏页"));
vi.mock("./pages/ProofreadPage", () => stubPage("校对页"));
vi.mock("./pages/StudentsPage", () => stubPage("学生管理页"));
vi.mock("./pages/UploadPage", () => stubPage("拍照上传页"));

/** 需要登录的全部路由（二期新增的三条也在里面）。 */
const GUARDED: Array<[string, string]> = [
  ["/", "期数列表页"],
  ["/students", "学生管理页"],
  ["/issues/2/upload", "拍照上传页"],
  ["/issues/2/essays", "状态看板页"],
  ["/essays/9/proofread", "校对页"],
  ["/issues/2/book", "作品集页"],
  ["/present/2", "投屏页"],
  ["/issues/2/ranking", "表彰榜页"],
  ["/issues/2/shares", "分享管理页"],
  ["/students/3/portfolio", "成长档案页"],
];

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App 路由：未登录时守卫必须生效", () => {
  beforeEach(() => {
    useAppStore.getState().logout();
  });
  afterEach(() => {
    useAppStore.getState().logout();
  });

  // 二期新增的三条路由一旦漏包进 RequireAuth，就会把班级数据挂在匿名 URL 下。
  it.each([
    ["/"],
    ["/issues/2/ranking"],
    ["/issues/2/shares"],
    ["/students/3/portfolio"],
    ["/essays/9/proofread"],
  ])("%s 未登录时渲染登录页", async (path) => {
    renderAt(path);
    expect(await screen.findByText("登录页")).toBeTruthy();
  });

  it("未登录也进不去任何一期看板", async () => {
    renderAt("/issues/2/essays");
    expect(await screen.findByText("登录页")).toBeTruthy();
    expect(screen.queryByText("状态看板页")).toBeNull();
  });
});

describe("App 路由：登录后每一页都可达", () => {
  beforeEach(() => {
    useAppStore.getState().setToken("tk-test");
  });
  afterEach(() => {
    useAppStore.getState().logout();
  });

  it.each(GUARDED)("%s 渲染%s", async (path, label) => {
    renderAt(path);
    expect(await screen.findByText(label)).toBeTruthy();
  });
});

describe("App 路由：家长分享页免鉴权", () => {
  beforeEach(() => {
    useAppStore.getState().logout();
  });

  it("带令牌的短链不用登录即可打开", async () => {
    renderAt("/share/tk-parent");
    expect(await screen.findByText("家长分享页")).toBeTruthy();
    expect(screen.queryByText("登录页")).toBeNull();
  });

  it("省略令牌的入口同样免鉴权，由页面自己渲染失效态", async () => {
    renderAt("/share");
    expect(await screen.findByText("家长分享页")).toBeTruthy();
  });
});

describe("App 路由：未知路径兜底", () => {
  beforeEach(() => {
    useAppStore.getState().logout();
  });
  afterEach(() => {
    useAppStore.getState().logout();
  });

  it("未知路径未登录时回到登录页而不是白屏", async () => {
    renderAt("/issues/2/nothing-here");
    expect(await screen.findByText("登录页")).toBeTruthy();
  });
});
