from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..services.datasets import DatasetService
from ..services.evaluations import EvaluationService


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def get_dataset_service(session: SessionDep) -> DatasetService:
    return DatasetService(session)


def get_evaluation_service(session: SessionDep) -> EvaluationService:
    return EvaluationService(session)


DatasetServiceDep = Annotated[DatasetService, Depends(get_dataset_service)]
EvaluationServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
