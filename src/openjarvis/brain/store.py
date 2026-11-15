"""The brain: JARVIS's long-term knowledge as a folder of linked Markdown pages.

Each page is one thing JARVIS knows about — the user ("Moi"), a person, a
project, a concept, a place, a source document — written as plain Markdown
with ``[[wikilinks]]`` between pages. The folder doubles as an Obsidian vault,
so the user can read, fix or delete anything JARVIS learned, and the links
form the graph drawn as the knowledge galaxy.

Layout::

    Cerveau/
      Moi.md                     the user, always the centre of the galaxy
      personnes/Alice.md
      projets/JARVIS.md
      concepts/…  lieux/…  sources/…  autres/…
      journal/2026-11-15.md      what was learned each day

Page format::

    ---
    type: projet
    cree: 2026-11-15
    maj: 2026-11-16
    ---
    # JARVIS

    ## Ce que je sais
    - 2026-11-15 : Installé sur Windows et Zorin. (conversation)

    ## Liens
    [[Moi]] · [[OpenJarvis]]

Only this module writes the folder; titles are sanitised into file names, so
no caller can make it write outside the brain directory.
"""

from __future__ import annotations

import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ME_TITLE = "Moi"

# type -> (folder, label, galaxy colour)
TYPES: Dict[str, Tuple[str, str, str]] = {
    "moi": ("", "Moi", "#ffd166"),
    "personne": ("personnes", "Personnes", "#ff8fab"),
    "projet": ("projets", "Projets", "#7bdff2"),
    "concept": ("concepts", "Concepts", "#b4a7ff"),
    "lieu": ("lieux", "Lieux", "#95e06c"),
    "source": ("sources", "Sources", "#f7a072"),
    "journal": ("journal", "Journal", "#8d99ae"),
    "autre": ("autres", "Autres", "#e0e6f0"),
}
_TYPE_ALIASES = {
    "person": "personne",
    "personnes": "personne",
    "people": "personne",
    "project": "projet",
    "projets": "projet",
    "idee": "concept",
    "idea": "concept",
    "sujet": "concept",
    "topic": "concept",
    "place": "lieu",
    "ville": "lieu",
    "document": "source",
    "article": "source",
    "user": "moi",
    "utilisateur": "moi",
}

_FACTS_HEADING = "## Ce que je sais"
_LINKS_HEADING = "## Liens"
_WIKILINK_RE = re.compile(r"\[\[([^\]\|#\n]+)(?:#[^\]\|\n]*)?(?:\|[^\]\n]*)?\]\]")
_FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
_MAX_TITLE = 80
_MAX_FACT = 600
_GHOST_EXCERPT = "Pas encore de page : JARVIS en a seulement entendu parler."


def fold(text: str) -> str:
    """Accent- and case-insensitive key: 'Équipe Projet' -> 'equipe projet'."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", stripped.casefold()).split())


def normalize_type(value: Optional[str]) -> str:
    key = fold(value or "")
    key = _TYPE_ALIASES.get(key, key)
    return key if key in TYPES else "autre"


def clean_title(title: str) -> str:
    """Make *title* safe as both a file name and a wikilink target."""
    text = _BAD_FILENAME.sub(" ", str(title))
    text = " ".join(text.split()).strip(" .")
    if not text:
        raise ValueError("empty title")
    if fold(text) in ("moi", "me", "myself", "utilisateur", "user"):
        return ME_TITLE
    return text[:_MAX_TITLE].rstrip(" .")


def default_brain_dir() -> Path:
    """``~/Documents/Cerveau``, following a OneDrive-redirected Documents."""
    home = Path.home()
    candidates = [home / "Documents"]
    if os.name == "nt":
        candidates.insert(0, home / "OneDrive" / "Documents")
    for docs in candidates:
        if docs.is_dir():
            return docs / "Cerveau"
    return home / "Documents" / "Cerveau"


@dataclass
class Page:
    title: str
    type: str
    path: Path
    created: str = ""
    updated: str = ""
    facts: List[str] = field(default_factory=list)
    body: str = ""
    links: List[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return fold(self.title)


def _parse_front(text: str) -> Tuple[Dict[str, str], str]:
    match = _FRONT_RE.match(text)
    if not match:
        return {}, text
    meta: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[match.end() :]


def parse_page(path: Path, text: str) -> Page:
    meta, rest = _parse_front(text)
    title_match = re.search(r"^# (.+)$", rest, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    facts: List[str] = []
    section = None
    for line in rest.splitlines():
        if line.startswith("## "):
            section = line.strip()
            continue
        if section == _FACTS_HEADING and line.startswith("- "):
            facts.append(line[2:].strip())
    links: List[str] = []
    seen = set()
    for target in _WIKILINK_RE.findall(rest):
        target = target.strip()
        if target and fold(target) not in seen and fold(target) != fold(title):
            seen.add(fold(target))
            links.append(target)
    page_type = normalize_type(meta.get("type"))
    if not meta.get("type"):
        # Hand-written page without front matter: its folder tells the kind.
        folder = fold(path.parent.name)
        page_type = next(
            (k for k, v in TYPES.items() if v[0] and fold(v[0]) == folder), "autre"
        )
    if fold(title) == fold(ME_TITLE):
        page_type = "moi"
    return Page(
        title=title,
        type=page_type,
        path=path,
        created=meta.get("cree", ""),
        updated=meta.get("maj", ""),
        facts=facts,
        body=rest.strip(),
        links=links,
    )


def render_page(page: Page) -> str:
    facts = "\n".join(f"- {fact}" for fact in page.facts) or "- (rien pour l'instant)"
    links = " · ".join(f"[[{t}]]" for t in page.links)
    parts = [
        "---",
        f"type: {page.type}",
        f"cree: {page.created}",
        f"maj: {page.updated}",
        "---",
        f"# {page.title}",
        "",
        _FACTS_HEADING,
        facts,
    ]
    if links:
        parts += ["", _LINKS_HEADING, links]
    return "\n".join(parts) + "\n"


def _replace_section(text: str, heading: str, body: str) -> str:
    """Replace the body of ``heading`` (up to the next ``## ``), or append it."""
    pattern = re.compile(
        rf"^{re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
    )
    block = f"{heading}\n{body}\n\n" if body else ""
    if pattern.search(text):
        return pattern.sub(lambda _m: block, text, count=1)
    if not body:
        return text
    return text.rstrip() + "\n\n" + block


def update_managed_sections(original: str, page: Page) -> str:
    """Rewrite only what JARVIS owns in an existing page.

    The facts section, the links section and the ``maj`` date are updated;
    everything else — notes the user wrote by hand, their own headings and
    links, extra front-matter keys — is kept as is.
    """
    text = original
    match = _FRONT_RE.match(text)
    if match:
        front = match.group(1)
        if re.search(r"^maj:.*$", front, re.MULTILINE):
            front = re.sub(
                r"^maj:.*$", f"maj: {page.updated}", front, flags=re.MULTILINE
            )
        else:
            front += f"\nmaj: {page.updated}"
        text = f"---\n{front}\n---\n" + text[match.end() :]
    facts = "\n".join(f"- {fact}" for fact in page.facts)
    text = _replace_section(text, _FACTS_HEADING, facts)
    # Links the user already wrote elsewhere in the page are not repeated.
    without_links = _replace_section(text, _LINKS_HEADING, "")
    written = {fold(t) for t in _WIKILINK_RE.findall(without_links)}
    extra = [t for t in page.links if fold(t) not in written]
    text = _replace_section(text, _LINKS_HEADING, " · ".join(f"[[{t}]]" for t in extra))
    return text.rstrip() + "\n"


class BrainStore:
    """Read and grow the Markdown brain. Thread-safe for a single process."""

    def __init__(
        self, root: Optional[Path | str] = None, *, context_pages: int = 3
    ) -> None:
        self.root = Path(root).expanduser() if root else default_brain_dir()
        self.context_pages = context_pages  # pages recalled into each prompt
        self._lock = threading.RLock()

    # -- reading -----------------------------------------------------------

    def _files(self) -> Iterable[Path]:
        if not self.root.is_dir():
            return []
        return (
            p
            for p in sorted(self.root.rglob("*.md"))
            if not any(part.startswith(".") for part in p.relative_to(self.root).parts)
        )

    def pages(self) -> List[Page]:
        out = []
        for path in self._files():
            try:
                out.append(parse_page(path, path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
        return out

    def find(self, title: str) -> Optional[Page]:
        key = fold(title)
        if not key:
            return None
        for page in self.pages():
            if page.key == key or fold(page.path.stem) == key:
                return page
        return None

    def backlinks(self, title: str) -> List[str]:
        key = fold(title)
        return [p.title for p in self.pages() if any(fold(t) == key for t in p.links)]

    # -- writing -----------------------------------------------------------

    def _path_for(self, title: str, page_type: str) -> Path:
        folder = TYPES[page_type][0]
        base = self.root / folder if folder else self.root
        path = (base / f"{title}.md").resolve()
        root = self.root.resolve()
        if path.parent != base.resolve() or not path.is_relative_to(root):
            raise ValueError(f"unsafe page title: {title!r}")
        return path

    def remember(
        self,
        title: str,
        fact: str,
        *,
        page_type: Optional[str] = None,
        links: Iterable[str] = (),
        source: str = "",
        today: Optional[date] = None,
    ) -> Tuple[Page, bool]:
        """Add *fact* to the page *title* (created if needed).

        Returns ``(page, added)``; ``added`` is False when the same fact was
        already known, so repeated conversations do not pile up duplicates.
        """
        title = clean_title(title)
        fact = " ".join(str(fact).split())[:_MAX_FACT]
        stamp = (today or date.today()).isoformat()
        with self._lock:
            page = self.find(title)
            if page is None:
                kind = "moi" if title == ME_TITLE else normalize_type(page_type)
                page = Page(
                    title=title,
                    type=kind,
                    path=self._path_for(title, kind),
                    created=stamp,
                    updated=stamp,
                )
            added = False
            if fact and fold(fact) not in {_fact_key(f) for f in page.facts}:
                suffix = f" ({' '.join(source.split())[:60]})" if source else ""
                page.facts.append(f"{stamp} : {fact}{suffix}")
                added = True
            for target in links:
                try:
                    target = clean_title(target)
                except ValueError:
                    continue
                if fold(target) != page.key and all(
                    fold(t) != fold(target) for t in page.links
                ):
                    page.links.append(target)
                    added = True
            if added or not page.path.exists():
                page.updated = stamp
                self._write(page)
            return page, added

    def _write(self, page: Page) -> None:
        page.path.parent.mkdir(parents=True, exist_ok=True)
        if page.path.exists():
            original = page.path.read_text(encoding="utf-8")
            text = update_managed_sections(original, page)
        else:
            text = render_page(page)
        tmp = page.path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, page.path)
        page.body = parse_page(page.path, text).body

    def log(self, entry: str, today: Optional[date] = None) -> Path:
        """Append one line to today's journal page."""
        stamp = (today or date.today()).isoformat()
        path = self.root / "journal" / f"{stamp}.md"
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(
                    f"---\ntype: journal\ncree: {stamp}\nmaj: {stamp}\n---\n"
                    f"# {stamp}\n\n",
                    encoding="utf-8",
                )
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"- {' '.join(entry.split())}\n")
        return path

    # -- searching ---------------------------------------------------------

    def search(self, query: str, limit: int = 5) -> List[Tuple[Page, float]]:
        """Rank pages by how many query words they contain (title counts x3)."""
        words = [w for w in fold(query).split() if len(w) > 1]
        if not words:
            return []
        scored = []
        for page in self.pages():
            if page.type == "journal":
                continue
            title = page.key
            text = fold(page.body)
            score = 0.0
            for w in words:
                if w in title.split() or title == w:
                    score += 3
                elif w in title:
                    score += 2
                hits = text.count(w)
                if hits:
                    score += 1 + min(hits, 5) * 0.2
            if title == fold(query):
                score += 5
            if score:
                scored.append((page, score))
        scored.sort(key=lambda item: (-item[1], item[0].title))
        return scored[:limit]

    # -- views -------------------------------------------------------------

    def summary(self, max_chars: int = 1800) -> str:
        """Short "what I know" sheet for the system prompt."""
        pages = [p for p in self.pages() if p.type != "journal"]
        if not pages:
            return ""
        me = next((p for p in pages if p.type == "moi"), None)
        lines = ["## Mon cerveau (ce que j'ai appris)"]
        if me and me.facts:
            lines.append("Sur l'utilisateur :")
            for fact in me.facts[-12:]:
                lines.append(f"- {fact_text(fact)}")
        degree: Dict[str, int] = {}
        for p in pages:
            for t in p.links:
                degree[fold(t)] = degree.get(fold(t), 0) + 1
        others = sorted(
            (p for p in pages if p.type != "moi"),
            key=lambda p: (-(len(p.links) + degree.get(p.key, 0)), p.title),
        )
        if others:
            lines.append(
                "Pages : "
                + ", ".join(
                    f"{p.title} ({TYPES[p.type][1].lower()})" for p in others[:40]
                )
            )
        lines.append(
            "Cherche dans le cerveau (brain_search) avant de répondre sur ces sujets."
        )
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    def graph(self, max_excerpt: int = 1200) -> Dict[str, object]:
        """Nodes and links for the galaxy. Unknown link targets become dim stars."""
        # The daily journal is a log, not knowledge: keep it out of the sky.
        pages = [p for p in self.pages() if p.type != "journal"]
        nodes: Dict[str, Dict[str, object]] = {}
        for p in pages:
            excerpt = "\n".join(f"• {f}" for f in p.facts) or _strip_markdown(p.body)
            nodes[p.key] = {
                "id": p.key,
                "title": p.title,
                "type": p.type,
                "updated": p.updated,
                "excerpt": excerpt[:max_excerpt],
                "path": str(p.path),
                "core": p.type == "moi",
            }
        links = []
        seen = set()
        for p in pages:
            for target in p.links:
                tkey = fold(target)
                if not tkey:
                    continue
                if tkey not in nodes:
                    nodes[tkey] = {
                        "id": tkey,
                        "title": target,
                        "type": "autre",
                        "excerpt": _GHOST_EXCERPT,
                        "core": False,
                    }
                pair = tuple(sorted((p.key, tkey)))
                if pair not in seen and pair[0] != pair[1]:
                    seen.add(pair)
                    links.append({"source": p.key, "target": tkey})
        return {
            "nodes": list(nodes.values()),
            "links": links,
            "types": {k: {"label": v[1], "color": v[2]} for k, v in TYPES.items()},
        }


_DATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2} : ")
_SOURCE_RE = re.compile(r" \([^()]*\)$")


def fact_text(line: str) -> str:
    """'2026-11-15 : Aime le jazz. (conversation)' -> 'Aime le jazz.'"""
    return _SOURCE_RE.sub("", _DATED_RE.sub("", line)).strip()


def _fact_key(line: str) -> str:
    return fold(fact_text(line))


def _strip_markdown(text: str) -> str:
    text = _FRONT_RE.sub("", text)
    text = re.sub(r"^#+ ", "", text, flags=re.MULTILINE)
    return _WIKILINK_RE.sub(lambda m: m.group(1), text).strip()


__all__ = [
    "BrainStore",
    "ME_TITLE",
    "Page",
    "TYPES",
    "clean_title",
    "default_brain_dir",
    "fact_text",
    "fold",
    "normalize_type",
    "parse_page",
    "render_page",
    "update_managed_sections",
]
