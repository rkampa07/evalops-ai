import pytest

from evalops.engine import metrics as m


class TestNormalization:
    def test_casefold_punctuation_whitespace(self):
        assert m.normalize_text("  Hello,   WORLD!! ") == "hello world"

    def test_case_sensitive_keeps_case(self):
        assert m.normalize_text("Hello World", case_sensitive=True) == "Hello World"

    def test_unicode_nfkc(self):
        # full-width characters normalize to ASCII
        assert m.normalize_text("ＡＢＣ") == "abc"

    def test_tokenize_empty(self):
        assert m.tokenize("   ...  ") == []


class TestExactMatch:
    def test_normalized_equal(self):
        assert m.exact_match("The answer is 42.", "the answer is 42")

    def test_different(self):
        assert not m.exact_match("answer is 41", "answer is 42")

    def test_case_sensitive(self):
        assert not m.exact_match("Paris", "paris", case_sensitive=True)


class TestTokenScores:
    def test_identical(self):
        s = m.token_scores("a b c", "a b c")
        assert (s.precision, s.recall, s.f1) == (1.0, 1.0, 1.0)

    def test_partial_overlap(self):
        # pred: the cat sat (3); ref: the cat sat down (4); overlap 3
        s = m.token_scores("the cat sat", "the cat sat down")
        assert s.precision == 1.0
        assert s.recall == pytest.approx(0.75)
        assert s.f1 == pytest.approx(2 * 1.0 * 0.75 / 1.75)

    def test_multiset_overlap_counts_duplicates_once_per_occurrence(self):
        s = m.token_scores("yes yes yes", "yes no")
        assert s.precision == pytest.approx(1 / 3)
        assert s.recall == pytest.approx(1 / 2)

    def test_no_overlap(self):
        assert m.token_scores("alpha", "beta").f1 == 0.0

    def test_both_empty(self):
        assert m.token_scores("", "").f1 == 1.0

    def test_one_empty(self):
        assert m.token_scores("", "x").f1 == 0.0
        assert m.token_scores("x", "").f1 == 0.0


class TestTerms:
    def test_phrase_match_on_token_boundaries(self):
        assert m.contains_term("Returns within 30 days.", "30 days")
        assert not m.contains_term("We refunded it", "refund")

    def test_case_insensitive_default(self):
        assert m.contains_term("Use SECURE Checkout", "secure checkout")

    def test_punctuation_in_term(self):
        assert m.contains_term("A $500 credit", "$500")

    def test_blank_term_never_matches(self):
        assert not m.contains_term("anything", "!!!")

    def test_missing_and_present(self):
        text = "photo within 14 days"
        assert m.missing_terms(text, ["photo", "receipt"]) == ["receipt"]
        assert m.present_terms(text, ["guarantee", "photo"]) == ["photo"]


SCHEMA = {
    "type": "object",
    "required": ["id", "qty"],
    "properties": {"id": {"type": "string"}, "qty": {"type": "integer", "minimum": 1}},
}


class TestJson:
    def test_valid(self):
        r = m.validate_json_output('{"id": "a", "qty": 2}', SCHEMA)
        assert r.parsed and r.valid and r.errors == []

    def test_whitespace_tolerated(self):
        assert m.validate_json_output('\n {"id": "a", "qty": 1}\n', SCHEMA).valid

    def test_not_json(self):
        r = m.validate_json_output("Here you go: {...}", SCHEMA)
        assert not r.parsed and not r.valid
        assert "not valid JSON" in r.errors[0]

    def test_schema_violation_reports_path(self):
        r = m.validate_json_output('{"id": "a", "qty": 0}', SCHEMA)
        assert r.parsed and not r.valid
        assert r.errors[0].startswith("qty:")

    def test_missing_required_at_root(self):
        r = m.validate_json_output('{"id": "a"}', SCHEMA)
        assert r.errors[0].startswith("<root>:")

    def test_invalid_schema_definition_rejected(self):
        with pytest.raises(ValueError, match="invalid JSON schema"):
            m.check_json_schema_definition({"type": "not-a-type"})


class TestPercentile:
    def test_empty(self):
        assert m.percentile([], 50) is None

    def test_single(self):
        assert m.percentile([7], 95) == 7.0

    def test_linear_interpolation_matches_numpy_default(self):
        values = [10, 20, 30, 40]
        assert m.percentile(values, 50) == 25.0
        assert m.percentile(values, 95) == pytest.approx(38.5)
        assert m.percentile(values, 0) == 10.0
        assert m.percentile(values, 100) == 40.0

    def test_out_of_range(self):
        with pytest.raises(ValueError):
            m.percentile([1, 2], 101)


class TestRegexSafety:
    EVIL = {"type": "string", "pattern": "^(a|a)*$"}

    def test_catastrophic_pattern_times_out_and_fails_closed(self):
        import json
        import time

        start = time.perf_counter()
        r = m.validate_json_output(json.dumps("a" * 40 + "!"), self.EVIL)
        assert time.perf_counter() - start < 1.0
        assert r.parsed and not r.valid
        assert "evaluation limit" in r.errors[0]

    def test_normal_patterns_behave_as_before(self):
        schema = {"type": "string", "pattern": "^KH-[0-9]{5}$"}
        assert m.validate_json_output('"KH-10442"', schema).valid
        r = m.validate_json_output('"KH-1"', schema)
        assert r.errors == ["<root>: 'KH-1' does not match '^KH-[0-9]{5}$'"]

    def test_property_names_pattern_is_also_protected(self):
        schema = {"type": "object", "propertyNames": {"pattern": "^(a|a)*$"}}
        r = m.validate_json_output('{"' + "a" * 40 + '!": 1}', schema)
        assert not r.valid and "evaluation limit" in r.errors[0]

    def test_run_budget_exhaustion_fails_closed(self):
        with m.regex_time_budget(0.0):
            r = m.validate_json_output('"abc"', {"type": "string", "pattern": "^a"})
        assert not r.valid and "budget" in r.errors[0]

    def test_budget_is_scoped(self):
        with m.regex_time_budget(0.0):
            pass
        assert m.validate_json_output('"abc"', {"type": "string", "pattern": "^a"}).valid

    def test_legacy_unsafe_schema_is_not_evaluated(self):
        # A schema stored before the save-time rule existed must not bypass it at runtime.
        schema = {"type": "object", "patternProperties": {"^(a|a)*$": {}}}
        r = m.validate_json_output('{"x": 1}', schema)
        assert not r.valid and "patternProperties" in r.errors[0]

    def test_unresolvable_ref_fails_case_instead_of_crashing(self):
        r = m.validate_json_output("{}", {"$ref": "https://example.invalid/schema.json"})
        assert not r.valid and r.errors[0].startswith("schema evaluation error")

    @pytest.mark.parametrize(
        ("schema", "message"),
        [
            ({"patternProperties": {"x": {}}}, "patternProperties"),
            ({"pattern": "("}, "regex"),  # rejected by jsonschema's own meta-schema check
            ({"pattern": "a" * 1001}, "longer than"),
            ({"allOf": [{"pattern": "a"}] * 51}, "too many patterns"),
        ],
    )
    def test_definition_checks(self, schema, message):
        with pytest.raises(ValueError, match=message):
            m.check_json_schema_definition(schema)
