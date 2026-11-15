"""Render the brain as a self-contained 3D "galaxy" web page.

Every page of the brain is a star (bigger when more connected, coloured by
type, "Moi" at the centre) and every ``[[link]]`` a thread of light. The
output is one HTML file with no external resource, so it works offline and
can be opened from the file system.
"""

from __future__ import annotations

import json
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.brain.store import BrainStore

_PLACEHOLDER = "/*__GALAXY_DATA__*/"


def _template() -> str:
    return (
        resources.files("openjarvis.brain")
        .joinpath("galaxy_template.html")
        .read_text(encoding="utf-8")
    )


def _embed(data: Dict[str, Any]) -> str:
    """JSON safe inside a <script> element, whatever the notes contain."""
    text = json.dumps(data, ensure_ascii=False)
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def render_galaxy(store: BrainStore, *, now: Optional[datetime] = None) -> str:
    data = store.graph()
    data["generated"] = (now or datetime.now()).strftime("%d/%m/%Y %H:%M")
    return _template().replace(_PLACEHOLDER, _embed(data), 1)


def write_galaxy(store: BrainStore, output: Optional[Path] = None) -> Path:
    """Write the galaxy page (default: ``<brain>/.galaxie.html``) and return it."""
    path = output or store.root / ".galaxie.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_galaxy(store), encoding="utf-8")
    return path


__all__ = ["render_galaxy", "write_galaxy"]
