from fastapi import APIRouter, Response, status

from ..schemas import (
    DatasetCreate,
    DatasetOut,
    DatasetUpdate,
    TestCaseBulkCreate,
    TestCaseCreate,
    TestCaseOut,
    TestCaseUpdate,
)
from .deps import DatasetServiceDep

router = APIRouter(tags=["datasets"])


@router.get("/datasets", response_model=list[DatasetOut])
def list_datasets(svc: DatasetServiceDep):
    return svc.list_datasets()


@router.post("/datasets", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
def create_dataset(body: DatasetCreate, svc: DatasetServiceDep):
    return svc.create_dataset(body)


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: int, svc: DatasetServiceDep):
    return svc.get_dataset_out(dataset_id)


@router.patch("/datasets/{dataset_id}", response_model=DatasetOut)
def update_dataset(dataset_id: int, body: DatasetUpdate, svc: DatasetServiceDep):
    return svc.update_dataset(dataset_id, body)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(dataset_id: int, svc: DatasetServiceDep):
    svc.delete_dataset(dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/datasets/{dataset_id}/test-cases", response_model=list[TestCaseOut])
def list_test_cases(dataset_id: int, svc: DatasetServiceDep):
    return svc.list_cases(dataset_id)


@router.post(
    "/datasets/{dataset_id}/test-cases",
    response_model=TestCaseOut,
    status_code=status.HTTP_201_CREATED,
)
def create_test_case(dataset_id: int, body: TestCaseCreate, svc: DatasetServiceDep):
    return svc.create_cases(dataset_id, [body])[0]


@router.post(
    "/datasets/{dataset_id}/test-cases/bulk",
    response_model=list[TestCaseOut],
    status_code=status.HTTP_201_CREATED,
)
def bulk_create_test_cases(dataset_id: int, body: TestCaseBulkCreate, svc: DatasetServiceDep):
    return svc.create_cases(dataset_id, body.test_cases)


@router.get("/test-cases/{case_id}", response_model=TestCaseOut)
def get_test_case(case_id: int, svc: DatasetServiceDep):
    return svc.get_case(case_id)


@router.patch("/test-cases/{case_id}", response_model=TestCaseOut)
def update_test_case(case_id: int, body: TestCaseUpdate, svc: DatasetServiceDep):
    return svc.update_case(case_id, body)


@router.delete("/test-cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test_case(case_id: int, svc: DatasetServiceDep):
    svc.delete_case(case_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
