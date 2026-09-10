/** 轻量全局状态（Zustand）：登录态 + 当前期数。 */

import { create } from "zustand";

import { clearToken, readToken, writeToken } from "./api/client";

interface AppState {
  token: string | null;
  currentIssueId: number | null;
  setToken: (token: string) => void;
  logout: () => void;
  setCurrentIssueId: (issueId: number | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  token: readToken(),
  currentIssueId: null,
  setToken: (token: string) => {
    writeToken(token);
    set({ token });
  },
  logout: () => {
    clearToken();
    set({ token: null });
  },
  setCurrentIssueId: (issueId: number | null) => set({ currentIssueId: issueId }),
}));
