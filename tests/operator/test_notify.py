import json

import pytest

from snipetech.operator.notify import Notifier, from_env, telegram_setup
from snipetech.operator.paths import OperatorPaths


def test_send_posts_truncated_text():
    calls = []
    n = Notifier("T", 7, post=lambda u, d: calls.append((u, d)))
    assert n.enabled and n.send("x" * 5000) is True
    url, data = calls[0]
    assert url.endswith("/botT/sendMessage") and len(data["text"]) == 4000 and data["chat_id"] == 7


def test_missing_token_is_disabled_and_silent():
    n = Notifier(None, None)
    assert not n.enabled and n.send("x") is False


def test_missing_chat_id_is_disabled():
    assert not Notifier("T", None).enabled


def test_send_failure_is_logged_not_raised():
    logs = []

    def boom(u, d):
        raise TimeoutError("slow")

    n = Notifier("T", 7, post=boom, log=logs.append)
    assert n.send("x") is False and logs and "TimeoutError" in logs[0]


def test_setup_stores_chat_id_and_says_hello(tmp_path):
    paths = OperatorPaths(tmp_path).ensure()
    sent = []
    cid = telegram_setup("T", paths, get=lambda u: {"result": [{"message": {"chat": {"id": 42, "type": "private"}}}]},
                         post=lambda u, d: sent.append(d))  # fmt: skip
    assert cid == 42 and json.loads(paths.telegram.read_text())["chat_id"] == 42
    assert sent and "connected" in sent[0]["text"]


def test_setup_without_a_message_raises(tmp_path):
    paths = OperatorPaths(tmp_path).ensure()
    with pytest.raises(RuntimeError, match="no message"):
        telegram_setup("T", paths, get=lambda u: {"result": []}, post=lambda u, d: None)


def test_from_env_reads_token_and_chat_file(tmp_path, monkeypatch):
    paths = OperatorPaths(tmp_path).ensure()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    assert not from_env(paths).enabled
    paths.telegram.write_text(json.dumps({"chat_id": 9}))
    n = from_env(paths)
    assert n.enabled and n.chat_id == 9
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert not from_env(paths).enabled
