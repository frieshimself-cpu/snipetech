"""What OpenRouter says of the agents' own key: its limit, what is left of it, when it ends (GET /api/v1/key, asked
with that key itself, so no new secret). 19 Nov 2026: OpenRouter's documents say a key can carry a credit limit and an
end date, and that a key with neither "lets a leaked key or a runaway agent spend your entire balance". Nothing here
could see either: a limit of 100 $ would stop the agents after 100 $ of use, whatever the account holds."""

import httpx

from snipetech import openrouter_account as OA

ANSWER = {"data": {"label": "sk-or-v1-abc...xyz", "limit": 100, "limit_remaining": 85.68, "limit_reset": "daily",
                   "usage": 14.32, "usage_daily": 14.32, "usage_weekly": 14.32, "usage_monthly": 14.32,
                   "is_free_tier": False, "expires_at": "2027-05-18T00:00:00Z"}}  # fmt: skip


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_key_is_read_with_the_agents_own_key_and_only_its_numbers_are_kept(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=ANSWER)

    row = OA.read_key(client=_client(handler))
    assert len(seen) == 1 and seen[0].method == "GET" and str(seen[0].url) == "https://openrouter.ai/api/v1/key"
    assert seen[0].headers["authorization"] == "Bearer or-test-key"
    assert row == {"ok": True, "limit": 100.0, "limit_remaining": 85.68, "limit_reset": "daily", "usage": 14.32,
                   "usage_daily": 14.32, "expires_at": "2027-05-18T00:00:00Z"}  # fmt: skip
    assert "label" not in row and "sk-or" not in str(row)  # the key's own masked form is not ours to print


def test_without_an_openrouter_key_nothing_is_asked(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("no request without a key")

    assert OA.read_key(client=_client(handler)) is None


def test_a_read_that_fails_says_so_in_few_words_and_never_repeats_the_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key-0123456789")

    def refused(request):
        return httpx.Response(401, json={"error": {"message": "No auth credentials found: or-test-key-0123456789"}})

    def page(request):
        return httpx.Response(200, text="<html>not json</html>")

    def down(request):
        raise httpx.ConnectTimeout("no answer")

    for handler, word in ((refused, "401"), (page, "no JSON"), (down, "ConnectTimeout")):
        row = OA.read_key(client=_client(handler))
        assert row["ok"] is False and word in row["error"], row
        assert "or-test-key-0123456789" not in str(row)


def test_the_words_say_what_would_stop_the_agents_by_surprise():
    """A limit below the day's budget and an end date within two weeks are the two things worth a warning."""
    now = 1_795_100_000  # 19 Nov 2026
    free = OA.words({"ok": True, "limit": None, "limit_remaining": None, "limit_reset": None, "usage": 14.32,
                     "usage_daily": 14.32, "expires_at": None}, day_budget_usd=500.0, now=now)  # fmt: skip
    assert free["warn"] is False and "no limit" in free["text"] and "14.32 $ used today" in free["text"]
    capped = OA.words({"ok": True, "limit": 100.0, "limit_remaining": 85.68, "limit_reset": None, "usage": 14.32,
                       "usage_daily": 14.32, "expires_at": None}, day_budget_usd=500.0, now=now)  # fmt: skip
    assert capped["warn"] is True and "85.68 $ left of a limit of 100 $" in capped["text"]
    assert "never refilled" in capped["text"] and "stop" in capped["text"]
    daily = OA.words({"ok": True, "limit": 520.0, "limit_remaining": 505.0, "limit_reset": "daily", "usage": 15.0,
                      "usage_daily": 15.0, "expires_at": "2027-05-18T00:00:00Z"}, day_budget_usd=500.0, now=now)  # fmt: skip
    assert daily["warn"] is False and "refilled daily" in daily["text"] and "18 May 2027" in daily["text"]
    ending = OA.words({"ok": True, "limit": None, "limit_remaining": None, "limit_reset": None, "usage": 1.0,
                       "usage_daily": 1.0, "expires_at": "2026-11-25T00:00:00Z"}, day_budget_usd=500.0, now=now)  # fmt: skip
    assert ending["warn"] is True and "ends on 25 Nov 2026" in ending["text"]
    assert OA.words({"ok": False, "error": "401: refused"}, 500.0, now)["warn"] is True


def test_the_doctor_reads_the_key_and_says_it_in_words(tmp_path, monkeypatch, capsys):
    import json

    from snipetech.cli import main

    argv = ["--db", str(tmp_path / "t.db"), "doctor", "--key"]
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main(argv) == 0 and "no OpenRouter key" in json.loads(capsys.readouterr().out)["text"]
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    capped = {"ok": True, "limit": 100.0, "limit_remaining": 5.0, "limit_reset": None, "usage": 95.0,
              "usage_daily": 95.0, "expires_at": None}  # fmt: skip
    monkeypatch.setattr(OA, "read_key", lambda *a, **k: capped)
    assert main(argv) == 1  # something would stop the agents by surprise: the command says so by its exit code
    said = json.loads(capsys.readouterr().out)
    assert said["warn"] is True and "5.00 $ left of a limit of 100 $" in said["text"] and said["limit"] == 100.0
