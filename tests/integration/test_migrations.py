from sqlalchemy import create_engine, inspect

from evalops.db import run_migrations


def test_migrations_create_schema_and_are_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    run_migrations(url)
    run_migrations(url)  # second run is a no-op
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            insp = inspect(conn)
            tables = set(insp.get_table_names())
            uniques = {u["name"] for u in insp.get_unique_constraints("test_cases")}
    finally:
        engine.dispose()
    assert {
        "datasets",
        "test_cases",
        "evaluation_runs",
        "evaluation_results",
        "alembic_version",
    } <= tables
    assert "uq_test_case_dataset_key" in uniques


def test_migration_matches_orm_models(tmp_path):
    """Fail if models change without a new Alembic migration."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from evalops import models  # noqa: F401
    from evalops.db import Base

    url = f"sqlite:///{tmp_path / 'm.db'}"
    run_migrations(url)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    assert diff == []
