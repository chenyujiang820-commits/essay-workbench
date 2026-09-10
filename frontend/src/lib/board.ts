/** 状态看板的纯逻辑：按状态分组、按学生统计（可独立单测）。 */

import type { EssaySummary } from "../api/types";

export interface BoardGroup {
  key: string;
  title: string;
  statuses: string[];
  essays: EssaySummary[];
}

/** 看板分组定义（顺序即展示顺序）。 */
export const BOARD_GROUPS: ReadonlyArray<{ key: string; title: string; statuses: string[] }> = [
  { key: "recognizing", title: "识别中", statuses: ["uploaded", "recognizing"] },
  { key: "review", title: "待校对", statuses: ["review"] },
  { key: "proofread", title: "已定稿", statuses: ["proofread"] },
  { key: "failed", title: "失败", statuses: ["failed"] },
];

/** 把作文列表按状态归入固定分组。 */
export function groupEssays(essays: EssaySummary[]): BoardGroup[] {
  return BOARD_GROUPS.map((group) => ({
    key: group.key,
    title: group.title,
    statuses: group.statuses,
    essays: essays.filter((essay) => group.statuses.includes(essay.status)),
  }));
}

export interface StudentCount {
  studentId: number;
  name: string;
  count: number;
}

/** 按学生统计篇数（按姓名升序）。 */
export function studentCounts(essays: EssaySummary[]): StudentCount[] {
  const map = new Map<number, StudentCount>();
  for (const essay of essays) {
    const existing = map.get(essay.student_id);
    if (existing) {
      existing.count += 1;
    } else {
      map.set(essay.student_id, {
        studentId: essay.student_id,
        name: essay.student_name ?? `#${essay.student_id}`,
        count: 1,
      });
    }
  }
  return Array.from(map.values()).sort((left, right) => left.name.localeCompare(right.name, "zh-Hans-CN"));
}
