import pytest

from evalops.engine import rag

KB = {
    "KB-RET-01": "Most items can be returned within 30 days of delivery for a full refund.",
    "KB-RET-03": "Return shipping is free for orders over $75. For orders of $75 or less, a "
    "$9.95 return shipping fee is deducted from the refund.",
    "KB-SHP-02": "We ship to Canada only. Canadian orders usually arrive in 7 to 12 business days.",
    "KB-PAY-01": "Kestrel never takes card numbers over chat. Payments are accepted only through "
    "secure checkout on the website.",
}


class TestRetrieval:
    def test_bm25_ranks_relevant_passage_first(self):
        index = rag.BM25Index(KB)
        assert index.search("How long do I have to return an item?", k=2)[0] == "KB-RET-01"
        assert index.search("shipping to Canada", k=1) == ["KB-SHP-02"]

    def test_search_is_deterministic_and_bounded(self):
        index = rag.BM25Index(KB)
        assert index.search("refund", k=3) == index.search("refund", k=3)
        assert len(index.search("refund", k=10)) == len(KB)

    def test_query_with_no_known_terms_returns_ranked_ties_by_id(self):
        assert rag.BM25Index(KB).search("zzzz qqqq", k=2) == ["KB-PAY-01", "KB-RET-01"]

    def test_recall_and_reciprocal_rank(self):
        m = rag.retrieval_metrics(["a", "b", "c"], ["b", "x"])
        assert m == {"retrieval_recall": 0.5, "reciprocal_rank": 0.5, "retrieval_k": 3}
        assert rag.retrieval_metrics(["a"], ["a"])["reciprocal_rank"] == 1.0
        assert rag.retrieval_metrics(["a"], ["z"]) == {
            "retrieval_recall": 0.0,
            "reciprocal_rank": 0.0,
            "retrieval_k": 1,
        }

    def test_retrieval_metrics_need_relevant_ids(self):
        with pytest.raises(ValueError):
            rag.retrieval_metrics(["a"], [])


class TestCitations:
    def test_parse_single_multiple_and_grouped(self):
        text = "Returns take 30 days [KB-RET-01]. Fees apply [KB-RET-03, KB-RET-01; KB-X-9]."
        assert rag.parse_citations(text) == ["KB-RET-01", "KB-RET-03", "KB-X-9"]

    def test_ignores_non_citation_brackets(self):
        assert rag.parse_citations("Use [this link] or [1].") == []


class TestClaims:
    def test_split_sentences_and_attach_citations(self):
        claims = rag.split_claims(
            "You can return items within 30 days [KB-RET-01]. Refunds go to the original "
            "payment method! Hope this helps."
        )
        assert [c.text for c in claims] == [
            "You can return items within 30 days.",
            "Refunds go to the original payment method!",
        ]
        assert claims[0].citations == ["KB-RET-01"] and claims[1].citations == []

    def test_decimal_numbers_do_not_split_sentences(self):
        claims = rag.split_claims("A $9.95 fee is deducted from the refund [KB-RET-03].")
        assert len(claims) == 1 and "$9.95" in claims[0].text

    def test_abstention_sentence_is_not_a_claim(self):
        assert rag.split_claims("I don't have that information in our policies.") == []

    def test_bullets_and_newlines_split(self):
        claims = rag.split_claims("- Orders over $75 ship returns free\n- Smaller orders pay $9.95")
        assert len(claims) == 2


class TestNumbers:
    def test_extracts_digits_money_and_words(self):
        assert rag.extract_numbers("a $9.95 fee within thirty days, 7 to 12 days") == {
            "9.95",
            "30",
            "7",
            "12",
        }

    def test_thousands_separator(self):
        assert rag.extract_numbers("over 1,000 points") == {"1000"}


class TestLexicalSupport:
    def test_paraphrase_with_matching_number_is_supported(self):
        r = rag.lexical_support("Items can be returned for a refund within 30 days.", KB)
        assert r.supported and r.passage_id == "KB-RET-01"

    def test_changed_number_is_unsupported(self):
        r = rag.lexical_support("Items can be returned for a refund within 60 days.", KB)
        assert not r.supported and r.missing_numbers == ["60"]

    def test_unrelated_claim_is_unsupported(self):
        r = rag.lexical_support("We offer free gift wrapping on all orders.", KB)
        assert not r.supported and r.coverage < rag.LEXICAL_SUPPORT_THRESHOLD

    def test_restricted_to_given_passages(self):
        r = rag.lexical_support(
            "Items can be returned within 30 days.", {"KB-SHP-02": KB["KB-SHP-02"]}
        )
        assert not r.supported


class TestAbstention:
    @pytest.mark.parametrize(
        "text",
        [
            "I don't have that information in our policies.",
            "I do not have that information.",
            "Sorry, our policies don't cover that.",
        ],
    )
    def test_detects_abstention(self, text):
        assert rag.is_abstention(text)

    def test_normal_answer_is_not_abstention(self):
        assert not rag.is_abstention("You can return items within 30 days [KB-RET-01].")


class TestEvaluateRag:
    def evaluate(self, output, **kw):
        args = {
            "relevant_ids": ["KB-RET-01"],
            "expect_abstain": False,
            "retrieved_ids": ["KB-RET-01", "KB-RET-03", "KB-SHP-02"],
            "knowledge_base": KB,
            "min_groundedness": 0.8,
        } | kw
        return rag.evaluate_rag(output, **args)

    def checks(self, failures):
        return sorted(f["check"] for f in failures)

    def test_grounded_cited_answer_passes(self):
        m, failures = self.evaluate(
            "You can return most items within 30 days for a full refund [KB-RET-01]."
        )
        assert failures == []
        assert m["retrieval_recall"] == 1.0 and m["reciprocal_rank"] == 1.0
        assert m["groundedness"] == 1.0 and m["unsupported_claims"] == []
        assert m["citations_total"] == 1 and m["citations_valid"] == 1
        assert m["abstained"] is False and m["abstention_correct"] is True

    def test_unsupported_claim_fails_groundedness(self):
        m, failures = self.evaluate(
            "You can return items within 30 days [KB-RET-01]. We also refund shipping costs "
            "for every order and give a 20% coupon [KB-RET-01]."
        )
        assert "groundedness" in self.checks(failures)
        assert m["groundedness"] == 0.5 and len(m["unsupported_claims"]) == 1

    def test_nonexistent_citation_fails(self):
        m, failures = self.evaluate("Returns are accepted within 30 days [KB-RET-99].")
        assert "citations" in self.checks(failures)
        assert m["citations_nonexistent"] == ["KB-RET-99"]

    def test_citation_not_in_retrieved_context_fails(self):
        m, failures = self.evaluate(
            "Payments are only accepted through secure checkout on the website [KB-PAY-01]."
        )
        assert m["citations_not_retrieved"] == ["KB-PAY-01"]
        assert "citations" in self.checks(failures)

    def test_answer_without_citations_fails(self):
        _, failures = self.evaluate("You can return most items within 30 days for a full refund.")
        assert "citations" in self.checks(failures)

    def test_expected_abstention(self):
        m, failures = self.evaluate(
            "I don't have that information in our policies.", relevant_ids=[], expect_abstain=True
        )
        assert failures == [] and m["abstained"] and m["abstention_correct"]
        assert m["groundedness"] is None and "retrieval_recall" not in m

    def test_answering_an_unanswerable_question_fails(self):
        m, failures = self.evaluate(
            "Yes, we offer free gift wrapping on all orders.", relevant_ids=[], expect_abstain=True
        )
        assert "abstention" in self.checks(failures)
        assert m["abstention_correct"] is False

    def test_declining_an_answerable_question_fails(self):
        m, failures = self.evaluate("I don't have that information in our policies.")
        assert self.checks(failures) == ["abstention"]
        assert m["abstained"] and not m["abstention_correct"]

    def test_unknown_retrieved_ids_rejected(self):
        with pytest.raises(ValueError, match="unknown passage"):
            self.evaluate("x", retrieved_ids=["NOPE-1"])
