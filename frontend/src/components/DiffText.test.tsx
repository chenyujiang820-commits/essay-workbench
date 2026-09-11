import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DiffSegment, Photo } from "../api/types";
import DiffText, { composeInitialText, describeSegment } from "./DiffText";

function photo(seq: number, diff: DiffSegment[] | null, engine1: string | null = null): Photo {
  return {
    id: seq,
    seq,
    file_path: `photos/${seq}.jpg`,
    width: null,
    height: null,
    engine1_text: engine1,
    engine2_text: null,
    diff_json: diff,
  };
}

describe("describeSegment", () => {
  it("keeps equal segments plain", () => {
    const view = describeSegment({ type: "equal", text_a: "春天", text_b: "春天" }, 0);
    expect(view.suspect).toBe(false);
    expect(view.variant).toBe("plain");
    expect(view.text).toBe("春天");
  });

  it("marks replace/delete/insert as suspect", () => {
    expect(describeSegment({ type: "replace", text_a: "校园", text_b: "学校" }, 0).suspect).toBe(true);
    expect(describeSegment({ type: "delete", text_a: "的", text_b: "" }, 0).suspect).toBe(true);
    expect(describeSegment({ type: "insert", text_a: "", text_b: "很" }, 0).suspect).toBe(true);
  });

  it("puts the engine2 candidate (text_b) into the title for replace", () => {
    const view = describeSegment({ type: "replace", text_a: "校园", text_b: "学校" }, 0);
    expect(view.text).toBe("校园");
    expect(view.title).toContain("学校");
  });

  it("shows the engine2 candidate for insert (text_a is empty)", () => {
    const view = describeSegment({ type: "insert", text_a: "", text_b: "很" }, 0);
    expect(view.text).toBe("很");
    expect(view.variant).toBe("suspect-b");
  });
});

describe("composeInitialText", () => {
  it("joins text_a across photos, falling back to engine1 text", () => {
    const photos = [
      photo(1, [{ type: "equal", text_a: "春天", text_b: "春天" }]),
      photo(2, null, "校园"),
    ];
    expect(composeInitialText(photos)).toBe("春天\n校园");
  });
});

describe("DiffText rendering", () => {
  it("highlights suspect segments and selects the owning photo on click", () => {
    const onSelectPhoto = vi.fn();
    const photos = [
      photo(1, [{ type: "equal", text_a: "春天", text_b: "春天" }]),
      photo(2, [{ type: "replace", text_a: "校园", text_b: "学校" }]),
    ];

    render(
      <DiffText photos={photos} value="春天校园" onChange={() => undefined} onSelectPhoto={onSelectPhoto} />,
    );

    const suspect = screen.getByText("校园");
    expect(suspect.className).toContain("diff-suspect");
    expect(suspect.getAttribute("title")).toContain("学校");
    expect(suspect.getAttribute("data-photo-seq")).toBe("2");

    fireEvent.click(suspect);
    expect(onSelectPhoto).toHaveBeenCalledWith(2);
  });

  it("renders a controlled textarea bound to value/onChange", () => {
    const onChange = vi.fn();
    render(<DiffText photos={[photo(1, null, "原文")]} value="原文" onChange={onChange} />);

    const textarea = screen.getByTestId("final-text") as HTMLTextAreaElement;
    expect(textarea.value).toBe("原文");
    fireEvent.change(textarea, { target: { value: "新文" } });
    expect(onChange).toHaveBeenCalledWith("新文");
  });

  it("places the final-text editor above the diff reference section", () => {
    const photos = [photo(1, [{ type: "replace", text_a: "校园", text_b: "学校" }])];
    const { container } = render(
      <DiffText photos={photos} value="校园" onChange={() => undefined} />,
    );

    const editor = container.querySelector('[data-testid="final-text"]');
    const diff = container.querySelector('[data-testid="diff-annotated"]');
    expect(editor).toBeTruthy();
    expect(diff).toBeTruthy();
    // diff 区必须位于定稿编辑区之后（DOM 前序关系断言，与样式无关）。
    const position = diff!.compareDocumentPosition(editor!);
    expect(position & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });
});
