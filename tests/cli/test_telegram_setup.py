"""Tests for ``jarvis channel setup-telegram``."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Dict, List

import pytest
import tomlkit
from click.testing import CliRunner

from openjarvis.cli import telegram_setup
from openjarvis.cli.telegram_setup import (
    TelegramAPI,
    TelegramSetupError,
    wait_for_private_message,
    write_telegram_config,
)

TOKEN = "123456789:" + "A" * 35


class _Resp:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeTelegram:
    """Scripted Bot API: each method pops its next queued result."""

    def __init__(self, **queues: List[Any]) -> None:
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls: List[tuple[str, Dict[str, Any]]] = []

    def post(self, url: str, json: Dict[str, Any]) -> _Resp:
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json))
        queue = self.queues.get(method, [])
        result = queue.pop(0) if queue else []
        if isinstance(result, _Resp):
            return result
        return _Resp({"ok": True, "result": result})


def _msg(update_id: int, chat_id: int, chat_type: str = "private") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"first_name": "Evan", "username": "evan"},
            "text": "bonjour",
        },
    }


class TestTelegramAPI:
    def test_errors_never_contain_the_token(self):
        class Boom:
            def post(self, url, json):
                raise RuntimeError(f"connection failed for {url}")

        with pytest.raises(TelegramSetupError) as err:
            TelegramAPI(TOKEN, client=Boom()).call("getMe")
        assert TOKEN not in str(err.value)
        assert "<jeton>" in str(err.value)

    def test_api_error_description_is_raised(self):
        fake = FakeTelegram(
            getMe=[_Resp({"ok": False, "description": "Unauthorized"}, 401)]
        )
        with pytest.raises(TelegramSetupError, match="Unauthorized"):
            TelegramAPI(TOKEN, client=fake).call("getMe")


class TestWaitForPrivateMessage:
    def test_backlog_is_skipped_and_first_private_message_wins(self):
        fake = FakeTelegram(
            getUpdates=[
                [_msg(10, 999)],  # backlog from before the setup
                [_msg(11, -100, "group"), _msg(12, 4242)],
                [],  # acknowledgement call
            ]
        )
        found = wait_for_private_message(TelegramAPI(TOKEN, client=fake), 60)
        assert found == ("4242", "Evan (@evan)")
        polls = [params for method, params in fake.calls if method == "getUpdates"]
        assert polls[0] == {"offset": -1, "timeout": 0}
        assert polls[1]["offset"] == 11  # backlog acknowledged, never returned
        assert polls[-1] == {"offset": 13, "timeout": 0}

    def test_times_out(self):
        ticks = iter([0.0, 0.0, 5.0, 11.0])
        fake = FakeTelegram(getUpdates=[[], []])
        found = wait_for_private_message(
            TelegramAPI(TOKEN, client=fake), 10, clock=lambda: next(ticks)
        )
        assert found is None


class TestWriteConfig:
    def test_keeps_other_settings_and_restricts_to_the_chat(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text(
            '# my config\n[engine]\ndefault = "ollama"\n\n'
            '[channel.telegram]\nallowed_chat_ids = "111"\n',
            encoding="utf-8",
        )
        write_telegram_config(path, TOKEN, "4242", "orchestrator")
        write_telegram_config(path, TOKEN, "4242", "orchestrator")  # idempotent
        text = path.read_text(encoding="utf-8")
        doc = tomlkit.parse(text)
        assert "# my config" in text
        assert doc["engine"]["default"] == "ollama"
        assert doc["channel"]["enabled"] is True
        assert doc["channel"]["default_channel"] == "telegram"
        assert doc["channel"]["default_agent"] == "orchestrator"
        assert doc["channel"]["telegram"]["bot_token"] == TOKEN
        assert doc["channel"]["telegram"]["allowed_chat_ids"] == "111,4242"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
    def test_file_is_private(self, tmp_path):
        path = tmp_path / "config.toml"
        write_telegram_config(path, TOKEN, "1", "orchestrator")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_loads_as_a_jarvis_config(self, tmp_path):
        from openjarvis.core.config import load_config

        path = tmp_path / "config.toml"
        write_telegram_config(path, TOKEN, "4242", "orchestrator")
        cfg = load_config(path=path)
        assert cfg.channel.enabled and cfg.channel.default_channel == "telegram"
        assert cfg.channel.telegram.allowed_chat_ids == "4242"


class TestCommand:
    def _run(self, monkeypatch, tmp_path: Path, fake: FakeTelegram, args, input):
        monkeypatch.setenv("OPENJARVIS_CONFIG", str(tmp_path / "config.toml"))
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            telegram_setup,
            "TelegramAPI",
            lambda token: TelegramAPI(token, client=fake),
        )
        return CliRunner().invoke(telegram_setup.setup_telegram, args, input=input)

    def test_happy_path(self, monkeypatch, tmp_path):
        fake = FakeTelegram(
            getMe=[{"username": "MonJarvisBot"}],
            getUpdates=[[], [_msg(1, 4242)], []],
        )
        result = self._run(monkeypatch, tmp_path, fake, [], f"{TOKEN}\no\n")
        assert result.exit_code == 0, result.output
        assert "@MonJarvisBot" in result.output
        assert TOKEN not in result.output
        cfg = tomlkit.parse((tmp_path / "config.toml").read_text(encoding="utf-8"))
        assert cfg["channel"]["telegram"]["allowed_chat_ids"] == "4242"
        sent = [params for method, params in fake.calls if method == "sendMessage"]
        assert [p["chat_id"] for p in sent] == ["4242"]

    def test_bad_token_format_is_rejected_offline(self, monkeypatch, tmp_path):
        fake = FakeTelegram()
        result = self._run(monkeypatch, tmp_path, fake, ["--token", "nope"], None)
        assert result.exit_code != 0
        assert fake.calls == []
        assert not (tmp_path / "config.toml").exists()

    def test_refusing_the_sender_writes_nothing(self, monkeypatch, tmp_path):
        fake = FakeTelegram(
            getMe=[{"username": "MonJarvisBot"}],
            getUpdates=[[], [_msg(1, 666)], []],
        )
        result = self._run(monkeypatch, tmp_path, fake, ["--token", TOKEN], "n\n")
        assert result.exit_code != 0
        assert not (tmp_path / "config.toml").exists()

    def test_conflict_hint(self, monkeypatch, tmp_path):
        conflict = _Resp(
            {"ok": False, "description": "Conflict: terminated by other getUpdates"},
            409,
        )
        fake = FakeTelegram(getMe=[{"username": "B"}], getUpdates=[conflict])
        result = self._run(monkeypatch, tmp_path, fake, ["--token", TOKEN], None)
        assert result.exit_code != 0
        assert "Ferme l'app" in result.output

    def test_registered_under_channel(self):
        from openjarvis.cli.channel_cmd import channel

        assert "setup-telegram" in channel.commands


@pytest.mark.parametrize(
    ("answers", "expected"),
    [("o\n", True), ("OUI\n", True), ("\n", True), ("non\n", False), ("x\nn\n", False)],
)
def test_french_confirmation(answers, expected):
    import click

    @click.command()
    def cmd():
        click.echo(f"result={telegram_setup._confirm_fr('Ok ?')}")

    out = CliRunner().invoke(cmd, input=answers).output
    assert f"result={expected}" in out
