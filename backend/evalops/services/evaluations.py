"""Evaluation runs, comparisons, release gates and readiness reports."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..engine import gates as gate_engine
from ..engine.regression import CaseSnapshot, CompareConfig, compare_runs
from ..engine.scoring import CaseSpec, EvalConfig, ResponseSpec, score_case, summarize
from ..errors import NotFoundError, UnprocessableError
from ..models import EvaluationResult, EvaluationRun
from ..repositories.datasets import DatasetRepository, TestCaseRepository
from ..repositories.runs import RunRepository
from ..schemas import (
    CompareConfigIn,
    ComparisonOut,
    GateConfigIn,
    GateEvaluationOut,
    GateRequest,
    ReportOut,
    ResultOut,
    RunCreate,
    RunDetailOut,
    RunList,
    RunOut,
    RunRef,
)
from .reporting import render_markdown


def run_out(run: EvaluationRun) -> RunOut:
    out = RunOut.model_validate(run)
    out.dataset_name = run.dataset.name if run.dataset else None
    return out


def run_ref(run: EvaluationRun) -> RunRef:
    return RunRef(
        id=run.id,
        model_name=run.model_name,
        prompt_version=run.prompt_version,
        label=run.label,
        created_at=run.created_at,
    )


class EvaluationService:
    def __init__(self, session: Session):
        self.s = session
        self.runs = RunRepository(session)
        self.datasets = DatasetRepository(session)
        self.cases = TestCaseRepository(session)

    # ------------------------------------------------------------ runs

    def create_run(self, data: RunCreate) -> RunDetailOut:
        dataset = self.datasets.get(data.dataset_id)
        if dataset is None:
            raise NotFoundError(f"dataset {data.dataset_id} not found")
        cases = self.cases.list_for_dataset(dataset.id)
        if not cases:
            raise UnprocessableError(f"dataset {dataset.id} has no test cases")

        by_key = {c.key: c for c in cases}
        unknown = sorted({r.case_key for r in data.responses} - by_key.keys())
        if unknown:
            raise UnprocessableError(f"responses reference unknown case keys: {unknown}")

        config = EvalConfig(**data.config.model_dump())
        responses = {r.case_key: ResponseSpec(r.output, r.latency_ms) for r in data.responses}

        results = []
        rows = []
        for case in cases:
            spec = CaseSpec(
                key=case.key,
                expected_output=case.expected_output,
                required_terms=list(case.required_terms),
                forbidden_terms=list(case.forbidden_terms),
                json_schema=case.json_schema,
                max_latency_ms=case.max_latency_ms,
                tags=list(case.tags),
            )
            response = responses.get(case.key)
            result = score_case(spec, response, config)
            results.append(result)
            rows.append(
                EvaluationResult(
                    test_case_id=case.id,
                    case_key=case.key,
                    output=response.output if response else None,
                    latency_ms=response.latency_ms if response else None,
                    passed=result.passed,
                    metrics=result.metrics,
                    failures=result.failures,
                    tags=result.tags,
                )
            )

        summary = summarize(results)
        run = EvaluationRun(
            dataset_id=dataset.id,
            model_name=data.model_name,
            prompt_version=data.prompt_version,
            label=data.label,
            status="completed",
            config=asdict(config),
            summary=summary,
            pass_rate=summary["pass_rate"],
            results=rows,
        )
        self.runs.add(run)
        self.s.commit()
        return self.get_run(run.id)

    def _get(self, run_id: int, *, with_results: bool = False) -> EvaluationRun:
        run = self.runs.get(run_id, with_results=with_results)
        if run is None:
            raise NotFoundError(f"evaluation run {run_id} not found")
        return run

    def get_run(self, run_id: int) -> RunDetailOut:
        run = self._get(run_id, with_results=True)
        out = RunDetailOut.model_validate(run)
        out.dataset_name = run.dataset.name
        return out

    def list_runs(self, **filters) -> RunList:
        items, total = self.runs.list(**filters)
        return RunList(
            items=[run_out(r) for r in items],
            total=total,
            limit=filters.get("limit", 50),
            offset=filters.get("offset", 0),
        )

    def delete_run(self, run_id: int) -> None:
        self.runs.delete(self._get(run_id))
        self.s.commit()

    # ------------------------------------------------------------ comparison

    def compare(self, baseline_id: int, candidate_id: int, cfg: CompareConfigIn) -> ComparisonOut:
        if baseline_id == candidate_id:
            raise UnprocessableError("baseline and candidate must be different runs")
        baseline = self._get(baseline_id, with_results=True)
        candidate = self._get(candidate_id, with_results=True)
        if baseline.dataset_id != candidate.dataset_id:
            raise UnprocessableError(
                "runs must use the same dataset to be compared "
                f"(baseline dataset {baseline.dataset_id}, "
                f"candidate dataset {candidate.dataset_id})"
            )

        def snapshots(run: EvaluationRun) -> dict[str, CaseSnapshot]:
            return {
                r.case_key: CaseSnapshot(r.passed, r.metrics.get("token_f1"), r.failures)
                for r in run.results
            }

        result = compare_runs(
            baseline.summary,
            candidate.summary,
            snapshots(baseline),
            snapshots(candidate),
            CompareConfig(**cfg.model_dump()),
        )
        return ComparisonOut(
            dataset_id=baseline.dataset_id,
            baseline=run_ref(baseline),
            candidate=run_ref(candidate),
            **result,
        )

    # ------------------------------------------------------------ gates

    def evaluate_gates(self, req: GateRequest) -> tuple[GateEvaluationOut, ComparisonOut | None]:
        candidate = self._get(req.candidate_run_id)
        comparison = None
        if req.baseline_run_id is not None:
            comparison = self.compare(req.baseline_run_id, req.candidate_run_id, req.compare)
        gates = gate_engine.GateConfig(**req.gates.model_dump())
        outcome = gate_engine.evaluate_gates(
            candidate.summary, gates, comparison.model_dump() if comparison else None
        )
        return (
            GateEvaluationOut(
                candidate_run_id=candidate.id,
                baseline_run_id=req.baseline_run_id,
                regression_detected=comparison.has_regression if comparison else None,
                **outcome,
            ),
            comparison,
        )

    # ------------------------------------------------------------ report

    def report(
        self, run_id: int, baseline_id: int | None, policy: GateConfigIn | None
    ) -> ReportOut:
        run = self._get(run_id, with_results=True)
        notes: list[str] = []

        if policy is None:
            policy_dict = gate_engine.DEFAULT_RELEASE_POLICY.configured()
            if baseline_id is None:
                # Comparison gates fail closed without a baseline; the default policy only
                # includes them when a baseline is supplied.
                policy_dict.pop("max_pass_rate_drop", None)
                policy_dict.pop("max_regressed_cases", None)
            policy = GateConfigIn(**policy_dict)
            notes.append("Gate policy: default release policy.")
        if baseline_id is None:
            notes.append(
                "No baseline run supplied: regression detection was not performed. "
                "Pass baseline_run_id to compare against the current production version."
            )

        gate_eval, comparison = self.evaluate_gates(
            GateRequest(candidate_run_id=run_id, baseline_run_id=baseline_id, gates=policy)
        )
        if run.summary.get("responded_cases", 0) < run.summary.get("total_cases", 0):
            missing = run.summary["total_cases"] - run.summary["responded_cases"]
            notes.append(
                f"{missing} test case(s) had no submitted response and were counted as failures."
            )
        notes.append(
            "Metrics are lexical and structural checks. They do not verify factual correctness "
            "or detect hallucinations; review failing and borderline cases manually."
        )

        baseline = self._get(baseline_id) if baseline_id is not None else None
        report = ReportOut(
            title=f"Release readiness: {run.model_name} / {run.prompt_version}",
            generated_at=datetime.now(UTC),
            run=run_out(run),
            dataset_name=run.dataset.name,
            baseline=run_ref(baseline) if baseline else None,
            readiness="ready" if gate_eval.decision == "pass" else "not_ready",
            gate_policy=policy.model_dump(exclude_none=True),
            gate_evaluation=gate_eval,
            comparison=comparison,
            failing_cases=[ResultOut.model_validate(r) for r in run.results if not r.passed],
            notes=notes,
            markdown="",
        )
        report.markdown = render_markdown(report)
        return report
