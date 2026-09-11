/**
 * API 客户端：统一注入 Bearer token、解析后端统一错误信封 {code,data,message}。
 *
 * 约定：成功 {code:0, data, message}；失败 {code:非0, data:null, message}。
 * 任何 HTTP 非 2xx 或 code !== 0 都抛出 ApiError。
 */

import type {
  Envelope,
  EssayDetail,
  EssaySummary,
  Issue,
  LoginResult,
  MetaInfo,
  PresentData,
  Student,
  TemplateInfo,
  UploadResult,
} from "./types";

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

function authHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const token = readToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

/**
 * 把任意来源的 code 归一为**非零**错误码。
 *
 * 信封契约里 ``code === 0`` 唯一表示"成功"，因此绝不允许出现在 ApiError 上；
 * 否则调用方无法区分成功与失败。依次回退：信封 code → HTTP 状态码 → 500。
 */
function normalizeErrorCode(raw: unknown, status: number): number {
  const candidate = typeof raw === "number" && Number.isFinite(raw) ? Math.trunc(raw) : 0;
  if (candidate !== 0) {
    return candidate;
  }
  return status > 0 ? status : 500;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = authHeaders(init.headers);
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

  if (!response.ok) {
    const message = payload?.message || `请求失败（HTTP ${response.status}）`;
    throw new ApiError(
      message,
      normalizeErrorCode(payload?.code, response.status),
      response.status,
    );
  }

  if (
    payload === null ||
    typeof payload !== "object" ||
    payload.code !== 0 ||
    payload.data === null ||
    payload.data === undefined
  ) {
    throw new ApiError(
      "服务器返回了无效响应",
      normalizeErrorCode(payload?.code, response.status),
      response.status,
    );
  }

  return payload.data;
}

/** 获取二进制资源（如原片/PDF），失败时仍尽力解析错误信封文案。 */
async function requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const headers = authHeaders(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    let code = response.status;
    try {
      const payload = (await response.json()) as Envelope<unknown>;
      if (payload?.message) {
        message = payload.message;
        code = normalizeErrorCode(payload.code, code);
      }
    } catch {
      /* 非 JSON 响应，沿用默认文案 */
    }
    throw new ApiError(message, code, response.status);
  }
  return response.blob();
}

/** 获取文本资源（如成册预览 HTML），失败时解析错误信封文案。 */
async function requestText(path: string, init: RequestInit = {}): Promise<string> {
  const headers = authHeaders(init.headers);
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    let code = response.status;
    try {
      const payload = (await response.json()) as Envelope<unknown>;
      if (payload?.message) {
        message = payload.message;
        code = normalizeErrorCode(payload.code, code);
      }
    } catch {
      /* 忽略 */
    }
    throw new ApiError(message, code, response.status);
  }
  return response.text();
}

export const api = {
  login(password: string): Promise<LoginResult> {
    return request<LoginResult>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
  },

  // -- 期数 ----------------------------------------------------------------
  listIssues(): Promise<Issue[]> {
    return request<Issue[]>("/issues");
  },

  createIssue(issueNo: number, weekStartDate: string): Promise<Issue> {
    return request<Issue>("/issues", {
      method: "POST",
      body: JSON.stringify({ issue_no: issueNo, week_start_date: weekStartDate }),
    });
  },

  getIssue(issueId: number): Promise<Issue> {
    return request<Issue>(`/issues/${issueId}`);
  },

  /** 改期号 / 周起始日（v1.2 补前端入口）。 */
  updateIssue(
    issueId: number,
    patch: { issue_no?: number; week_start_date?: string },
  ): Promise<Issue> {
    return request<Issue>(`/issues/${issueId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  },

  deleteIssue(issueId: number, confirm: boolean): Promise<{ id: number; deleted_essays: number }> {
    return request<{ id: number; deleted_essays: number }>(
      `/issues/${issueId}${confirm ? "?confirm=true" : ""}`,
      { method: "DELETE" },
    );
  },

  // -- 学生 ----------------------------------------------------------------
  listStudents(): Promise<Student[]> {
    return request<Student[]>("/students");
  },

  createStudent(studentNo: string, name: string): Promise<Student> {
    return request<Student>("/students", {
      method: "POST",
      body: JSON.stringify({ student_no: studentNo, name }),
    });
  },

  updateStudent(
    studentId: number,
    patch: { student_no?: string; name?: string },
  ): Promise<Student> {
    return request<Student>(`/students/${studentId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  },

  deactivateStudent(studentId: number): Promise<{ id: number }> {
    return request<{ id: number }>(`/students/${studentId}`, { method: "DELETE" });
  },

  importStudents(
    students: { student_no: string; name: string }[],
  ): Promise<{ created: number; updated: number }> {
    return request<{ created: number; updated: number }>("/students/import", {
      method: "POST",
      body: JSON.stringify({ students }),
    });
  },

  // -- 作文 ----------------------------------------------------------------
  listIssueEssays(issueId: number): Promise<EssaySummary[]> {
    return request<EssaySummary[]>(`/issues/${issueId}/essays`);
  },

  getEssay(essayId: number): Promise<EssayDetail> {
    return request<EssayDetail>(`/essays/${essayId}`);
  },

  updateEssay(
    essayId: number,
    payload: { final_text: string; proofread: boolean; title?: string },
  ): Promise<EssayDetail> {
    return request<EssayDetail>(`/essays/${essayId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  /** 重新排队识别（v1.2 FR-10）：已定稿会被后端 409 拒绝。 */
  retryEssay(essayId: number): Promise<UploadResult> {
    return request<UploadResult>(`/essays/${essayId}/recognize`, { method: "POST" });
  },

  /** 多图一次提交一篇作文（multipart：student_id + files[]）。 */
  uploadEssay(issueId: number, studentId: number, files: File[]): Promise<UploadResult> {
    const form = new FormData();
    form.append("student_id", String(studentId));
    for (const file of files) {
      form.append("files", file, file.name);
    }
    return request<UploadResult>(`/issues/${issueId}/essays`, {
      method: "POST",
      body: form,
    });
  },

  // -- 元信息 --------------------------------------------------------------
  /** 班级名与版本（免鉴权）。 */
  fetchMeta(): Promise<MetaInfo> {
    return request<MetaInfo>("/meta");
  },

  // -- 原片 ----------------------------------------------------------------
  fetchPhotoBlob(photoId: number): Promise<Blob> {
    return requestBlob(`/photos/${photoId}/file`);
  },

  // -- 成册导出 / 投屏 ----------------------------------------------------
  listTemplates(): Promise<TemplateInfo[]> {
    return request<TemplateInfo[]>("/exports/templates");
  },

  /** 渲染整册 HTML（iframe 预览用，与 PDF 同一套模板）。 */
  fetchExportPreview(
    issueId: number,
    options: { template: string; order: string },
  ): Promise<string> {
    const query = new URLSearchParams({ template: options.template, order: options.order });
    return requestText(`/exports/${issueId}/preview?${query.toString()}`);
  },

  /** 投屏数据（逐篇 name/title/paragraphs）。 */
  fetchPresent(issueId: number, order = "student_no"): Promise<PresentData> {
    const query = new URLSearchParams({ order });
    return request<PresentData>(`/exports/${issueId}/present?${query.toString()}`);
  },

  /** 导出整册 PDF。 */
  exportBook(issueId: number, options: { template: string; order: string }): Promise<Blob> {
    return requestBlob(`/exports/${issueId}`, {
      method: "POST",
      body: JSON.stringify(options),
    });
  },

  /** 导出单篇版式 PDF。 */
  exportSingle(issueId: number, essayId: number, template: string): Promise<Blob> {
    const query = new URLSearchParams({ template });
    return requestBlob(`/exports/${issueId}/single/${essayId}?${query.toString()}`, {
      method: "POST",
    });
  },
};

export { request };
