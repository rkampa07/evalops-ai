import type { Run } from "../api/types";
import { dateTime, pct } from "../lib/format";

/** Column chart of pass rate per run (oldest → newest), drawn as inline SVG. */
export function PassRateChart({ runs, threshold = 0.9 }: { runs: Run[]; threshold?: number }) {
  const ordered = [...runs].reverse();
  const width = 640;
  const height = 200;
  const pad = { top: 12, right: 12, bottom: 28, left: 40 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const slot = innerW / Math.max(ordered.length, 1);
  const barW = Math.min(36, slot * 0.7);
  const y = (v: number) => pad.top + innerH * (1 - v);

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Pass rate by run">
      {[0, 0.5, 1].map((t) => (
        <g key={t}>
          <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="chart-grid" />
          <text x={pad.left - 6} y={y(t) + 4} className="chart-axis" textAnchor="end">
            {t * 100}%
          </text>
        </g>
      ))}
      <line x1={pad.left} x2={width - pad.right} y1={y(threshold)} y2={y(threshold)} className="chart-threshold" />
      <text x={width - pad.right} y={y(threshold) - 4} className="chart-axis" textAnchor="end">
        gate {pct(threshold, 0)}
      </text>
      {ordered.map((run, i) => {
        const v = run.pass_rate ?? 0;
        const x = pad.left + i * slot + (slot - barW) / 2;
        const tone = v >= threshold ? "good" : v >= 0.7 ? "warn" : "bad";
        return (
          <g key={run.id}>
            <rect x={x} y={y(v)} width={barW} height={innerH * v} rx={3} className={`chart-bar chart-${tone}`}>
              <title>{`#${run.id} ${run.model_name} ${run.prompt_version}: ${pct(run.pass_rate)} (${dateTime(run.created_at)})`}</title>
            </rect>
            <text x={x + barW / 2} y={height - 10} className="chart-axis" textAnchor="middle">
              #{run.id}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
