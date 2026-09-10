/** 作文/任务状态徽标：统一状态文案与配色。 */

interface StatusMeta {
  label: string;
  className: string;
}

const STATUS_META: Record<string, StatusMeta> = {
  uploaded: { label: "已上传", className: "bg-slate-100 text-slate-700" },
  recognizing: { label: "识别中", className: "bg-sky-100 text-sky-700" },
  review: { label: "待校对", className: "bg-amber-100 text-amber-800" },
  proofread: { label: "已定稿", className: "bg-emerald-100 text-emerald-700" },
  failed: { label: "识别失败", className: "bg-rose-100 text-rose-700" },
};

/** 返回状态的中文文案（未知状态回退为原始值）。 */
export function statusLabel(status: string): string {
  return STATUS_META[status]?.label ?? status;
}

export default function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? { label: status, className: "bg-slate-100 text-slate-700" };
  return (
    <span
      data-status={status}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${meta.className}`}
    >
      {meta.label}
    </span>
  );
}
