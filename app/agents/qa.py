"""QA agent for final SoW review."""

from __future__ import annotations

from app.services.llm import call_llm


class QAAgent:
    """Reviews and refines complete SoW drafts."""

    def review_document(self, draft: str) -> str:
        """Improve consistency and clarity while preserving enterprise tone."""
        system_prompt = (
            "You are a quality assurance editor for enterprise consulting deliverables. "
            "Improve clarity, consistency, and remove contradictions while preserving "
            "technical accuracy and formal tone. Return only the revised document text."
        )
        user_prompt = f"Review and improve this Statement of Work draft:\n\n{draft}"
        return call_llm(system_prompt=system_prompt, user_prompt=user_prompt).strip()
