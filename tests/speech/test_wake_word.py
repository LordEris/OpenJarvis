"""Tests for transcript-based wake-word detection and the hands-free loop."""

from __future__ import annotations

import io
import math
import struct
import wave
from types import SimpleNamespace
from typing import List
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from openjarvis.cli._voice_chat import VOICE_EXIT, VoiceSession, listen_for_command
from openjarvis.core.config import JarvisConfig
from openjarvis.speech.wake_word import (
    extract_command,
    has_speech,
    is_stop_phrase,
    parse_wake_words,
)

WAKE = ["jarvis"]


def _wav(amplitude: int, seconds: float = 0.2, rate: int = 16000) -> bytes:
    n = int(seconds * rate)
    samples = [
        int(amplitude * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n)
    ]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{n}h", *samples))
    return buf.getvalue()


LOUD = _wav(8000)
QUIET = _wav(50)


class TestExtractCommand:
    @pytest.mark.parametrize(
        ("heard", "command"),
        [
            ("Jarvis, ouvre Spotify.", "ouvre Spotify."),
            ("jarvis mets le son à 30 %", "mets le son à 30 %"),
            ("Dis Jarvis, quelle heure est-il ?", "quelle heure est-il ?"),
            ("Ok Jarvis... pause", "pause"),
            ("Quelle heure est-il, Jarvis ?", "Quelle heure est-il"),
            ("Ouvre Spotify, Jarvis.", "Ouvre Spotify"),
            ("Djarvis, ouvre Firefox", "ouvre Firefox"),
            ("Jarvice, mets la musique", "mets la musique"),
            ("JARVIS : lance YouTube", "lance YouTube"),
        ],
    )
    def test_addressed_sentences(self, heard, command):
        assert extract_command(heard, WAKE) == command

    @pytest.mark.parametrize(
        "heard", ["Jarvis ?", "Jarvis.", "Hé Jarvis !", "jarvis", "Bonjour Jarvis"]
    )
    def test_bare_wake_word_returns_empty_request(self, heard):
        assert extract_command(heard, WAKE) == ""

    @pytest.mark.parametrize(
        "heard",
        [
            "J'ai revu Iron Man hier et le personnage de Jarvis était génial.",
            "Ouvre Spotify",
            "",
            "Jardin, arrosage demain",
        ],
    )
    def test_not_addressed(self, heard):
        assert extract_command(heard, WAKE) is None

    def test_custom_and_multi_word_wake_phrases(self):
        words = parse_wake_words("Vendredi, hey maison")
        assert words == ["vendredi", "hey maison"]
        assert extract_command("Vendredi, éteins la lumière", words) == (
            "éteins la lumière"
        )
        assert extract_command("Hey maison, il fait froid", words) == "il fait froid"
        assert extract_command("Jarvis, bonjour", words) is None

    def test_empty_setting_falls_back_to_jarvis(self):
        assert parse_wake_words(" , ") == ["jarvis"]


class TestStopPhrases:
    @pytest.mark.parametrize(
        "text", ["Au revoir !", "arrête-toi", "Ferme-toi.", "Goodbye"]
    )
    def test_stop(self, text):
        assert is_stop_phrase(text)

    def test_longer_sentences_are_not_stop(self):
        assert not is_stop_phrase("dis au revoir à Paul de ma part")


class TestHasSpeech:
    def test_loud_and_quiet(self):
        assert has_speech(LOUD)
        assert not has_speech(QUIET)

    def test_garbage_is_silence(self):
        assert not has_speech(b"not a wav")


# ---------------------------------------------------------------------------
# listen_for_command
# ---------------------------------------------------------------------------


def _session(language: str = "fr") -> VoiceSession:
    config = JarvisConfig()
    config.speech.language = language
    return VoiceSession(config)


def _run(heard: List[str], recordings: List[object], language: str = "fr"):
    """Drive listen_for_command with scripted recordings and transcripts."""
    backend = MagicMock()
    backend.transcribe.side_effect = [SimpleNamespace(text=t) for t in heard]
    spoken: List[str] = []

    def fake_record(**kwargs):
        item = recordings.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    with (
        patch("openjarvis.speech._discovery.get_speech_backend", return_value=backend),
        patch(
            "openjarvis.speech.voice_io.record_until_silence", side_effect=fake_record
        ),
        patch(
            "openjarvis.cli._voice_chat.speak",
            side_effect=lambda text, *a, **k: spoken.append(text),
        ),
    ):
        result = listen_for_command(MagicMock(), _session(language))
    return result, backend, spoken


class TestListenForCommand:
    def test_ignores_silence_and_chatter_then_returns_the_request(self):
        result, backend, spoken = _run(
            heard=["on mange quoi ce soir ?", "Jarvis, ouvre Spotify"],
            recordings=[QUIET, LOUD, LOUD],
        )
        assert result == "ouvre Spotify"
        assert backend.transcribe.call_count == 2  # silence never transcribed
        assert spoken == []

    def test_bare_wake_word_acknowledges_then_takes_next_sentence(self):
        result, _, spoken = _run(
            heard=["Jarvis ?", "mets la musique"],
            recordings=[LOUD, LOUD],
        )
        assert result == "mets la musique"
        assert spoken == ["Oui ?"]

    def test_acknowledgement_follows_the_language(self):
        _, _, spoken = _run(
            heard=["Jarvis?", "what time is it"],
            recordings=[LOUD, LOUD],
            language="en",
        )
        assert spoken == ["Yes?"]

    def test_stop_phrase_ends_the_session(self):
        result, _, _ = _run(heard=["Jarvis, au revoir"], recordings=[LOUD])
        assert result is VOICE_EXIT

    def test_ctrl_c_ends_the_session(self):
        result, _, _ = _run(heard=[], recordings=[KeyboardInterrupt()])
        assert result is VOICE_EXIT

    def test_mic_error_ends_the_session(self):
        result, _, _ = _run(heard=[], recordings=[OSError("no input device")])
        assert result is VOICE_EXIT


def test_chat_wake_flag_uses_hands_free_input():
    from openjarvis.cli.chat_cmd import chat

    engine = MagicMock()
    engine.engine_id = "mock"
    config = JarvisConfig()
    config.intelligence.default_model = "test-model"
    with (
        patch("openjarvis.cli.chat_cmd.load_config", return_value=config),
        patch("openjarvis.engine.get_engine", return_value=("mock", engine)),
        patch("openjarvis.intelligence.register_builtin_models"),
        patch(
            "openjarvis.cli.chat_cmd.listen_for_command", return_value=VOICE_EXIT
        ) as listen,
        patch("openjarvis.cli.chat_cmd.read_voice_input") as read_input,
    ):
        result = CliRunner().invoke(chat, ["--wake", "--model", "test-model"])
    assert result.exit_code == 0, result.output
    assert "Hands-free mode ON" in result.output
    listen.assert_called_once()
    read_input.assert_not_called()
