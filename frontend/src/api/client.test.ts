import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, clearToken, writeToken } from "./client";

function fakeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    clearToken();
  });

  it("returns data on success envelope", async () => {
    const fetchMock = vi.fn(async () =>
      fakeResponse({ code: 0, data: { token: "t", token_type: "bearer", expires_in: 100 }, message: "" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.login("pw");
    expect(result.token).toBe("t");

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Headers).get("Content-Type")).toBe("application/json");
  });

  it("throws ApiError on error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => fakeResponse({ code: 401, data: null, message: "口令错误" }, 401)),
    );

    await expect(api.login("bad")).rejects.toBeInstanceOf(ApiError);
    await expect(api.login("bad")).rejects.toMatchObject({ code: 401, status: 401 });
  });

  it("injects bearer token when present", async () => {
    writeToken("abc123");
    const fetchMock = vi.fn(async () =>
      fakeResponse({ code: 0, data: { token: "t", token_type: "bearer", expires_in: 1 }, message: "" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.login("pw");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Headers).get("Authorization")).toBe("Bearer abc123");
  });

  it("rejects a successful response without a valid envelope", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(null)));

    await expect(api.login("pw")).rejects.toMatchObject({
      code: 200,
      status: 200,
      message: "服务器返回了无效响应",
    });
  });

  it("rejects an envelope with missing data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => fakeResponse({ code: 0, message: "", data: null })),
    );

    await expect(api.login("pw")).rejects.toMatchObject({
      code: 200,
      status: 200,
      message: "服务器返回了无效响应",
    });
  });

  it("never surfaces a zero error code when the server sends code 0 with a failure status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => fakeResponse({ code: 0, data: null, message: "后端异常" }, 500)),
    );

    const error = await api.login("pw").catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).not.toBe(0);
    expect((error as ApiError).code).toBe(500);
    expect((error as ApiError).message).toBe("后端异常");
  });

  it("falls back to HTTP status when the failure envelope omits code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({ data: null }, 404)));

    const error = await api.getEssay(1).catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe(404);
  });
});
