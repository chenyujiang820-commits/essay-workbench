import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DiffSegment, Photo } from "../api/types";
import DiffText, {
  composeInitialText,
  describeSegment,
  listSuspectKeys,
  splitSentences,
  suspectKey,
} from "./DiffText";

function photo(seq: number, diff: DiffSegment[] | null, engine1: string | null = null): Photo {
  return {
    id: seq,
    seq,
    file_path: `photos/${seq}.jpg`,
    width: null,
    height: null,
    low_resolution: 0,
    engine1_text: engine1,
    engine2_text: null,
    diff_json: diff,
  };
}

/** 取对照面板里的某个存疑 span（textarea 里也有正文文本，故不用 getByText）。 */
function suspectSpan(key: string): HTMLElement {
  const span = document.querySelector<HTMLElement>(`[data-suspect-key="${key}"]`);
  expect(span).toBeTruthy();
  return span as HTMLElement;
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

describe("存疑处 key 清单（FR-09 存疑清点）", () => {
  it("lists every suspect across photos in reading order", () => {
    const photos = [
      photo(1, [
        { type: "equal", text_a: "春天", text_b: "春天" },
        { type: "replace", text_a: "校园", text_b: "学校" },
      ]),
      photo(2, [{ type: "insert", text_a: "", text_b: "很" }]),
      photo(3, null, "只有主引擎文本"),
    ];
    expect(listSuspectKeys(photos)).toEqual([suspectKey(1, 1), suspectKey(2, 0)]);
    expect(listSuspectKeys(photos)).toEqual(["1:1", "2:0"]);
  });

  it("returns an empty list when nothing is suspect", () => {
    expect(listSuspectKeys([photo(1, [{ type: "equal", text_a: "春", text_b: "春" }])])).toEqual([]);
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

  it("defaults the comparison panel to expanded", () => {
    render(
      <DiffText
        photos={[photo(1, [{ type: "replace", text_a: "校园", text_b: "学校" }])]}
        value=""
        onChange={() => undefined}
      />,
    );
    expect((screen.getByTestId("diff-details") as HTMLDetailsElement).open).toBe(true);
  });

  it("reports the suspect key when a suspect is clicked and when Enter is pressed", () => {
    const onSuspectView = vi.fn();
    render(
      <DiffText
        photos={[photo(2, [{ type: "replace", text_a: "校园", text_b: "学校" }])]}
        value="请以原片为准"
        onChange={() => undefined}
        onSuspectView={onSuspectView}
      />,
    );

    const suspect = suspectSpan("2:0");
    expect(suspect.getAttribute("data-suspect-key")).toBe("2:0");
    expect(suspect.hasAttribute("data-viewed")).toBe(false);

    fireEvent.click(suspect);
    expect(onSuspectView).toHaveBeenCalledWith("2:0");
    fireEvent.keyDown(suspect, { key: "Enter" });
    expect(onSuspectView).toHaveBeenCalledTimes(2);
  });

  it("shows viewed suspects with an outline instead of the highlight, still clickable", () => {
    const onSuspectView = vi.fn();
    render(
      <DiffText
        photos={[photo(2, [{ type: "replace", text_a: "校园", text_b: "学校" }])]}
        value="请以原片为准"
        onChange={() => undefined}
        viewedSuspects={new Set(["2:0"])}
        onSuspectView={onSuspectView}
      />,
    );

    const suspect = suspectSpan("2:0");
    expect(suspect.getAttribute("data-viewed")).toBe("1");
    expect(suspect.className).not.toContain("diff-suspect");
    expect(suspect.className).toContain("ring-1");
    fireEvent.click(suspect);
    expect(onSuspectView).toHaveBeenCalledWith("2:0");
  });

  it("counts suspects and viewed suspects in the panel header", () => {
    const photos = [
      photo(1, [{ type: "delete", text_a: "的", text_b: "" }]),
      photo(2, [{ type: "replace", text_a: "校园", text_b: "学校" }]),
    ];
    const { rerender } = render(
      <DiffText photos={photos} value="的校园" onChange={() => undefined} />,
    );
    expect(screen.getByTestId("suspect-counter").textContent).toBe("存疑 2 处 · 已查看 0 处");

    rerender(
      <DiffText
        photos={photos}
        value="的校园"
        onChange={() => undefined}
        viewedSuspects={new Set(["1:0", "2:0"])}
      />,
    );
    expect(screen.getByTestId("suspect-counter").textContent).toBe("存疑 2 处 · 已查看 2 处");
  });
});

describe("句级切分（OPT-01 句级跳转）", () => {
  it("在中文句末标点断句，标点归属前一句", () => {
    expect(splitSentences("春天来了。校园也热闹了！")).toEqual(["春天来了。", "校园也热闹了！"]);
  });

  it("句末之后紧跟的右引号仍属于本句", () => {
    expect(splitSentences("他说：“你好。”她点点头。")).toEqual(["他说：“你好。”", "她点点头。"]);
  });

  it("无标点、空串与纯空白都保持一格（插入候选必须仍可点）", () => {
    expect(splitSentences("没有标点的长句")).toEqual(["没有标点的长句"]);
    expect(splitSentences("")).toEqual([""]);
    expect(splitSentences("   ")).toEqual(["   "]);
  });

  it("尾随空白挂靠最后一句，不产生空白存疑单元", () => {
    expect(splitSentences("第一句。第二句。 ")).toEqual(["第一句。", "第二句。 "]);
  });

  it("一个跨句存疑段落会展开成多个 key", () => {
    const photos = [
      photo(2, [{ type: "replace", text_a: "春天来了。校园也热闹了！", text_b: "春天到了。学校也热闹了！" }]),
    ];
    expect(listSuspectKeys(photos)).toEqual([suspectKey(2, 0), suspectKey(2, 0, 1)]);
    expect(listSuspectKeys(photos)).toEqual(["2:0", "2:0:1"]);
  });

  it("每句各自成格、各自可点，并按句计数", () => {
    const onSuspectView = vi.fn();
    const photos = [photo(2, [{ type: "replace", text_a: "春天来了。校园也热闹了！", text_b: "学校" }])];
    render(
      <DiffText photos={photos} value="" onChange={() => undefined} onSuspectView={onSuspectView} />,
    );

    expect(suspectSpan("2:0").textContent).toBe("春天来了。");
    expect(suspectSpan("2:0:1").textContent).toBe("校园也热闹了！");
    fireEvent.click(suspectSpan("2:0:1"));
    expect(onSuspectView).toHaveBeenCalledWith("2:0:1");
    expect(screen.getByTestId("suspect-counter").textContent).toBe("存疑 2 处 · 已查看 0 处");
  });
});
