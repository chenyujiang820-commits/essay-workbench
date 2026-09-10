/**
 * 轻量全局状态（Zustand）：登录态 + 当前期数 + 轮询定时器管理。
 *
 * 轮询约定：识别中的作文由状态看板按 3s 轮询 `GET /api/essays/{id}`。
 * 定时器集中由本 store 持有，保证「同一作文只有一个定时器」，避免重复轮询；
 * 页面卸载时调用 {@link stopAllPolling} 即可全部停止。
 */

import { create } from "zustand";

import { clearToken, readToken, writeToken } from "./api/client";

export type PollCallback = () => void | Promise<void>;

/** 模块级定时器表：不进入响应式 state，避免无谓重渲染，同时保证唯一性。 */
const timers = new Map<number, ReturnType<typeof setInterval>>();

interface AppState {
  token: string | null;
  currentIssueId: number | null;
  /** 当前处于轮询中的作文 id（响应式镜像，供 UI/测试观察）。 */
  pollingIds: number[];
  setToken: (token: string) => void;
  logout: () => void;
  setCurrentIssueId: (issueId: number | null) => void;
  startPolling: (essayId: number, callback: PollCallback, intervalMs?: number) => void;
  stopPolling: (essayId: number) => void;
  stopAllPolling: () => void;
}

export const DEFAULT_POLL_INTERVAL_MS = 3000;

export const useAppStore = create<AppState>((set, get) => ({
  token: readToken(),
  currentIssueId: null,
  pollingIds: [],

  setToken: (token: string) => {
    writeToken(token);
    set({ token });
  },

  logout: () => {
    get().stopAllPolling();
    clearToken();
    set({ token: null });
  },

  setCurrentIssueId: (issueId: number | null) => set({ currentIssueId: issueId }),

  startPolling: (essayId: number, callback: PollCallback, intervalMs = DEFAULT_POLL_INTERVAL_MS) => {
    if (timers.has(essayId)) {
      return; // 已在轮询，避免重复定时器
    }
    const timer = setInterval(() => {
      void callback();
    }, intervalMs);
    timers.set(essayId, timer);
    set({ pollingIds: Array.from(timers.keys()) });
  },

  stopPolling: (essayId: number) => {
    const timer = timers.get(essayId);
    if (timer === undefined) {
      return;
    }
    clearInterval(timer);
    timers.delete(essayId);
    set({ pollingIds: Array.from(timers.keys()) });
  },

  stopAllPolling: () => {
    for (const timer of timers.values()) {
      clearInterval(timer);
    }
    timers.clear();
    set({ pollingIds: [] });
  },
}));

/** 仅供测试/诊断：当前实际注册的定时器数量。 */
export function activePollingCount(): number {
  return timers.size;
}
