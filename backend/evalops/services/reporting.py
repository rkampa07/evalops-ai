"""Render a release-readiness report as Markdown (for PRs, tickets, or customer hand-off)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..schemas import ReportOut

MAX_FAILURES_LISTED = 25


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else f"{value:.0f}"
    return str(value)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: ReportOut) -> str:
    run, s, g = report.run, report.run.summary, report.gate_evaluation
    verdict = "READY FOR RELEASE" if report.readiness == "ready" else "NOT READY FOR RELEASE"
    lines = [
        f"# {report.title}",
        "",
        f"**Verdict: {verdict}** ({g.passed_gates} passed, {g.failed_gates} failed, "
        f"{g.skipped_gates} skipped gates)",
        "",
        f"- Run: #{run.id} {run.label or ''}".rstrip(),
        f"- Dataset: {report.dataset_name}",
        f"- Model: `{run.model_name}`, prompt version `{run.prompt_version}`",
        f"- Generated: {report.generated_at.isoformat(timespec='seconds')}",
    ]
    if report.baseline:
        b = report.baseline
        lines.append(f"- Baseline: run #{b.id} `{b.model_name}` / `{b.prompt_version}`")
    lines += [
        "",
        "## Summary metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Cases passed | {s.passed_cases} / {s.total_cases} ({_pct(s.pass_rate)}) |",
        f"| Exact match rate | {_pct(s.exact_match_rate)} |",
        f"| Mean token F1 | {_fmt(s.mean_token_f1)} |",
        f"| Required-terms pass rate | {_pct(s.required_terms_pass_rate)} |",
        f"| Forbidden-term violations | {s.forbidden_violation_count} |",
        f"| JSON schema pass rate | {_pct(s.schema_pass_rate)} |",
        f"| Latency p50 / p95 (ms) | {_fmt(s.latency_p50_ms)} / {_fmt(s.latency_p95_ms)} |",
        "",
        "## Release gates",
        "",
        "| Gate | Threshold | Actual | Status |",
        "|---|---|---|---|",
    ]
    for gate in g.gates:
        lines.append(
            f"| {gate.gate} | {gate.comparator} {_fmt(gate.threshold)} | {_fmt(gate.actual)} "
            f"| {gate.status.upper()} |"
        )

    if report.comparison:
        c = report.comparison
        lines += ["", "## Regression analysis", ""]
        if c.has_regression:
            lines += [f"- {r}" for r in c.regression_reasons]
        else:
            lines.append("- No regressions detected within configured tolerances.")
        counts = c.case_counts
        lines += [
            "",
            f"Cases: {counts['regressed']} regressed, {counts['fixed']} fixed, "
            f"{counts['f1_drop']} with F1 drop, {counts['unchanged_pass']} unchanged pass, "
            f"{counts['unchanged_fail']} unchanged fail.",
        ]

    lines += ["", "## Failing cases", ""]
    if not report.failing_cases:
        lines.append("None.")
    else:
        lines += ["| Case | Tags | Failed checks |", "|---|---|---|"]
        for r in report.failing_cases[:MAX_FAILURES_LISTED]:
            checks = "; ".join(f"{f.check}: {f.message}" for f in r.failures)
            lines.append(f"| {r.case_key} | {', '.join(r.tags)} | {_cell(checks)} |")
        extra = len(report.failing_cases) - MAX_FAILURES_LISTED
        if extra > 0:
            lines.append(f"\n...and {extra} more.")

    lines += ["", "## Notes", ""] + [f"- {n}" for n in report.notes]
    return "\n".join(lines) + "\n"
