import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { EssayDetail } from "../api/types";
import ProofreadPage from "./ProofreadPage";

// 隔离 react-zoom-pan-pinch（其依赖 jsdom 不支持的 ResizeObserver 测量）。
vi.mock("../components/PhotoViewer", () => ({
  default: () => <div data-testid="photo-viewer" />,
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getEssay: vi.fn(),
      updateEssay: vi.fn(),
      fetchPhotoBlob: vi.fn(),
    },
  };
});

const detail: EssayDetail = {
  id: 3,
  issue_id: 1,
  student_id: 7,
  student_name: "张三",
  title: "",
  status: "review",
  low_confidence: 1,
  created_at: "2026-09-07T00:00:00+00:00",
  proofread_at: null,
  final_text: "",
  teacher_comment: null,
  score: null,
  selected: 0,
  photos: [
    {
      id: 11,
      seq: 1,
      file_path: "photos/1.jpg",
      width: null,
      height: null,
      engine1_text: "春天",
      engine2_text: "春天",
      diff_json: [{ type: "equal", text_a: "春天", text_b: "春天" }],
    },
    {
      id: 12,
      seq: 2,
      file_path: "photos/2.jpg",
      width: null,
      height: null,
      engine1_text: "校园",
      engine2_text: "学校",
      diff_json: [{ type: "replace", text_a: "校园", text_b: "学校" }],
    },
  ],
  task: null,
};

function renderProofread() {
  return render(
    <MemoryRouter initialEntries={["/essays/3/proofread"]}>
      <Routes>
        <Route path="/essays/:essayId/proofread" element={<ProofreadPage />} />
        <Route path="/issues/:issueId/essays" element={<div>看板页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProofreadPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:mock"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
    vi.mocked(api.fetchPhotoBlob).mockResolvedValue(
      new Blob([new Uint8Array([1])], { type: "image/jpeg" }),
    );
    vi.mocked(api.getEssay).mockResolvedValue(detail);
  });

  it("shows the low-confidence banner and seeds the final text from diff", async () => {
    renderProofread();
    await screen.findByTestId("final-text");

    expect(screen.getByText(/本篇识别置信度偏低/)).toBeTruthy();
    const textarea = screen.getByTestId("final-text") as HTMLTextAreaElement;
    expect(textarea.value).toBe("春天\n校园");
  });

  it("switches the highlighted photo when a suspect segment is clicked", async () => {
    renderProofread();
    await screen.findByText("校园");

    const secondTab = screen.getByRole("button", { name: "第 2 张" });
    expect(secondTab.className).not.toContain("bg-slate-900");

    fireEvent.click(screen.getByText("校园"));
    await waitFor(() => expect(secondTab.className).toContain("bg-slate-900"));
  });

  it("asks for confirmation before leaving with unsaved changes", async () => {
    renderProofread();
    const textarea = (await screen.findByTestId("final-text")) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "改过的定稿文字" } });
    await waitFor(() => expect(screen.getByText("未保存")).toBeTruthy());

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "返回看板" }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("看板页")).toBeNull();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "返回看板" }));
    await waitFor(() => expect(screen.getByText("看板页")).toBeTruthy());

    confirmSpy.mockRestore();
  });

  it("saves the proofread text and returns to the board", async () => {
    vi.mocked(api.updateEssay).mockResolvedValue({ ...detail, status: "proofread" });
    renderProofread();
    const textarea = (await screen.findByTestId("final-text")) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "最终定稿" } });

    fireEvent.click(screen.getByRole("button", { name: "保存并定稿" }));

    await waitFor(() =>
      expect(api.updateEssay).toHaveBeenCalledWith(3, {
        final_text: "最终定稿",
        proofread: true,
      }),
    );
    await waitFor(() => expect(screen.getByText("看板页")).toBeTruthy());
  });
});
