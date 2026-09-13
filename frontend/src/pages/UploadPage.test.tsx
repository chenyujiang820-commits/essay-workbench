import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import UploadPage from "./UploadPage";

vi.mock("../lib/image", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/image")>();
  return {
    ...actual,
    // 默认：压缩产物即入参、尺寸未知（不触发画质提醒）
    processImages: vi.fn(
      async (files: File[]) => files.map((file) => ({ file, width: 0, height: 0 })),
    ),
  };
});

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getIssue: vi.fn(),
      listStudents: vi.fn(),
      uploadEssay: vi.fn(),
    },
  };
});

function renderUpload() {
  return render(
    <MemoryRouter initialEntries={["/issues/1/upload"]}>
      <Routes>
        <Route path="/issues/:issueId/upload" element={<UploadPage />} />
        <Route path="/issues/:issueId/essays" element={<div>看板页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("UploadPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:thumb"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
    vi.mocked(api.getIssue).mockResolvedValue({
      id: 1,
      issue_no: 1,
      week_start_date: "2026-09-07",
      created_at: "2026-09-07T00:00:00+00:00",
      essay_count: 0,
    });
    vi.mocked(api.listStudents).mockResolvedValue([
      { id: 7, student_no: "S001", name: "张三", active: 1 },
    ]);
  });

  it("exposes a mobile image input accepting multiple images without forcing the camera", async () => {
    renderUpload();
    const input = (await screen.findByTestId("photo-input")) as HTMLInputElement;

    expect(input.getAttribute("accept")).toBe("image/*");
    // 不再强制调起相机：老师也可从相册选择已拍好的照片
    expect(input.getAttribute("capture")).toBeNull();
    expect(input.multiple).toBe(true);
    expect(input.getAttribute("type")).toBe("file");
  });

  it("shows an error when image compression fails instead of failing silently", async () => {
    const { processImages } = await import("../lib/image");
    vi.mocked(processImages).mockRejectedValueOnce(new Error("decode failed"));
    renderUpload();

    fireEvent.change(await screen.findByTestId("photo-input"), {
      target: { files: [new File([new Uint8Array([1])], "a.jpg", { type: "image/jpeg" })] },
    });

    await waitFor(() => expect(screen.getByText("图片处理失败，请重试或换一张照片")).toBeTruthy());
  });

  it("submits all selected images as a single essay payload", async () => {
    vi.mocked(api.uploadEssay).mockResolvedValue({
      essay_id: 3,
      status: "uploaded",
      photo_count: 2,
      task: null,
    });
    renderUpload();

    await screen.findByRole("option", { name: /张三/ });
    fireEvent.change(screen.getByLabelText("学生"), { target: { value: "7" } });

    const first = new File([new Uint8Array([1])], "a.jpg", { type: "image/jpeg" });
    const second = new File([new Uint8Array([2])], "b.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByTestId("photo-input"), {
      target: { files: [first, second] },
    });

    await waitFor(() => expect(screen.getByText(/已选 2 张/)).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "上传并识别" }));

    await waitFor(() => expect(api.uploadEssay).toHaveBeenCalledTimes(1));
    expect(api.uploadEssay).toHaveBeenCalledWith(1, 7, [first, second]);
    await waitFor(() => expect(screen.getByText(/已上传识别中/)).toBeTruthy());
  });

  it("shows thumbnails and allows removing a single photo", async () => {
    renderUpload();

    await screen.findByRole("option", { name: /张三/ });
    fireEvent.change(screen.getByTestId("photo-input"), {
      target: { files: [new File([new Uint8Array([1])], "a.jpg", { type: "image/jpeg" })] },
    });
    fireEvent.change(screen.getByTestId("photo-input"), {
      target: { files: [new File([new Uint8Array([2])], "b.jpg", { type: "image/jpeg" })] },
    });

    await waitFor(() => expect(screen.getByText("已选 2 张：")).toBeTruthy());
    expect(screen.getAllByTestId("photo-input")).toBeTruthy();
    const thumbs = screen.getAllByRole("img", { name: /已选第/ });
    expect(thumbs.length).toBe(2);

    fireEvent.click(screen.getByRole("button", { name: "删除第 1 张" }));
    await waitFor(() => expect(screen.getByText("已选 1 张：")).toBeTruthy());
    expect(screen.getAllByRole("img", { name: /已选第/ }).length).toBe(1);
  });

  it("blocks submission until a student is chosen", async () => {
    renderUpload();
    await screen.findByRole("option", { name: /张三/ });

    fireEvent.change(screen.getByTestId("photo-input"), {
      target: { files: [new File([new Uint8Array([1])], "a.jpg", { type: "image/jpeg" })] },
    });
    await waitFor(() => expect(screen.getByText(/已选 1 张/)).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "上传并识别" }));

    await waitFor(() => expect(screen.getByText("请先选择学生")).toBeTruthy());
    expect(api.uploadEssay).not.toHaveBeenCalled();
  });

  it("renders the capture guide with all four shooting tips", async () => {
    renderUpload();
    const guide = await screen.findByTestId("capture-guide");
    expect(guide.textContent).toMatch(/光线/);
    expect(guide.textContent).toMatch(/垂直/);
    expect(guide.textContent).toMatch(/铺满/);
    expect(guide.textContent).toMatch(/600/);
    expect(guide.textContent).toMatch(/800/);
  });

  it("flags a low-resolution pick with a badge, keeps it deletable and still submittable", async () => {
    const small = new File([new Uint8Array([1])], "IMG_small.jpg", { type: "image/jpeg" });
    const { processImages } = await import("../lib/image");
    // 原图 500x400：短边 400 < 600 → 画质偏低
    vi.mocked(processImages).mockResolvedValue([
      { file: small, width: 500, height: 400 },
    ]);

    renderUpload();
    await screen.findByTestId("photo-input");

    const input = screen.getByTestId("photo-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [small] } });

    const badge = await screen.findByTestId("low-res-badge");
    expect(badge.textContent).toBe("画质偏低");
    expect(screen.getByText(/画质偏低，识别准确率会下降/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "删除第 1 张" }));
    await waitFor(() => expect(screen.queryByTestId("low-res-badge")).toBeNull());

    fireEvent.change(input, { target: { files: [small] } });
    await screen.findByTestId("low-res-badge");

    fireEvent.change(screen.getByLabelText("学生"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "上传并识别" }));
    await waitFor(() => expect(api.uploadEssay).toHaveBeenCalledTimes(1));
    // 提醒不阻断：低画质照片照常提交
    expect((api.uploadEssay as ReturnType<typeof vi.fn>).mock.calls[0][2]).toHaveLength(1);
  });

  it("warns about mobile camera formats the backend rejects and skips them", async () => {
    renderUpload();
    await screen.findByTestId("photo-input");

    fireEvent.change(screen.getByTestId("photo-input"), {
      target: {
        files: [new File([new Uint8Array([1])], "IMG_0007.HEIC", { type: "image/heic" })],
      },
    });

    const warning = await screen.findByTestId("format-warning");
    expect(warning.textContent).toMatch(/IMG_0007\.HEIC/);
    // 提示必须给可执行的改法，不能只说「格式不支持」
    expect(warning.textContent).toMatch(/兼容性/);
    // 被挑出的照片不进入待上传列表：免得提交后才收到看不懂的 400
    expect(screen.queryByText(/已选 \d+ 张/)).toBeNull();
  });

  it("keeps the acceptable photos of a mixed mobile batch", async () => {
    renderUpload();
    await screen.findByTestId("photo-input");

    fireEvent.change(screen.getByTestId("photo-input"), {
      target: {
        files: [
          new File([new Uint8Array([1])], "IMG_0008.HEIC", { type: "image/heic" }),
          new File([new Uint8Array([2])], "IMG_0009.jpg", { type: "image/jpeg" }),
        ],
      },
    });

    await screen.findByTestId("format-warning");
    await waitFor(() => expect(screen.getByText(/已选 1 张/)).toBeTruthy());
  });

  it("renders a selectable student grid", async () => {
    vi.mocked(api.listStudents).mockResolvedValue([
      { id: 7, student_no: "S001", name: "张三", active: 1 },
      { id: 8, student_no: "S002", name: "李四", active: 1 },
    ]);
    renderUpload();
    await screen.findByTestId("student-grid");

    fireEvent.click(screen.getByLabelText("选择张三"));
    fireEvent.click(screen.getByLabelText("选择李四"));
    expect((screen.getByLabelText("选择张三") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("upload-queue").textContent).toContain("已选 2 人");
  });
});
