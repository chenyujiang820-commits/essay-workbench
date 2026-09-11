import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import StatusBadge, { statusLabel } from "./StatusBadge";

function badgeClass(status: string): { text: string; className: string } {
  const { container } = render(<StatusBadge status={status} />);
  const badge = container.querySelector<HTMLElement>(`[data-status="${status}"]`);
  expect(badge).toBeTruthy();
  return { text: badge!.textContent ?? "", className: badge!.className };
}

describe("StatusBadge", () => {
  it("gives a failed essay its own visible label and rose styling", () => {
    const failed = badgeClass("failed");
    expect(failed.text).toBe("识别失败");
    expect(failed.className).toContain("bg-rose-100");
    expect(failed.className).toContain("text-rose-700");
  });

  it("styles failed differently from every other status", () => {
    const failed = badgeClass("failed");
    for (const status of ["uploaded", "recognizing", "review", "proofread"]) {
      const other = badgeClass(status);
      expect(other.text).not.toBe(failed.text);
      expect(other.className).not.toBe(failed.className);
    }
  });

  it("exposes the label for the failed status and falls back for unknown ones", () => {
    expect(statusLabel("failed")).toBe("识别失败");
    expect(statusLabel("something_new")).toBe("something_new");
  });
});
