import { describe, expect, it } from "vitest";

import { compressImage, targetSize } from "./image";

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
