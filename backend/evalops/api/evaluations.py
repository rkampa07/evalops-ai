from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from fastapi.responses import PlainTextResponse

from ..errors import NotFoundError
from ..schemas import (
    AssessmentCreate,
    CitationAssessmentJobOut,
    CitationAssessmentResultOut,
    ClaimAssessmentJobOut,
    ClaimAssessmentResultOut,
    CompareConfigIn,
    ComparisonOut,
    EvaluationJobOut,
    GateEvaluationOut,
    GateRequest,
    ModelGradesIn,
    Page,
    ReportOut,
    ResultOut,
    RunCreate,
    RunDetailOut,
    RunList,
    RunOut,
)
from ..services.citation_assessments import job_out as citation_assessment_job_out
from ..services.claim_assessments import claim_job_out
from ..services.jobs import job_out
from .deps import (
    CitationAssessmentServiceDep,
    ClaimAssessmentServiceDep,
    EvaluationJobServiceDep,
    EvaluationServiceDep,
    get_workspace_access,
    require_editor,
)

router = APIRouter(
    prefix="/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(get_workspace_access)],
)

PositiveId = Annotated[int, Query(gt=0)]
PathId = Annotated[int, Path(gt=0)]


@router.post(
    "/runs",
    response_model=RunDetailOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_editor)],
)
def create_run(body: RunCreate, svc: EvaluationServiceDep):
    return svc.create_run(body)


@router.post(
    "/jobs",
    response_model=EvaluationJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_editor)],
)
def submit_evaluation_job(
    body: RunCreate,
    response: Response,
    svc: EvaluationJobServiceDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    job, replayed = svc.submit(body, idempotency_key.strip())
    if replayed:
        response.status_code = status.HTTP_200_OK
    return job_out(job, replayed=replayed)


@router.get("/jobs/{job_id}", response_model=EvaluationJobOut)
def get_evaluation_job(job_id: int, svc: EvaluationJobServiceDep):
    return job_out(svc.get(job_id))


@router.post(
    "/jobs/{job_id}/cancel", response_model=EvaluationJobOut, dependencies=[Depends(require_editor)]
)
def cancel_evaluation_job(job_id: int, svc: EvaluationJobServiceDep):
    return job_out(svc.cancel(job_id))


@router.post(
    "/runs/{run_id}/results/{result_id}/assessments",
    response_model=CitationAssessmentJobOut | ClaimAssessmentJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_editor)],
)
def submit_citation_assessment(
    run_id: PathId,
    result_id: PathId,
    body: AssessmentCreate,
    response: Response,
    citation_svc: CitationAssessmentServiceDep,
    claim_svc: ClaimAssessmentServiceDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
):
    if body.evaluator == "citation":
        job, replayed = citation_svc.submit(
            run_id,
            result_id,
            citation_expected=body.citation_expected,
            idempotency_key=idempotency_key.strip(),
        )
        output = citation_assessment_job_out(job, replayed=replayed)
    else:
        job, replayed = claim_svc.submit(run_id, result_id, idempotency_key=idempotency_key.strip())
        output = claim_job_out(job, replayed=replayed)
    if replayed:
        response.status_code = status.HTTP_200_OK
    return output


@router.get(
    "/assessments/{assessment_id}", response_model=CitationAssessmentJobOut | ClaimAssessmentJobOut
)
def get_citation_assessment(
    assessment_id: PathId,
    citation_svc: CitationAssessmentServiceDep,
    claim_svc: ClaimAssessmentServiceDep,
):
    try:
        return citation_assessment_job_out(citation_svc.get(assessment_id))
    except NotFoundError:
        return claim_job_out(claim_svc.get(assessment_id))


@router.get(
    "/assessments/{assessment_id}/result",
    response_model=CitationAssessmentResultOut | ClaimAssessmentResultOut,
)
def get_citation_assessment_result(
    assessment_id: PathId,
    citation_svc: CitationAssessmentServiceDep,
    claim_svc: ClaimAssessmentServiceDep,
):
    try:
        job = citation_svc.get(assessment_id)
    except NotFoundError:
        return claim_svc.get_result(assessment_id)
    return citation_svc.get_result(job.id)


@router.post(
    "/assessments/{assessment_id}/cancel",
    response_model=CitationAssessmentJobOut | ClaimAssessmentJobOut,
    dependencies=[Depends(require_editor)],
)
def cancel_citation_assessment(
    assessment_id: PathId,
    citation_svc: CitationAssessmentServiceDep,
    claim_svc: ClaimAssessmentServiceDep,
):
    try:
        return citation_assessment_job_out(citation_svc.cancel(assessment_id))
    except NotFoundError:
        return claim_job_out(claim_svc.cancel(assessment_id))


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


@router.get("/runs/{run_id}/results", response_model=Page[ResultOut])
def list_run_results(
    run_id: int,
    svc: EvaluationServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    failed_only: bool = False,
):
    return svc.list_run_results(run_id, limit=limit, offset=offset, failed_only=failed_only)


@router.get("/runs/{run_id}/summary", response_model=RunOut)
def get_run_summary(run_id: int, svc: EvaluationServiceDep):
    return svc.get_run_summary(run_id)


@router.delete(
    "/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_editor)]
)
def delete_run(run_id: int, svc: EvaluationServiceDep):
    svc.delete_run(run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/runs/{run_id}/model-grades",
    response_model=RunDetailOut,
    dependencies=[Depends(require_editor)],
)
def attach_model_grades(run_id: int, body: ModelGradesIn, svc: EvaluationServiceDep):
    """Attach claim-level verdicts from a model judge. Stored and reported as estimates."""
    return svc.attach_model_grades(run_id, body)


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
