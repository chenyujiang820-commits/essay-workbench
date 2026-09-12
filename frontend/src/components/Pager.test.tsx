import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Pager from "./Pager";

describe("Pager", () => {
  it("navigates with keyboard (arrows / space / escape)", () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    const onExit = vi.fn();
    render(
      <Pager index={1} total={3} onPrev={onPrev} onNext={onNext} onExit={onExit}>
        <div>内容</div>
      </Pager>,
    );

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(onNext).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(onPrev).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: " " });
    expect(onNext).toHaveBeenCalledTimes(2);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it("supports touch swipe left/right", () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    render(
      <Pager index={1} total={3} onPrev={onPrev} onNext={onNext}>
        <div>x</div>
      </Pager>,
    );

    const pager = screen.getByTestId("pager");
    fireEvent.touchStart(pager, { touches: [{ clientX: 200 }] });
    fireEvent.touchEnd(pager, { changedTouches: [{ clientX: 100 }] });
    expect(onNext).toHaveBeenCalledTimes(1);

    fireEvent.touchStart(pager, { touches: [{ clientX: 100 }] });
    fireEvent.touchEnd(pager, { changedTouches: [{ clientX: 220 }] });
    expect(onPrev).toHaveBeenCalledTimes(1);
  });

  it("ignores small swipes and renders progress text", () => {
    const onNext = vi.fn();
    render(
      <Pager index={0} total={5} onPrev={() => undefined} onNext={onNext} progressText="1 / 3">
        <div>x</div>
      </Pager>,
    );

    const pager = screen.getByTestId("pager");
    fireEvent.touchStart(pager, { touches: [{ clientX: 100 }] });
    fireEvent.touchEnd(pager, { changedTouches: [{ clientX: 120 }] });
    expect(onNext).not.toHaveBeenCalled();

    expect(screen.getByTestId("pager-progress").textContent).toBe("1 / 3");
  });

  it("hides the control bar in pure mode", () => {
    render(
      <Pager index={0} total={3} onPrev={() => undefined} onNext={() => undefined} showControls={false}>
        <div>x</div>
      </Pager>,
    );
    expect(screen.queryByTestId("pager-controls")).toBeNull();
  });

  it("hasPrevPage / hasNextPage 覆盖篇内屏号判据（跨篇时按钮不许变灰）", () => {
    render(
      <Pager
        index={2}
        total={3}
        hasPrevPage
        hasNextPage
        onPrev={() => undefined}
        onNext={() => undefined}
      >
        <div>x</div>
      </Pager>,
    );

    // index=2、total=3：旧判据下「下一页」已经 disabled，触屏就翻不到下一篇了。
    expect(screen.getByRole("button", { name: "下一页 →" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "← 上一页" }).hasAttribute("disabled")).toBe(false);
  });

  it("显式 false 优先于 index：首页也能禁用上一页", () => {
    render(
      <Pager index={1} total={3} hasPrevPage={false} onPrev={() => undefined} onNext={() => undefined}>
        <div>x</div>
      </Pager>,
    );

    expect(screen.getByRole("button", { name: "← 上一页" }).hasAttribute("disabled")).toBe(true);
  });

  it("缺省不传新 props 时保持旧的按 index 判定（向后兼容）", () => {
    render(
      <Pager index={0} total={3} onPrev={() => undefined} onNext={() => undefined}>
        <div>x</div>
      </Pager>,
    );

    expect(screen.getByRole("button", { name: "← 上一页" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "下一页 →" }).hasAttribute("disabled")).toBe(false);
  });
});
