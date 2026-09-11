import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import PreviewFrame, { computePreviewScale, PREVIEW_DESIGN_WIDTH } from "./PreviewFrame";

describe("computePreviewScale", () => {
  it("keeps scale 1 on wide containers", () => {
    expect(computePreviewScale(1200)).toBe(1);
    expect(computePreviewScale(PREVIEW_DESIGN_WIDTH)).toBe(1);
  });

  it("shrinks the A4 preview on narrow screens so it never scrolls sideways", () => {
    expect(computePreviewScale(397)).toBeCloseTo(0.5);
    expect(computePreviewScale(300)).toBeCloseTo(300 / PREVIEW_DESIGN_WIDTH);
  });

  it("clamps the scale to a readable minimum and handles invalid widths", () => {
    expect(computePreviewScale(100)).toBe(0.25);
    expect(computePreviewScale(0)).toBe(1);
    expect(computePreviewScale(-10)).toBe(1);
  });
});

describe("PreviewFrame", () => {
  it("renders an iframe scaled to fit its container", () => {
    // jsdom 无布局：clientWidth 为 0 → scale 退化为 1，仅验证渲染结构。
    const { getByTestId } = render(<PreviewFrame html="<p>预览</p>" />);
    const iframe = getByTestId("book-preview") as HTMLIFrameElement;
    expect(iframe.getAttribute("srcdoc")).toContain("预览");
    expect(getByTestId("preview-frame")).toBeTruthy();
  });
});
