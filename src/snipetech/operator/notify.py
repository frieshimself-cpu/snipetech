"""Telegram for decision-level notes. Missing token or chat id → disabled; any send failure is logged, never raised."""

from __future__ import annotations

import json
import os
from typing import Callable

import httpx

from .paths import OperatorPaths

API = "https://api.telegram.org"
MAX_TEXT = 4000


def _post(url: str, data: dict) -> object:
    r = httpx.post(url, json=data, timeout=10)
    r.raise_for_status()
    return r.json()


def _get(url: str) -> dict:
    r = httpx.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


class Notifier:
    def __init__(self, token: str | None, chat_id: int | None, post: Callable[[str, dict], object] | None = None,
                 log: Callable[[str], None] = print):  # fmt: skip
        self.token, self.chat_id, self.post, self.log = token or None, chat_id, post or _post, log

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            self.post(f"{API}/bot{self.token}/sendMessage",
                      {"chat_id": self.chat_id, "text": str(text)[:MAX_TEXT], "disable_web_page_preview": True})  # fmt: skip
            return True
        except Exception as exc:  # noqa: BLE001 - a Telegram outage must never fail a round
            self.log(f"[operator] telegram send failed: {type(exc).__name__}: {exc}")
            return False


def from_env(paths: OperatorPaths, log: Callable[[str], None] = print) -> Notifier:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = None
    try:
        chat_id = int(json.loads(paths.telegram.read_text()).get("chat_id"))
    except (OSError, ValueError, TypeError, AttributeError):
        chat_id = None
    return Notifier(token, chat_id, log=log)


def telegram_setup(token: str, paths: OperatorPaths, get: Callable[[str], dict] | None = None,
                   post: Callable[[str, dict], object] | None = None) -> int:  # fmt: skip
    """Find the owner's private chat from the bot's updates (they must have messaged it once), store it, say hello."""
    updates = (get or _get)(f"{API}/bot{token}/getUpdates")
    for u in updates.get("result", []):
        chat = ((u.get("message") or u.get("edited_message") or {}).get("chat")) or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            chat_id = int(chat["id"])
            paths.ensure()
            paths.telegram.write_text(json.dumps({"chat_id": chat_id}))
            Notifier(token, chat_id, post=post).send("snipetech operator connected")
            return chat_id
    raise RuntimeError("no message from you yet: open the bot in Telegram, press Start or send 'hi', then retry")
