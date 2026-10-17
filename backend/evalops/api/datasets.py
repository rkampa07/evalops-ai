from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from ..schemas import (
    DatasetCreate,
    DatasetOut,
    DatasetUpdate,
    Page,
    PassageBulkCreate,
    PassageOut,
    PassageUpdate,
    TestCaseBulkCreate,
    TestCaseCreate,
    TestCaseOut,
    TestCaseUpdate,
)
from .deps import DatasetServiceDep, get_workspace_access, require_editor

router = APIRouter(tags=["datasets"], dependencies=[Depends(get_workspace_access)])


@router.get("/datasets", response_model=list[DatasetOut] | Page[DatasetOut])
def list_datasets(
    svc: DatasetServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
):
    return svc.list_datasets(limit=limit, offset=offset)


@router.post(
    "/datasets",
    response_model=DatasetOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_editor)],
)
def create_dataset(body: DatasetCreate, svc: DatasetServiceDep):
    return svc.create_dataset(body)


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: int, svc: DatasetServiceDep):
    return svc.get_dataset_out(dataset_id)


@router.patch(
    "/datasets/{dataset_id}", response_model=DatasetOut, dependencies=[Depends(require_editor)]
)
def update_dataset(dataset_id: int, body: DatasetUpdate, svc: DatasetServiceDep):
    return svc.update_dataset(dataset_id, body)


@router.delete(
    "/datasets/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_editor)],
)
def delete_dataset(dataset_id: int, svc: DatasetServiceDep):
    svc.delete_dataset(dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/datasets/{dataset_id}/test-cases",
    response_model=list[TestCaseOut] | Page[TestCaseOut],
)
def list_test_cases(
    dataset_id: int,
    svc: DatasetServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
):
    return svc.list_cases(dataset_id, limit=limit, offset=offset)


@router.post(
    "/datasets/{dataset_id}/test-cases",
    response_model=TestCaseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_editor)],
)
def create_test_case(dataset_id: int, body: TestCaseCreate, svc: DatasetServiceDep):
    return svc.create_cases(dataset_id, [body])[0]


@router.post(
    "/datasets/{dataset_id}/test-cases/bulk",
    response_model=list[TestCaseOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_editor)],
)
def bulk_create_test_cases(dataset_id: int, body: TestCaseBulkCreate, svc: DatasetServiceDep):
    return svc.create_cases(dataset_id, body.test_cases)


@router.get("/test-cases/{case_id}", response_model=TestCaseOut)
def get_test_case(case_id: int, svc: DatasetServiceDep):
    return svc.get_case(case_id)


@router.patch(
    "/test-cases/{case_id}", response_model=TestCaseOut, dependencies=[Depends(require_editor)]
)
def update_test_case(case_id: int, body: TestCaseUpdate, svc: DatasetServiceDep):
    return svc.update_case(case_id, body)


@router.delete(
    "/test-cases/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_editor)],
)
def delete_test_case(case_id: int, svc: DatasetServiceDep):
    svc.delete_case(case_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/datasets/{dataset_id}/passages", response_model=list[PassageOut] | Page[PassageOut])
def list_passages(
    dataset_id: int,
    svc: DatasetServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
):
    return svc.list_passages(dataset_id, limit=limit, offset=offset)


@router.post(
    "/datasets/{dataset_id}/passages",
    response_model=list[PassageOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_editor)],
)
def create_passages(dataset_id: int, body: PassageBulkCreate, svc: DatasetServiceDep):
    return svc.create_passages(dataset_id, body.passages)


@router.patch(
    "/datasets/{dataset_id}/passages/{passage_id}",
    response_model=PassageOut,
    dependencies=[Depends(require_editor)],
)
def update_passage(dataset_id: int, passage_id: str, body: PassageUpdate, svc: DatasetServiceDep):
    return svc.update_passage(dataset_id, passage_id, body)


@router.delete(
    "/datasets/{dataset_id}/passages/{passage_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_editor)],
)
def delete_passage(dataset_id: int, passage_id: str, svc: DatasetServiceDep):
    svc.delete_passage(dataset_id, passage_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
