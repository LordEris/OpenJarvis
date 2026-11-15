"""Learning: turn conversations and documents into linked brain pages.

``KnowledgeExtractor`` asks the local model for *structured* knowledge — the
subject of each fact, its kind and the other subjects it connects to — instead
of the flat sentences of :class:`~openjarvis.memory.extractor.FactExtractor`.
``BrainMemoryService`` plugs it into the existing background memory pipeline
(same queue, same prompt-injection scanning and quarantine), writes the
knowledge into the brain, and keeps feeding the flat fact store so every
existing recall path still benefits.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

from openjarvis.brain.store import ME_TITLE, BrainStore, clean_title, normalize_type
from openjarvis.core.types import Message, Role
from openjarvis.memory.service import MemoryService
from openjarvis.memory.store import TRUST_AUTO, TRUST_UNTRUSTED, FactStore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You maintain the long-term knowledge of a personal assistant called JARVIS.
From the text below, extract the durable knowledge worth keeping:
- about the user: identity, preferences, habits, goals, routines, relationships;
- about people, projects, places, organisations or topics the user cares about;
- facts the user explicitly asked to remember or taught the assistant.
Ignore small talk, one-off requests, the assistant's opinions about itself,
and general knowledge unrelated to the user or their interests.
Write each fact as one short standalone sentence in the language of the text.

Respond with ONLY a JSON array, no prose. Each element:
{"page": "<subject; \\"Moi\\" when the fact is about the user>",
 "type": "moi|personne|projet|concept|lieu|source|autre",
 "fact": "<the fact>",
 "links": ["<other subjects this fact connects to>"]}
Respond [] when there is nothing durable.

Example — text: "User: Ma sœur Léa vient de commencer médecine à Lyon."
[{"page": "Léa", "type": "personne", "fact": "Sœur de l'utilisateur, étudiante \
en médecine à Lyon.", "links": ["Moi", "Lyon", "Médecine"]},
 {"page": "Moi", "type": "moi", "fact": "A une sœur, Léa.", "links": ["Léa"]}]"""

_MAX_ITEMS = 10
_MAX_LINKS = 6
_MAX_FACT_CHARS = 300


@dataclass
class KnowledgeItem:
    page: str
    type: str
    fact: str
    links: List[str] = field(default_factory=list)

    def flat(self) -> str:
        """The fact as one sentence for the flat fact store."""
        return self.fact if self.page == ME_TITLE else f"{self.page} : {self.fact}"


def parse_items(content: str) -> List[KnowledgeItem]:
    """Parse model output into validated knowledge items. Never raises."""
    if not content:
        return []
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    items: List[KnowledgeItem] = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            page = clean_title(str(entry.get("page", "")))
        except ValueError:
            continue
        fact = " ".join(str(entry.get("fact", "")).split())[:_MAX_FACT_CHARS]
        if not fact:
            continue
        kind = "moi" if page == ME_TITLE else normalize_type(entry.get("type"))
        links: List[str] = []
        raw_links = entry.get("links") or []
        if isinstance(raw_links, str):
            raw_links = [raw_links]
        for link in raw_links if isinstance(raw_links, list) else []:
            try:
                title = clean_title(str(link))
            except ValueError:
                continue
            if title != page and title not in links:
                links.append(title)
        key = (page.casefold(), fact.casefold())
        if key in seen:
            continue
        seen.add(key)
        items.append(KnowledgeItem(page, kind, fact, links[:_MAX_LINKS]))
        if len(items) >= _MAX_ITEMS:
            break
    return items


class KnowledgeExtractor:
    """Extract structured knowledge items with the local model."""

    def __init__(
        self,
        engine: Any,
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 900,
    ) -> None:
        self._engine = engine
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def extract_text(self, text: str) -> List[KnowledgeItem]:
        text = (text or "").strip()
        if not text:
            return []
        messages = [
            Message(role=Role.SYSTEM, content=_SYSTEM_PROMPT),
            Message(role=Role.USER, content=text),
        ]
        try:
            result = self._engine.generate(
                messages,
                model=self._model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception:  # noqa: BLE001 - learning is best-effort
            logger.debug("Knowledge extraction failed", exc_info=True)
            return []
        content = result.get("content", "") if isinstance(result, dict) else result
        return parse_items(str(content or ""))

    def extract_items(
        self, user_text: str, assistant_text: str = ""
    ) -> List[KnowledgeItem]:
        user_text = (user_text or "").strip()
        if not user_text:
            return []
        exchange = f"User: {user_text}"
        if assistant_text and assistant_text.strip():
            exchange += f"\nAssistant: {assistant_text.strip()}"
        return self.extract_text(exchange)

    # FactExtractor-compatible surface, so MemoryService can drive it.
    def extract(self, user_text: str, assistant_text: str = "") -> List[str]:
        return [i.flat() for i in self.extract_items(user_text, assistant_text)]


def learn(
    brain: BrainStore,
    items: Iterable[KnowledgeItem],
    *,
    source: str,
    scan: Optional[Any] = None,
) -> tuple[List[KnowledgeItem], List[KnowledgeItem]]:
    """Write *items* into *brain*; return (learned, quarantined).

    *scan* is ``callable(text) -> bool`` returning True when the text looks
    like a prompt injection: such items never reach the brain, whose pages
    are fed back into later prompts.
    """
    learned: List[KnowledgeItem] = []
    quarantined: List[KnowledgeItem] = []
    for item in items:
        text = " ".join([item.page, item.fact, *item.links])
        if scan is not None and scan(text):
            quarantined.append(item)
            continue
        _, added = brain.remember(
            item.page,
            item.fact,
            page_type=item.type,
            links=item.links,
            source=source,
        )
        if added:
            learned.append(item)
    if learned:
        summary = "; ".join(f"[[{i.page}]] : {i.fact}" for i in learned)
        brain.log(f"Appris ({source}) : {summary}")
    return learned, quarantined


class BrainMemoryService(MemoryService):
    """MemoryService that also grows the brain from every chat exchange."""

    def __init__(
        self,
        store: FactStore,
        extractor: KnowledgeExtractor,
        brain: BrainStore,
        **kwargs: Any,
    ) -> None:
        super().__init__(store, extractor, **kwargs)  # type: ignore[arg-type]
        self._knowledge = extractor
        self._brain = brain

    def _process(self, job: Any) -> None:
        user_text, assistant_text = job
        if self._blocks_exchange(self._scan(f"{user_text}\n{assistant_text}")):
            logger.info("Brain learning skipped: injection detected in exchange")
            return
        items = self._knowledge.extract_items(user_text, assistant_text)
        if not items:
            return
        learned, quarantined = learn(
            self._brain,
            items,
            source="conversation",
            scan=lambda text: self._flagged(self._scan(text)),
        )
        self._store.add_many_with_trust(
            [i.flat() for i in learned], source="auto", trust=TRUST_AUTO
        )
        if quarantined:
            self._store.add_many_with_trust(
                [i.flat() for i in quarantined], source="auto", trust=TRUST_UNTRUSTED
            )
            logger.info(
                "Brain: quarantined %d extracted item(s) as untrusted",
                len(quarantined),
            )
        if learned:
            logger.debug("Brain learned %d new item(s)", len(learned))


__all__ = [
    "BrainMemoryService",
    "KnowledgeExtractor",
    "KnowledgeItem",
    "learn",
    "parse_items",
]
