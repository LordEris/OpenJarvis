"""Tests for the desktop-control tools (open_app, system_volume, media_control)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from openjarvis.tools import pc_control
from openjarvis.tools.pc_control import (
    AppEntry,
    MediaControlTool,
    OpenAppTool,
    SystemVolumeTool,
    match_apps,
)


def _app(name: str, *aliases: str) -> AppEntry:
    keys = tuple(sorted({pc_control._normalize(n) for n in (name, *aliases)}))
    return AppEntry(name=name, keys=keys, launch=("gtk-launch", name.lower()))


def _proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(pc_control, "_platform", lambda: "linux")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(pc_control, "_platform", lambda: "windows")


# ---------------------------------------------------------------------------
# App index and matching
# ---------------------------------------------------------------------------


class TestMatchApps:
    APPS = [
        _app("Firefox", "Navigateur web Firefox"),
        _app("Paramètres", "Settings"),
        _app("LibreOffice Writer"),
        _app("LibreOffice Calc"),
    ]

    def test_exact_name_wins(self):
        assert [a.name for a in match_apps("firefox", self.APPS)] == ["Firefox"]

    def test_accents_and_case_are_ignored(self):
        assert [a.name for a in match_apps("PARAMETRES", self.APPS)] == ["Paramètres"]

    def test_localized_alias_matches(self):
        assert [a.name for a in match_apps("settings", self.APPS)] == ["Paramètres"]

    def test_prefix_returns_every_candidate(self):
        names = {a.name for a in match_apps("libreoffice", self.APPS)}
        assert names == {"LibreOffice Writer", "LibreOffice Calc"}

    def test_all_words_must_appear(self):
        assert [a.name for a in match_apps("writer libre", self.APPS)] == [
            "LibreOffice Writer"
        ]

    def test_empty_query_matches_nothing(self):
        assert match_apps("   ", self.APPS) == []


class TestDesktopFileParsing:
    def _write(self, tmp_path: Path, body: str, name: str = "app.desktop") -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_localized_names_become_keys(self, tmp_path):
        path = self._write(
            tmp_path,
            "[Desktop Entry]\nType=Application\nName=Files\nName[fr]=Fichiers\n"
            "Exec=nautilus %U\n",
            "org.gnome.Nautilus.desktop",
        )
        entry = pc_control._parse_desktop_file(path, ("gtk-launch",))
        assert entry is not None
        assert "fichiers" in entry.keys and "files" in entry.keys
        # Launched by desktop id: the Exec line is never interpreted by us.
        assert entry.launch == ("gtk-launch", "org.gnome.Nautilus")

    def test_gio_launcher_uses_the_file_path(self, tmp_path):
        path = self._write(tmp_path, "[Desktop Entry]\nType=Application\nName=X\n")
        entry = pc_control._parse_desktop_file(path, ("gio", "launch"))
        assert entry is not None and entry.launch == ("gio", "launch", str(path))

    @pytest.mark.parametrize(
        "extra",
        ["NoDisplay=true", "Hidden=true", "Type=Link"],
    )
    def test_hidden_entries_are_skipped(self, tmp_path, extra):
        body = "[Desktop Entry]\nType=Application\nName=X\n" + extra + "\n"
        path = self._write(tmp_path, body)
        assert pc_control._parse_desktop_file(path, ("gtk-launch",)) is None

    def test_garbage_is_skipped(self, tmp_path):
        path = self._write(tmp_path, "not an ini file")
        assert pc_control._parse_desktop_file(path, ("gtk-launch",)) is None

    def test_index_prefers_user_entries(self, tmp_path, monkeypatch):
        user = tmp_path / "user" / "applications"
        system = tmp_path / "sys" / "applications"
        user.mkdir(parents=True)
        system.mkdir(parents=True)
        common = "[Desktop Entry]\nType=Application\nName={}\n"
        (user / "firefox.desktop").write_text(common.format("Firefox (user)"))
        (system / "firefox.desktop").write_text(common.format("Firefox"))
        (system / "gedit.desktop").write_text(common.format("Text Editor"))
        monkeypatch.setattr(pc_control, "_linux_app_dirs", lambda: [user, system])
        monkeypatch.setattr(pc_control.shutil, "which", lambda name: "/usr/bin/" + name)
        names = sorted(a.name for a in pc_control._linux_apps())
        assert names == ["Firefox (user)", "Text Editor"]


# ---------------------------------------------------------------------------
# open_app
# ---------------------------------------------------------------------------


class TestOpenApp:
    def _tool(self, tmp_path: Path, apps: Optional[List[AppEntry]] = None):
        return OpenAppTool(
            allowed_dirs=[str(tmp_path)],
            app_index=lambda: list(apps or []),
        )

    def test_spec_needs_no_confirmation(self, tmp_path):
        spec = self._tool(tmp_path).spec
        assert spec.name == "open_app"
        assert spec.requires_confirmation is False
        assert spec.parameters["required"] == ["target"]

    def test_https_url_opens_in_browser(self, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr(
            pc_control.webbrowser, "open", lambda url, new=0: opened.append(url) or True
        )
        result = self._tool(tmp_path).execute(target="https://www.youtube.com")
        assert result.success
        assert opened == ["https://www.youtube.com"]

    @pytest.mark.parametrize(
        "target",
        [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "ms-msdt:/id PCWDiagnostic",
            "search-ms:query=x",
            "http://",
        ],
    )
    def test_other_schemes_are_refused(self, tmp_path, monkeypatch, linux, target):
        monkeypatch.setattr(
            pc_control.webbrowser,
            "open",
            lambda *a, **k: pytest.fail("browser must not be called"),
        )
        result = self._tool(tmp_path).execute(target=target)
        assert not result.success
        assert "Refused" in result.content

    def test_file_inside_allowed_dir_opens(self, tmp_path, monkeypatch):
        doc = tmp_path / "rapport.pdf"
        doc.write_text("x")
        opened = []
        monkeypatch.setattr(pc_control, "_open_path_with_os", opened.append)
        result = self._tool(tmp_path).execute(target=str(doc))
        assert result.success, result.content
        assert opened == [doc.resolve()]

    def test_folder_inside_allowed_dir_opens(self, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr(pc_control, "_open_path_with_os", opened.append)
        result = self._tool(tmp_path).execute(target=str(tmp_path))
        assert result.success and opened == [tmp_path.resolve()]
        assert "folder" in result.content

    def test_file_outside_allowed_dirs_is_refused(self, tmp_path, monkeypatch):
        allowed = tmp_path / "Documents"
        allowed.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("x")
        monkeypatch.setattr(
            pc_control, "_open_path_with_os", lambda p: pytest.fail("must not open")
        )
        tool = OpenAppTool(allowed_dirs=[str(allowed)], app_index=list)
        result = tool.execute(target=str(secret))
        assert not result.success and "outside the allowed" in result.content

    def test_symlink_escape_is_refused(self, tmp_path, monkeypatch):
        allowed = tmp_path / "Documents"
        allowed.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        link = allowed / "link.txt"
        link.symlink_to(outside)
        monkeypatch.setattr(
            pc_control, "_open_path_with_os", lambda p: pytest.fail("must not open")
        )
        tool = OpenAppTool(allowed_dirs=[str(allowed)], app_index=list)
        assert not tool.execute(target=str(link)).success

    @pytest.mark.parametrize(
        "name", ["setup.exe", "run.bat", "x.ps1", "script.sh", "app.desktop", "a.lnk"]
    )
    def test_programs_and_scripts_are_refused(self, tmp_path, monkeypatch, name):
        payload = tmp_path / name
        payload.write_text("x")
        monkeypatch.setattr(
            pc_control, "_open_path_with_os", lambda p: pytest.fail("must not open")
        )
        result = self._tool(tmp_path).execute(target=str(payload))
        assert not result.success and "program or script" in result.content

    @pytest.mark.skipif(os.name == "nt", reason="exec bit is POSIX-only")
    def test_executable_bit_is_refused_on_posix(self, tmp_path, monkeypatch, linux):
        payload = tmp_path / "innocent.pdf"
        payload.write_text("#!/bin/sh\n")
        payload.chmod(0o755)
        monkeypatch.setattr(
            pc_control, "_open_path_with_os", lambda p: pytest.fail("must not open")
        )
        assert not self._tool(tmp_path).execute(target=str(payload)).success

    def test_missing_path_is_reported(self, tmp_path):
        result = self._tool(tmp_path).execute(target=str(tmp_path / "nope.txt"))
        assert not result.success and "Not found" in result.content

    def test_app_is_launched_from_the_index(self, tmp_path, monkeypatch, linux):
        spawned = []
        monkeypatch.setattr(pc_control, "_spawn", spawned.append)
        tool = self._tool(tmp_path, [_app("Spotify"), _app("Firefox")])
        result = tool.execute(target="spotify")
        assert result.success and result.metadata["app"] == "Spotify"
        assert spawned == [("gtk-launch", "spotify")]

    def test_ambiguous_name_returns_candidates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc_control, "_spawn", lambda a: pytest.fail("no launch"))
        tool = self._tool(
            tmp_path, [_app("LibreOffice Writer"), _app("LibreOffice Calc")]
        )
        result = tool.execute(target="libreoffice")
        assert not result.success
        assert result.metadata["candidates"] == [
            "LibreOffice Calc",
            "LibreOffice Writer",
        ]

    def test_unknown_app_is_not_run_as_a_command(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc_control, "_spawn", lambda a: pytest.fail("no launch"))
        result = self._tool(tmp_path, [_app("Firefox")]).execute(target="rm -rf ~")
        assert not result.success and "No installed application" in result.content

    def test_windows_shortcut_uses_startfile(self, tmp_path, monkeypatch, windows):
        started = []
        monkeypatch.setattr(pc_control.os, "startfile", started.append, raising=False)
        lnk = AppEntry("Spotify", ("spotify",), ("startfile", r"C:\menu\Spotify.lnk"))
        result = self._tool(tmp_path, [lnk]).execute(target="Spotify")
        assert result.success and started == [r"C:\menu\Spotify.lnk"]

    def test_windows_settings_alias(self, tmp_path, monkeypatch, windows):
        started = []
        monkeypatch.setattr(pc_control.os, "startfile", started.append, raising=False)
        result = self._tool(tmp_path).execute(target="Paramètres")
        assert result.success and started == ["ms-settings:"]

    def test_windows_index_has_builtins_and_skips_uninstallers(
        self, tmp_path, monkeypatch
    ):
        menu = tmp_path / "pd" / "Microsoft/Windows/Start Menu/Programs/VLC"
        menu.mkdir(parents=True)
        (menu / "VLC media player.lnk").write_text("")
        (menu / "Uninstall VLC.lnk").write_text("")
        monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
        monkeypatch.setenv("APPDATA", str(tmp_path / "ad"))
        apps = pc_control._windows_apps()
        names = {a.name for a in apps}
        assert "VLC media player" in names and "Uninstall VLC" not in names
        calc = match_apps("calculatrice", apps)
        assert calc and calc[0].launch == ("calc.exe",)

    def test_spawn_never_uses_a_shell(self, monkeypatch, linux):
        calls = []

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))

        monkeypatch.setattr(pc_control.subprocess, "Popen", fake_popen)
        pc_control._spawn(("gtk-launch", "firefox"))
        argv, kwargs = calls[0]
        assert argv == ["gtk-launch", "firefox"]
        assert not kwargs.get("shell")
        assert kwargs["start_new_session"] is True


# ---------------------------------------------------------------------------
# system_volume
# ---------------------------------------------------------------------------


class _FakeBackend(pc_control._VolumeBackend):
    name = "fake"

    def __init__(self, level: int = 40, muted: bool = False) -> None:
        self.level, self.muted = level, muted

    def get(self) -> Tuple[Optional[int], Optional[bool]]:
        return self.level, self.muted

    def set(self, level: int) -> None:
        self.level = level

    def mute(self, state: Optional[bool]) -> None:
        self.muted = (not self.muted) if state is None else state


class TestSystemVolume:
    def _tool(self, backend):
        return SystemVolumeTool(backend_factory=lambda: backend)

    def test_get(self):
        result = self._tool(_FakeBackend(55, True)).execute(action="get")
        assert result.success and result.content == "Volume is 55% (muted)."

    @pytest.mark.parametrize(("asked", "applied"), [(150, 100), (-5, 0), (33.6, 34)])
    def test_set_is_clamped(self, asked, applied):
        backend = _FakeBackend()
        result = self._tool(backend).execute(action="set", level=asked)
        assert result.success and backend.level == applied

    def test_set_without_level_fails(self):
        assert not self._tool(_FakeBackend()).execute(action="set").success

    def test_up_and_down_use_the_step(self):
        backend = _FakeBackend(50)
        tool = self._tool(backend)
        tool.execute(action="up", step=20)
        assert backend.level == 70
        tool.execute(action="down")
        assert backend.level == 60
        tool.execute(action="up", step=90)
        assert backend.level == 100

    def test_mute_and_unmute(self):
        backend = _FakeBackend()
        tool = self._tool(backend)
        assert tool.execute(action="mute").success and backend.muted
        assert tool.execute(action="unmute").success and not backend.muted

    def test_unknown_action(self):
        assert not self._tool(_FakeBackend()).execute(action="explode").success

    def test_no_backend(self):
        result = SystemVolumeTool(backend_factory=lambda: None).execute(action="get")
        assert not result.success and "No volume control" in result.content

    def test_backend_error_is_reported(self):
        class Broken(_FakeBackend):
            def set(self, level):
                raise RuntimeError("no sink")

        result = self._tool(Broken()).execute(action="set", level=10)
        assert not result.success and "no sink" in result.content


class TestVolumeBackends:
    def test_pactl(self, monkeypatch):
        calls = []

        def fake_run(argv):
            calls.append(list(argv))
            if argv[1] == "get-sink-volume":
                return _proc("Volume: front-left: 52428 /  80% / -5.81 dB, ...")
            if argv[1] == "get-sink-mute":
                return _proc("Mute: no")
            return _proc()

        monkeypatch.setattr(pc_control, "_run", fake_run)
        backend = pc_control._PactlBackend()
        assert backend.get() == (80, False)
        backend.step(30)  # 80 + 30 is clamped to 100 rather than 110
        assert calls[-1] == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"]
        backend.mute(True)
        assert calls[-1] == ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"]

    def test_wpctl(self, monkeypatch):
        calls = []

        def fake_run(argv):
            calls.append(list(argv))
            return _proc("Volume: 0.45 [MUTED]\n")

        monkeypatch.setattr(pc_control, "_run", fake_run)
        backend = pc_control._WpctlBackend()
        assert backend.get() == (45, True)
        backend.set(70)
        assert calls[-1] == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.70"]

    def test_amixer(self, monkeypatch):
        out = "Front Left: Playback 39321 [60%] [-12.00dB] [off]"
        monkeypatch.setattr(pc_control, "_run", lambda argv: _proc(out))
        assert pc_control._AmixerBackend().get() == (60, True)

    def test_failed_command_raises(self, monkeypatch):
        monkeypatch.setattr(pc_control, "_run", lambda argv: _proc("", 1))
        with pytest.raises(RuntimeError):
            pc_control._PactlBackend().set(10)

    def test_linux_backend_selection(self, monkeypatch, linux):
        monkeypatch.setattr(
            pc_control.shutil,
            "which",
            lambda name: "/usr/bin/wpctl" if name == "wpctl" else None,
        )
        assert isinstance(pc_control.volume_backend(), pc_control._WpctlBackend)
        monkeypatch.setattr(pc_control.shutil, "which", lambda name: None)
        assert pc_control.volume_backend() is None

    def test_windows_media_keys_without_pycaw(self, monkeypatch):
        presses = []
        monkeypatch.setattr(
            pc_control, "_press_vk", lambda vk, times=1: presses.append((vk, times))
        )
        backend = pc_control._WindowsBackend.__new__(pc_control._WindowsBackend)
        backend._endpoint = None
        backend.name = "windows"
        backend.set(40)
        assert presses == [
            (pc_control._VK_VOLUME_DOWN, 50),
            (pc_control._VK_VOLUME_UP, 20),
        ]
        presses.clear()
        backend.step(-10)
        assert presses == [(pc_control._VK_VOLUME_DOWN, 5)]
        presses.clear()
        backend.mute(False)
        assert [vk for vk, _ in presses] == [
            pc_control._VK_VOLUME_UP,
            pc_control._VK_VOLUME_DOWN,
        ]


# ---------------------------------------------------------------------------
# media_control
# ---------------------------------------------------------------------------


class TestMediaControl:
    def test_playerctl(self, monkeypatch, linux):
        calls = []
        monkeypatch.setattr(pc_control.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(
            pc_control, "_run", lambda argv: calls.append(list(argv)) or _proc()
        )
        result = MediaControlTool().execute(action="pause")
        assert result.success
        assert calls == [["playerctl", "play-pause"]]

    def test_mpris_fallback(self, monkeypatch, linux):
        calls = []
        names = "(['org.freedesktop.DBus', 'org.mpris.MediaPlayer2.spotify'],)"

        def fake_run(argv):
            calls.append(list(argv))
            return _proc(names if "ListNames" in argv[-1] else "()")

        monkeypatch.setattr(
            pc_control.shutil,
            "which",
            lambda n: None if n == "playerctl" else "/usr/bin/" + n,
        )
        monkeypatch.setattr(pc_control, "_run", fake_run)
        result = MediaControlTool().execute(action="next")
        assert result.success and "spotify" in result.content
        assert calls[-1][calls[-1].index("--dest") + 1] == (
            "org.mpris.MediaPlayer2.spotify"
        )
        assert calls[-1][-1] == "org.mpris.MediaPlayer2.Player.Next"

    def test_no_player_running(self, monkeypatch, linux):
        monkeypatch.setattr(
            pc_control.shutil,
            "which",
            lambda n: None if n == "playerctl" else "/usr/bin/" + n,
        )
        monkeypatch.setattr(pc_control, "_run", lambda argv: _proc("([],)"))
        result = MediaControlTool().execute(action="next")
        assert not result.success and "no media player" in result.content

    def test_windows_presses_the_media_key(self, monkeypatch, windows):
        presses = []
        monkeypatch.setattr(
            pc_control, "_press_vk", lambda vk, times=1: presses.append(vk)
        )
        assert MediaControlTool().execute(action="previous").success
        assert presses == [pc_control._VK_MEDIA_PREV]

    def test_unknown_action(self, linux):
        assert not MediaControlTool().execute(action="rewind").success


def test_open_app_is_bound_to_the_callers_allowed_dirs(tmp_path):
    from openjarvis.core.config import JarvisConfig
    from openjarvis.tools._paths import file_tool_kwargs

    config = JarvisConfig()
    config.security.allowed_dirs = [str(tmp_path)]
    kwargs = file_tool_kwargs("open_app", config)
    assert kwargs == {"allowed_dirs": [str(tmp_path)]}
    tool = OpenAppTool(**kwargs, app_index=list)
    assert tool._allowed_dirs() == [tmp_path.resolve()]
