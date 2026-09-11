import { describe, expect, it } from "vitest";

import { compressImage, isLowResolution, MIN_LONG_EDGE, MIN_SHORT_EDGE, targetSize } from "./image";

describe("targetSize", () => {
  it("scales the longest edge down to the cap", () => {
    expect(targetSize(4000, 3000, 2000)).toEqual({ width: 2000, height: 1500 });
  });

  it("handles portrait orientation", () => {
    expect(targetSize(1500, 3000, 2000)).toEqual({ width: 1000, height: 2000 });
  });

  it("keeps images already within the cap unchanged", () => {
    expect(targetSize(800, 600, 2000)).toEqual({ width: 800, height: 600 });
  });

  it("guards against zero-size inputs", () => {
    expect(targetSize(0, 0, 2000)).toEqual({ width: 0, height: 0 });
  });
});

describe("compressImage", () => {
  it("short-circuits non-image files without touching the canvas", async () => {
    const pdf = new File([new Uint8Array([1, 2, 3])], "note.pdf", { type: "application/pdf" });
    const result = await compressImage(pdf);
    expect(result).toBe(pdf);
  });

  it("passes through files whose type is empty", async () => {
    const blob = new File([new Uint8Array([9])], "mystery", { type: "" });
    const result = await compressImage(blob);
    expect(result).toBe(blob);
  });
});

describe("isLowResolution", () => {
  // 口径必须与后端 app/images.py::is_low_resolution 逐值一致
  it.each([
    [600, 800, false],
    [800, 600, false],
    [599, 2000, true],
    [2000, 599, true],
    [799, 2000, false],
    [100, 2000, true],
    [1080, 1428, false],
    [1095, 1553, false],
    [600, 799, true],
  ])("%ix%i -> %s", (width, height, expected) => {
    expect(isLowResolution(width, height)).toBe(expected);
  });

  it("treats unknown dimensions as not-low so uploads are never blocked", () => {
    expect(isLowResolution(null, null)).toBe(false);
    expect(isLowResolution(undefined, 1000)).toBe(false);
    expect(isLowResolution(0, 0)).toBe(false);
  });

  it("exposes the same thresholds as the PRD (800x600)", () => {
    expect(MIN_SHORT_EDGE).toBe(600);
    expect(MIN_LONG_EDGE).toBe(800);
  });
});
