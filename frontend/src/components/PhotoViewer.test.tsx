import { render, screen } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import PhotoViewer from "./PhotoViewer";

interface ZoomControls {
  zoomIn: () => void;
  zoomOut: () => void;
  resetTransform: () => void;
}

// 隔离 react-zoom-pan-pinch：jsdom 没有 ResizeObserver / 真实布局，这里只保留渲染契约。
vi.mock("react-zoom-pan-pinch", () => ({
  TransformWrapper: ({ children }: { children: (controls: ZoomControls) => ReactElement }) =>
    children({ zoomIn: vi.fn(), zoomOut: vi.fn(), resetTransform: vi.fn() }),
  TransformComponent: ({ children }: { children: ReactNode }) => (
    <div data-testid="transform-component">{children}</div>
  ),
}));

describe("PhotoViewer", () => {
  it("renders the original image with zoom controls once the url is ready", () => {
    render(<PhotoViewer url="blob:mock" seq={2} />);

    const image = screen.getByRole("img", { name: "第 2 张原片" }) as HTMLImageElement;
    expect(image.getAttribute("src")).toBe("blob:mock");
    expect(screen.getByLabelText("放大")).toBeTruthy();
    expect(screen.queryByTestId("photo-low-resolution")).toBeNull();
  });

  it("shows a placeholder while the original is still loading", () => {
    render(<PhotoViewer url={null} seq={1} />);
    expect(screen.getByText("原片加载中…")).toBeTruthy();
  });

  it("badges a low-resolution original so the teacher knows to zoom in", () => {
    render(<PhotoViewer url="blob:mock" seq={3} lowResolution />);

    const badge = screen.getByTestId("photo-low-resolution");
    expect(badge.textContent).toContain("画质偏低");
    expect(badge.textContent).toContain("第 3 张");
    // 角标只做提示，不遮挡图片本体，也不改变可访问名。
    expect(screen.getByRole("img", { name: "第 3 张原片" })).toBeTruthy();
  });
});
