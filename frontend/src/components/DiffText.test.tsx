import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DiffSegment, Photo } from "../api/types";
import DiffText, {
  buildOcrCompare,
  composeInitialText,
  describeSegment,
  listFallbackSuspectKeys,
  listSuspectKeys,
  ocrSuspectKey,
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

  it("lets the comparison editor update the same controlled draft", () => {
    const onChange = vi.fn();
    render(
      <DiffText
        photos={[photo(1, [{ type: "replace", text_a: "原文", text_b: "建议" }])]}
        value="原文"
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByTestId("comparison-editor"), { target: { value: "修改后的稿子" } });
    expect(onChange).toHaveBeenCalledWith("修改后的稿子");
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
    expect(screen.getByTestId("diff-toggle").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("diff-annotated").hasAttribute("hidden")).toBe(false);
  });

  it("collapses the comparison panel from its own toggle instead of a details summary", () => {
    render(
      <DiffText
        photos={[photo(1, [{ type: "replace", text_a: "校园", text_b: "学校" }])]}
        value=""
        onChange={() => undefined}
      />,
    );
    const toggle = screen.getByTestId("diff-toggle");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("diff-annotated").hasAttribute("hidden")).toBe(true);
    expect(screen.getByTestId("diff-details").className).toContain("min-h-0");
    fireEvent.click(toggle);
    expect(screen.getByTestId("diff-annotated").hasAttribute("hidden")).toBe(false);
  });

  it("tells the teacher how much reference content is inside the scrollable panel", () => {
    render(
      <DiffText
        photos={[photo(1, [{ type: "replace", text_a: "校园", text_b: "学校" }])]}
        value=""
        onChange={() => undefined}
      />,
    );
    expect(screen.getByTestId("compare-count").textContent).toContain("共 1 段");
    const focus = screen.getByTestId("diff-focus");
    expect(focus.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(focus);
    expect(focus.getAttribute("aria-pressed")).toBe("true");
    expect(focus.textContent).toBe("还原");
    expect(screen.getByTestId("diff-details").getAttribute("style")).toContain("position: fixed");
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

describe("识别原文对照（全篇无 diff 时的 fallback 模式）", () => {
  /** 取 fallback 存疑单元：与既有 diff 存疑处共用 data-suspect-key 选择器。 */
  function ocrSpan(key: string): HTMLElement {
    const span = document.querySelector<HTMLElement>(`[data-suspect-key="${key}"]`);
    expect(span).toBeTruthy();
    return span as HTMLElement;
  }

  it("渲染识别原文对照，而不是「暂无 diff 数据」空壳", () => {
    const photos = [photo(1, null, "三圈了，之后【?】胡老师他们跑完了")];
    render(
      <DiffText photos={photos} value="三圈了，之后胡老师他们跑完了" onChange={() => undefined} />,
    );

    const box = screen.getByTestId("ocr-compare");
    expect(box.textContent).toContain("三圈了，之后");
    expect(box.textContent).toContain("胡老师他们跑完了");
    expect(box.textContent).not.toContain("暂无 diff 数据");
    expect(screen.getByTestId("diff-annotated").contains(box)).toBe(true);
  });

  it("按照片 seq 取原文，engine1_text 为空的照片跳过", () => {
    const photos = [
      photo(3, null, null),
      photo(1, null, "第一张的原文。"),
      photo(2, [], "第二张的原文。"),
    ];
    render(<DiffText photos={photos} value="" onChange={() => undefined} />);

    expect(screen.queryByTestId("ocr-line-3")).toBeNull();
    const lines = screen.getAllByTestId(/ocr-line-/);
    expect(lines.map((line) => line.getAttribute("data-testid"))).toEqual([
      "ocr-line-1",
      "ocr-line-2",
    ]);
    expect(ocrSuspectKey(1, 0)).toBe("1:ocr:0");
  });

  it("未识别占位符标成「未识别」存疑单元，点击定位原片", () => {
    const onSelectPhoto = vi.fn();
    const onSuspectView = vi.fn();
    const photos = [photo(1, null, "三圈了，之后【?】胡老师他们跑完了")];
    render(
      <DiffText
        photos={photos}
        value="三圈了，之后胡老师他们跑完了"
        onChange={() => undefined}
        onSelectPhoto={onSelectPhoto}
        onSuspectView={onSuspectView}
      />,
    );

    const mark = ocrSpan("1:ocr:0");
    expect(mark.textContent).toBe("【?】");
    expect(mark.getAttribute("data-suspect")).toBe("ocr-unknown");
    expect(mark.getAttribute("data-photo-seq")).toBe("1");
    expect(mark.getAttribute("title")).toContain("未能识别");
    expect(mark.className).toContain("text-amber-800");
    expect(mark.className).not.toContain("diff-suspect");

    fireEvent.click(mark);
    expect(onSelectPhoto).toHaveBeenCalledWith(1);
    expect(onSuspectView).toHaveBeenCalledWith("1:ocr:0");
    expect(screen.getByTestId("suspect-counter").textContent).toBe("存疑 1 处 · 已查看 0 处");
  });

  it("回车与点击等价，已查看后保留可点击的描边态", () => {
    const onSelectPhoto = vi.fn();
    const photos = [photo(2, null, "他【?】说你好。")];
    render(
      <DiffText
        photos={photos}
        value="他说你好。"
        onChange={() => undefined}
        onSelectPhoto={onSelectPhoto}
        viewedSuspects={new Set(["2:ocr:0"])}
      />,
    );

    const mark = ocrSpan("2:ocr:0");
    expect(mark.getAttribute("data-viewed")).toBe("1");
    expect(mark.className).toContain("ring-1");
    expect(mark.getAttribute("title")).toContain("已查看");
    fireEvent.keyDown(mark, { key: "Enter" });
    expect(onSelectPhoto).toHaveBeenCalledWith(2);
    expect(screen.getByTestId("suspect-counter").textContent).toBe("存疑 1 处 · 已查看 1 处");
  });

  it("覆盖 ? ？ □ ▯ 【?】 等未识别写法", () => {
    const text = "一?二。三？四。五□六。七▯八。九【?】十。";
    const photos = [photo(1, null, text)];
    const unknowns = buildOcrCompare(photos, text)
      .flatMap((line) => line.parts)
      .filter((part) => part.kind === "unknown")
      .map((part) => part.text);
    expect(unknowns).toEqual(["?", "？", "□", "▯", "【?】"]);
    expect(listFallbackSuspectKeys(photos, text)).toEqual(["1:ocr:0", "1:ocr:1", "1:ocr:2", "1:ocr:3", "1:ocr:4"]);

    // 〔?〕在断句后括号会被分到下一句，但其中的 ? 仍然要标出来。
    const bracket = [photo(1, null, "他说〔?〕好了。")];
    expect(listFallbackSuspectKeys(bracket, "他说〔?〕好了。").length).toBe(1);
  });

  it("老师从定稿里删掉的句子标成「已修改/删减」", () => {
    const photos = [photo(1, null, "春天来了。校园也热闹了！")];
    render(<DiffText photos={photos} value="春天来了。" onChange={() => undefined} />);

    const changed = ocrSpan("1:ocr:0");
    expect(changed.textContent).toBe("校园也热闹了！");
    expect(changed.getAttribute("data-suspect")).toBe("ocr-changed");
    expect(changed.getAttribute("title")).toContain("定稿");
    expect(changed.className).toContain("text-rose-700");
    expect(screen.getByTestId("ocr-compare").textContent).toContain("春天来了。");
  });

  it("比对忽略空白差异；纯标点碎句不算改动句", () => {
    expect(listFallbackSuspectKeys([photo(1, null, "春天 来了。校园\t也热闹了！")], "春天来了。校园也热闹了！")).toEqual([]);
    // 「。」这类断句碎块只含标点，不该因为不在定稿里就被标红；
    // 含未识别字的句子只标占位符本身，真正被改动的句子另算一个点，两者不重复计数。
    const fragmentPhoto = [photo(1, null, "春天【?】。校园很美丽！")];
    expect(buildOcrCompare(fragmentPhoto, "春天。")[0].parts.map((part) => [part.text, part.kind])).toEqual([
      ["春天", null],
      ["【?】", "unknown"],
      ["。", null],
      ["校园很美丽！", "changed"],
    ]);
    expect(listFallbackSuspectKeys(fragmentPhoto, "春天。")).toEqual(["1:ocr:0", "1:ocr:1"]);
  });

  it("整篇一致时只给一行轻量说明，徽标显示无存疑", () => {
    const photos = [photo(1, null, "春天来了。校园也热闹了！")];
    render(<DiffText photos={photos} value="春天来了。校园也热闹了！" onChange={() => undefined} />);

    expect(screen.getByTestId("ocr-compare-clean").textContent).toContain("识别原文与定稿一致");
    expect(screen.queryByTestId("ocr-compare")).toBeNull();
    expect(document.querySelectorAll("[data-suspect-key]").length).toBe(0);
    expect(screen.getByTestId("suspect-counter").textContent).toBe("无存疑");
  });

  it("既无原文也无 diff 时仍给一行提示，不画空框", () => {
    render(<DiffText photos={[photo(1, null, null)]} value="" onChange={() => undefined} />);

    expect(screen.getByTestId("diff-annotated").textContent).toContain("暂无 diff 数据");
    expect(screen.queryByTestId("ocr-compare")).toBeNull();
    expect(screen.queryByTestId("ocr-compare-clean")).toBeNull();
  });

  it("徽标 N 与 listFallbackSuspectKeys 同源", () => {
    const photos = [
      photo(1, null, "春天【?】。校园很美丽！"),
      photo(2, null, "我们一起跑步。"),
    ];
    const value = "春天。校园很美！";
    const expected = listFallbackSuspectKeys(photos, value);
    render(<DiffText photos={photos} value={value} onChange={() => undefined} />);

    expect(expected).toEqual(["1:ocr:0", "1:ocr:1", "2:ocr:0"]);
    expect(Array.from(document.querySelectorAll<HTMLElement>("[data-suspect-key]")).map((node) => node.dataset.suspectKey)).toEqual(expected);
    expect(screen.getByTestId("suspect-counter").textContent).toBe(`存疑 ${expected.length} 处 · 已查看 0 处`);
  });

  it("fallback 不进入 listSuspectKeys（定稿前确认框口径不变）", () => {
    const photos = [
      photo(1, null, "春天【?】。校园很美丽！"),
      photo(2, null, "我们一起跑步。"),
    ];
    expect(listSuspectKeys(photos)).toEqual([]);
    const keys = listFallbackSuspectKeys(photos, "");
    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) {
      expect(key).toMatch(/:ocr:\d+$/);
      expect(suspectKey(1, 0)).not.toBe(key);
    }
    // 有 diff 时仍走 diff 路径，fallback 完全不参与计数。
    expect(listSuspectKeys([photo(1, [{ type: "replace", text_a: "校园", text_b: "学校" }], "校园")])).toEqual(["1:0"]);
  });

  // ------------------------------------------------- GAP-13（真机反馈：框画得太大、太乱）
  // 真机 essay 1 原样：三行页眉后面紧跟一个跨行的长句，中间没有句号。
  // 只按句末标点切会把「页眉 + 长句」并成一个单元，老师删掉页眉后整句被连坐标红。
  const realHeaderPhoto = [
    photo(
      1,
      null,
      "逸云手写\n“致最美逆行者”\n作文题目：《岂曰无衣，与子同袍》\n在2020年的年初，一场疫情席卷了神州大地，\n病毒肆虐横行，掩盖了新桃符的浓浓年味，\n蚕食了春日里的盎然生机。\n逸云手写",
    ),
  ];
  const realHeaderFinal =
    "在2020年的年初，一场疫情席卷了神州大地，\n病毒肆虐横行，掩盖了新桃符的浓浓年味，\n蚕食了春日里的盎然生机。";

  it("页眉与正文长句不再并成一个框：每个框只覆盖一行", () => {
    const parts = buildOcrCompare(realHeaderPhoto, realHeaderFinal)
      .flatMap((line) => line.parts)
      .filter((part) => part.key !== null);

    expect(parts.map((part) => part.text)).toEqual([
      "逸云手写",
      "“致最美逆行者”",
      "作文题目：《岂曰无衣，与子同袍》",
      "逸云手写",
    ]);
    expect(parts.every((part) => part.kind === "changed")).toBe(true);
    expect(listFallbackSuspectKeys(realHeaderPhoto, realHeaderFinal)).toEqual([
      "1:ocr:0",
      "1:ocr:1",
      "1:ocr:2",
      "1:ocr:3",
    ]);
  });

  it("按行切之后行分隔符仍在，原文不会糊成一整段", () => {
    const parts = buildOcrCompare(realHeaderPhoto, realHeaderFinal).flatMap((line) => line.parts);

    expect(parts.filter((part) => part.text === "\n")).toHaveLength(6);
    expect(parts.map((part) => part.text).join("")).toContain("蚕食了春日里的盎然生机。");
  });
});
