import { describe, expect, it } from "vitest";

import type { EssaySummary } from "../api/types";
import { BOARD_GROUPS, groupEssays, studentCounts } from "./board";

function essay(partial: Partial<EssaySummary> & { id: number }): EssaySummary {
  return {
    issue_id: 1,
    student_id: 1,
    student_name: "张三",
    title: "",
    status: "review",
    low_confidence: 0,
    photo_count: 1,
    low_resolution_count: 0,
    created_at: "2026-09-07T00:00:00+00:00",
    proofread_at: null,
    teacher_comment: null,
    score: null,
    stars: 0,
    selected: 0,
    ...partial,
  };
}

describe("groupEssays", () => {
  it("buckets essays into the four fixed groups", () => {
    const essays = [
      essay({ id: 1, status: "uploaded" }),
      essay({ id: 2, status: "recognizing" }),
      essay({ id: 3, status: "review" }),
      essay({ id: 4, status: "proofread" }),
      essay({ id: 5, status: "failed" }),
    ];

    const groups = groupEssays(essays);
    expect(groups.map((group) => group.key)).toEqual(BOARD_GROUPS.map((group) => group.key));

    const recognizing = groups.find((group) => group.key === "recognizing");
    expect(recognizing?.essays.map((item) => item.id)).toEqual([1, 2]);
    expect(groups.find((group) => group.key === "review")?.essays.map((item) => item.id)).toEqual([3]);
    expect(groups.find((group) => group.key === "proofread")?.essays.map((item) => item.id)).toEqual([4]);
    expect(groups.find((group) => group.key === "failed")?.essays.map((item) => item.id)).toEqual([5]);
  });

  it("drops unknown statuses from every group", () => {
    const groups = groupEssays([essay({ id: 9, status: "weird" })]);
    expect(groups.every((group) => group.essays.length === 0)).toBe(true);
  });
});

describe("studentCounts", () => {
  it("counts essays per student", () => {
    const counts = studentCounts([
      essay({ id: 1, student_id: 2, student_name: "李四" }),
      essay({ id: 2, student_id: 1, student_name: "张三" }),
      essay({ id: 3, student_id: 1, student_name: "张三" }),
    ]);
    const byId = Object.fromEntries(counts.map((item) => [item.studentId, item]));
    expect(byId[1]).toEqual({ studentId: 1, name: "张三", count: 2 });
    expect(byId[2]).toEqual({ studentId: 2, name: "李四", count: 1 });
  });

  it("sorts students by name ascending", () => {
    const counts = studentCounts([
      essay({ id: 1, student_id: 2, student_name: "Bob" }),
      essay({ id: 2, student_id: 1, student_name: "Alice" }),
    ]);
    expect(counts.map((item) => item.name)).toEqual(["Alice", "Bob"]);
  });
});
