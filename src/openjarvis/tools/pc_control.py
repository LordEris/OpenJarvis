"""Desktop control tools — open apps/sites/files, system volume, media keys.

Three small tools that let the assistant act on the local desktop the way a
voice assistant is expected to ("ouvre Spotify", "monte le son", "pause"):

* ``open_app`` — launch an *installed* application by name, open an
  ``http(s)`` URL in the default browser, or open a file/folder inside the
  configured ``[security] allowed_dirs``.
* ``system_volume`` — get/set/step the output volume and mute state.
* ``media_control`` — play/pause, next, previous, stop.

Safety model (these tools run without a confirmation prompt so they also work
from the desktop app and remote channels):

* Nothing goes through a shell: every process is spawned from an argv list.
* ``open_app`` only launches apps found in the OS application menus
  (Start Menu shortcuts, freedesktop ``.desktop`` entries, ``/Applications``)
  plus a short list of Windows built-ins — never an arbitrary command.
* URLs must be ``http``/``https``; ``file:``, ``javascript:``, custom
  protocol handlers and the like are refused.
* Files must live inside ``[security] allowed_dirs`` and executables or
  scripts are refused, so a prompt-injected page cannot get a downloaded
  payload launched.

Windows, Linux (PulseAudio/PipeWire, MPRIS) and macOS are supported; each
platform path uses only tools that ship with the OS by default, with
optional extras (``pycaw`` on Windows, ``playerctl`` on Linux) used when
present.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_CMD_TIMEOUT = 10.0
_MAX_CANDIDATES = 8

# Extensions that execute code when "opened" on at least one platform.
_EXECUTABLE_SUFFIXES = frozenset(
    {
        ".appimage", ".application", ".bat", ".cmd", ".com", ".cpl", ".desktop",
        ".exe", ".gadget", ".hta", ".inf", ".jar", ".js", ".jse", ".lnk", ".msc",
        ".msi", ".msp", ".pif", ".ps1", ".psm1", ".py", ".pyw", ".reg", ".run",
        ".scf", ".scr", ".sh", ".url", ".vb", ".vbe", ".vbs", ".ws", ".wsf",
        ".wsh", ".command", ".app", ".pkg", ".deb", ".rpm",
    }
)  # fmt: skip

# Windows apps that have no Start Menu shortcut but are always present.
_WINDOWS_BUILTINS: Dict[str, str] = {
    "notepad": "notepad.exe",
    "bloc-notes": "notepad.exe",
    "bloc notes": "notepad.exe",
    "calculator": "calc.exe",
    "calculatrice": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "explorateur": "explorer.exe",
    "explorateur de fichiers": "explorer.exe",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe",
    "gestionnaire des taches": "taskmgr.exe",
}
# Settings are a protocol handler; this exact URI is the only non-http(s)
# target the tool ever opens.
_WINDOWS_SETTINGS_NAMES = frozenset({"settings", "parametres", "reglages"})


def _platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _normalize(text: str) -> str:
    """Casefold and strip accents so 'Paramètres' matches 'parametres'."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.casefold()).strip()


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=_CMD_TIMEOUT,
        check=False,
    )


def _spawn(argv: Sequence[str]) -> None:
    """Start a GUI process without waiting for it or inheriting our stdio."""
    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if _platform() == "windows":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(list(argv), **kwargs)  # noqa: S603 - argv list, no shell


def _result(tool: str, content: str, success: bool = True, **meta: Any) -> ToolResult:
    return ToolResult(tool_name=tool, content=content, success=success, metadata=meta)


# ---------------------------------------------------------------------------
# Installed-application index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppEntry:
    """One launchable application found in the OS application menus."""

    name: str
    keys: Tuple[str, ...]  # normalized names it answers to
    launch: Tuple[str, ...]  # argv, or ("startfile", path) on Windows


def _linux_app_dirs() -> List[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots = [data_home, *data_dirs.split(":")]
    roots += [
        "/var/lib/flatpak/exports/share",
        str(Path.home() / ".local/share/flatpak/exports/share"),
    ]
    dirs = [Path(r) / "applications" for r in roots if r]
    dirs.append(Path("/var/lib/snapd/desktop/applications"))
    seen: set[Path] = set()
    unique = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def _parse_desktop_file(path: Path, launcher: Sequence[str]) -> Optional[AppEntry]:
    parser = configparser.RawConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keep "Name[fr]" case
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    entry = parser["Desktop Entry"]
    if entry.get("Type", "Application") != "Application":
        return None
    if entry.get("NoDisplay", "false").lower() == "true":
        return None
    if entry.get("Hidden", "false").lower() == "true":
        return None
    name = entry.get("Name")
    if not name:
        return None
    names = {name, path.stem}
    for key, value in entry.items():
        if (key.startswith("Name[") or key.startswith("GenericName")) and value:
            names.add(value)
    keys = tuple(sorted({_normalize(n) for n in names if n.strip()}))
    if launcher[0] == "gtk-launch":
        argv = (*launcher, path.stem)
    else:  # gio launch <path>
        argv = (*launcher, str(path))
    return AppEntry(name=name, keys=keys, launch=argv)


def _linux_apps() -> List[AppEntry]:
    if shutil.which("gtk-launch"):
        launcher: Tuple[str, ...] = ("gtk-launch",)
    elif shutil.which("gio"):
        launcher = ("gio", "launch")
    else:
        return []
    apps: Dict[str, AppEntry] = {}
    for directory in _linux_app_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            # Earlier directories win (user overrides system), like the menus.
            if path.stem in apps:
                continue
            entry = _parse_desktop_file(path, launcher)
            if entry is not None:
                apps[path.stem] = entry
    return list(apps.values())


def _windows_apps() -> List[AppEntry]:
    roots = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData")),
        Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))),
    ]
    apps: Dict[str, AppEntry] = {}
    for root in roots:
        menu = root / "Microsoft/Windows/Start Menu/Programs"
        if not menu.is_dir():
            continue
        for lnk in sorted(menu.rglob("*.lnk")):
            stem = lnk.stem
            lowered = stem.casefold()
            if "uninstall" in lowered or "désinstaller" in lowered:
                continue
            key = _normalize(stem)
            apps.setdefault(key, AppEntry(stem, (key,), ("startfile", str(lnk))))
    for alias, exe in _WINDOWS_BUILTINS.items():
        key = _normalize(alias)
        apps.setdefault(key, AppEntry(alias, (key,), (exe,)))
    return list(apps.values())


def _macos_apps() -> List[AppEntry]:
    apps: Dict[str, AppEntry] = {}
    for root in (
        Path("/Applications"),
        Path("/System/Applications"),
        Path.home() / "Applications",
    ):
        if not root.is_dir():
            continue
        for bundle in sorted(root.glob("*.app")):
            key = _normalize(bundle.stem)
            apps.setdefault(
                key, AppEntry(bundle.stem, (key,), ("open", "-a", str(bundle)))
            )
    return list(apps.values())


def installed_apps() -> List[AppEntry]:
    """Return the applications visible in this OS's application menus."""
    platform = _platform()
    if platform == "windows":
        return _windows_apps()
    if platform == "macos":
        return _macos_apps()
    return _linux_apps()


def match_apps(query: str, apps: Sequence[AppEntry]) -> List[AppEntry]:
    """Rank apps for *query*: exact name, then prefix, then substring."""
    q = _normalize(query)
    if not q:
        return []
    exact = [a for a in apps if q in a.keys]
    if exact:
        return exact
    prefix = [a for a in apps if any(k.startswith(q) for k in a.keys)]
    if prefix:
        return prefix
    words = q.split()
    return [a for a in apps if all(any(w in k for k in a.keys) for w in words)]


# ---------------------------------------------------------------------------
# open_app
# ---------------------------------------------------------------------------


def _looks_like_url(target: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)) and not re.match(
        r"^[a-zA-Z]:[\\/]", target
    )


def _looks_like_path(target: str) -> bool:
    return (
        target.startswith(("~", "/", "./", "../", "\\\\"))
        or bool(re.match(r"^[a-zA-Z]:[\\/]", target))
        or "/" in target
        or "\\" in target
    )


def _is_executable_file(path: Path) -> bool:
    if path.suffix.casefold() in _EXECUTABLE_SUFFIXES:
        return True
    if _platform() != "windows" and path.is_file():
        return os.access(path, os.X_OK)
    return False


def _open_path_with_os(path: Path) -> None:
    platform = _platform()
    if platform == "windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif platform == "macos":
        _spawn(["open", str(path)])
    else:
        _spawn(["xdg-open", str(path)])


@ToolRegistry.register("open_app")
class OpenAppTool(BaseTool):
    """Open an installed application, an http(s) URL, or an allowed file."""

    tool_id = "open_app"

    def __init__(
        self,
        allowed_dirs: Optional[List[str]] = None,
        app_index: Optional[Callable[[], List[AppEntry]]] = None,
    ) -> None:
        self._allowed_dirs_override = allowed_dirs
        self._app_index = app_index or installed_apps

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_app",
            description=(
                "Open something on the user's computer: an installed application"
                " by name (e.g. 'Spotify', 'Firefox', 'Calculatrice'), a web page"
                " (http or https URL, e.g. 'https://www.youtube.com'), or a file or"
                " folder in the user's allowed directories. Only applications from"
                " the system's app menu can be launched; if several match, the"
                " candidates are returned so you can ask the user which one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Application name, http(s) URL, or file/folder path."
                        ),
                    },
                },
                "required": ["target"],
            },
            category="desktop",
            timeout_seconds=20.0,
        )

    def _allowed_dirs(self) -> List[Path]:
        from openjarvis.tools._paths import resolve_allowed_dirs

        dirs = resolve_allowed_dirs(self._allowed_dirs_override)
        return [Path(d).expanduser().resolve() for d in dirs]

    def execute(self, **params: Any) -> ToolResult:
        target = str(params.get("target", "")).strip()
        if not target:
            return _result("open_app", "No target provided.", success=False)
        try:
            if _looks_like_url(target):
                return self._open_url(target)
            if _looks_like_path(target):
                return self._open_path(target)
            return self._open_application(target)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return _result("open_app", f"Could not open {target!r}: {exc}", False)

    def _open_url(self, target: str) -> ToolResult:
        parsed = urlparse(target)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            # One exception: the Windows Settings app, by its fixed URI.
            if _platform() == "windows" and target.lower() == "ms-settings:":
                os.startfile("ms-settings:")  # type: ignore[attr-defined]
                return _result("open_app", "Opened Windows Settings.")
            return _result(
                "open_app",
                f"Refused: only http and https links can be opened, not {target!r}.",
                success=False,
            )
        if not webbrowser.open(target, new=2):
            return _result("open_app", "No web browser available.", success=False)
        return _result("open_app", f"Opened {target} in the browser.", url=target)

    def _open_path(self, target: str) -> ToolResult:
        path = Path(target).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return _result("open_app", f"Not found: {target}", success=False)
        allowed = self._allowed_dirs()
        if allowed and not any(
            resolved == d or resolved.is_relative_to(d) for d in allowed
        ):
            dirs = ", ".join(str(d) for d in allowed)
            return _result(
                "open_app",
                f"Refused: {resolved} is outside the allowed directories ({dirs}).",
                success=False,
            )
        if resolved.is_file() and _is_executable_file(resolved):
            return _result(
                "open_app",
                f"Refused: {resolved.name} is a program or script; only documents"
                " and folders can be opened. Ask the user to open it themselves.",
                success=False,
            )
        _open_path_with_os(resolved)
        kind = "folder" if resolved.is_dir() else "file"
        return _result("open_app", f"Opened {kind} {resolved}.", path=str(resolved))

    def _open_application(self, target: str) -> ToolResult:
        if _platform() == "windows" and _normalize(target) in _WINDOWS_SETTINGS_NAMES:
            os.startfile("ms-settings:")  # type: ignore[attr-defined]
            return _result("open_app", "Opened Windows Settings.")
        apps = self._app_index()
        if not apps:
            return _result(
                "open_app",
                "Could not list installed applications on this system.",
                success=False,
            )
        matches = match_apps(target, apps)
        if not matches:
            return _result(
                "open_app",
                f"No installed application matches {target!r}.",
                success=False,
            )
        if len(matches) > 1:
            names = sorted({m.name for m in matches})
            if len(names) > 1:
                shown = ", ".join(names[:_MAX_CANDIDATES])
                more = " …" if len(names) > _MAX_CANDIDATES else ""
                return _result(
                    "open_app",
                    f"Several applications match {target!r}: {shown}{more}."
                    " Ask the user which one, then call open_app with its name.",
                    success=False,
                    candidates=names,
                )
        app = matches[0]
        if app.launch[0] == "startfile":
            os.startfile(app.launch[1])  # type: ignore[attr-defined]
        else:
            _spawn(app.launch)
        return _result("open_app", f"Launched {app.name}.", app=app.name)


# ---------------------------------------------------------------------------
# system_volume
# ---------------------------------------------------------------------------

# Windows virtual-key codes for the multimedia keys.
_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF
_VK_MEDIA_NEXT = 0xB0
_VK_MEDIA_PREV = 0xB1
_VK_MEDIA_STOP = 0xB2
_VK_MEDIA_PLAY_PAUSE = 0xB3
_KEYEVENTF_KEYUP = 0x0002


def _press_vk(vk: int, times: int = 1) -> None:
    """Tap a Windows multimedia key *times* times (no extra dependency)."""
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        time.sleep(0.005)


def _clamp(level: Any) -> int:
    value = int(round(float(level)))
    return max(0, min(100, value))


class _VolumeBackend:
    """get() -> (percent|None, muted|None); set(); step(); mute(bool|None)."""

    name = "none"

    def get(self) -> Tuple[Optional[int], Optional[bool]]:
        return None, None

    def set(self, level: int) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def step(self, delta: int) -> None:
        current, _ = self.get()
        if current is None:
            raise RuntimeError("current volume unknown")
        self.set(_clamp(current + delta))

    def mute(self, state: Optional[bool]) -> None:  # pragma: no cover
        raise NotImplementedError


class _PactlBackend(_VolumeBackend):
    """PulseAudio and PipeWire (pipewire-pulse) via ``pactl``."""

    name = "pactl"
    _SINK = "@DEFAULT_SINK@"

    def get(self) -> Tuple[Optional[int], Optional[bool]]:
        vol = _run(["pactl", "get-sink-volume", self._SINK])
        mute = _run(["pactl", "get-sink-mute", self._SINK])
        m = re.search(r"(\d+)%", vol.stdout or "")
        level = int(m.group(1)) if vol.returncode == 0 and m else None
        muted = None
        if mute.returncode == 0:
            muted = "yes" in (mute.stdout or "").lower()
        return level, muted

    def set(self, level: int) -> None:
        _check(_run(["pactl", "set-sink-volume", self._SINK, f"{level}%"]))

    def step(self, delta: int) -> None:
        current, _ = self.get()
        if current is not None:
            self.set(_clamp(current + delta))  # never go above 100 %
            return
        sign = "+" if delta >= 0 else "-"
        _check(_run(["pactl", "set-sink-volume", self._SINK, f"{sign}{abs(delta)}%"]))

    def mute(self, state: Optional[bool]) -> None:
        arg = "toggle" if state is None else ("1" if state else "0")
        _check(_run(["pactl", "set-sink-mute", self._SINK, arg]))


class _WpctlBackend(_VolumeBackend):
    """PipeWire via ``wpctl`` (WirePlumber)."""

    name = "wpctl"
    _SINK = "@DEFAULT_AUDIO_SINK@"

    def get(self) -> Tuple[Optional[int], Optional[bool]]:
        out = _run(["wpctl", "get-volume", self._SINK])
        m = re.search(r"Volume:\s*([0-9.]+)", out.stdout or "")
        if out.returncode != 0 or not m:
            return None, None
        return round(float(m.group(1)) * 100), "MUTED" in out.stdout

    def set(self, level: int) -> None:
        _check(_run(["wpctl", "set-volume", self._SINK, f"{level / 100:.2f}"]))

    def mute(self, state: Optional[bool]) -> None:
        arg = "toggle" if state is None else ("1" if state else "0")
        _check(_run(["wpctl", "set-mute", self._SINK, arg]))


class _AmixerBackend(_VolumeBackend):
    """ALSA fallback via ``amixer`` on the Master control."""

    name = "amixer"

    def get(self) -> Tuple[Optional[int], Optional[bool]]:
        out = _run(["amixer", "sget", "Master"])
        m = re.search(r"\[(\d+)%\]", out.stdout or "")
        if out.returncode != 0 or not m:
            return None, None
        return int(m.group(1)), "[off]" in out.stdout

    def set(self, level: int) -> None:
        _check(_run(["amixer", "-q", "sset", "Master", f"{level}%"]))

    def mute(self, state: Optional[bool]) -> None:
        arg = "toggle" if state is None else ("mute" if state else "unmute")
        _check(_run(["amixer", "-q", "sset", "Master", arg]))


class _MacBackend(_VolumeBackend):
    name = "osascript"

    def get(self) -> Tuple[Optional[int], Optional[bool]]:
        out = _run(
            [
                "osascript",
                "-e",
                "set s to get volume settings",
                "-e",
                'return (output volume of s as text) & "," & (output muted of s)',
            ]
        )
        if out.returncode != 0 or "," not in out.stdout:
            return None, None
        level, muted = out.stdout.strip().split(",", 1)
        try:
            return int(level), muted.strip() == "true"
        except ValueError:
            return None, None

    def set(self, level: int) -> None:
        _check(_run(["osascript", "-e", f"set volume output volume {int(level)}"]))

    def mute(self, state: Optional[bool]) -> None:
        if state is None:
            _, muted = self.get()
            state = not muted
        flag = "true" if state else "false"
        _check(_run(["osascript", "-e", f"set volume output muted {flag}"]))


class _WindowsBackend(_VolumeBackend):
    """Exact control with ``pycaw`` when installed, media keys otherwise."""

    name = "windows"

    def __init__(self) -> None:
        self._endpoint = None
        try:  # optional dependency
            from ctypes import POINTER, cast

            from comtypes import CLSCTX_ALL  # type: ignore[import-not-found]
            from pycaw.pycaw import (  # type: ignore[import-not-found]
                AudioUtilities,
                IAudioEndpointVolume,
            )

            device = AudioUtilities.GetSpeakers()
            iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self._endpoint = cast(iface, POINTER(IAudioEndpointVolume))
            self.name = "pycaw"
        except Exception:  # noqa: BLE001 - any failure means "use media keys"
            self._endpoint = None

    def get(self) -> Tuple[Optional[int], Optional[bool]]:
        if self._endpoint is None:
            return None, None
        level = round(self._endpoint.GetMasterVolumeLevelScalar() * 100)
        return level, bool(self._endpoint.GetMute())

    def set(self, level: int) -> None:
        if self._endpoint is not None:
            self._endpoint.SetMasterVolumeLevelScalar(level / 100, None)
            return
        # Each media-key press moves the volume by 2 %: go to 0, then up.
        _press_vk(_VK_VOLUME_DOWN, 50)
        _press_vk(_VK_VOLUME_UP, round(level / 2))

    def step(self, delta: int) -> None:
        if self._endpoint is not None:
            super().step(delta)
            return
        presses = max(1, round(abs(delta) / 2))
        _press_vk(_VK_VOLUME_UP if delta > 0 else _VK_VOLUME_DOWN, presses)

    def mute(self, state: Optional[bool]) -> None:
        if self._endpoint is not None:
            if state is None:
                state = not bool(self._endpoint.GetMute())
            self._endpoint.SetMute(1 if state else 0, None)
            return
        if state is False:
            # Any volume-key press unmutes; up then down keeps the level.
            _press_vk(_VK_VOLUME_UP)
            _press_vk(_VK_VOLUME_DOWN)
        else:
            # Without pycaw the mute state is unknown: the key toggles it.
            _press_vk(_VK_VOLUME_MUTE)


def _check(proc: subprocess.CompletedProcess) -> None:
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail or f"exit code {proc.returncode}")


def volume_backend() -> Optional[_VolumeBackend]:
    """Pick the volume backend available on this machine."""
    platform = _platform()
    if platform == "windows":
        return _WindowsBackend()
    if platform == "macos":
        return _MacBackend()
    if shutil.which("pactl"):
        return _PactlBackend()
    if shutil.which("wpctl"):
        return _WpctlBackend()
    if shutil.which("amixer"):
        return _AmixerBackend()
    return None


def _describe(backend: _VolumeBackend, fallback: str) -> str:
    level, muted = backend.get()
    if level is None:
        return fallback
    state = " (muted)" if muted else ""
    return f"Volume is {level}%{state}."


@ToolRegistry.register("system_volume")
class SystemVolumeTool(BaseTool):
    """Read or change the computer's output volume."""

    tool_id = "system_volume"

    def __init__(
        self, backend_factory: Optional[Callable[[], Optional[Any]]] = None
    ) -> None:
        self._backend_factory = backend_factory or volume_backend

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="system_volume",
            description=(
                "Read or change the computer's sound volume. Actions: 'get',"
                " 'set' (with level 0-100), 'up' / 'down' (by step, default 10),"
                " 'mute', 'unmute'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "set", "up", "down", "mute", "unmute"],
                    },
                    "level": {
                        "type": "integer",
                        "description": "Target volume in percent, for 'set'.",
                    },
                    "step": {
                        "type": "integer",
                        "description": "Percent to add or remove, for 'up'/'down'.",
                    },
                },
                "required": ["action"],
            },
            category="desktop",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action", "")).strip().lower()
        backend = self._backend_factory()
        if backend is None:
            return _result(
                "system_volume",
                "No volume control found (install pactl, wpctl or amixer).",
                success=False,
            )
        try:
            if action == "get":
                level, muted = backend.get()
                if level is None:
                    return _result(
                        "system_volume",
                        "The current volume cannot be read on this system.",
                        success=False,
                    )
                return _result(
                    "system_volume",
                    f"Volume is {level}%{' (muted)' if muted else ''}.",
                    level=level,
                    muted=muted,
                )
            if action == "set":
                if params.get("level") is None:
                    return _result(
                        "system_volume", "'set' needs a level (0-100).", False
                    )
                level = _clamp(params["level"])
                backend.set(level)
                return _result(
                    "system_volume",
                    _describe(backend, f"Volume set to {level}%."),
                )
            if action in ("up", "down"):
                step = abs(int(params.get("step") or 10))
                backend.step(step if action == "up" else -step)
                verb = "Raised" if action == "up" else "Lowered"
                return _result(
                    "system_volume",
                    _describe(backend, f"{verb} the volume by about {step}%."),
                )
            if action in ("mute", "unmute"):
                backend.mute(action == "mute")
                if action == "mute" and backend.name == "windows":
                    return _result("system_volume", "Toggled mute (Windows media key).")
                done = "Muted" if action == "mute" else "Unmuted"
                return _result("system_volume", f"{done} the sound.")
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            return _result("system_volume", f"Volume change failed: {exc}", False)
        return _result(
            "system_volume",
            f"Unknown action {action!r}; use get, set, up, down, mute or unmute.",
            success=False,
        )


# ---------------------------------------------------------------------------
# media_control
# ---------------------------------------------------------------------------

_MEDIA_ACTIONS = {
    # action: (playerctl verb, MPRIS method, Windows VK)
    "play_pause": ("play-pause", "PlayPause", _VK_MEDIA_PLAY_PAUSE),
    "next": ("next", "Next", _VK_MEDIA_NEXT),
    "previous": ("previous", "Previous", _VK_MEDIA_PREV),
    "stop": ("stop", "Stop", _VK_MEDIA_STOP),
}


def _mpris_players() -> List[str]:
    out = _run(
        [
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.DBus",
            "--object-path", "/org/freedesktop/DBus",
            "--method", "org.freedesktop.DBus.ListNames",
        ]
    )  # fmt: skip
    if out.returncode != 0:
        return []
    return re.findall(r"'(org\.mpris\.MediaPlayer2\.[^']+)'", out.stdout)


def _media_linux(action: str) -> str:
    verb, method, _ = _MEDIA_ACTIONS[action]
    if shutil.which("playerctl"):
        _check(_run(["playerctl", verb]))
        return "playerctl"
    if not shutil.which("gdbus"):
        raise RuntimeError("install playerctl to control media players")
    players = _mpris_players()
    if not players:
        raise RuntimeError("no media player is running")
    _check(
        _run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                players[0],
                "--object-path",
                "/org/mpris/MediaPlayer2",
                "--method",
                f"org.mpris.MediaPlayer2.Player.{method}",
            ]
        )  # fmt: skip
    )
    return players[0].rsplit(".", 1)[-1]


@ToolRegistry.register("media_control")
class MediaControlTool(BaseTool):
    """Play/pause, skip or stop whatever media player is active."""

    tool_id = "media_control"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="media_control",
            description=(
                "Control the music or video currently playing on the computer"
                " (Spotify, YouTube in the browser, VLC...). Actions:"
                " 'play_pause', 'next', 'previous', 'stop'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": sorted(_MEDIA_ACTIONS),
                    },
                },
                "required": ["action"],
            },
            category="desktop",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action", "")).strip().lower().replace("-", "_")
        if action in ("play", "pause", "toggle"):
            action = "play_pause"
        if action not in _MEDIA_ACTIONS:
            return _result(
                "media_control",
                f"Unknown action {action!r}; use {', '.join(sorted(_MEDIA_ACTIONS))}.",
                success=False,
            )
        try:
            platform = _platform()
            if platform == "windows":
                _press_vk(_MEDIA_ACTIONS[action][2])
                via = "media key"
            elif platform == "macos":
                return _result(
                    "media_control",
                    "Media control is not supported on macOS yet.",
                    success=False,
                )
            else:
                via = _media_linux(action)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            return _result("media_control", f"Media control failed: {exc}", False)
        return _result("media_control", f"Sent {action} ({via}).", action=action)


__all__ = [
    "AppEntry",
    "MediaControlTool",
    "OpenAppTool",
    "SystemVolumeTool",
    "installed_apps",
    "match_apps",
    "volume_backend",
]
