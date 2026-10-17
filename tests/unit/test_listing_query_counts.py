from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from evalops.chat.service import ChatService
from evalops.config import Settings
from evalops.db import Base
from evalops.models import (
    ChatMessage,
    ChatSession,
    Dataset,
    EvaluationRun,
    Passage,
    TestCase,
)
from evalops.services.datasets import DatasetService


def _seed(engine, count: int) -> None:
    with Session(engine) as session:
        datasets = []
        for index in range(count):
            dataset = Dataset(name=f"dataset-{index:03}", description=f"description-{index}")
            dataset.passages = [
                Passage(passage_id=f"P-{index}-{p}", text=f"passage {p}")
                for p in range(index % 3 + 1)
            ]
            datasets.append(dataset)
        session.add_all(datasets)
        session.flush()

        for index, dataset in enumerate(datasets):
            dataset.test_cases.append(
                TestCase(key="case-1", input="question", content_hash="a" * 64)
            )
            for _run_index in range(2):
                dataset.runs.append(
                    EvaluationRun(
                        model_name="synthetic",
                        prompt_version="test",
                        config={},
                        summary={},
                        pass_rate=1.0,
                    )
                )
            chat = ChatSession(
                dataset_id=dataset.id,
                title=f"session-{index}",
                prompt_version="rag-v1",
                created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
            )
            chat.messages = [
                ChatMessage(role="user", content="question"),
                ChatMessage(role="assistant", content="answer"),
            ]
            session.add(chat)
        session.commit()


def _count_statements(engine, operation):
    count = 0

    def before_cursor_execute(*_args):
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        result = operation()
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    return result, count


@pytest.mark.parametrize("record_count", [1, 10, 100])
def test_dataset_listing_uses_fixed_query_count_and_preserves_counts(record_count: int) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    _seed(engine, record_count)

    with Session(engine) as session:
        rows, query_count = _count_statements(
            engine, lambda: DatasetService(session).list_datasets()
        )

    assert query_count == 4  # cases, runs, passages, and ordered dataset list
    assert [row.name for row in rows] == [f"dataset-{i:03}" for i in range(record_count)]
    assert all(row.test_case_count == 1 and row.run_count == 2 for row in rows)
    assert [row.passage_count for row in rows] == [i % 3 + 1 for i in range(record_count)]
    engine.dispose()


@pytest.mark.parametrize("record_count", [1, 10, 100])
def test_chat_session_listing_uses_fixed_query_count_and_preserves_fields(
    record_count: int,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    _seed(engine, record_count)

    with Session(engine) as session:
        rows, query_count = _count_statements(
            engine,
            lambda: ChatService(session, None, Settings(), None).list_sessions(),
        )

    assert query_count == 2  # sessions joined to datasets, then grouped message counts
    assert [row.title for row in rows] == [f"session-{i}" for i in reversed(range(record_count))]
    assert all(row.dataset_name is not None and row.message_count == 2 for row in rows)
    engine.dispose()
