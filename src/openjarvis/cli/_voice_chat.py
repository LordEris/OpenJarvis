"""Voice input/output helpers for the interactive chat session."""

from __future__ import annotations

from typing import Any, Optional

from rich.markup import escape

VOICE_EXIT = object()

# Backend selection is shared with the API server and lives in
# openjarvis.speech._tts_discovery. Importing that at module level would pull
# the whole speech stack -- numpy included -- into every `jarvis` invocation,
# which tests/cli/test_cli.py guards against, so these aliases resolve lazily.
_LAZY_TTS_NAMES = {
    "_TTS_BACKEND_ORDER": "TTS_BACKEND_ORDER",
    "_BACKEND_DEFAULT_VOICE": "BACKEND_DEFAULT_VOICE",
}


def __getattr__(name: str) -> Any:
    target = _LAZY_TTS_NAMES.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from openjarvis.speech import _tts_discovery

    return getattr(_tts_discovery, target)


def _terminal_safe_text(value: object) -> str:
    """Remove terminal controls and escape Rich markup from dynamic text."""
    printable = "".join(
        char for char in str(value) if char in ("\n", "\t") or char.isprintable()
    )
    return escape(printable)


class VoiceSession:
    """Cache healthy speech backends for one interactive chat session."""

    def __init__(self, config: object | None = None) -> None:
        self._config = config
        self._stt_resolved = False
        self._stt_backend: Any = None
        self._tts_backend: Any = None
        self._tts_attempted: set[str] = set()
        self._voice_prefs: tuple[str, str, float] | None = None
        self._voice_warned: set[str] = set()

    def get_stt_backend(self) -> Any:
        """Resolve and health-check STT once, then reuse the loaded backend."""
        if not self._stt_resolved:
            from openjarvis.core.config import load_config
            from openjarvis.speech._discovery import get_speech_backend

            config = self._config if self._config is not None else load_config()
            self._stt_backend = get_speech_backend(config)
            self._stt_resolved = True
        return self._stt_backend

    def get_stt_language(self) -> Optional[str]:
        """Return the configured ``speech.language``, or None to auto-detect."""
        from openjarvis.core.config import load_config

        config = self._config if self._config is not None else load_config()
        return (getattr(config.speech, "language", "") or "").strip() or None

    def get_wake_words(self) -> list[str]:
        """Return the normalized ``speech.wake_word`` list (default: jarvis)."""
        from openjarvis.core.config import load_config
        from openjarvis.speech.wake_word import parse_wake_words

        config = self._config if self._config is not None else load_config()
        return parse_wake_words(getattr(config.speech, "wake_word", "") or "jarvis")

    def get_tts_backend(self) -> Any:
        """Return a cached healthy TTS backend, falling through once per key."""
        if self._tts_backend is not None:
            return self._tts_backend

        from openjarvis.speech._tts_discovery import get_tts_backend

        preferred, _, _ = self.get_voice_preferences()
        self._tts_backend = get_tts_backend(preferred, attempted=self._tts_attempted)
        return self._tts_backend

    def get_voice_preferences(self) -> tuple[str, str, float]:
        """Resolve configured (tts_backend, voice_id, speed), cached per session."""
        if self._voice_prefs is None:
            from openjarvis.core.config import load_config
            from openjarvis.speech._tts_discovery import voice_preferences

            config = self._config if self._config is not None else load_config()
            self._voice_prefs = voice_preferences(config)
        return self._voice_prefs

    def voice_for_backend(self, backend: Any, console: Any = None) -> tuple[str, float]:
        """Return the voice ID valid for ``backend``, plus the configured speed.

        Voice IDs are not portable across backends, so the configured
        ``speech.voice_id`` is honored only when ``backend`` is the configured
        ``speech.tts_backend``. Otherwise the fallback backend's own default is
        used and the substitution is reported once per session.
        """
        want_backend, voice_id, speed = self.get_voice_preferences()
        active = getattr(backend, "backend_id", "") or ""
        if active == want_backend:
            return voice_id, speed

        from openjarvis.speech._tts_discovery import default_voice_for

        substitute = default_voice_for(active)
        if console is not None and active not in self._voice_warned:
            self._voice_warned.add(active)
            console.print(
                f"[dim yellow]Voice: {want_backend!r} unavailable — using "
                f"{active!r} with its default voice "
                f"({substitute or 'backend default'}).[/dim yellow]"
            )
        return substitute, speed

    def discard_tts_backend(self) -> None:
        """Forget a backend that failed synthesis and allow the next fallback."""
        self._tts_backend = None


def read_voice_input(console: Any, session: VoiceSession) -> Optional[str] | object:
    """Accept a typed command/message, or record after an empty submission."""
    try:
        typed = input("You> [type, or press Enter to speak] ")
    except (EOFError, KeyboardInterrupt):
        return VOICE_EXIT
    typed = typed.strip()
    return typed if typed else record_voice(console, session)


def record_voice(
    console: Any,
    session: VoiceSession | None = None,
) -> Optional[str] | object:
    """Record from mic, transcribe, and return text or a loop sentinel."""
    from openjarvis.speech.voice_io import record_until_silence

    active_session = session or VoiceSession()
    backend = active_session.get_stt_backend()
    if backend is None:
        console.print(
            "[red]No speech-to-text backend available. "
            "Install the voice dependencies with: "
            "pip install 'OpenJarvis[speech]', or configure a healthy "
            "OpenAI/Deepgram backend.[/red]"
        )
        return VOICE_EXIT

    console.print("[dim cyan]Listening… (speak now, stops on silence)[/dim cyan]")
    try:
        audio_bytes = record_until_silence()
    except KeyboardInterrupt:
        return VOICE_EXIT
    except Exception as exc:
        # PortAudio failures are regular Exceptions (and can surface as
        # OSError), so report them without letting terminal control sequences
        # through. SystemExit deliberately continues to propagate, while
        # Ctrl-C maps to the chat loop's graceful-exit sentinel above.
        console.print(f"[red]Mic error: {_terminal_safe_text(exc)}[/red]")
        return VOICE_EXIT

    console.print("[dim]Transcribing…[/dim]")
    try:
        result = backend.transcribe(
            audio_bytes, format="wav", language=active_session.get_stt_language()
        )
        text = result.text.strip()
        if text:
            console.print(f"[bold]You (voice):[/bold] {_terminal_safe_text(text)}")
            return text
        console.print("[dim]Nothing heard — try again.[/dim]")
        return None
    except Exception as exc:
        console.print(f"[red]Transcription error: {_terminal_safe_text(exc)}[/red]")
        return None


_WAKE_IDLE_SECONDS = 10.0  # mic chunk length while waiting for the wake word
_FOLLOW_UP_SECONDS = 6.0  # time to start the request after a bare "Jarvis ?"


def _transcribe(console: Any, session: VoiceSession, backend: Any, audio: bytes):
    try:
        result = backend.transcribe(
            audio, format="wav", language=session.get_stt_language()
        )
    except Exception as exc:
        console.print(f"[red]Transcription error: {_terminal_safe_text(exc)}[/red]")
        return ""
    return (result.text or "").strip()


def listen_for_command(
    console: Any,
    session: VoiceSession,
) -> Optional[str] | object:
    """Hands-free input: wait until a sentence addresses the wake word.

    Every sentence picked up by the microphone is transcribed locally; those
    that do not start or end with the wake word are dropped. "Jarvis, ouvre
    Spotify" returns "ouvre Spotify" directly; a bare "Jarvis ?" gets a short
    spoken acknowledgement and the next sentence is the request. Saying "au
    revoir" (or Ctrl+C) ends the session.
    """
    from openjarvis.speech.voice_io import record_until_silence
    from openjarvis.speech.wake_word import (
        extract_command,
        has_speech,
        is_stop_phrase,
    )

    backend = session.get_stt_backend()
    if backend is None:
        console.print(
            "[red]No speech-to-text backend available. "
            "Install the voice dependencies with: "
            "pip install 'OpenJarvis[speech]'.[/red]"
        )
        return VOICE_EXIT
    wake_words = session.get_wake_words()
    label = _terminal_safe_text(wake_words[0].title())
    console.print(f"[dim cyan]Listening for “{label}”… (Ctrl+C to quit)[/dim cyan]")

    def record(startup: float) -> bytes | object:
        try:
            return record_until_silence(startup_silence_seconds=startup)
        except KeyboardInterrupt:
            return VOICE_EXIT
        except Exception as exc:
            console.print(f"[red]Mic error: {_terminal_safe_text(exc)}[/red]")
            return VOICE_EXIT

    while True:
        audio = record(_WAKE_IDLE_SECONDS)
        if audio is VOICE_EXIT:
            return VOICE_EXIT
        assert isinstance(audio, bytes)
        if not has_speech(audio):
            continue
        heard = _transcribe(console, session, backend, audio)
        command = extract_command(heard, wake_words) if heard else None
        if command is None:
            continue  # not addressed to the assistant
        if not command:
            language = (session.get_stt_language() or "").lower()
            speak("Oui ?" if language.startswith("fr") else "Yes?", console, session)
            audio = record(_FOLLOW_UP_SECONDS)
            if audio is VOICE_EXIT:
                return VOICE_EXIT
            assert isinstance(audio, bytes)
            if not has_speech(audio):
                continue
            heard = _transcribe(console, session, backend, audio)
            command = extract_command(heard, wake_words) or heard
            if not command:
                continue
        if is_stop_phrase(command):
            return VOICE_EXIT
        console.print(f"[bold]You (voice):[/bold] {_terminal_safe_text(command)}")
        return command


def speak(text: str, console: Any, session: VoiceSession | None = None) -> None:
    """Synthesize and play text, reusing a healthy backend for the session."""
    from openjarvis.speech.voice_io import play_wav

    active_session = session or VoiceSession()

    # Resolved before the loop: a bad config value must surface as a config
    # error, not be swallowed by the per-backend fallback handler below.
    try:
        active_session.get_voice_preferences()
    except Exception as exc:
        console.print(f"[red]Invalid speech config: {_terminal_safe_text(exc)}[/red]")
        return

    while (backend := active_session.get_tts_backend()) is not None:
        try:
            voice_id, speed = active_session.voice_for_backend(backend, console)
            synth_kwargs: dict[str, Any] = {"output_format": "wav", "speed": speed}
            if voice_id:
                synth_kwargs["voice_id"] = voice_id
            result = backend.synthesize(text, **synth_kwargs)
            if result.audio:
                play_wav(result.audio, sample_rate=result.sample_rate)
            return
        except Exception as exc:
            console.print(
                f"[dim yellow]Voice backend "
                f"{getattr(backend, 'backend_id', '?')!r} failed: "
                f"{_terminal_safe_text(exc)}[/dim yellow]"
            )
            active_session.discard_tts_backend()

    console.print(
        "[dim yellow]No TTS backend available — install kokoro: "
        "pip install kokoro[/dim yellow]"
    )


__all__ = [
    "VOICE_EXIT",
    "VoiceSession",
    "listen_for_command",
    "read_voice_input",
    "record_voice",
    "speak",
]
