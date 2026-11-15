"""``jarvis channel setup-telegram`` — pair a Telegram bot with this JARVIS.

Talking to JARVIS from a phone needs three things in ``config.toml``: the
bot token, the chat allowed to use the bot, and the channel switched on.
This command collects them interactively:

1. checks the token with Telegram's ``getMe``;
2. asks the user to send any message to the bot and reads their chat id
   from ``getUpdates`` (so nobody has to look an id up by hand);
3. writes ``[channel]`` / ``[channel.telegram]`` with that chat id as the
   only allowed one — an empty allow-list would let anyone who finds the
   bot drive the computer.

The bot then answers whenever ``jarvis serve`` runs (the desktop app, or
``jarvis start`` in the background). Tools that need a confirmation, such as
``shell_exec``, stay refused on that path.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import click

_API_URL = "https://api.telegram.org/bot{token}/{method}"
# "123456789:AA..." — digits, colon, then 30+ url-safe characters.
_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


class TelegramSetupError(Exception):
    """A Telegram API call failed; the message never contains the token."""


class TelegramAPI:
    """Minimal synchronous Bot API client used only during setup."""

    def __init__(self, token: str, client: Any = None) -> None:
        self._token = token
        if client is None:
            import httpx

            client = httpx.Client(timeout=40.0)
        self._client = client

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "<jeton>") if self._token else text

    def call(self, method: str, **params: Any) -> Any:
        url = _API_URL.format(token=self._token, method=method)
        try:
            resp = self._client.post(url, json=params)
        except Exception as exc:  # noqa: BLE001 - network errors of any kind
            raise TelegramSetupError(self._redact(str(exc))) from None
        try:
            data = resp.json()
        except ValueError:
            raise TelegramSetupError(f"réponse HTTP {resp.status_code}") from None
        if not data.get("ok"):
            detail = data.get("description") or f"HTTP {resp.status_code}"
            raise TelegramSetupError(self._redact(str(detail)))
        return data.get("result")


def _who(message: dict) -> str:
    user = message.get("from") or message.get("chat") or {}
    name = " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    )
    if user.get("username"):
        name = f"{name} (@{user['username']})".strip()
    return name or "inconnu"


def wait_for_private_message(
    api: TelegramAPI,
    timeout_s: float = 180.0,
    *,
    poll_s: int = 25,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[Tuple[str, str]]:
    """Return ``(chat_id, who)`` of the first private message sent to the bot.

    Messages sent before the call are acknowledged and ignored, so an old
    "/start" from someone else cannot be mistaken for the owner's.
    """
    backlog = api.call("getUpdates", offset=-1, timeout=0) or []
    offset = backlog[-1]["update_id"] + 1 if backlog else None
    deadline = clock() + timeout_s
    while clock() < deadline:
        wait = max(1, min(poll_s, int(deadline - clock())))
        params: dict = {"timeout": wait, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        for update in api.call("getUpdates", **params) or []:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            if chat.get("type") == "private" and "id" in chat:
                # Acknowledge it so `jarvis serve` does not answer it later.
                api.call("getUpdates", offset=offset, timeout=0)
                return str(chat["id"]), _who(message)
    return None


def _confirm_fr(question: str) -> bool:
    """Yes/no prompt that understands French answers (click.confirm is y/n only)."""
    while True:
        answer = click.prompt(f"{question} [O/n]", default="o", show_default=False)
        answer = answer.strip().lower()
        if answer in ("o", "oui", "y", "yes"):
            return True
        if answer in ("n", "non", "no"):
            return False
        click.echo("Réponds par o (oui) ou n (non).")


def config_path() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return Path(os.environ.get("OPENJARVIS_CONFIG", DEFAULT_CONFIG_DIR / "config.toml"))


def write_telegram_config(path: Path, token: str, chat_id: str, agent: str) -> None:
    """Enable the Telegram channel for *chat_id* only, keeping other settings."""
    import tomlkit

    doc = (
        tomlkit.parse(path.read_text(encoding="utf-8"))
        if path.exists()
        else tomlkit.document()
    )
    if "channel" not in doc:
        doc.add("channel", tomlkit.table())
    channel = doc["channel"]
    channel["enabled"] = True
    channel["default_channel"] = "telegram"
    channel["default_agent"] = agent
    if "telegram" not in channel:
        channel.add("telegram", tomlkit.table())
    telegram = channel["telegram"]
    existing = [
        cid.strip()
        for cid in str(telegram.get("allowed_chat_ids", "")).split(",")
        if cid.strip()
    ]
    if chat_id not in existing:
        existing.append(chat_id)
    telegram["bot_token"] = token
    telegram["allowed_chat_ids"] = ",".join(existing)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # the token is a secret


@click.command("setup-telegram")
@click.option(
    "--token",
    envvar="TELEGRAM_BOT_TOKEN",
    default=None,
    help="Jeton du bot donné par @BotFather (sinon il est demandé).",
)
@click.option(
    "--agent",
    default="orchestrator",
    show_default=True,
    help="Agent qui répond sur Telegram.",
)
@click.option(
    "--wait",
    "wait_s",
    default=180,
    show_default=True,
    help="Secondes d'attente de ton message au bot.",
)
def setup_telegram(token: Optional[str], agent: str, wait_s: int) -> None:
    """Piloter JARVIS depuis ton téléphone avec un bot Telegram."""
    # httpx logs full request URLs at INFO, and the token is part of the URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    click.echo(
        "\nPiloter JARVIS depuis ton téléphone (Telegram)\n\n"
        "1. Dans Telegram, ouvre une conversation avec @BotFather.\n"
        "2. Envoie /newbot, choisis un nom puis un identifiant finissant par 'bot'.\n"
        "3. BotFather te donne un jeton du type 123456789:AA...\n"
        "   Garde-le secret : il donne le contrôle du bot.\n"
        "Ferme l'app OpenJarvis pendant la configuration.\n"
    )
    if not token:
        token = click.prompt("Colle le jeton ici", hide_input=True).strip()
    if not _TOKEN_RE.match(token):
        raise click.ClickException(
            "Ce jeton n'a pas le bon format (attendu : 123456789:AA...)."
        )

    api = TelegramAPI(token)
    try:
        me = api.call("getMe")
    except TelegramSetupError as exc:
        raise click.ClickException(f"Telegram refuse ce jeton : {exc}") from None
    bot = f"@{me.get('username', '?')}"
    click.echo(f"Bot reconnu : {bot}\n")
    click.echo(
        f"Sur ton téléphone, ouvre {bot} dans Telegram et envoie-lui un message"
        f" (par exemple « bonjour »). J'attends {wait_s} secondes..."
    )

    try:
        found = wait_for_private_message(api, float(wait_s))
    except TelegramSetupError as exc:
        hint = ""
        if "conflict" in str(exc).lower():
            hint = " Ferme l'app OpenJarvis ou arrête `jarvis start`, puis relance."
        raise click.ClickException(f"Lecture des messages impossible : {exc}.{hint}")
    if found is None:
        raise click.ClickException(
            "Aucun message reçu. Relance la commande et écris au bot pendant l'attente."
        )
    chat_id, who = found
    if not _confirm_fr(f"Message reçu de {who}. C'est bien toi ?"):
        raise click.ClickException("Configuration annulée, rien n'a été modifié.")

    path = config_path()
    write_telegram_config(path, token, chat_id, agent)
    try:
        api.call(
            "sendMessage",
            chat_id=chat_id,
            text="JARVIS est relié à ce compte. Parle-moi ici quand JARVIS tourne.",
        )
    except TelegramSetupError:
        pass  # the config is written; the greeting is only a courtesy

    click.echo(
        f"\nC'est prêt. Seul ce compte Telegram peut parler à {bot}.\n"
        f"Configuration écrite dans {path}\n\n"
        "Le bot répond tant que JARVIS tourne :\n"
        "  - ouvre l'app OpenJarvis, ou\n"
        "  - lance `jarvis start` (en arrière-plan) ; `jarvis stop` pour l'arrêter.\n"
        "Depuis le téléphone, JARVIS peut ouvrir des applis, régler le son ou la\n"
        "musique, chercher sur le web ; les commandes système restent bloquées."
    )


__all__ = [
    "TelegramAPI",
    "TelegramSetupError",
    "setup_telegram",
    "wait_for_private_message",
    "write_telegram_config",
]
