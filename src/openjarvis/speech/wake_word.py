"""Wake-word detection on transcripts: "Jarvis, ouvre Spotify".

Hands-free mode transcribes each spoken sentence locally (the configured
speech-to-text backend, faster-whisper by default) and only reacts when the
sentence addresses the assistant by name. Working on text rather than on raw
audio needs no extra model or dependency, handles any wake word in any
language, and lets the command ride along in the same sentence.

A sentence counts as addressed to the assistant when the wake word is one of
its first three words ("Jarvis, ...", "Dis Jarvis, ...", "Ok Jarvis ...")
or its last word ("..., Jarvis ?"). A name buried mid-sentence, as when a
TV mentions it, is ignored.
"""

from __future__ import annotations

import array
import io
import re
import sys
import unicodedata
import wave
from typing import List, Optional, Sequence

# Speech-to-text spellings of "Jarvis" seen in practice, beyond edit distance 1.
_KNOWN_VARIANTS = {
    "jarvis": {"jarvice", "jarvisse", "djarvis", "jervis", "jarviss", "jarvi"},
}
_LEADING_WINDOW = 3
# Words that only call out to the assistant: "Hé Jarvis !" is a bare call.
_CALL_WORDS = {
    "he", "hey", "eh", "ok", "okay", "dis", "dites", "allo", "salut",
    "bonjour", "bonsoir", "coucou", "hello", "hi", "yo", "oh", "alors",
}  # fmt: skip
_PUNCT = " \t\n,.;:!?¿¡-–—…'\"«»“”"
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)

# Saying one of these to the assistant ends the hands-free session.
_STOP_PHRASES = {
    "au revoir",
    "ferme toi",
    "arrete toi",
    "eteins toi",
    "bonne nuit",
    "goodbye",
    "good bye",
    "quit",
    "exit",
}


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation: 'Arrête-toi !' -> 'arrete toi'."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", stripped.casefold()).split())


def parse_wake_words(value: str | Sequence[str]) -> List[str]:
    """Split the ``[speech] wake_word`` setting into normalized words."""
    items = value.split(",") if isinstance(value, str) else list(value)
    words = [normalize(str(item)) for item in items]
    return [w for w in words if w] or ["jarvis"]


def _distance_at_most_one(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = 0
    edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return edits + (len(b) - j) <= 1


def _matches(token: str, wake: str) -> bool:
    if token == wake or token in _KNOWN_VARIANTS.get(wake, ()):
        return True
    # Fuzzy matching only for words long enough not to collide ("jarvis"
    # but not "ok").
    return len(wake) >= 5 and _distance_at_most_one(token, wake)


def _find(tokens: List[str], wake_words: Sequence[str]) -> Optional[tuple[int, int]]:
    """Return (first, last) token index of a wake phrase at a valid position."""
    for wake in wake_words:
        parts = wake.split()
        n = len(parts)
        for start in range(len(tokens) - n + 1):
            window = tokens[start : start + n]
            if all(_matches(t, p) for t, p in zip(window, parts)):
                end = start + n - 1
                if start < _LEADING_WINDOW or end == len(tokens) - 1:
                    return start, end
    return None


def extract_command(transcript: str, wake_words: Sequence[str]) -> Optional[str]:
    """Return the request addressed to the assistant, or None if not addressed.

    ``"Jarvis, ouvre Spotify."`` -> ``"ouvre Spotify."``;
    ``"Quelle heure est-il, Jarvis ?"`` -> ``"Quelle heure est-il"``;
    ``"Jarvis ?"`` -> ``""`` (woken, the request comes next);
    ``"J'ai vu Iron Man et Jarvis était génial"`` -> ``None``.
    """
    matches = list(_WORD_RE.finditer(transcript))
    tokens = [normalize(m.group()) for m in matches]
    found = _find(tokens, wake_words)
    if found is None:
        return None
    first, last = found
    after = transcript[matches[last].end() :].strip(_PUNCT)
    if after:
        # Keep a closing punctuation mark: it can carry the question.
        return transcript[matches[last].end() :].lstrip(_PUNCT).strip()
    before = transcript[: matches[first].start()].strip(_PUNCT)
    if all(word in _CALL_WORDS for word in normalize(before).split()):
        return ""  # "Hé Jarvis !", "Bonjour Jarvis" — just calling
    return before


def is_stop_phrase(text: str) -> bool:
    """True for "au revoir", "arrête-toi", "goodbye"... said on their own."""
    return normalize(text) in _STOP_PHRASES


def has_speech(wav_bytes: bytes, threshold: float = 500.0, chunk: int = 1024) -> bool:
    """True if any chunk of a 16-bit mono WAV is louder than *threshold* (RMS)."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            if wf.getsampwidth() != 2:
                return True  # unknown format: let the transcriber decide
            frames = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError):
        return False
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":
        samples.byteswap()  # WAV is little-endian
    limit = threshold * threshold
    for start in range(0, len(samples), chunk):
        block = samples[start : start + chunk]
        if block and sum(s * s for s in block) / len(block) > limit:
            return True
    return False


__all__ = [
    "extract_command",
    "has_speech",
    "is_stop_phrase",
    "normalize",
    "parse_wake_words",
]
