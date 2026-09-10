import { afterEach, describe, expect, it, vi } from "vitest";

import { activePollingCount, useAppStore } from "./store";

describe("polling store", () => {
  afterEach(() => {
    useAppStore.getState().stopAllPolling();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("registers a single timer per essay and dedupes re-registration", () => {
    const setIntervalSpy = vi.spyOn(globalThis, "setInterval");
    useAppStore.getState().stopAllPolling();

    useAppStore.getState().startPolling(1, () => undefined);
    useAppStore.getState().startPolling(1, () => undefined); // 重复登记应被忽略

    expect(activePollingCount()).toBe(1);
    expect(useAppStore.getState().pollingIds).toEqual([1]);
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
  });

  it("stops one poll and stops them all", () => {
    const clearSpy = vi.spyOn(globalThis, "clearInterval");
    const { startPolling, stopPolling, stopAllPolling } = useAppStore.getState();

    startPolling(1, () => undefined);
    startPolling(2, () => undefined);
    expect(activePollingCount()).toBe(2);

    stopPolling(1);
    expect(useAppStore.getState().pollingIds).toEqual([2]);
    expect(activePollingCount()).toBe(1);

    stopAllPolling();
    expect(activePollingCount()).toBe(0);
    expect(useAppStore.getState().pollingIds).toEqual([]);
    expect(clearSpy).toHaveBeenCalled();
  });

  it("invokes the callback on each interval tick", () => {
    vi.useFakeTimers();
    const callback = vi.fn();

    useAppStore.getState().startPolling(5, callback, 3000);
    vi.advanceTimersByTime(3000);
    expect(callback).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(3000);
    expect(callback).toHaveBeenCalledTimes(2);

    useAppStore.getState().stopPolling(5);
    vi.advanceTimersByTime(9000);
    expect(callback).toHaveBeenCalledTimes(2);
  });
});
