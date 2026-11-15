"""Brain tools: let the agent search, read and grow its long-term knowledge.

The brain is a folder of linked Markdown pages (see
:mod:`openjarvis.brain.store`). Background learning already fills it from
conversations; these tools let the agent use it on purpose — look something
up before answering, remember what the user asks it to keep, follow links
between subjects, or show the knowledge galaxy.
"""

from __future__ import annotations

import webbrowser
from typing import Any, Callable, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_SEARCH_LIMIT = 5


def _default_brain() -> Any:
    from openjarvis.brain.recall import brain_dir
    from openjarvis.brain.store import BrainStore
    from openjarvis.core.config import load_config

    return BrainStore(brain_dir(load_config()))


class _BrainTool(BaseTool):
    def __init__(self, brain: Any = None) -> None:
        self._brain = brain

    @property
    def brain(self) -> Any:
        if self._brain is None:
            self._brain = _default_brain()
        return self._brain

    def _ok(self, content: str, **meta: Any) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=content, metadata=meta)

    def _fail(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=content, success=False)


@ToolRegistry.register("brain_search")
class BrainSearchTool(_BrainTool):
    """Search the brain pages for a subject."""

    tool_id = "brain_search"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="brain_search",
            description=(
                "Search your long-term memory (the brain: what you learned about"
                " the user, people, projects, places and topics). Use it before"
                " answering anything personal or about a past conversation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Words to look for."}
                },
                "required": ["query"],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.brain.recall import format_page

        query = str(params.get("query", "")).strip()
        if not query:
            return self._fail("No query provided.")
        hits = self.brain.search(query, limit=_SEARCH_LIMIT)
        if not hits:
            return self._ok(f"Nothing in the brain about {query!r} yet.", count=0)
        blocks = [format_page(page, max_facts=6) for page, _ in hits]
        return self._ok("\n\n".join(blocks), count=len(hits))


@ToolRegistry.register("brain_read")
class BrainReadTool(_BrainTool):
    """Read one brain page with the pages linking to it."""

    tool_id = "brain_read"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="brain_read",
            description=(
                "Read everything you know about one subject of your brain, with"
                " the subjects it is linked to. Use the exact page title."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Page title."}
                },
                "required": ["title"],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.brain.recall import format_page

        title = str(params.get("title", "")).strip()
        page = self.brain.find(title) if title else None
        if page is None:
            return self._fail(f"No brain page titled {title!r}. Try brain_search.")
        text = format_page(page, max_facts=40)
        backlinks = self.brain.backlinks(page.title)
        if backlinks:
            text += "\nCité par : " + ", ".join(backlinks[:20])
        return self._ok(text, title=page.title)


@ToolRegistry.register("brain_remember")
class BrainRememberTool(_BrainTool):
    """Write a durable fact into the brain."""

    tool_id = "brain_remember"

    def __init__(
        self,
        brain: Any = None,
        scanner: Optional[Callable[[str], bool]] = None,
    ) -> None:
        super().__init__(brain)
        self._scanner = scanner

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="brain_remember",
            description=(
                "Store a durable fact in your long-term memory when the user"
                " tells you something worth keeping or asks you to remember it."
                " One short sentence per call. Use page 'Moi' for facts about the"
                " user; otherwise the subject's name. List related subjects in"
                " 'links' so the knowledge stays connected."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": "Subject: 'Moi' for the user, or a name.",
                    },
                    "fact": {"type": "string", "description": "The fact to keep."},
                    "type": {
                        "type": "string",
                        "enum": [
                            "moi",
                            "personne",
                            "projet",
                            "concept",
                            "lieu",
                            "source",
                            "autre",
                        ],
                    },
                    "links": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Related subjects (page titles).",
                    },
                },
                "required": ["page", "fact"],
            },
            category="memory",
        )

    def _looks_injected(self, text: str) -> bool:
        if self._scanner is not None:
            return self._scanner(text)
        from openjarvis.memory.service import _build_scanner

        scanner = _build_scanner()
        if scanner is None:
            return False
        try:
            result = scanner.scan(text)
        except Exception:  # noqa: BLE001 - the scanner is a defence layer
            return False
        level = getattr(result, "threat_level", "")
        name = str(getattr(level, "value", level) or "").lower()
        return not getattr(result, "is_clean", True) and name in ("high", "critical")

    def execute(self, **params: Any) -> ToolResult:
        page = str(params.get("page", "")).strip()
        fact = str(params.get("fact", "")).strip()
        if not page or not fact:
            return self._fail("Both 'page' and 'fact' are required.")
        links = params.get("links") or []
        if isinstance(links, str):
            links = [links]
        links = [str(link) for link in links if str(link).strip()][:8]
        if self._looks_injected(" ".join([page, fact, *links])):
            return self._fail("Refused: this text looks like an injected instruction.")
        try:
            saved, added = self.brain.remember(
                page,
                fact,
                page_type=params.get("type"),
                links=links,
                source="demandé",
            )
        except ValueError as exc:
            return self._fail(f"Could not save: {exc}")
        if added:
            self.brain.log(f"Appris (demandé) : [[{saved.title}]] : {fact}")
            return self._ok(
                f"Noted in the brain page '{saved.title}'.", page=saved.title
            )
        return self._ok(f"Already known (page '{saved.title}').", page=saved.title)


@ToolRegistry.register("brain_galaxy")
class BrainGalaxyTool(_BrainTool):
    """Draw the brain as a 3D galaxy and open it in the browser."""

    tool_id = "brain_galaxy"

    def __init__(
        self, brain: Any = None, opener: Optional[Callable[[str], Any]] = None
    ) -> None:
        super().__init__(brain)
        self._opener = opener or (lambda url: webbrowser.open(url, new=2))

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="brain_galaxy",
            description=(
                "Show the user your knowledge galaxy: every subject you know is"
                " a star, linked to related ones. Opens it in the web browser."
            ),
            parameters={"type": "object", "properties": {}},
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.brain.galaxy import write_galaxy

        path = write_galaxy(self.brain)
        graph = self.brain.graph()
        stars = len(graph["nodes"])
        opened = self._opener(path.as_uri())
        where = "opened in the browser" if opened is not False else f"saved to {path}"
        return self._ok(
            f"Galaxy with {stars} star(s) {where}.", path=str(path), stars=stars
        )


__all__: List[str] = [
    "BrainGalaxyTool",
    "BrainReadTool",
    "BrainRememberTool",
    "BrainSearchTool",
]
