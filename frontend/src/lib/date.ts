/** 日期工具（本地时区展示，避免日期归一化偏移）。 */

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** 计算给定日期所在周的周一（本地时区），返回 `YYYY-MM-DD`。 */
export function mondayIso(reference: Date = new Date()): string {
  const day = reference.getDay(); // 0=周日 .. 6=周六
  const offset = (day + 6) % 7; // 距周一的偏移天数
  const monday = new Date(reference);
  monday.setDate(reference.getDate() - offset);
  return `${monday.getFullYear()}-${pad(monday.getMonth() + 1)}-${pad(monday.getDate())}`;
}

/** 把 ISO 字符串格式化为本地 `YYYY-MM-DD`；纯日期串原样返回。 */
export function formatDate(iso: string): string {
  if (!iso) {
    return "";
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    return iso;
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
