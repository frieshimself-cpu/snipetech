"""apply_patch writes the config atomically, records a param_change event per key and tells Telegram; run_strategist
applies in bounds, asks out of bounds, runs once a week, and a dry run applies nothing. Config.save never writes
values that came from the environment (the Helius key lives in the RPC URL)."""

import asyncio
import tomllib

from snipetech.config import Config
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store
from snipetech.strategist.apply import apply_patch, set_path
from snipetech.strategist.run import run_strategist

NOW = 1_793_923_200 + 5 * 3600
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


def _cfg_file(tmp_path, **env):
    p = tmp_path / "snipetech.toml"
    cfg = Config()
    cfg.operator.wallet = W
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    cfg.save(p)
    return Config.load(p)


def test_set_path_handles_dataclasses_and_dicts():
    cfg = Config()
    set_path(cfg, "qualify.landing_pos", "+9")
    set_path(cfg, "book.bid_arms.paid.priority", 0.03)
    set_path(cfg, "qualify.min_n_tape", 25.0)
    assert (
        cfg.qualify.landing_pos == "+9"
        and cfg.book.bid_arms["paid"]["priority"] == 0.03
        and cfg.qualify.min_n_tape == 25
    )
    assert isinstance(cfg.qualify.min_n_tape, int)


def test_apply_patch_writes_config_events_and_telegram(tmp_path):
    cfg = _cfg_file(tmp_path)
    OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")
    sent = []
    notifier = Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))
    changes = apply_patch(
        cfg.path, {"qualify.landing_pos": "+9", "book.explore_share": 0.25}, "more exploration", st, notifier, NOW
    )
    assert [c["key"] for c in changes] == ["qualify.landing_pos", "book.explore_share"]
    again = Config.load(cfg.path)
    assert again.qualify.landing_pos == "+9" and again.book.explore_share == 0.25 and again.book.enabled is False
    rows = st.events(kind="param_change", limit=10)
    data = {r["data"]["key"]: r["data"] for r in rows}  # the store parses `data` back into a dict
    assert data["qualify.landing_pos"] == {
        "key": "qualify.landing_pos",
        "old": "+6",
        "new": "+9",
        "reason": "more exploration",
        "evidence_ts": NOW,
    }
    assert len(sent) == 2 and all("strategist" in t for t in sent) and not list(tmp_path.glob("*.tmp"))
    st.close()


def test_landing_choice_applies_to_qualify(tmp_path):
    from snipetech.qualify.run import effective_landing_pos

    cfg = _cfg_file(tmp_path)
    st = Store(":memory:")
    apply_patch(cfg.path, {"qualify.landing_pos": "+9"}, "r", st, Notifier("T", 7, post=lambda u, d: None), NOW)
    assert effective_landing_pos(st, Config.load(cfg.path).qualify) == "+9"
    st.close()


class FakeSvc:
    def __init__(self, store):
        self.store = store


def _scripted_turn(patch, reason="because"):
    async def turn(cfg, evidence, ledger, notes, client=None, now=None):
        return {"patch": patch, "reason": reason, "usd": 0.5, "stopped": "end_turn"}

    return turn


def _run(tmp_path, patch, **kw):
    cfg = _cfg_file(tmp_path)
    paths = OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")
    sent = []
    out = asyncio.run(run_strategist(FakeSvc(st), cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                                     turn=_scripted_turn(patch), **kw))  # fmt: skip
    return out, st, sent, paths, cfg


def test_in_bounds_patch_is_applied_and_logged(tmp_path):
    out, st, sent, paths, cfg = _run(tmp_path, {"book.explore_share": 0.25})
    assert out["applied"] == {"book.explore_share": 0.25} and out["refusals"] == []
    assert Config.load(cfg.path).book.explore_share == 0.25 and st.coverage("strategist:last")["ts"] == NOW
    assert any("book.explore_share" in t for t in sent)
    st.close()


def test_question_when_out_of_bounds(tmp_path):
    out, st, sent, paths, cfg = _run(tmp_path, {"book.explore_share": 0.25, "book.fill_max_sol": 3.0})
    assert out["applied"] == {} and out["refusals"] and Config.load(cfg.path).book.explore_share == 0.2
    assert any("fill_max_sol" in t and "?" in t for t in sent)  # the owner is asked, nothing is applied
    assert st.coverage("strategist:proposal")["patch"]["book.fill_max_sol"] == 3.0
    st.close()


def test_weekly_guard(tmp_path):
    out, st, sent, paths, cfg = _run(tmp_path, {"book.explore_share": 0.25})
    assert out["applied"]
    out2 = asyncio.run(run_strategist(FakeSvc(st), Config.load(cfg.path), NOW + 3600, notifier=Notifier("T", 7, post=lambda u, d: None),
                                      turn=_scripted_turn({"book.explore_share": 0.3})))  # fmt: skip
    assert out2["skipped"] and "week" in out2["skipped"] and Config.load(cfg.path).book.explore_share == 0.25
    out3 = asyncio.run(run_strategist(FakeSvc(st), Config.load(cfg.path), NOW + 3600, notifier=Notifier("T", 7, post=lambda u, d: None),
                                      turn=_scripted_turn({"book.explore_share": 0.3}), force=True))  # fmt: skip
    assert out3["applied"] == {"book.explore_share": 0.3}
    st.close()


def test_dry_run_applies_nothing(tmp_path):
    out, st, sent, paths, cfg = _run(tmp_path, {"book.explore_share": 0.25}, dry_run=True)
    assert out["dry_run"] and out["would_apply"] == {"book.explore_share": 0.25}
    assert Config.load(cfg.path).book.explore_share == 0.2 and st.coverage("strategist:last") == {} and sent == []
    st.close()


def test_no_change_writes_a_note_and_no_event(tmp_path):
    out, st, sent, paths, cfg = _run(tmp_path, {})
    assert (
        out["applied"] == {}
        and st.events(kind="param_change", limit=5) == []
        and st.coverage("strategist:last")["ts"] == NOW
    )
    assert "no change" in paths.notes.read_text().lower()
    st.close()


def test_cli_strategist_parses():
    from snipetech.cli import build_parser

    a = build_parser().parse_args(["strategist", "run", "--dry-run"])
    assert a.cmd == "strategist" and a.action == "run" and a.dry_run is True and a.force is False


def test_config_save_never_writes_environment_values(tmp_path, monkeypatch):
    """Config.load applies HELIUS_API_KEY to the RPC URLs; saving must write the file's own values back, not the key."""
    p = tmp_path / "snipetech.toml"
    Config().save(p)
    monkeypatch.setenv("HELIUS_API_KEY", "helius-secret-123")
    monkeypatch.delenv("SNIPETECH_RPC_URL", raising=False)
    cfg = Config.load(p)
    assert "helius-secret-123" in cfg.rpc.http_url
    cfg.book.explore_share = 0.25
    cfg.save()
    text = p.read_text()
    assert "helius-secret-123" not in text and "explore_share = 0.25" in text
    assert tomllib.loads(text)["rpc"]["http_url"] == Config().rpc.http_url
