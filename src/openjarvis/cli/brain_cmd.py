"""``jarvis cerveau`` — JARVIS's brain: galaxy, search, and learning from documents."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import List, Optional

import click
from rich.console import Console

_CHUNK_CHARS = 3500
_MAX_CHUNKS = 40


def _brain(config=None):
    from openjarvis.brain.recall import brain_dir
    from openjarvis.brain.store import BrainStore
    from openjarvis.core.config import load_config

    return BrainStore(brain_dir(config or load_config()))


def _read_source(source: str) -> tuple[str, str]:
    """Return (name, text) for a file path, or for literal text."""
    path = Path(source).expanduser()
    if not path.is_file():
        return "Note", source
    if path.suffix.lower() == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            raise click.ClickException(
                "Lire un PDF demande pdfplumber : pip install 'openjarvis[pdf]'"
            ) from None
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return path.stem, text


def _chunks(text: str) -> List[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) > _CHUNK_CHARS:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para[: _CHUNK_CHARS * 2]
    if current:
        chunks.append(current)
    return chunks[:_MAX_CHUNKS]


@click.group("cerveau")
def brain() -> None:
    """Le cerveau de JARVIS : ce qu'il a appris, en pages reliées et en galaxie."""


@brain.command("galaxie")
@click.option(
    "--sortie",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Fichier HTML à écrire (défaut : <cerveau>/.galaxie.html).",
)
@click.option("--sans-ouvrir", "no_open", is_flag=True, help="Ne pas ouvrir.")
def galaxy(output: Optional[Path], no_open: bool) -> None:
    """Dessine le cerveau en galaxie 3D et l'ouvre dans le navigateur."""
    from openjarvis.brain.galaxy import write_galaxy

    store = _brain()
    path = write_galaxy(store, output)
    stars = len(store.graph()["nodes"])
    click.echo(f"Galaxie : {stars} étoile(s) → {path}")
    if not no_open:
        webbrowser.open(path.resolve().as_uri(), new=2)


@brain.command("chercher")
@click.argument("query", nargs=-1, required=True)
def search(query: tuple[str, ...]) -> None:
    """Cherche dans ce que JARVIS sait."""
    from openjarvis.brain.recall import format_page

    console = Console()
    hits = _brain().search(" ".join(query), limit=5)
    if not hits:
        click.echo("Rien dans le cerveau sur ce sujet pour l'instant.")
        return
    for page, _ in hits:
        console.print(format_page(page, max_facts=8), markup=False)
        console.print(f"  ({page.path})\n", style="dim", markup=False)


@brain.command("etat")
def status() -> None:
    """Combien de pages, de quels types, et où est le cerveau."""
    from openjarvis.brain.store import TYPES
    from openjarvis.core.config import load_config

    config = load_config()
    store = _brain(config)
    pages = store.pages()
    counts: dict[str, int] = {}
    for page in pages:
        counts[page.type] = counts.get(page.type, 0) + 1
    enabled = "activé" if config.brain.enabled else "désactivé ([brain] enabled)"
    click.echo(f"Cerveau : {store.root}  ({enabled})")
    if not pages:
        click.echo("Encore vide : parle à JARVIS, il apprendra.")
        return
    for kind, (_, label, _) in TYPES.items():
        if counts.get(kind):
            click.echo(f"  {label:<10} {counts[kind]}")
    links = len(store.graph()["links"])
    click.echo(f"  {len(pages)} pages, {links} liens")


@brain.command("apprendre")
@click.argument("source")
@click.option("-m", "--model", "model_name", default=None, help="Modèle à utiliser.")
def learn_cmd(source: str, model_name: Optional[str]) -> None:
    """Apprend d'un document (.txt, .md, .pdf) ou d'un texte entre guillemets."""
    from openjarvis.brain.learning import KnowledgeExtractor, learn
    from openjarvis.core.config import load_config
    from openjarvis.engine import get_engine
    from openjarvis.memory.service import _build_scanner

    config = load_config()
    name, text = _read_source(source)
    chunks = _chunks(text)
    if not chunks:
        raise click.ClickException("Rien à lire dans cette source.")
    model = model_name or config.intelligence.default_model
    resolved = get_engine(config, None, model=model or None)
    if resolved is None:
        raise click.ClickException(
            "Aucun moteur d'IA disponible : lance Ollama (ollama serve) puis réessaie."
        )
    _, engine = resolved
    extractor = KnowledgeExtractor(engine, model)
    scanner = _build_scanner()

    def injected(chunk_text: str) -> bool:
        if scanner is None:
            return False
        try:
            result = scanner.scan(chunk_text)
        except Exception:  # noqa: BLE001
            return False
        return not getattr(result, "is_clean", True)

    store = _brain(config)
    learned_pages: List[str] = []
    total = 0
    with click.progressbar(chunks, label=f"Lecture de « {name} »") as bar:
        for chunk in bar:
            items = extractor.extract_text(f"Document « {name} » :\n{chunk}")
            learned, _ = learn(store, items, source=name, scan=injected)
            total += len(learned)
            for item in learned:
                if item.page not in learned_pages:
                    learned_pages.append(item.page)
    if name != "Note":
        store.remember(
            name,
            f"Document lu par JARVIS ({total} connaissance(s) retenue(s)).",
            page_type="source",
            links=learned_pages[:30],
            source="document",
        )
    click.echo(
        f"{total} nouvelle(s) connaissance(s) dans {len(learned_pages)} page(s)."
    )
    if learned_pages:
        click.echo("Pages : " + ", ".join(learned_pages[:15]))


__all__ = ["brain"]
