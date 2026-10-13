"""Optional local-LLM tooling for generating and judging RAG answers.

Used by offline scripts (backend/scripts/rag_*.py), never by the API process, so the server
stays deterministic and never holds model weights in memory. MLX is imported lazily: without
the `local-ai` extra installed, the fixture backend still works.
"""
