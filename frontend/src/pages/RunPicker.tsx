import type { Run } from "../api/types";
import { runLabel } from "../lib/format";

export function RunPicker({
  label,
  runs,
  value,
  onChange,
  allowNone,
  noneLabel = "None",
}: {
  label: string;
  runs: Run[];
  value: string;
  onChange: (v: string) => void;
  allowNone?: boolean;
  noneLabel?: string;
}) {
  return (
    <label>
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{allowNone ? noneLabel : "Select a run…"}</option>
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {runLabel(r)}
          </option>
        ))}
      </select>
    </label>
  );
}
