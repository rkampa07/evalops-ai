from fastapi import APIRouter
from sqlalchemy import text

from .. import __version__
from .deps import SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: SessionDep) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__, "database": "ok"}
