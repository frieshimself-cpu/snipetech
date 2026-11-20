"""What the owner pays for, kept from lapsing unseen (20 Nov 2026 audit, after Helius's plan ran out on 19 Nov: "my
subscription ran out"): a reminder before each plan's end date the owner records (/renew), and an alert within minutes
when a provider turns our key away, for the RPC and OKTech as for the tape. And no key in any log: on 19 Nov the
one-minute watch wrote Helius's address with its key into the round's log 84 times."""

import asyncio
import json

import httpx
import pytest

from snipetech.config import Config
from snipetech.oktech import OktechError
from snipetech.operator.budget import Ledger
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.refusals import (
    OKTECH_REFUSED_KEY,
    RPC_REFUSED_KEY,
    clear_refusal,
    note_refusal,
    refusal_status,
)
from snipetech.operator.resources import (
    RENEWALS_KEY,
    alert_lines,
    check_resources,
    oktech_refusal,
    parse_day,
    provider_refusal,
    renew_reply,
    renewal_rows,
)
from snipetech.operator.snapshot import take_snapshot
from snipetech.store import Store
from tests.operator.test_gate import snap

DAY = 86400
NOV_20 = 1_795_132_800  # 20 Nov 2026 00:00 UTC
T = NOV_20 + 3 * 3600  # 03:00 UTC
DEC_20 = NOV_20 + 30 * DAY
KEY = "SECRETKEY0123456789abcdef"
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
SUSPENDED = '{"jsonrpc":"2.0","error":{"code":-32401,"message":"Project is suspended"},"id":1}'


@pytest.fixture
def store(tmp_path):
    st = Store(tmp_path / "t.db")
    yield st
    st.close()


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "spend.jsonl", Config().operator.prices)


def _refused_http(status: int = 403, body: str = SUSPENDED) -> httpx.HTTPStatusError:
    """What httpx's raise_for_status raises for Helius's refusal: its words name the address, key and all."""
    req = httpx.Request("POST", f"https://mainnet.helius-rpc.com/?api-key={KEY}")
    resp = httpx.Response(status, request=req, text=body)
    return httpx.HTTPStatusError(f"Client error '{status} Forbidden' for url '{req.url}'", request=req, response=resp)


def _rows(store, ledger, now, **snap_over):
    return {r.name: r for r in check_resources(Config(), store, snap(**snap_over), ledger, disk_free_gb=lambda: 200.0,
                                               birdeye_credits=None, now=now)}  # fmt: skip


# -- no key in an error's words -------------------------------------------------------------------------------------


def test_a_refused_rpc_call_raises_words_without_the_key_and_keeps_the_status():
    """19 Nov 23:12-00:34: every refused RPC call raised httpx's own words, "Client error '403 Forbidden' for url
    'https://mainnet.helius-rpc.com/?api-key=<the key>'", and the one-minute watch logged them. The client's own error
    names the status and the provider's words, never the address."""
    from snipetech.solana.rpc import SolanaRpc

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=SUSPENDED)

    async def run():
        rpc = SolanaRpc(f"https://mainnet.helius-rpc.com/?api-key={KEY}", max_rps=50, max_retries=1)
        await rpc.client.aclose()
        rpc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await rpc.get_slot()
        finally:
            await rpc.close()

    with pytest.raises(httpx.HTTPStatusError) as caught:
        asyncio.run(run())
    assert caught.value.response.status_code == 403
    assert KEY not in str(caught.value) and "api-key" not in str(caught.value) and "403" in str(caught.value)


def test_the_rounds_log_never_holds_a_key(tmp_path, monkeypatch):
    from snipetech.operator.run import _log

    monkeypatch.setenv("HELIUS_API_KEY", KEY)
    paths = OperatorPaths(tmp_path).ensure()
    _log(paths, T, f"watch: skipped: HTTPStatusError: Client error '403 Forbidden' for url 'https://x/?api-key={KEY}'")
    _log(paths, T, "watch: skipped: for url 'https://y/?api-key=anotherkey9876543210&x=1'")  # a key not in the env
    text = paths.log.read_text()
    assert KEY not in text and "anotherkey9876543210" not in text and text.count("api-key=<redacted>") == 2


def test_the_keys_written_before_the_fix_are_scrubbed_once(tmp_path):
    from snipetech.operator.run import scrub_key_urls_once

    paths = OperatorPaths(tmp_path).ensure()
    paths.log.write_text(
        "a fine line\n" + f"2026-11-19T23:12:05Z watch: skipped: for url 'https://h/?api-key={KEY}'\n" * 3
    )
    paths.jobs.mkdir(parents=True, exist_ok=True)
    job = paths.jobs / "20261119-232000-abc123.log"
    job.write_text(f"error: Client error '403 Forbidden' for url 'https://h/?api-key={KEY}'\n")
    (paths.jobs / "20261119-232000-abc123.exit").write_text("1\n")
    running = paths.jobs / "20261119-224559-ac7619.log"  # no exit file: its job still writes to it, so it is left
    running.write_text(f"for url 'https://h/?api-key={KEY}'\n")
    assert scrub_key_urls_once(paths) == 4
    assert KEY not in paths.log.read_text() and KEY not in job.read_text() and KEY in running.read_text()
    assert paths.log.read_text().startswith("a fine line\n") and paths.log.read_text().count("api-key=<redacted>") == 3
    paths.log.write_text(paths.log.read_text() + f"api-key={KEY}\n")
    assert scrub_key_urls_once(paths) == 0  # once: the next lines are kept clean where they are written


# -- a provider turning our key away ------------------------------------------------------------------------------


def test_what_counts_as_a_provider_turning_our_key_away():
    assert refusal_status(_refused_http(403)) == 403 and refusal_status(_refused_http(401, "")) == 401
    assert refusal_status(_refused_http(429, "")) is None and refusal_status(_refused_http(500, "")) is None
    assert refusal_status(OktechError(402, "PAYMENT_REQUIRED", "VIP expired")) == 402
    assert refusal_status(OktechError(403, "FORBIDDEN", "subscription expired")) == 403
    assert refusal_status(OktechError(0, "NETWORK", "network error: ConnectError")) is None
    assert refusal_status(OktechError(429, "RATE_LIMITED", "still rate limited")) is None
    assert refusal_status(RuntimeError("HTTP 403")) is None


def test_a_refusal_keeps_its_start_across_rounds_and_a_late_one_starts_afresh(store):
    note_refusal(store, RPC_REFUSED_KEY, _refused_http(), T)
    note_refusal(store, RPC_REFUSED_KEY, _refused_http(), T + 900)
    rec = store.coverage(RPC_REFUSED_KEY)
    assert rec["since"] == T and rec["last"] == T + 900 and "Project is suspended" in rec["why"]
    assert KEY not in json.dumps(rec) and "api-key" not in json.dumps(rec)
    clear_refusal(store, RPC_REFUSED_KEY, T + 1800)
    assert store.coverage(RPC_REFUSED_KEY)["cleared"] == T + 1800
    note_refusal(store, RPC_REFUSED_KEY, _refused_http(), T + 3600)
    assert store.coverage(RPC_REFUSED_KEY)["since"] == T + 3600  # a new outage, not the old one
    note_refusal(store, RPC_REFUSED_KEY, _refused_http(), T + 3 * 3600)  # nothing read it for two hours
    assert store.coverage(RPC_REFUSED_KEY)["since"] == T + 3 * 3600


class _Svc:
    def __init__(self, store, oktech=None):
        self.store, self._oktech = store, oktech

    async def oktech_status(self):
        if self._oktech is not None:
            raise self._oktech
        return {"snipers": []}


def _round(tmp_path, svc, now, balances):
    cfg = Config()
    cfg.operator.wallet = W
    return asyncio.run(take_snapshot(svc, OperatorPaths(tmp_path).ensure(), cfg, prev=None, now=now, services=dict,
                                     balances=balances))  # fmt: skip


def test_the_round_writes_down_a_refused_rpc_and_the_helius_row_names_it(tmp_path, store, ledger):
    """The round's wallet read refused (403) twice, 15 minutes apart: the `helius` row says the RPC is refused, in
    Helius's own words, since the first round, and where it is fixed. A read that works clears it."""

    def refused(wallet):
        raise _refused_http()

    first = _round(tmp_path, _Svc(store), T - 900, refused)
    _round(tmp_path, _Svc(store), T, refused)
    assert all(KEY not in e for e in first.errors)
    row = _rows(store, ledger, T, tape_last_ts=T - 5)["helius"]
    assert row.status == "crit" and "the RPC" in row.message and "Project is suspended" in row.message
    assert "15 min" in row.message and "dashboard.helius.dev" in row.message and "paid period ended" in row.message
    assert "tape records nothing" not in row.message  # the stream was not refused: the tape is fine
    _round(tmp_path, _Svc(store), T + 900, lambda w: (1.0, 0.0, []))
    assert store.coverage(RPC_REFUSED_KEY)["cleared"] == T + 900
    assert provider_refusal(store, T + 900) is None


def test_the_tape_and_the_rpc_refused_together_are_one_helius_row(store, ledger):
    store.set_coverage("tape:refused", {"since": T - 1800, "last": T - 60, "why": "Project is suspended"})
    note_refusal(store, RPC_REFUSED_KEY, _refused_http(), T - 600)
    row = provider_refusal(store, T)
    assert row.status == "crit" and "30 min" in row.message  # since the first of the two
    assert "the tape's stream" in row.message and "the RPC" in row.message and "tape records nothing" in row.message


def test_the_round_writes_down_a_refused_oktech_key_and_says_it_once_it_lasts(tmp_path, store, ledger):
    """OKTech runs our exits: a key it refuses (a lapsed plan) leaves a sniper's coin unsold. Until 20 Nov the round's
    failed OKTech read only woke an agent; now it is an alert once it lasts five minutes."""
    svc = _Svc(store, oktech=OktechError(403, "FORBIDDEN", "subscription expired"))

    def ok(wallet):
        return 1.0, 0.0, []

    _round(tmp_path, svc, T - 900, ok)
    assert _rows(store, ledger, T - 900)["oktech_key"].status == "warn"  # the first refusal: not yet an alert
    _round(tmp_path, svc, T, ok)
    rows = _rows(store, ledger, T)
    row = rows["oktech_key"]
    assert row.status == "crit" and "subscription expired" in row.message and "/sell" in row.message
    assert "OKTech" in row.message and alert_lines([row]) == [f"oktech_key: {row.message}"]
    _round(tmp_path, _Svc(store), T + 900, ok)
    assert oktech_refusal(store, T + 900) is None


def test_oktech_not_answering_is_no_refusal(tmp_path, store, ledger):
    _round(tmp_path, _Svc(store, oktech=OktechError(0, "NETWORK", "network error")), T, lambda w: (1.0, 0.0, []))
    assert store.coverage(OKTECH_REFUSED_KEY) == {} and "oktech_key" not in _rows(store, ledger, T)


def test_the_minute_watch_writes_down_a_refused_rpc_without_the_key(tmp_path, monkeypatch):
    from snipetech.operator.run import commands_pass
    from tests.operator.test_run import _watch_env

    paths, st, cfg, svc, _m, ours = _watch_env(tmp_path, monkeypatch)

    async def refused(address, ignore=()):
        raise _refused_http()

    svc.open_positions = refused
    try:
        rc = asyncio.run(commands_pass(cfg, svc, now=lambda: ours.ts + 70, send=lambda x: True,
                                       commands_get=lambda url, params: {"result": []}))  # fmt: skip
        rec = st.coverage(RPC_REFUSED_KEY)
    finally:
        st.close()
    assert rc == 0 and "Project is suspended" in rec["why"] and rec["since"] == ours.ts + 70
    assert KEY not in paths.log.read_text() and "watch: skipped" in paths.log.read_text()


# -- plan end dates -------------------------------------------------------------------------------------------------


def test_a_plan_end_date_is_read_as_a_billing_page_writes_it():
    for words in (["2026-12-20"], ["20", "Dec"], ["Dec", "20,", "2026"], ["december", "20"], ["20th", "December"]):
        assert parse_day(words, T) == DEC_20, words
    assert (
        parse_day(["19", "Nov"], T) == NOV_20 - DAY + 365 * DAY
    )  # a day without its year that has passed: next year's
    for words in (["soon"], ["2026-02-30"], ["next", "week"], ["20", "21", "Dec"], []):
        assert parse_day(words, T) is None, words


def test_the_owner_records_lists_and_forgets_a_plan_end_date(store):
    assert renew_reply(store, ["helius", "20", "Dec"], T).startswith(
        "recorded: Helius plan ends 20 Dec 2026 (in 30 days)"
    )
    assert store.coverage(RENEWALS_KEY)["helius"]["due"] == DEC_20
    assert "not one of" in renew_reply(store, ["heliux", "2026-12-20"], T)
    assert "is not a day" in renew_reply(store, ["server", "next", "week"], T)
    assert "has passed" in renew_reply(store, ["server", "2026-11-19"], T)
    assert "more than a year" in renew_reply(store, ["server", "2028-01-01"], T)
    assert set(store.coverage(RENEWALS_KEY)) == {"helius"}
    listing = renew_reply(store, [], T)
    assert "helius: 20 Dec 2026 (in 30 days)" in listing and "server: no date" in listing and "/renew" in listing
    assert renew_reply(store, ["helius", "off"], T).startswith("Helius: no end date kept now")
    assert "helius" not in store.coverage(RENEWALS_KEY)


def test_a_plan_end_is_a_reminder_from_three_days_out_with_where_to_renew(store, ledger):
    renew_reply(store, ["helius", "2026-12-20"], T)
    renew_reply(store, ["server", "2026-11-25"], T)
    renew_reply(store, ["oktech", "2026-11-22"], T)
    rows = {r.name: r for r in renewal_rows(store, T)}
    assert rows["renew_helius"].status == "ok" and rows["renew_server"].status == "warn"  # 5 days: shown, not sent
    r = rows["renew_oktech"]
    assert r.status == "crit" and r.message.startswith("OKTech plan ends 22 Nov 2026 (in 2 days): renew at")
    assert "no sniper can buy or sell" in r.message
    assert alert_lines(list(rows.values())) == [f"renew_oktech: {r.message}"]
    assert _rows(store, ledger, T)["renew_oktech"].status == "crit"  # the round says it, once a day by its own name


def test_a_day_that_passed_while_the_service_still_answers_moves_on_a_month(store):
    renew_reply(store, ["server", "2026-11-21"], T)
    hours_after = NOV_20 + DAY + 7200  # 21 Nov 02:00: within the day after the end, nothing moves yet
    assert {r.name: r for r in renewal_rows(store, hours_after)}["renew_server"].status == "warn"
    later = NOV_20 + 2 * DAY + 3600  # 22 Nov 01:00: a day past the end and the round still runs
    row = {r.name: r for r in renewal_rows(store, later)}["renew_server"]
    assert row.status == "ok" and "21 Dec 2026" in row.message and "/renew server" in row.message
    assert store.coverage(RENEWALS_KEY)["server"]["due"] == NOV_20 + 31 * DAY


def test_a_day_that_passed_while_the_provider_refuses_is_an_alarm_and_never_moves_on(store):
    renew_reply(store, ["helius", "2026-11-21"], T)
    later = NOV_20 + 3 * DAY
    store.set_coverage("tape:refused", {"since": later - 3600, "last": later - 30, "why": "Project is suspended"})
    row = {r.name: r for r in renewal_rows(store, later)}["renew_helius"]
    assert row.status == "crit" and "ended 21 Nov 2026" in row.message and "dashboard.helius.dev" in row.message
    assert store.coverage(RENEWALS_KEY)["helius"]["due"] == NOV_20 + DAY


def test_with_no_date_recorded_helius_ends_about_a_month_after_it_let_us_back_in(store):
    """19-20 Nov: Helius suspended the project when the plan ran out and let the recorder back in at 00:34 once it was
    renewed. With no date recorded, the reminder comes a month on from there, said as an estimate. A refusal that names
    no suspension (10 Nov: 33 minutes of "Unable to verify project") is no renewal and estimates nothing."""
    cleared = NOV_20 + 34 * 60 + 36
    store.set_coverage("tape:refused", {"since": NOV_20 - 4980, "last": cleared - 180, "why": "Project is suspended",
                                        "cleared": cleared})  # fmt: skip
    assert {r.name: r for r in renewal_rows(store, T)}["renew_helius"].status == "ok"
    row = {r.name: r for r in renewal_rows(store, NOV_20 + 28 * DAY)}["renew_helius"]
    assert row.status == "crit" and "about 20 Dec 2026" in row.message and "/renew helius" in row.message
    renew_reply(store, ["helius", "2026-12-24"], NOV_20 + 28 * DAY)
    assert {r.name: r for r in renewal_rows(store, NOV_20 + 28 * DAY)}["renew_helius"].status == "warn"  # the owner's
    store.set_coverage(RENEWALS_KEY, {})
    store.set_coverage("tape:refused", {"since": T - 1980, "last": T - 60, "why": "Unable to verify project",
                                        "cleared": T})  # fmt: skip
    assert "renew_helius" not in {r.name for r in renewal_rows(store, T + 600)}


def test_renew_is_an_owner_command_in_telegram(tmp_path, store):
    from snipetech.operator.commands import HELP, Command, handle_command
    from snipetech.operator.tools import NoteSink

    paths = OperatorPaths(tmp_path / "data").ensure()
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    sent: list[str] = []

    class Svc:
        pass

    Svc.store = store

    class Notifier:
        def send(self, text):
            sent.append(text)
            return True

    cmd = Command("renew", ["server", "2026-12-01"], chat_id=1, update_id=1, date=int(T))
    reply = asyncio.run(handle_command(cmd, svc=Svc(), cfg=cfg, paths=paths, snap=None, notifier=Notifier(),
                                       notes=NoteSink(paths, now=lambda: T), now=T))  # fmt: skip
    assert reply.startswith("recorded: The server's plan ends 1 Dec 2026") and sent == [reply]
    assert store.coverage(RENEWALS_KEY)["server"]["due"] == NOV_20 + 11 * DAY
    assert "/renew" in HELP and "recorded a plan end date" in paths.notes.read_text()
