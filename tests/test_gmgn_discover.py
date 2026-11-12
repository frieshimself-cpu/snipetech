"""GMGN as a discovery source: the wallets it ranks (its KOL list, its smart-money list, the winners of its hottest
pump.fun coins) land in `candidates` under the source "gmgn" with their numbers; the ones GMGN names get a label the
lab's groups read (`kol` for a known person, `wallet` for any other named wallet), never over a label somebody else
wrote; the hourly run asks GMGN no more often than `[gmgn] every_hours`.

The stand-in and where its answers come from: tests/gmgn_fake.py."""

import json

import httpx
import pytest

from snipetech import gmgn as G
from snipetech.config import Config
from snipetech.discovery.run import DiscoverContext, run_discover
from snipetech.discovery.sources import GmgnSource, default_sources
from snipetech.gmgn import Gmgn
from snipetech.service import Service
from snipetech.store import Store
from tests.gmgn_fake import DEV, K1, K2, K3, KEY, M1, M2, M3, POOL, RAT, S1, S2, fake_gmgn, refused

NOW = 1_794_502_100


def _gmgn(seen=None, routes=None, **kw):
    return Gmgn(KEY, transport=fake_gmgn(seen, routes), sleep=lambda s: None, **kw)


def _ctx(tmp_path, cfg=None, **clients):
    st = Store(tmp_path / "t.db")
    svc = Service(cfg or Config(), store=st)
    return DiscoverContext(svc=svc, cfg=svc.cfg, now=NOW, log=lambda m: None, **clients), st


def _labels(st) -> dict:
    return {
        a: (kind, label, source) for a, label, kind, source in st._q("SELECT address, label, kind, source FROM labels")
    }


# -- who GMGN ranks ----------------------------------------------------------------------------------------------------


def test_the_people_on_gmgns_lists_with_profit_and_win_rate():
    seen: list[httpx.Request] = []
    rows = G.people(_gmgn(seen), period="30d", stats_top=2)
    assert [r["wallet"] for r in rows][:3] == [K1, S2, S1]  # most realized profit first; a loser after a winner
    assert {r["wallet"] for r in rows} == {K1, K2, K3, S1, S2}
    cupsey = rows[0]
    assert cupsey["name"] == "Cupsey" and cupsey["twitter"] == "cupseyy" and cupsey["lists"] == ["kol", "smartmoney"]
    assert cupsey["realized_usd"] == 15234.52 and cupsey["cost_usd"] == 48000.0 and cupsey["period"] == "30d"
    assert (cupsey["buys"], cupsey["sells"], cupsey["trades"], cupsey["days"]) == (140, 131, 271, 30)
    assert cupsey["win_rate"] == 0.62 and cupsey["followers"] == 251000 and cupsey["funded_by"] == "Coinbase"
    assert rows[1]["win_rate"] == pytest.approx(0.4318) and rows[1]["tokens"] == 44  # the second of the two asked
    assert "win_rate" not in rows[2]  # stats_top 2: the third wallet's statistics were not asked
    paths = [r.url.path for r in seen]
    assert paths.count("/v1/user/wallet_profits") == 1 and paths.count("/v1/user/wallet_stats") == 2
    # GMGN's list had no profit row for these two: they stay, without numbers
    assert {r["wallet"] for r in rows if r.get("realized_usd") is None} == {K2, K3}


def test_one_list_alone_and_a_list_that_fails():
    rows = G.people(_gmgn(), lists=("kol",), stats_top=0)
    assert {r["wallet"] for r in rows} == {K1, K2, K3}
    said: list[str] = []
    down = {"/v1/user/smartmoney": refused(502, "INTERNAL_API_UNAVAILABLE", "down")}
    rows = G.people(_gmgn(routes=down), stats_top=0, log=said.append)
    assert {r["wallet"] for r in rows} == {K1, K2, K3} and "smartmoney list" in said[0]
    with pytest.raises(G.GmgnError, match="AUTH_KEY_INVALID"):
        G.people(_gmgn(routes={p: refused(401, "AUTH_KEY_INVALID") for p in ("/v1/user/kol", "/v1/user/smartmoney")}))


def test_the_winners_of_the_hottest_pump_fun_coins():
    seen: list[httpx.Request] = []
    rows = G.coin_winners(_gmgn(seen), coins=2, per_coin=50)
    rank = seen[0].url.params
    assert seen[0].url.path == "/v1/market/rank" and rank.get_list("platforms") == ["Pump.fun"]
    assert [r.url.params["address"] for r in seen[1:]] == [M1, M2]
    # the coin's own wallets (its creator) and its insiders are no trade to follow; a pool is no wallet
    assert [r["wallet"] for r in rows] == [S1, K1] and not {DEV, RAT, POOL} & {r["wallet"] for r in rows}
    assert rows[0]["coin_profit_usd"] == pytest.approx(4130.4) and rows[0]["coin_hits"] == 2
    assert rows[0]["coin_tags"] == ["sniper", "top_holder"] and rows[0]["coin_mints"] == [M1, M2]
    assert rows[1]["name"] == "Cupsey" and rows[1]["tags"] == ["kol"]
    one = G.coin_winners(_gmgn(seen), mint=M3)
    assert seen[-1].url.params["address"] == M3 and one[0]["coin_hits"] == 1


# -- into the candidates table -----------------------------------------------------------------------------------------


def test_gmgn_wallets_become_candidates_with_their_numbers(tmp_path):
    ctx, st = _ctx(tmp_path, gmgn=_gmgn())
    try:
        out = run_discover(ctx, [GmgnSource()])
        assert out["errors"] == {} and out["by_source"] == {"gmgn": 5} and out["new"] == 5
        cands = {c["wallet"]: c for c in st.candidates()}
        assert set(cands) == {K1, K2, K3, S1, S2}
        assert all(c["sources"] == ["gmgn"] and c["status"] == "candidate" for c in cands.values())
        s = cands[K1]["stats"]["gmgn"]
        assert s["name"] == "Cupsey" and s["twitter"] == "cupseyy" and s["source"] == "gmgn" and s["scanned_at"] == NOW
        assert s["lists"] == ["kol", "smartmoney", "top_traders"] and s["tags"] == ["gmgn", "kol", "smart_degen"]
        # the names the other sources' histories use, so the qualify stage can read this one the same way
        assert (s["trades"], s["win_rate"], s["realized_usd"], s["days"]) == (271, 0.62, 15234.52, 30)
        assert s["coin_profit_usd"] == pytest.approx(910.0 * 3) and s["coin_hits"] == 3  # three hot coins
        assert cands[S1]["stats"]["gmgn"]["realized_usd"] == -2210.4  # a loser is a candidate too: the lab decides
        # GMGN's profit list had no row for this one: its numbers come from its own statistics
        k3 = cands[K3]["stats"]["gmgn"]
        assert k3["realized_usd"] == 880.25 and k3["win_rate"] == pytest.approx(0.4318) and k3["follow_count"] == 12
        json.dumps(cands[K1]["stats"])  # plain data all the way down
    finally:
        st.close()


def _naming_on() -> Config:
    """Naming is off by default since 12 Nov (GMGN's names would change who the lab's known wallets are before one
    real answer was read); these tests pin what it does once switched on."""
    cfg = Config()
    assert cfg.gmgn.label is False
    cfg.gmgn.label = True
    return cfg


def test_named_wallets_get_the_label_the_labs_groups_read(tmp_path):
    ctx, st = _ctx(tmp_path, cfg=_naming_on(), gmgn=_gmgn())
    try:
        run_discover(ctx, [GmgnSource()])
        assert _labels(st) == {
            K1: ("kol", "Cupsey", "gmgn"),  # a known person by GMGN's list: the lab's group `kol`
            K2: ("kol", "frankdegods", "gmgn"),  # no name, an X handle: that is its name
            S2: ("wallet", "Latuche", "gmgn"),  # named, not a KOL: with the tracked wallets
        }  # K3 and S1 have neither name nor handle: candidates without a label
        from snipetech.analysis.lab import label_groups

        groups = label_groups(st)
        assert groups["kol"] == {K1, K2} and S2 in groups["wallet"] and S2 in groups["trader"]
    finally:
        st.close()


def test_a_label_somebody_else_wrote_is_never_replaced(tmp_path):
    ctx, st = _ctx(tmp_path, cfg=_naming_on(), gmgn=_gmgn())
    try:
        st.set_label(K1, "cupsey main (mine)", "wallet", "file")  # the owner's tracker export
        st.set_label(S2, "old gmgn name", "wallet", "gmgn")  # our own earlier label: kept up to date
        st.set_label(K2, "FOMO router", "service", "user")
        out = run_discover(ctx, [GmgnSource()])
        labels = _labels(st)
        assert labels[K1] == ("wallet", "cupsey main (mine)", "file")
        assert labels[S2] == ("wallet", "Latuche", "gmgn")
        assert labels[K2] == ("service", "FOMO router", "user")
        # a wallet we know as a platform's is not a candidate at all
        assert K2 not in {c["wallet"] for c in st.candidates()} and out["by_source"] == {"gmgn": 4}
    finally:
        st.close()


def test_labelling_can_be_switched_off(tmp_path):
    cfg = Config()
    cfg.gmgn.label = False
    ctx, st = _ctx(tmp_path, cfg=cfg, gmgn=_gmgn())
    try:
        assert run_discover(ctx, [GmgnSource()])["new"] == 5
        assert _labels(st) == {}
    finally:
        st.close()


def test_a_name_typed_on_gmgn_is_kept_to_one_short_line():
    row = {"name": "  Bad\nName\x00 " + "x" * 200, "twitter": None}
    feed = [{"wallet": K1, "name": G._text(row["name"]), "twitter": None, "tags": [], "side": "buy", "usd": 1.0}]
    known = G.known_as(G.wallets_from_feeds({"kol": feed})[0])
    assert "\n" not in known and "\x00" not in known and len(known) <= 60 and known.startswith("Bad Name")


# -- the pace and the failures -----------------------------------------------------------------------------------------


def test_the_hourly_run_asks_gmgn_once_an_hour_and_only_with_a_key(tmp_path):
    seen: list[httpx.Request] = []
    ctx, st = _ctx(tmp_path, gmgn=_gmgn(seen))
    try:
        src = [s for s in default_sources(ctx) if s.name == "gmgn"]
        assert len(src) == 1 and src[0].every_secs == 3600 == Config().gmgn.every_hours * 3600
        assert run_discover(ctx, src)["by_source"] == {"gmgn": 5}
        asked = len(seen)
        ctx.now = NOW + 20 * 60  # the operator's idle picker, or a run by hand, twenty minutes later
        out = run_discover(ctx, src)
        assert out["by_source"] == {} and "gmgn" in out["skipped"] and len(seen) == asked
        ctx.now = NOW + 55 * 60  # the next hourly run (the timer fires a little early or late)
        assert run_discover(ctx, src)["by_source"] == {"gmgn": 5} and len(seen) > asked
        ctx.cfg.gmgn.every_hours = 6.0
        assert [s.every_secs for s in default_sources(ctx) if s.name == "gmgn"] == [6 * 3600]
        ctx.gmgn = None  # no GMGN_API_KEY: the source is not there
        assert "gmgn" not in {s.name for s in default_sources(ctx)}
        assert GmgnSource().fetch(ctx) == []
    finally:
        st.close()


def test_a_refused_key_is_this_sources_error_and_names_no_key(tmp_path):
    routes = {
        p: refused(401, "AUTH_KEY_INVALID", f"key {KEY} unknown") for p in ("/v1/user/kol", "/v1/user/smartmoney")
    }
    ctx, st = _ctx(tmp_path, gmgn=_gmgn(routes=routes))
    try:
        out = run_discover(ctx, [GmgnSource()])
        assert out["by_source"] == {"gmgn": 0} and "HTTP 401 AUTH_KEY_INVALID" in out["errors"]["gmgn"]
        assert KEY not in json.dumps(out)
        # it failed, so it is asked again at the next run, not an hour later
        ctx.gmgn = _gmgn()
        ctx.now = NOW + 60
        assert run_discover(ctx, [GmgnSource()])["by_source"] == {"gmgn": 5}
    finally:
        st.close()


def test_a_rate_limit_ends_the_asking_and_keeps_what_was_found(tmp_path):
    seen: list[httpx.Request] = []
    banned = httpx.Response(
        429, json={"code": 429, "error": "RATE_LIMIT_BANNED", "message": "banned", "reset_at": 4_000_000_000}
    )
    ctx, st = _ctx(tmp_path, gmgn=_gmgn(seen, routes={"/v1/user/wallet_stats": banned}))
    try:
        out = run_discover(ctx, [GmgnSource()])
        assert out["by_source"] == {"gmgn": 5} and out["errors"] == {}
        assert [r.url.path for r in seen].count("/v1/user/wallet_stats") == 1  # one refusal, no second try
        s = {c["wallet"]: c["stats"]["gmgn"] for c in st.candidates()}
        assert s[K1]["realized_usd"] == 15234.52 and "win_rate" not in s[K1]
    finally:
        st.close()
