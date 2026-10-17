from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from evalops.db import make_engine, run_migrations


def _url(tmp_path, name: str = "workspace.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def _tables(engine):
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return metadata.tables


def _legacy_job_values(dataset_id: int, now: datetime) -> dict:
    return {
        "status": "queued",
        "idempotency_key": "legacy-job-key",
        "request_hash": "a" * 64,
        "request_payload": {"dataset_id": dataset_id},
        "dataset_id": dataset_id,
        "dataset_version": 1,
        "scoring_input_fingerprint": "b" * 64,
        "knowledge_base_hash": "c" * 64,
        "completed_count": 0,
        "total_count": 1,
        "created_at": now,
        "updated_at": now,
        "attempt_count": 0,
    }


def _seed_legacy_database(engine) -> None:
    tables = _tables(engine)
    now = datetime(2026, 1, 2, tzinfo=UTC)
    summary = {
        "total_cases": 1,
        "responded_cases": 1,
        "passed_cases": 1,
        "failed_cases": 0,
        "pass_rate": 1.0,
        "failure_counts": {},
        "tag_pass_rates": {},
    }
    with engine.begin() as connection:
        connection.execute(
            tables["datasets"]
            .insert()
            .values(
                id=17,
                name="legacy-dataset",
                description="pre-workspace data",
                version=2,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            tables["test_cases"]
            .insert()
            .values(
                id=23,
                dataset_id=17,
                key="legacy-case",
                input="question",
                expected_output="answer",
                required_terms=["answer"],
                forbidden_terms=[],
                json_schema=None,
                max_latency_ms=None,
                tags=["legacy"],
                relevant_passage_ids=["P-17"],
                expect_abstain=False,
                version=1,
                content_hash="d" * 64,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            tables["passages"]
            .insert()
            .values(
                id=29,
                dataset_id=17,
                passage_id="P-17",
                title="Legacy evidence",
                text="answer",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            tables["evaluation_runs"]
            .insert()
            .values(
                id=31,
                dataset_id=17,
                model_name="legacy-model",
                prompt_version="legacy-v1",
                label="baseline",
                status="completed",
                config={"min_token_f1": 0.5},
                summary=summary,
                pass_rate=1.0,
                methodology_version="1.0.0",
                dataset_version=2,
                knowledge_base_hash="e" * 64,
                generation=None,
                model_graded=None,
                created_at=now,
            )
        )
        connection.execute(
            tables["evaluation_results"]
            .insert()
            .values(
                id=37,
                run_id=31,
                test_case_id=23,
                case_key="legacy-case",
                output="answer",
                latency_ms=12.0,
                passed=True,
                metrics={"token_f1": 1.0},
                failures=[],
                tags=["legacy"],
                case_version=1,
                case_hash="f" * 64,
                case_snapshot={"key": "legacy-case"},
                retrieved_passage_ids=["P-17"],
                generation=None,
                model_grades=None,
            )
        )
        connection.execute(tables["evaluation_jobs"].insert().values(**_legacy_job_values(17, now)))
        connection.execute(
            tables["chat_sessions"]
            .insert()
            .values(
                id=41,
                dataset_id=17,
                title="Legacy chat",
                prompt_version="legacy-v1",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            tables["chat_messages"]
            .insert()
            .values(
                id=43,
                session_id=41,
                role="assistant",
                content="answer",
                trace={"retrieved": ["P-17"]},
                saved_test_case_id=23,
                created_at=now,
            )
        )


def test_empty_database_upgrade_creates_legacy_workspace_and_all_tables(tmp_path):
    url = _url(tmp_path)
    run_migrations(url)
    run_migrations(url)
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        with engine.connect() as connection:
            legacy = connection.execute(text("SELECT id, slug, is_legacy FROM workspaces")).one()
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            membership_count = connection.execute(
                text("SELECT count(*) FROM workspace_memberships")
            ).scalar()
        assert {"users", "workspaces", "workspace_memberships"} <= tables
        assert legacy == (1, "legacy", 1)
        assert revision == "0014"
        assert {"auth_sessions", "oidc_login_transactions"} <= tables
        assert membership_count == 0
        for table in ("datasets", "evaluation_runs", "evaluation_jobs", "chat_sessions"):
            column = next(c for c in inspector.get_columns(table) if c["name"] == "workspace_id")
            assert column["nullable"] is False
    finally:
        engine.dispose()


def test_populated_legacy_upgrade_preserves_rows_and_backfills_related_ownership(tmp_path):
    url = _url(tmp_path, "populated.db")
    run_migrations(url, revision="0006")
    engine = create_engine(url)
    _seed_legacy_database(engine)
    engine.dispose()

    run_migrations(url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT id, name, workspace_id FROM datasets")
            ).all() == [(17, "legacy-dataset", 1)]
            assert connection.execute(
                text("SELECT id, dataset_id, workspace_id FROM evaluation_runs")
            ).all() == [(31, 17, 1)]
            assert connection.execute(
                text(
                    "SELECT id, dataset_id, workspace_id, submitted_by_user_id FROM evaluation_jobs"
                )
            ).all() == [(1, 17, 1, None)]
            assert connection.execute(
                text("SELECT id, dataset_id, workspace_id, created_by_user_id FROM chat_sessions")
            ).all() == [(41, 17, 1, None)]
            assert connection.execute(
                text("SELECT id, run_id, output, passed, metrics FROM evaluation_results")
            ).all() == [(37, 31, "answer", 1, '{"token_f1": 1.0}')]
            assert connection.execute(
                text("SELECT id, session_id, content, saved_test_case_id FROM chat_messages")
            ).all() == [(43, 41, "answer", 23)]
            assert connection.execute(text("SELECT id, dataset_id, key FROM test_cases")).all() == [
                (23, 17, "legacy-case")
            ]
            assert connection.execute(
                text("SELECT id, dataset_id, passage_id FROM passages")
            ).all() == [(29, 17, "P-17")]
            assert connection.execute(text("SELECT issuer, subject FROM users")).all() == []
            assert (
                connection.execute(text("SELECT count(*) FROM workspace_memberships")).scalar() == 0
            )
    finally:
        engine.dispose()


def test_sqlite_rejects_run_whose_workspace_differs_from_dataset(tmp_path):
    url = _url(tmp_path, "ownership-fk.db")
    run_migrations(url, revision="0006")
    engine = create_engine(url)
    _seed_legacy_database(engine)
    engine.dispose()
    run_migrations(url)

    engine = make_engine(url)
    tables = _tables(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            tables["workspaces"]
            .insert()
            .values(
                id=2,
                name="Other workspace",
                slug="other",
                is_legacy=False,
                created_at=now,
            )
        )
        existing_run = connection.execute(
            tables["evaluation_runs"].select().where(tables["evaluation_runs"].c.id == 31)
        ).one()
        mismatched_run = dict(existing_run._mapping)
        mismatched_run.pop("id")
        mismatched_run["workspace_id"] = 2

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(tables["evaluation_runs"].insert().values(**mismatched_run))
    engine.dispose()


def test_orphaned_job_stops_migration_without_deleting_data(tmp_path):
    url = _url(tmp_path, "orphan.db")
    run_migrations(url, revision="0006")
    engine = create_engine(url)
    now = datetime.now(UTC)
    tables = _tables(engine)
    with engine.begin() as connection:
        connection.execute(
            tables["evaluation_jobs"].insert().values(**_legacy_job_values(987, now))
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match=r"jobs without datasets.*IDs \[1\]"):
        run_migrations(url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM evaluation_jobs")).scalar() == 1
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert revision == "0006"
            assert "workspaces" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_membership_role_check_accepts_supported_roles_and_rejects_others(tmp_path):
    url = _url(tmp_path, "roles.db")
    run_migrations(url)
    engine = create_engine(url)
    now = datetime.now(UTC)
    tables = _tables(engine)
    with engine.begin() as connection:
        connection.execute(
            tables["users"]
            .insert()
            .values(id=1, issuer="https://id.example", subject="user-1", created_at=now)
        )
        connection.execute(
            tables["workspace_memberships"]
            .insert()
            .values(user_id=1, workspace_id=1, role="owner", created_at=now)
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            tables["users"]
            .insert()
            .values(id=2, issuer="https://id.example", subject="user-2", created_at=now)
        )
        connection.execute(
            tables["workspace_memberships"]
            .insert()
            .values(user_id=2, workspace_id=1, role="superuser", created_at=now)
        )
    engine.dispose()
