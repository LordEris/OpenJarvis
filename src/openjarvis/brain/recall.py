"""Recall: bring the brain pages relevant to a question into the prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from openjarvis.brain.store import ME_TITLE, TYPES, BrainStore, Page, fact_text


def brain_dir(config: Any) -> Path:
    from openjarvis.brain.store import default_brain_dir

    path = str(getattr(getattr(config, "brain", None), "path", "") or "").strip()
    return Path(path).expanduser() if path else default_brain_dir()


def brain_for(config: Any) -> Optional[BrainStore]:
    """The configured brain when ``[brain] enabled = true``, else None."""
    brain_cfg = getattr(config, "brain", None)
    if brain_cfg is None or not getattr(brain_cfg, "enabled", False):
        return None
    return BrainStore(
        brain_dir(config),
        context_pages=int(getattr(brain_cfg, "context_pages", 3) or 0),
    )


def format_page(page: Page, max_facts: int = 12) -> str:
    kind = TYPES.get(page.type, TYPES["autre"])[1]
    lines = [f"# {page.title} ({kind.lower()})"]
    facts = page.facts[-max_facts:]
    if facts:
        lines += [f"- {fact_text(f)}" for f in facts]
    elif page.body:
        lines.append(page.body[:800])
    if page.links:
        lines.append("Liens : " + ", ".join(page.links[:12]))
    return "\n".join(lines)


def brain_results(
    brain: BrainStore, query: str, top_k: Optional[int] = None
) -> List[Any]:
    """Pages to recall for *query*, as retrieval results ("Moi" always first).

    Returns ``openjarvis.tools.storage._stubs.RetrievalResult`` objects so they
    flow through the same budgeting and formatting as indexed documents.
    """
    from openjarvis.tools.storage._stubs import RetrievalResult

    if top_k is None:
        top_k = brain.context_pages
    if top_k <= 0:
        return []
    results: List[Any] = []
    seen = set()
    me = brain.find(ME_TITLE)
    if me is not None and me.facts:
        results.append(
            RetrievalResult(
                content=format_page(me),
                score=1.0,
                source=f"cerveau/{me.title}",
                metadata={"brain": True},
            )
        )
        seen.add(me.key)
    for page, score in brain.search(query, limit=top_k + 1):
        if page.key in seen:
            continue
        seen.add(page.key)
        results.append(
            RetrievalResult(
                content=format_page(page),
                score=min(1.0, score / 10.0),
                source=f"cerveau/{page.title}",
                metadata={"brain": True},
            )
        )
        if len(results) >= top_k + (1 if me is not None and me.facts else 0):
            break
    return results


__all__ = ["brain_dir", "brain_for", "brain_results", "format_page"]
