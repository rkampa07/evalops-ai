"""Chat turn pipeline: retrieve -> prompt -> generate -> live lexical check -> persist trace.

Message text and prompts are stored in the local database for the Execution Inspector and are
never written to logs. Chat results are kept apart from evaluation runs and release gates.
"""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..config import Settings
from ..engine import rag
from ..engine.scoring import EvalConfig
from ..engine.versioning import METHODOLOGY_VERSION
from ..errors import NotFoundError, UnprocessableError
from ..llm.backends import GenerationSettings
from ..llm.rag_pipeline import PROMPTS
from ..llm.retriever_cache import RetrieverCache
from ..models import ChatMessage, ChatSession, Dataset
from ..schemas import (
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionOut,
    ChatTurnOut,
    Page,
    PassageIn,
    SaveChatTestCaseIn,
    TestCaseCreate,
)
from ..services.datasets import DatasetService
from .diagnostics import diagnose
from .prompting import (
    CHAT_PROMPT_VERSION,
    Exchange,
    build_chat_messages,
    chat_prompt_hash,
    retrieval_query,
    select_history,
)
from .runtime import ChatRuntime

LIVE_CHECK_LABEL = (
    "Live lexical check (methodology {version}): word overlap and exact numbers against this "
    "turn's retrieved passages. Not verified semantic correctness; not a release-gate result."
)
RETRIEVAL_K = 3


class ChatService:
    def __init__(
        self,
        session: Session,
        runtime: ChatRuntime,
        settings: Settings,
        retriever_cache: RetrieverCache,
        *,
        workspace_id: int = 1,
        user_id: int | None = None,
    ):
        self.s = session
        self.runtime = runtime
        self.settings = settings
        self.retriever_cache = retriever_cache
        self.workspace_id = workspace_id
        self.user_id = user_id

    # ------------------------------------------------------------ sessions

    def _session(self, session_id: int) -> ChatSession:
        chat = self.s.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.created_by_user_id == self.user_id,
            )
        )
        if chat is None:
            raise NotFoundError(f"chat session {session_id} not found")
        return chat

    def _out(self, chat: ChatSession, detail: bool = False, message_count: int | None = None):
        cls = ChatSessionDetail if detail else ChatSessionOut
        out = cls.model_validate(chat)
        out.dataset_name = chat.dataset.name if chat.dataset else None
        out.message_count = len(chat.messages) if message_count is None else message_count
        return out

    def list_sessions(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> list[ChatSessionOut] | Page[ChatSessionOut]:
        paginated = limit is not None or offset is not None
        page_limit = (limit or 50) if paginated else None
        page_offset = offset or 0
        stmt = (
            select(ChatSession)
            .where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.created_by_user_id == self.user_id,
            )
            .options(joinedload(ChatSession.dataset))
            .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        )
        if page_limit is not None:
            stmt = stmt.limit(page_limit).offset(page_offset)
        chats = list(self.s.scalars(stmt))
        if not chats:
            if not paginated:
                return []
            return Page(
                items=[],
                total=self.s.scalar(
                    select(func.count())
                    .select_from(ChatSession)
                    .where(
                        ChatSession.workspace_id == self.workspace_id,
                        ChatSession.created_by_user_id == self.user_id,
                    )
                )
                or 0,
                limit=page_limit,
                offset=page_offset,
            )

        message_counts = (
            select(ChatMessage.session_id, func.count())
            .group_by(ChatMessage.session_id)
            .where(ChatMessage.session_id.in_([chat.id for chat in chats]))
        )
        counts = dict(self.s.execute(message_counts).all())
        items = [self._out(chat, message_count=counts.get(chat.id, 0)) for chat in chats]
        if not paginated:
            return items
        return Page(
            items=items,
            total=self.s.scalar(
                select(func.count())
                .select_from(ChatSession)
                .where(
                    ChatSession.workspace_id == self.workspace_id,
                    ChatSession.created_by_user_id == self.user_id,
                )
            )
            or 0,
            limit=page_limit,
            offset=page_offset,
        )

    def create_session(self, data: ChatSessionCreate) -> ChatSessionOut:
        if data.prompt_version not in PROMPTS:
            raise UnprocessableError(
                f"unknown prompt_version {data.prompt_version!r}; choose from {sorted(PROMPTS)}"
            )
        dataset = self.s.scalar(
            select(Dataset).where(
                Dataset.id == data.dataset_id, Dataset.workspace_id == self.workspace_id
            )
        )
        if dataset is None:
            raise NotFoundError(f"dataset {data.dataset_id} not found")
        if not dataset.passages:
            raise UnprocessableError("live chat needs a dataset with knowledge-base passages")
        chat = ChatSession(
            dataset_id=dataset.id,
            workspace_id=self.workspace_id,
            created_by_user_id=self.user_id,
            prompt_version=data.prompt_version,
            title=data.title,
        )
        self.s.add(chat)
        self.s.commit()
        return self._out(chat, message_count=0)

    def get_session(self, session_id: int) -> ChatSessionDetail:
        chat = self._session(session_id)
        out = self._out(chat, detail=True)
        knowledge_base = {p.passage_id: p.text for p in chat.dataset.passages}
        for message in out.messages:
            trace = message.trace
            if trace and "diagnostics" not in trace:
                # Replies recorded before diagnostics existed: computed from the stored output and
                # this turn's stored passages, shown but not written back to the trace.
                retrieved = {p["passage_id"]: p["text"] for p in trace["retrieval"]["passages"]}
                diagnostics = diagnose(message.content, retrieved, knowledge_base)
                message.trace = trace | {"diagnostics": diagnostics | {"computed_on_read": True}}
        return out

    def delete_session(self, session_id: int) -> None:
        self.s.delete(self._session(session_id))
        self.s.commit()

    # ------------------------------------------------------------ turns

    @staticmethod
    def _history(chat: ChatSession) -> list[Exchange]:
        exchanges, pending = [], None
        for message in chat.messages:
            if message.role == "user":
                pending = message.content
            elif pending is not None:
                exchanges.append(Exchange(pending, message.content))
                pending = None
        return exchanges

    def send_message(self, session_id: int, content: str) -> ChatTurnOut:
        limit = self.settings.chat_max_message_chars
        if len(content) > limit:
            raise UnprocessableError(f"message longer than {limit} characters")
        chat = self._session(session_id)
        retriever = self.retriever_cache.get(chat.dataset_id, chat.dataset.passages, k=RETRIEVAL_K)
        knowledge_base = retriever.knowledge_base

        full_history = self._history(chat)
        query, used_history = retrieval_query(content, full_history)
        retrieved, scores = retriever.retrieve_with_scores(query)
        history, truncated = select_history(
            full_history,
            self.settings.chat_history_exchanges,
            self.settings.chat_history_char_budget,
        )
        messages = build_chat_messages(chat.prompt_version, history, content, retrieved)
        settings = GenerationSettings(max_tokens=self.settings.chat_max_tokens)

        # Raises BusyError / ChatDisabledError before anything is persisted, so a retry
        # simply resends the message.
        generation, model_info = self.runtime.generate(messages, settings)

        retrieved_ids = [p["passage_id"] for p in retrieved]
        trace = {
            "retrieval": {
                "query": query,
                "used_previous_turn": used_history,
                **retriever.describe(),
                "passages": [
                    p | {"bm25_score": round(scores[p["passage_id"]], 4)} for p in retrieved
                ],
            },
            "prompt": {
                "template_version": chat.prompt_version,
                "chat_prompt_version": CHAT_PROMPT_VERSION,
                "hash": chat_prompt_hash(chat.prompt_version),
                "history_exchanges_included": len(history),
                "history_truncated": truncated,
                "messages": messages,
            },
            "model": model_info,
            "settings": asdict(settings),
            "generation": generation.metadata(),
            "live_check": self._live_check(generation.text, retrieved_ids, knowledge_base),
            # Chat-only heuristic warnings; never affect the live check, runs or gates.
            "diagnostics": diagnose(
                generation.text, {p["passage_id"]: p["text"] for p in retrieved}, knowledge_base
            ),
            "judge": {"status": "not run (optional model judge is off by default)"},
        }
        user = ChatMessage(role="user", content=content)
        assistant = ChatMessage(role="assistant", content=generation.text, trace=trace)
        chat.messages.extend([user, assistant])
        if chat.title == "New chat":
            chat.title = content[:60]
        self.s.commit()
        return ChatTurnOut(
            session=self._out(chat),
            user_message=user,
            assistant_message=assistant,
        )

    @staticmethod
    def _live_check(output: str, retrieved_ids: list[str], knowledge_base: dict) -> dict:
        """Deterministic RAG checks against THIS turn's retrieved passages only.

        Chat has no ground truth for answerability, so abstention is reported, not graded.
        """
        config = EvalConfig()
        metrics, failures = rag.evaluate_rag(
            output,
            relevant_ids=[],
            expect_abstain=False,
            retrieved_ids=retrieved_ids,
            knowledge_base=knowledge_base,
            min_groundedness=config.min_groundedness,
        )
        failures = [f for f in failures if f["check"] != "abstention"]
        return {
            "label": LIVE_CHECK_LABEL.format(version=METHODOLOGY_VERSION),
            "methodology_version": METHODOLOGY_VERSION,
            "passed_lexical_checks": not failures,
            "failures": failures,
            "abstained": metrics["abstained"],
            "abstention": "not graded (chat has no answerability ground truth)",
            "citations": metrics["citations"],
            "citations_valid": metrics["citations_valid"],
            "citations_nonexistent": metrics["citations_nonexistent"],
            "citations_not_retrieved": metrics["citations_not_retrieved"],
            # Does the cited passage itself lexically support the sentence citing it?
            "citation_pairs_total": metrics["citation_pairs_total"],
            "citation_pairs_supporting": metrics["citation_pairs_supporting"],
            "claims": metrics["claims"],
            "groundedness": metrics["groundedness"],
            "unsupported_claims": metrics["unsupported_claims"],
            "min_groundedness": config.min_groundedness,
        }

    # ------------------------------------------------------------ save as test case

    def _capture_dataset(self, source: Dataset) -> Dataset:
        """Captured cases go to '<name>-chat-captures' so the source dataset never changes."""
        name = f"{source.name}-chat-captures"[:200]
        existing = self.s.scalar(
            select(Dataset).where(Dataset.name == name, Dataset.workspace_id == self.workspace_id)
        )
        if existing is not None:
            return existing
        target = Dataset(
            name=name,
            workspace_id=self.workspace_id,
            description=f"Test cases captured from live chat over '{source.name}'. "
            "Gold evidence and abstention expectations are human-selected.",
        )
        self.s.add(target)
        self.s.flush()
        DatasetService(self.s, workspace_id=self.workspace_id).create_passages(
            target.id,
            [
                PassageIn(passage_id=p.passage_id, title=p.title, text=p.text)
                for p in source.passages
            ],
        )
        return target

    def save_test_case(self, message_id: int, data: SaveChatTestCaseIn):
        message = self.s.scalar(
            select(ChatMessage)
            .join(ChatSession)
            .where(
                ChatMessage.id == message_id,
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.created_by_user_id == self.user_id,
            )
        )
        if message is None or message.role != "assistant":
            raise NotFoundError(f"assistant message {message_id} not found")
        chat = message.session
        previous = [m for m in chat.messages if m.id < message.id and m.role == "user"]
        if not previous:
            raise UnprocessableError("no user message precedes this reply")
        known = {p.passage_id for p in chat.dataset.passages}
        unknown = sorted(set(data.relevant_passage_ids) - known)
        if unknown:
            raise UnprocessableError(f"unknown passage ids for this knowledge base: {unknown}")

        target = self._capture_dataset(chat.dataset)
        case = TestCaseCreate(
            key=data.key,
            input=data.input or previous[-1].content,
            relevant_passage_ids=data.relevant_passage_ids,
            expect_abstain=data.expect_abstain,
            required_terms=data.required_terms,
            tags=["rag", "chat-capture"],
        )
        (created,) = DatasetService(self.s, workspace_id=self.workspace_id).create_cases(
            target.id, [case]
        )
        message.saved_test_case_id = created.id
        self.s.commit()
        return created
