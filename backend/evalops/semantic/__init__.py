"""Experimental claim-level semantic evaluation (Phase 4C).

Isolated: not used by methodology 2.0.0 scoring, runs, comparisons, release gates or live chat.
`verify(question, claim, evidence)` classifies one claim as supported, contradicted,
insufficient_evidence or needs_human_review. Citations are not an input; citation validity stays
a separate diagnostic. Real NLI inference needs an optional dependency (torch) and a pinned,
already-downloaded model; tests use the fixture backend.
"""
