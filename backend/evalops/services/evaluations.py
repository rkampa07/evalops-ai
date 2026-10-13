"""Evaluation runs, comparisons, release gates and readiness reports."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..engine import gates as gate_engine
from ..engine.metrics import regex_time_budget
from ..engine.regression import CaseSnapshot, CompareConfig, compare_runs
from ..engine.scoring import (
    CaseResult,
    CaseSpec,
    EvalConfig,
    ResponseSpec,
    score_case,
    summarize,
)
from ..engine.versioning import (
    METHODOLOGY_VERSION,
    case_definition,
    knowledge_base_hash,
    methodology_compatibility,
)
from ..errors import NotFoundError, UnprocessableError
from ..models import EvaluationResult, EvaluationRun
from ..repositories.datasets import DatasetRepository, TestCaseRepository
from ..repositories.runs import RunRepository
from ..schemas import (
    CompareConfigIn,
    ComparisonOut,
    EvalConfigIn,
    GateConfigIn,
    GateEvaluationOut,
    GateRequest,
    MetricDelta,
    ModelGradesIn,
    ReportOut,
    ResultOut,
    RunCreate,
    RunDetailOut,
    RunList,
    RunOut,
    RunRef,
)
from .reporting import render_markdown


def estimated_summary(results, judge: dict) -> dict:
    """Aggregate model-judge verdicts. Labelled 'estimated'; never merged into `summary`."""
    graded = [r for r in results if r.model_grades]
    verdicts = [c for r in graded for c in r.model_grades["claims"]]
    decided = [c for c in verdicts if c["verdict"] != "error"]
    supported = sum(c["verdict"] == "supported" for c in decided)
    agree = total_pairs = 0
    for r in graded:
        heuristic = [c["supported"] for c in r.metrics.get("claims", [])]
        for h, c in zip(heuristic, r.model_grades["claims"], strict=False):
            if c["verdict"] != "error":
                total_pairs += 1
                agree += h == (c["verdict"] == "supported")
    return {
        "label": "estimated",
        "judge": judge,
        "graded_cases": len(graded),
        "claims_total": len(verdicts),
        "claims_supported": supported,
        "claims_error": len(verdicts) - len(decided),
        "estimated_groundedness": round(supported / len(decided), 6) if decided else None,
        "estimated_unsupported_claims": len(decided) - supported,
        "heuristic_judge_agreement": round(agree / total_pairs, 6) if total_pairs else None,
    }


# Upper bound on total JSON-Schema regex matching time per run (defence in depth on top of the
# per-match limit in engine/metrics.py).
RUN_REGEX_BUDGET_S = 5.0


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

        knowledge_base = {p.passage_id: p.text for p in dataset.passages}
        self._validate_rag_inputs(cases, data, knowledge_base)

        config = EvalConfig(**data.config.model_dump())
        responses = {
            r.case_key: ResponseSpec(r.output, r.latency_ms, r.retrieved_passage_ids)
            for r in data.responses
        }
        generation = {r.case_key: r.generation for r in data.responses}

        results = []
        rows = []
        with regex_time_budget(RUN_REGEX_BUDGET_S):
            for case in cases:
                results.append(
                    self._score(
                        case,
                        responses.get(case.key),
                        config,
                        rows,
                        knowledge_base,
                        generation.get(case.key),
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
            methodology_version=METHODOLOGY_VERSION,
            dataset_version=dataset.version,
            knowledge_base_hash=knowledge_base_hash(knowledge_base),
            generation=data.generation,
            results=rows,
        )
        self.runs.add(run)
        self.s.commit()
        return self.get_run(run.id)

    @staticmethod
    def _validate_rag_inputs(cases, data: RunCreate, knowledge_base: dict[str, str]) -> None:
        rag_cases = [c for c in cases if c.relevant_passage_ids or c.expect_abstain]
        retrieved = any(r.retrieved_passage_ids is not None for r in data.responses)
        if (rag_cases or retrieved) and not knowledge_base:
            raise UnprocessableError(
                "this run needs RAG evaluation but the dataset has no knowledge-base passages"
            )
        missing = sorted(
            {pid for c in rag_cases for pid in c.relevant_passage_ids} - knowledge_base.keys()
        )
        if missing:
            raise UnprocessableError(f"test cases reference unknown passage ids: {missing}")
        unknown = sorted(
            {pid for r in data.responses for pid in (r.retrieved_passage_ids or [])}
            - knowledge_base.keys()
        )
        if unknown:
            raise UnprocessableError(f"responses retrieved unknown passage ids: {unknown}")

    @staticmethod
    def _score(
        case,
        response,
        config: EvalConfig,
        rows: list[EvaluationResult],
        knowledge_base: dict[str, str] | None = None,
        generation: dict | None = None,
    ) -> CaseResult:
        """Score one case and append its result row, snapshotting the definition used."""
        definition = case_definition(case)
        spec = CaseSpec(
            key=case.key,
            expected_output=case.expected_output,
            required_terms=list(case.required_terms),
            forbidden_terms=list(case.forbidden_terms),
            json_schema=case.json_schema,
            max_latency_ms=case.max_latency_ms,
            tags=list(case.tags),
            relevant_passage_ids=list(case.relevant_passage_ids or []),
            expect_abstain=bool(case.expect_abstain),
        )
        result = score_case(spec, response, config, knowledge_base)
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
                retrieved_passage_ids=response.retrieved_passage_ids if response else None,
                generation=generation,
                case_version=case.version,
                case_hash=case.content_hash,
                case_snapshot={
                    "key": case.key,
                    **definition,
                    "tags": list(case.tags),
                    "version": case.version,
                },
            )
        )
        return result

    # ------------------------------------------------------------ model-based grades

    def attach_model_grades(self, run_id: int, body: ModelGradesIn) -> RunDetailOut:
        """Attach claim-level verdicts from a model judge (estimated, not ground truth).

        Graded claims must be exactly the claims EvalOps extracted for that result, so
        deterministic and estimated verdicts always refer to the same text. Re-attaching
        replaces earlier grades.
        """
        run = self._get(run_id, with_results=True)
        by_key = {r.case_key: r for r in run.results}
        unknown = sorted({g.case_key for g in body.grades} - by_key.keys())
        if unknown:
            raise UnprocessableError(f"grades reference case keys not in this run: {unknown}")
        for grade in body.grades:
            result = by_key[grade.case_key]
            expected = [c["text"] for c in result.metrics.get("claims", [])]
            got = [c.claim for c in grade.claims]
            if got != expected:
                raise UnprocessableError(
                    f"claims for {grade.case_key!r} do not match the run's extracted claims; "
                    "fetch the run and grade metrics.claims in order"
                )
        judge = body.judge.model_dump()
        for grade in body.grades:
            by_key[grade.case_key].model_grades = {
                "label": "estimated",
                "judge": judge,
                "latency_ms": grade.latency_ms,
                "claims": [c.model_dump() for c in grade.claims],
            }
        run.model_graded = estimated_summary(run.results, judge)
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

        b_res = {r.case_key: r for r in baseline.results}
        c_res = {r.case_key: r for r in candidate.results}
        issues = self._run_compatibility_issues(baseline, candidate)

        # Cases are comparable only if both runs scored them against the same definition.
        excluded: dict[str, str] = {}
        for key in sorted(b_res.keys() & c_res.keys()):
            b_hash, c_hash = b_res[key].case_hash, c_res[key].case_hash
            if b_hash is None or c_hash is None:
                excluded[key] = "definition_unknown"
            elif b_hash != c_hash:
                excluded[key] = "definition_changed"
        for kind, explanation in (
            ("definition_changed", "changed definition between the runs"),
            ("definition_unknown", "have no recorded definition (legacy results)"),
        ):
            keys = [k for k, v in excluded.items() if v == kind]
            if keys:
                issues.append(
                    {
                        "severity": "warning",
                        "kind": kind,
                        "message": f"{len(keys)} case(s) {explanation} and were excluded "
                        f"from regression detection: {keys}",
                    }
                )
        only = sorted(b_res.keys() ^ c_res.keys())
        if only:
            issues.append(
                {
                    "severity": "warning",
                    "kind": "case_set_changed",
                    "message": f"{len(only)} case(s) exist in only one run and were excluded "
                    f"from regression detection: {only}",
                }
            )

        comparable = sorted((b_res.keys() & c_res.keys()) - excluded.keys())
        if not comparable:
            issues.append(
                {
                    "severity": "error",
                    "kind": "no_comparable_cases",
                    "message": "no test case has the same definition in both runs",
                }
            )

        def summary_of(results: dict[str, EvaluationResult]) -> dict:
            return summarize(
                [
                    CaseResult(
                        k,
                        results[k].passed,
                        results[k].metrics,
                        results[k].failures,
                        list(results[k].tags),
                    )
                    for k in comparable
                ]
            )

        def snapshots(results: dict[str, EvaluationResult]) -> dict[str, CaseSnapshot]:
            return {
                k: CaseSnapshot(r.passed, r.metrics.get("token_f1"), r.failures)
                for k, r in results.items()
            }

        result = compare_runs(
            summary_of(b_res),
            summary_of(c_res),
            snapshots(b_res),
            snapshots(c_res),
            CompareConfig(**cfg.model_dump()),
            excluded=excluded,
            issues=issues,
        )
        est_deltas, est_note = [], None
        if result["compatible"]:
            est_deltas, est_note = self._estimated_deltas(
                baseline, candidate, b_res, c_res, comparable, CompareConfig(**cfg.model_dump())
            )
        return ComparisonOut(
            dataset_id=baseline.dataset_id,
            baseline=run_ref(baseline),
            candidate=run_ref(candidate),
            estimated_metric_deltas=est_deltas,
            estimated_note=est_note,
            **result,
        )

    @staticmethod
    def _estimated_deltas(baseline, candidate, b_res, c_res, comparable, cfg: CompareConfig):
        """Model-judge deltas over comparable cases, only if both runs used the same judge."""
        b_judge = (baseline.model_graded or {}).get("judge")
        c_judge = (candidate.model_graded or {}).get("judge")
        if not b_judge or not c_judge:
            return [], "estimated metrics need model-based grades on both runs"
        same = ("model_id", "model_revision", "prompt_hash")
        if any(b_judge.get(k) != c_judge.get(k) for k in same):
            return [], "runs were graded by different judges or judge prompts; not compared"

        def groundedness(results):
            verdicts = [
                c["verdict"]
                for k in comparable
                if results[k].model_grades
                for c in results[k].model_grades["claims"]
                if c["verdict"] != "error"
            ]
            return (
                round(sum(v == "supported" for v in verdicts) / len(verdicts), 6)
                if verdicts
                else None
            )

        b, c = groundedness(b_res), groundedness(c_res)
        delta = None if b is None or c is None else round(c - b, 6)
        if delta is None:
            status = "not_applicable"
        elif delta < -cfg.metric_tolerance - 1e-12:
            status = "regressed"
        elif delta > cfg.metric_tolerance + 1e-12:
            status = "improved"
        else:
            status = "unchanged"
        return [
            MetricDelta(
                metric="estimated_groundedness",
                baseline=b,
                candidate=c,
                delta=delta,
                direction="higher_is_better",
                status=status,
            )
        ], f"estimated by {c_judge['model_id']}; not ground truth and not part of has_regression"

    @staticmethod
    def _run_compatibility_issues(
        baseline: EvaluationRun, candidate: EvaluationRun
    ) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        b_cfg = EvalConfigIn(**baseline.config).model_dump()
        c_cfg = EvalConfigIn(**candidate.config).model_dump()
        diffs = [f"{k}: {b_cfg[k]} -> {c_cfg[k]}" for k in b_cfg if b_cfg[k] != c_cfg[k]]
        if diffs:
            issues.append(
                {
                    "severity": "error",
                    "kind": "scoring_config",
                    "message": "runs were scored with different scoring configs ("
                    + "; ".join(diffs)
                    + "); re-run both with the same config to compare",
                }
            )
        b_kb, c_kb = baseline.knowledge_base_hash, candidate.knowledge_base_hash
        if b_kb and c_kb and b_kb != c_kb:
            issues.append(
                {
                    "severity": "error",
                    "kind": "knowledge_base",
                    "message": "the knowledge-base passages changed between the runs, so "
                    "groundedness and citation checks used different evidence",
                }
            )
        elif bool(b_kb) != bool(c_kb):
            issues.append(
                {
                    "severity": "warning",
                    "kind": "knowledge_base",
                    "message": "only one of the runs was evaluated against a knowledge base",
                }
            )
        b_ver, c_ver = baseline.methodology_version, candidate.methodology_version
        severity = methodology_compatibility(b_ver, c_ver)
        if severity == "error":
            issues.append(
                {
                    "severity": "error",
                    "kind": "methodology_version",
                    "message": f"scoring methodology {b_ver} vs {c_ver} differs in major "
                    "version; scores are not comparable",
                }
            )
        elif severity == "warning":
            issues.append(
                {
                    "severity": "warning",
                    "kind": "methodology_version",
                    "message": f"scoring methodology {b_ver} vs {c_ver}: compatible "
                    "(scores for well-formed inputs are unchanged between these versions)",
                }
            )
        return issues

    # ------------------------------------------------------------ gates

    def evaluate_gates(self, req: GateRequest) -> tuple[GateEvaluationOut, ComparisonOut | None]:
        candidate = self._get(req.candidate_run_id)
        comparison = None
        if req.baseline_run_id is not None:
            comparison = self.compare(req.baseline_run_id, req.candidate_run_id, req.compare)
        gates = gate_engine.GateConfig(**req.gates.model_dump())
        # Estimated metrics are exposed to gates under their own, explicitly named keys.
        estimated = (candidate.model_graded or {}).get("estimated_groundedness")
        summary = candidate.summary | {"estimated_groundedness": estimated}
        outcome = gate_engine.evaluate_gates(
            summary, gates, comparison.model_dump() if comparison else None
        )
        return (
            GateEvaluationOut(
                candidate_run_id=candidate.id,
                baseline_run_id=req.baseline_run_id,
                regression_detected=(
                    comparison.has_regression if comparison and comparison.compatible else None
                ),
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
            if run.summary.get("rag_cases"):
                policy_dict |= gate_engine.DEFAULT_RAG_POLICY
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
        if comparison is not None:
            if not comparison.compatible:
                reasons = "; ".join(
                    i.message for i in comparison.compatibility_issues if i.severity == "error"
                )
                notes.append(
                    f"Baseline run #{baseline_id} is not comparable with this run ({reasons}). "
                    "Regression detection was not performed and comparison gates failed closed."
                )
            else:
                notes.extend(
                    f"Comparison warning: {i.message}" for i in comparison.compatibility_issues
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
        if run.summary.get("rag_cases"):
            notes.append(
                "RAG groundedness is a lexical heuristic (content-word coverage plus exact "
                "numbers) against the retrieved passages. See docs/rag-evaluation.md for its "
                "measured agreement with labels."
            )
        if run.model_graded:
            judge = run.model_graded.get("judge", {})
            notes.append(
                f"Model-based metrics are ESTIMATES from {judge.get('model_id')} "
                f"(prompt {judge.get('prompt_version')}), not ground truth, and are not used "
                "by the default gate policy."
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
