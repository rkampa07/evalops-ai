from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import PlainTextResponse

from ..schemas import (
    CompareConfigIn,
    ComparisonOut,
    GateEvaluationOut,
    GateRequest,
    ReportOut,
    RunCreate,
    RunDetailOut,
    RunList,
)
from .deps import EvaluationServiceDep

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

PositiveId = Annotated[int, Query(gt=0)]


@router.post("/runs", response_model=RunDetailOut, status_code=status.HTTP_201_CREATED)
def create_run(body: RunCreate, svc: EvaluationServiceDep):
    return svc.create_run(body)


@router.get("/runs", response_model=RunList)
def list_runs(
    svc: EvaluationServiceDep,
    dataset_id: Annotated[int | None, Query(gt=0)] = None,
    model_name: Annotated[str | None, Query(max_length=200)] = None,
    prompt_version: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return svc.list_runs(
        dataset_id=dataset_id,
        model_name=model_name,
        prompt_version=prompt_version,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(run_id: int, svc: EvaluationServiceDep):
    return svc.get_run(run_id)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: int, svc: EvaluationServiceDep):
    svc.delete_run(run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runs/{run_id}/report", response_model=ReportOut)
def get_report(
    run_id: int,
    svc: EvaluationServiceDep,
    baseline_run_id: Annotated[int | None, Query(gt=0)] = None,
    format: Literal["json", "markdown"] = "json",
):
    report = svc.report(run_id, baseline_run_id, policy=None)
    if format == "markdown":
        return PlainTextResponse(report.markdown, media_type="text/markdown; charset=utf-8")
    return report


@router.get("/compare", response_model=ComparisonOut)
def compare(
    svc: EvaluationServiceDep,
    baseline_run_id: PositiveId,
    candidate_run_id: PositiveId,
    metric_tolerance: Annotated[float, Query(ge=0, le=1)] = 0.02,
    latency_tolerance_pct: Annotated[float, Query(ge=0, le=10)] = 0.2,
    case_f1_drop_threshold: Annotated[float, Query(ge=0, le=1)] = 0.15,
):
    cfg = CompareConfigIn(
        metric_tolerance=metric_tolerance,
        latency_tolerance_pct=latency_tolerance_pct,
        case_f1_drop_threshold=case_f1_drop_threshold,
    )
    return svc.compare(baseline_run_id, candidate_run_id, cfg)


@router.post("/gates", response_model=GateEvaluationOut)
def evaluate_gates(body: GateRequest, svc: EvaluationServiceDep):
    outcome, _ = svc.evaluate_gates(body)
    return outcome
