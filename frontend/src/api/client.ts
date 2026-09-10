/**
 * API 客户端：统一注入 Bearer token、解析后端统一错误信封 {code,data,message}。
 *
 * 约定：成功 {code:0, data, message}；失败 {code:非0, data:null, message}。
 * 任何 HTTP 非 2xx 或 code !== 0 都抛出 ApiError。
 */

import type { Envelope, LoginResult } from "./types";

const TOKEN_KEY = "ewb_token";
const API_BASE = "/api";

export function readToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function writeToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* localStorage 不可用时忽略（隐私模式） */
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 忽略 */
  }
}

export class ApiError extends Error {
  readonly code: number;
  readonly status: number;

  constructor(message: string, code: number, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = readToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  let payload: Envelope<T> | null = null;
  try {
    payload = (await response.json()) as Envelope<T>;
  } catch {
    payload = null;
  }

  if (!response.ok || (payload !== null && payload.code !== 0)) {
    const message = payload?.message || `请求失败（HTTP ${response.status}）`;
    const code = payload?.code ?? response.status;
    throw new ApiError(message, code, response.status);
  }

  return (payload as Envelope<T>).data as T;
}

export const api = {
  login(password: string): Promise<LoginResult> {
    return request<LoginResult>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
  },
};

export { request };
