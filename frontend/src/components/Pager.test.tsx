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
});
