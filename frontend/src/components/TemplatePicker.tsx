/** 成册模板选择：三选一（切换即刷新预览）。 */

import type { TemplateInfo } from "../api/types";

export interface TemplatePickerProps {
  templates: TemplateInfo[];
  value: string;
  onChange: (key: string) => void;
  disabled?: boolean;
}

export default function TemplatePicker({
  templates,
  value,
  onChange,
  disabled = false,
}: TemplatePickerProps) {
  return (
    <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="成册模板">
      {templates.map((template) => {
        const active = template.key === value;
        return (
          <button
            key={template.key}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            title={template.description}
            onClick={() => onChange(template.key)}
            className={`rounded-md px-3 py-1.5 text-sm ${
              active
                ? "bg-slate-900 text-white"
                : "border border-slate-300 text-slate-700 hover:border-slate-500"
            } disabled:opacity-60`}
          >
            {template.name}
          </button>
        );
      })}
    </div>
  );
}
