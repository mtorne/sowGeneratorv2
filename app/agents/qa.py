"""QA agent for final SoW review."""

from __future__ import annotations

from app.services.llm import call_llm


class QAAgent:
    """Reviews and lightly refines complete SoW drafts."""

    def review_document(self, draft: str) -> str:
        """Apply a lightweight enterprise consistency pass."""
        system_prompt = (
            "You are a QA reviewer for enterprise Statements of Work. Perform a light review only: "
            "check internal consistency (database types, kubernetes versions, sizing), remove duplicate "
            "phrasing, and maintain professional tone. Do not rewrite entire sections unless required "
            "to resolve inconsistencies. Return only the revised document text."
        )
        user_prompt = f"Review this Statement of Work draft and apply minimal edits:\n\n{draft}"
        return call_llm(system_prompt=system_prompt, user_prompt=user_prompt).strip()
