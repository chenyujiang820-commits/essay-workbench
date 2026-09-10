import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, clearToken } from "./client";

function fakeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("api client (T03 endpoints)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    clearToken();
  });

  it("parses the students list envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        fakeResponse({
          code: 0,
          data: [{ id: 1, student_no: "S001", name: "张三", active: 1 }],
          message: "",
        }),
      ),
    );

    const students = await api.listStudents();
    expect(students).toHaveLength(1);
    expect(students[0].name).toBe("张三");
  });

  it("builds a multipart body with student_id + N files", async () => {
    const fetchMock = vi.fn(async () =>
      fakeResponse({
        code: 0,
        data: { essay_id: 3, status: "uploaded", photo_count: 2, task: null },
        message: "",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const first = new File([new Uint8Array([1])], "a.jpg", { type: "image/jpeg" });
    const second = new File([new Uint8Array([2])], "b.jpg", { type: "image/jpeg" });
    await api.uploadEssay(1, 7, [first, second]);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/issues/1/essays");
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("student_id")).toBe("7");
    expect(body.getAll("files")).toHaveLength(2);
  });

  it("sends a JSON PATCH when saving a proofread essay", async () => {
    const fetchMock = vi.fn(async () => fakeResponse({ code: 0, data: {}, message: "" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.updateEssay(3, { final_text: "春天", proofread: true });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/essays/3");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ final_text: "春天", proofread: true });
  });

  it("returns a blob for the photo file endpoint", async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, blob: async () => blob }) as unknown as Response),
    );

    const result = await api.fetchPhotoBlob(11);
    expect(result).toBe(blob);
  });

  it("throws ApiError when the photo file is missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: false,
            status: 404,
            json: async () => ({ code: 404, data: null, message: "照片不存在" }),
          }) as unknown as Response,
      ),
    );

    await expect(api.fetchPhotoBlob(11)).rejects.toBeInstanceOf(ApiError);
    await expect(api.fetchPhotoBlob(11)).rejects.toMatchObject({ code: 404, status: 404 });
  });
});
