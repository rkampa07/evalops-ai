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


def test_upgrade_preserves_and_backfills_legacy_data(tmp_path):
    """Build a database at revision 0001 with legacy rows, then upgrade to head."""
    import json

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from evalops.config import Settings
    from evalops.engine.versioning import case_definition, definition_hash
    from evalops.main import create_app

    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    run_migrations(url, revision="0001")
    engine = create_engine(url)
    t0, t_run, t_edit = (
        "2026-10-01 10:00:00.000000",
        "2026-10-02 10:00:00.000000",
        "2026-10-03 10:00:00.000000",
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO datasets (id, name, description, created_at, updated_at) "
                "VALUES (1, 'legacy', '', :t, :t)"
            ),
            {"t": t0},
        )
        for cid, key, updated in ((1, "stable", t0), (2, "edited_later", t_edit)):
            conn.execute(
                text(
                    "INSERT INTO test_cases (id, dataset_id, key, input, expected_output, "
                    "required_terms, forbidden_terms, json_schema, max_latency_ms, tags, "
                    "created_at, updated_at) VALUES (:id, 1, :key, 'q', NULL, :req, '[]', NULL, "
                    "NULL, '[]', :t0, :upd)"
                ),
                {"id": cid, "key": key, "req": json.dumps(["yes"]), "t0": t0, "upd": updated},
            )
        cfg = json.dumps(
            {
                "min_token_f1": 0.5,
                "require_exact_match": False,
                "case_sensitive": False,
                "default_max_latency_ms": None,
            }
        )
        for run_id, passed in ((1, 1), (2, 0)):
            summary = {
                "total_cases": 2,
                "responded_cases": 2,
                "passed_cases": 1 + passed,
                "failed_cases": 1 - passed,
                "pass_rate": (1 + passed) / 2,
                "exact_match_rate": None,
                "mean_token_precision": None,
                "mean_token_recall": None,
                "mean_token_f1": None,
                "required_terms_pass_rate": (1 + passed) / 2,
                "forbidden_terms_pass_rate": None,
                "forbidden_violation_count": 0,
                "schema_pass_rate": None,
                "latency_pass_rate": None,
                "latency_mean_ms": None,
                "latency_p50_ms": None,
                "latency_p95_ms": None,
                "latency_max_ms": None,
                "failure_counts": {},
                "tag_pass_rates": {},
            }
            conn.execute(
                text(
                    "INSERT INTO evaluation_runs (id, dataset_id, model_name, prompt_version, "
                    "label, status, config, summary, pass_rate, created_at) VALUES "
                    "(:id, 1, 'm', :pv, '', 'completed', :cfg, :summary, :pr, :t)"
                ),
                {
                    "id": run_id,
                    "pv": f"v{run_id}",
                    "cfg": cfg,
                    "summary": json.dumps(summary),
                    "pr": summary["pass_rate"],
                    "t": t_run,
                },
            )
            for case_id, key, ok in ((1, "stable", passed), (2, "edited_later", 1)):
                conn.execute(
                    text(
                        "INSERT INTO evaluation_results (run_id, test_case_id, case_key, output, "
                        "latency_ms, passed, metrics, failures, tags) VALUES "
                        "(:run, :case, :key, 'out', NULL, :ok, :metrics, '[]', '[]')"
                    ),
                    {
                        "run": run_id,
                        "case": case_id,
                        "key": key,
                        "ok": ok,
                        "metrics": json.dumps(
                            {"responded": True, "required_terms_passed": bool(ok)}
                        ),
                    },
                )
    engine.dispose()

    run_migrations(url)  # upgrade 0001 -> head

    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM evaluation_results")).scalar() == 4
        assert conn.execute(text("SELECT version FROM datasets")).scalar() == 1
        runs = conn.execute(
            text("SELECT methodology_version, dataset_version FROM evaluation_runs")
        ).all()
        assert runs == [("1.0.0", None), ("1.0.0", None)]
        hashes = dict(conn.execute(text("SELECT key, content_hash FROM test_cases")).all())
        results = conn.execute(
            text("SELECT case_key, case_hash, case_version FROM evaluation_results")
        ).all()
    engine.dispose()

    expected = definition_hash(
        case_definition(
            {
                "input": "q",
                "expected_output": None,
                "required_terms": ["yes"],
                "forbidden_terms": [],
                "json_schema": None,
                "max_latency_ms": None,
            }
        )
    )
    assert hashes["stable"] == expected
    for key, case_hash, case_version in results:
        if key == "stable":
            assert (case_hash, case_version) == (expected, 1)
        else:  # edited after the runs: the scored definition can't be proven -> unknown
            assert (case_hash, case_version) == (None, None)

    settings = Settings(database_url=url, allowed_hosts=["testserver"], cors_origins=[])
    with TestClient(create_app(settings)) as c:
        cmp = c.get("/evaluations/compare", params={"baseline_run_id": 1, "candidate_run_id": 2})
        assert cmp.status_code == 200, cmp.text
        body = cmp.json()
        changes = {x["case_key"]: x["change"] for x in body["case_changes"]}
        assert changes == {"stable": "regressed", "edited_later": "definition_unknown"}
        assert body["has_regression"] is True and body["compatible"] is True
        # a new run under the current MAJOR methodology (2.x) is not comparable with 1.0.0 runs
        new = c.post(
            "/evaluations/runs",
            json={
                "dataset_id": 1,
                "model_name": "m",
                "prompt_version": "v3",
                "responses": [
                    {"case_key": "stable", "output": "yes"},
                    {"case_key": "edited_later", "output": "yes"},
                ],
            },
        ).json()
        cmp = c.get(
            "/evaluations/compare", params={"baseline_run_id": 1, "candidate_run_id": new["id"]}
        ).json()
        assert cmp["compatible"] is False and cmp["has_regression"] is False
        assert any(
            i["kind"] == "methodology_version" and i["severity"] == "error"
            for i in cmp["compatibility_issues"]
        )
