"""Research jobs give the processor to the tape recorder first. 7 Nov 16:58-17:21: with an events scan and a qualify run
(its lab on four cores) going, the recorder fell 25-44 s behind the chain and its coverage mark up to 5 minutes behind;
the PumpSwap stream dropped for falling behind (16:41). The heavy commands lower their own priority; the recorder, the
operator's rounds, the book, the measure stage and OKTech actions keep theirs."""

from snipetech import cli


def test_heavy_research_commands_lower_their_priority(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(cli.os, "nice", lambda n: calls.append(n) or n)
    for cmd in ("lab", "qualify", "events", "research", "paper", "discover", "study"):
        cli._yield_cpu(cmd)
    assert calls == [cli.BATCH_NICE] * 7 and cli.BATCH_NICE >= 10


def test_the_recorder_and_the_live_paths_keep_their_priority(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(cli.os, "nice", lambda n: calls.append(n) or n)
    for cmd in ("live", "operate", "book", "measure", "oktech", "remote", "ops-serve", "serve", "snapshot-db"):
        cli._yield_cpu(cmd)
    assert calls == []


def test_every_command_passes_through_the_priority_check(monkeypatch, tmp_path):
    seen: list[str] = []
    monkeypatch.setattr(cli, "_yield_cpu", lambda cmd: seen.append(cmd))
    cli.main(["--db", str(tmp_path / "t.db"), "config", "get", "storage.db_path"])
    assert seen == ["config"]


def test_a_refused_priority_change_never_stops_the_job(monkeypatch):
    def refuse(n):
        raise PermissionError("not allowed")

    monkeypatch.setattr(cli.os, "nice", refuse)
    cli._yield_cpu("lab")  # no exception


def test_a_research_job_on_a_paid_rpc_asks_faster_than_the_always_on_services():
    """10 Nov: the owner's Helius plan allows 200 requests a second and showed no rate limit hit; a scan of the whole
    tape was held to our own 40 a second and took hours. A batch command on a paid endpoint asks at 120 a second with
    24 requests in flight; the recorder's, the operator's and the book's rate stays, and so does a rate the owner set."""
    from snipetech.config import Config

    cfg = Config()
    cfg.rpc.max_rps, cfg.rpc.max_concurrency = 40, 8
    cli._batch_rpc(cfg, "book")
    assert (cfg.rpc.max_rps, cfg.rpc.max_concurrency) == (40, 8)
    cli._batch_rpc(cfg, "launch-tools")
    assert (cfg.rpc.max_rps, cfg.rpc.max_concurrency) == (120, 24)
    public = Config()  # the public endpoint's 8 a second is a limit of the endpoint, not ours
    cli._batch_rpc(public, "launch-tools")
    assert public.rpc.max_rps == 8.0
    fast = Config()
    fast.rpc.max_rps = 300  # already above ours: left alone
    cli._batch_rpc(fast, "launch-tools")
    assert fast.rpc.max_rps == 300


def test_a_rate_the_owner_set_in_the_environment_is_kept(monkeypatch):
    from snipetech.config import Config

    monkeypatch.setenv("SNIPETECH_RPC_RPS", "60")
    cfg = Config()
    cfg.rpc.max_rps, cfg.rpc.max_concurrency = 60, 8
    cli._batch_rpc(cfg, "launch-tools")
    assert (cfg.rpc.max_rps, cfg.rpc.max_concurrency) == (60, 8)
