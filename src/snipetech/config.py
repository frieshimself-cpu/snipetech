"""Configuration: a TOML file (default ./snipetech.toml) plus environment overrides.

Every number that changes a profitability verdict lives here so you can calibrate it to
*your* sniper: landing latency, sizes, priority fees / tips, bot fees. Run
`snipetech calibrate <your-sniper-wallet>` to measure latency from your own history.
"""

from __future__ import annotations

import dataclasses
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(os.environ.get("SNIPETECH_CONFIG", "snipetech.toml"))


@dataclass
class RpcConfig:
    http_url: str = "https://api.mainnet-beta.solana.com"
    ws_url: str = ""  # derived from http_url when empty
    max_rps: float = 8.0  # public RPC is ~10 rps; raise for Helius/Triton/QuickNode
    max_concurrency: int = 8
    timeout_secs: float = 30.0
    max_retries: int = 6
    commitment: str = "confirmed"

    def websocket_url(self) -> str:
        if self.ws_url:
            return self.ws_url
        return self.http_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


@dataclass
class CostConfig:
    priority_fee_sol: float = 0.0002  # compute-budget fee you pay per transaction
    tip_sol: float = 0.001  # Jito / sender tip per transaction
    platform_fee_bps: int = 0  # bot or terminal fee per trade (e.g. 100 = 1%)
    default_pump_fee_bps: int = 125  # only used when no on-chain fee rate is available
    monthly_fixed_cost_sol: float = 0.0  # RPC / VPS / bot subscription, for break-even math

    def per_tx_sol(self) -> float:
        return self.priority_fee_sol + self.tip_sol


@dataclass
class ExitConfig:
    take_profit: float = 1.0  # +100%; 0 disables
    tp_sell_fraction: float = 1.0  # fraction of the bag sold at take-profit
    stop_loss: float = 0.35  # -35%; 0 disables
    trailing_stop: float = 0.0  # e.g. 0.3 = sell 30% below the peak; 0 disables
    trailing_activation: float = 0.0  # only trail after this gain
    max_hold_secs: float = 120.0
    sell_on_dev_sell: bool = True  # exit when the dev / bundle wallets start dumping


@dataclass
class SnipeConfig:
    buy_sol: float = 0.5
    entry_slot_offset: int = 1  # slots after the create tx your buy lands (0 = same slot)
    entry_position: str = "middle"  # start | middle | end of the landing slot
    exit_latency_slots: int = 1  # slots between seeing a trigger and your sell landing
    analysis_window_secs: int = 900
    max_launches: int = 150  # launches per deployer to analyze
    min_launches: int = 5  # below this a verdict is INSUFFICIENT_DATA
    rinse_window_secs: int = 30  # insiders dumping within this window = rinse
    rinse_dump_fraction: float = 0.5  # share of insider bag sold to count as a dump
    runner_multiple: float = 2.0  # position value multiple that counts as a runner
    competition_slots: int = 3  # outside buyers landing within this many slots of the create are "snipers"
    exit: ExitConfig = field(default_factory=ExitConfig)


@dataclass
class CopyConfig:
    size_sol: float = 0.5
    size_mode: str = "fixed"  # fixed | proportional
    proportional_ratio: float = 0.1  # copier SOL per target SOL when proportional
    max_size_sol: float = 5.0
    buy_latency_slots: int = 1
    sell_latency_slots: int = 1
    landing_position: str = "middle"
    lookback_days: float = 14.0
    max_signatures: int = 5000
    min_positions: int = 10
    crowd_window_slots: int = 3


@dataclass
class VerdictConfig:
    confidence: float = 0.8  # one-sided bootstrap confidence for the EV lower bound
    min_ev_sol: float = 0.0  # EV lower bound needed for SNIPE / COPY
    recent_launches: int = 5  # window used for behaviour-change warnings
    bootstrap_samples: int = 2000


@dataclass
class StorageConfig:
    db_path: str = "snipetech.db"
    live_window_secs: int = 900  # keep live trades this long after each launch
    retain_days: float = 30.0


@dataclass
class ApiConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    api_key: str = ""  # if set, required as `X-API-Key`


@dataclass
class LiveConfig:
    webhook_url: str = ""
    index_amm: bool = False  # also index all PumpSwap trades (heavy)
    rescore_interval_secs: int = 120
    funder_lookup: bool = True  # resolve who funded brand-new deployer wallets
    source: str = "auto"  # auto (LaserStream for the tape when HELIUS_API_KEY is set) | laserstream | logs
    laserstream_endpoint: str = "laserstream-mainnet-fra.helius-rpc.com:443"


@dataclass
class CopierConfig:
    """The live copytrader (`snipetech copytrade`). Paper mode unless `executor` says otherwise."""

    executor: str = "paper"  # paper | webhook (orders to your own bot) | native (signs and sends itself)
    webhook_url: str = ""  # webhook: where orders are POSTed (HMAC-signed with $SNIPETECH_WEBHOOK_SECRET)
    keypair_path: str = ""  # native: Solana CLI keypair JSON (or $SNIPETECH_KEYPAIR)
    size_sol: float = 0.1
    size_mode: str = "fixed"  # fixed | proportional (to the copied wallet's buy)
    proportional_ratio: float = 0.1
    min_size_sol: float = 0.01
    max_size_sol: float = 1.0
    min_leader_sol: float = 0.05  # ignore the copied wallet's dust buys
    slippage_bps: int = 1500  # min tokens out vs the price right after the copied buy
    priority_fee_sol: float = 0.0005
    tip_sol: float = 0.0
    venues: str = "curve,amm"  # pump.fun curve, PumpSwap
    mirror_sells: bool = True  # sell the same fraction when the copied wallet sells
    take_profit: float = 0.0  # own exits on top of mirroring; 0 = off (1.0 = +100%)
    stop_loss: float = 0.5  # sell everything at -50%
    trailing_stop: float = 0.0
    max_hold_secs: float = 0.0
    max_open_positions: int = 5
    max_total_sol: float = 2.0  # capital in open positions at once
    daily_loss_limit_sol: float = 1.0  # stop opening positions after losing this much today
    cooldown_secs: int = 600  # no re-entry into a token after exiting it
    skip_deployer_verdicts: str = "AVOID,BLOCK"
    paper_latency_ms: int = 400  # paper fills use the market this long after the copied trade
    commitment: str = "processed"  # wallet streams: processed is fastest


@dataclass
class OktechConfig:
    """OKTech Sniper API (`snipetech oktech ...`). The key comes from $OKS_KEY, never this file."""

    base_url: str = "https://oktech.me/api/sniper-api/v1"
    wallet: str = ""  # which imported OKTech wallet buys (name); empty = your OKTech default wallet
    tip_share: float = 0.7  # how a recommended bribe splits into tip (rest: priority fee)
    copy_exit_threshold_percent: float = 50.0  # ctbot: sell when the copied wallet sells at least this much
    copy_min_wallets: int = 1  # ctbot: copied wallets that must buy within the window
    copy_time_window_ms: int = 10_000
    jetstream: bool = True  # ctbot speedMode
    preconf: bool = True  # the account is approved; OKTech ignores the flag on task types it gates (harmless to ask)
    max_snipers: int = 20  # never plan more than this many (OKTech runs at most 20 at once)
    # verdict snipers (`oktech push`): capped like every other sniper, and never bigger than this per buy
    verdict_max_buys: int = 5
    max_buy_sol: float = 1.0
    # Sells: OKTech sends every sell 2-3 times on separate nonce accounts (measured 5-6 Nov; the losers fail with pump
    # error 6023 and still pay their priority fee), so the sell bid is a fixed small fee for every arm, never the buy bid.
    sell_fee_sol: float = 0.002  # autoSell sellPriorityFeeSol and sellTipAmount on copy snipers
    # Compute-unit limit per buy (extraParams.computeUnits). OKTech's default is 150,000; our buys used 54-79k. The leader
    # ranks by fee per CU, so a tighter limit at the same SOL fee bids higher. Unset = OKTech's default; set it per arm
    # from measured consumption (fills.cu_used), never below the largest buy seen.
    compute_units: int | None = None


@dataclass
class AgentConfig:
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_tokens: int = 16000


DEFAULT_OPERATOR_PRICES: dict[str, float] = {  # USD per million tokens
    "in": 3.0,
    "out": 15.0,
    "cache_read": 0.3,
    "cache_write": 3.75,  # claude-sonnet-5-5
    "analyst_in": 5.0,
    "analyst_out": 25.0,
    "analyst_cache_read": 0.5,
    "analyst_cache_write": 6.25,  # claude-opus-5-5
}


@dataclass
class OperatorConfig:
    """The server-side operator: a 15-minute snapshot, Claude only when something changed (docs/superpowers/specs)."""

    wallet: str = ""  # the sniper wallet to watch (public key)
    daily_usd: float = 15.0  # no more Claude calls today past this
    turn_usd: float = 2.0  # one turn stops after the response that crosses this
    max_jobs: int = 3  # research jobs running at once (one `lab` at a time regardless)
    max_tool_calls: int = 20
    equity_alert_sol: float = 0.1
    tape_stale_secs: int = 300
    analyst_after_utc: str = "06:00"
    digest_hour_utc: int = 7  # the daily Telegram digest goes out in the first round after this hour (UTC)
    model: str = "claude-sonnet-5-5"
    analyst_model: str = "claude-opus-5-5"
    max_tokens: int = 8000
    analyst_max_tokens: int = 16000
    effort: str = "medium"
    prices: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_OPERATOR_PRICES))


@dataclass
class BirdeyeConfig:
    """Birdeye Data API (docs/birdeye.md). The key is BIRDEYE_API_KEY in the environment."""

    daily_cu: int = 250_000  # compute units a day a scan may spend (plan: 8 M a month)
    top_coins: int = 30  # hottest coins from the tape whose top traders become candidates
    pnl_top: int = 100  # candidates that get a 30-day PnL summary (30 CU each)
    gainers_periods: list[str] = field(default_factory=lambda: ["1W", "30d"])
    top_trader_frame: str = "7d"
    min_trades: int = 5


@dataclass
class SolanaTrackerConfig:
    """SolanaTracker Data API (Advanced plan: 200 k requests a month). Key: SOLANATRACKER_API_KEY."""

    daily_requests: int = 6000
    pages: int = 5  # leaderboard pages of 100 per scan
    days: int = 90
    max_trades: int = 3000  # above this a wallet is a bot, not a trader to copy
    min_win_rate: float = 35.0
    max_single_share: float = 0.6  # profit share allowed from one coin
    min_realized_usd: float = 500.0
    max_single_token_pct: float = 60.0  # server-side filter


@dataclass
class AutotuneConfig:
    """What the weekly strategist (`snipetech strategist run`, Sundays 05:00 UTC) may change by itself, and how far.
    A key not in `bounds` is never touched; a proposal outside them becomes a question on Telegram instead of a
    change. `min`/`max` are absolute, `rel_min`/`rel_max` multiply the current value, `choices` lists the allowed
    strings. `book.enabled`, wallets, keys and sizes above these ceilings are never tunable."""

    enabled: bool = True
    turn_usd: float = 10.0  # one Opus turn a week, cut off at this
    # where the strategist writes its changes: a small TOML next to the database (the owner's file under /etc stays
    # untouched and root-owned); merged over the file for [book] and [qualify] only. Empty = <db dir>/autotune.toml
    overlay: str = ""
    bounds: dict[str, dict] = field(default_factory=lambda: {
        "book.fill_max_sol": {"min": 0.1, "max": 1.0},
        "book.min_score": {"rel_min": 0.5, "rel_max": 2.0},
        "book.explore_share": {"min": 0.0, "max": 0.3},
        "book.bid_arms.control.priority": {"min": 0.001, "max": 0.05},
        "book.bid_arms.control.tip": {"min": 0.002, "max": 0.05},
        "book.bid_arms.paid.priority": {"min": 0.001, "max": 0.05},
        "book.bid_arms.paid.tip": {"min": 0.002, "max": 0.05},
        "qualify.max_gap3_pct": {"min": 5, "max": 30},
        "qualify.min_n_tape": {"min": 10, "max": 40},
        "qualify.landing_pos": {"choices": ["+3", "+6", "+9"]},
    })  # fmt: skip


@dataclass
class ResourcesConfig:
    """Thresholds for the operator's resource checks (credits, data-API usage, SOL float, disk)."""

    anthropic_credit_usd: float = 100.0  # what the owner loaded; `config set` records when
    anthropic_alert_pct: float = 20.0
    anthropic_min_days: float = 3.0
    birdeye_alert_pct: float = 15.0
    solanatracker_month_requests: int = 200_000
    solanatracker_alert_pct: float = 15.0
    vybe_month_credits: int = 25_000
    vybe_alert_pct: float = 15.0
    disk_alert_gb: float = 30.0
    sol_float_margin: float = 0.2
    oktech_fail_alert: int = 3


@dataclass
class BookConfig:
    """The live book's bounds (phase 4 builds the policy; phase 1 only reads these for the SOL float check)."""

    enabled: bool = False
    daily_risk_sol: float = 2.0
    fill_min_sol: float = 0.2
    fill_max_sol: float = 1.0
    max_wallets: int = 6
    equity_floor_sol: float = 3.0
    day_stop_sol: float = 1.0
    min_score: float = 0.005  # SOL per trade lower bound at the landing (verdict score) to be copied
    explore_share: float = 0.2  # share of wallets run on the alternative bid arm
    bid_arms: dict[str, dict] = field(
        default_factory=lambda: {"control": {"priority": 0.001, "tip": 0.002}, "paid": {"priority": 0.02, "tip": 0.02}}
    )
    send_count: int = 1
    max_fee_share: float = 0.03  # an arm's fee may be at most this share of the fill
    live_n: int = 10  # live fills before a wallet's live mean outranks its paper score
    max_buys_per_sniper: int = 3  # OKTech totalSnipes per book sniper: a sniper still selling a coin is never stopped, so this bounds how far it can overrun a stop (the book replaces spent snipers)
    min_copy_sol: float = 0.3  # copy only the wallet's buys of at least this size


@dataclass
class QualifyConfig:
    """The qualify stage's gates (docs/superpowers/specs/2026-11-06-learning-loop-design.md, Stage 3)."""

    landing_pos: str = "+6"  # the landing the by-wallet lab measures at (the live copy test's regime)
    min_hist_trades: int = 20
    max_hist_trades: int = 3000  # above this the wallet is a bot
    min_win_rate: float = 35.0  # percent
    min_realized_usd: float = 500.0
    max_single_share: float = 0.6  # one coin may not be more than this share of the realized profit
    min_venue_share: float = 0.5  # share of the wallet's trades on pump.fun venues (curve, amm)
    min_n_tape: int = 20
    min_n_tape_strong: int = 10  # enough when the history is strong
    strong_win_rate: float = 60.0
    strong_coins: int = 50
    max_gap3_pct: float = 15.0  # median price gap to the 3rd follower above this = too crowded for our landing
    review_qualified_h: int = 24
    review_candidate_h: int = 72
    review_retired_h: int = 168
    lab_interval_h: float = 2.0  # at most one qualify lab per this many hours (the lab is the heavy job)
    max_wallets: int = 300  # due candidates measured per lab


@dataclass
class MeasureConfig:
    """The measure stage: live fills audited every five minutes (spec Stage 5)."""

    half_life_days: float = 7.0  # live expectancy decay
    min_arm_fills: int = 30  # fills on one arm before its p75 position becomes the qualify landing
    overlap_secs: int = 600  # re-read this much before the last run (late tape rows)
    lookback_days: int = 7  # window for the bid model and the live expectancy


@dataclass
class Config:
    rpc: RpcConfig = field(default_factory=RpcConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    snipe: SnipeConfig = field(default_factory=SnipeConfig)
    copy: CopyConfig = field(default_factory=CopyConfig)
    verdict: VerdictConfig = field(default_factory=VerdictConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    copier: CopierConfig = field(default_factory=CopierConfig)
    oktech: OktechConfig = field(default_factory=OktechConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    birdeye: BirdeyeConfig = field(default_factory=BirdeyeConfig)
    solanatracker: SolanaTrackerConfig = field(default_factory=SolanaTrackerConfig)
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    qualify: QualifyConfig = field(default_factory=QualifyConfig)
    measure: MeasureConfig = field(default_factory=MeasureConfig)
    book: BookConfig = field(default_factory=BookConfig)
    autotune: AutotuneConfig = field(default_factory=AutotuneConfig)
    path: Path | None = field(default=None, compare=False, repr=False)
    env_keys: list[str] = field(
        default_factory=list, compare=False, repr=False
    )  # values that came from the environment
    overlay_path: str | None = field(
        default=None, compare=False, repr=False
    )  # the strategist's overlay, when it was read

    @classmethod
    def load(cls, path: str | Path | None = None, overlay: bool = True) -> Config:
        """The owner's file, then the environment, then (unless `overlay` is False) the strategist's overlay."""
        p = Path(path) if path else DEFAULT_PATH
        cfg = cls()
        if p.exists():
            with p.open("rb") as fh:
                _apply(cfg, tomllib.load(fh))
        cfg.path = p
        _apply_env(cfg)
        if overlay:
            _apply_overlay(cfg)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d.pop("path", None)
        d.pop("env_keys", None)
        d.pop("overlay_path", None)
        return d

    def get(self, dotted: str) -> Any:
        obj: Any = self
        for part in dotted.split("."):
            obj = getattr(obj, part)
        return obj

    def set(self, dotted: str, value: Any) -> Any:
        """Set `section.key` (or `snipe.exit.key`), coercing to the field's type."""
        parts = dotted.split(".")
        obj: Any = self
        for part in parts[:-1]:
            obj = getattr(obj, part)
        name = parts[-1]
        if not dataclasses.is_dataclass(obj) or name not in {f.name for f in dataclasses.fields(obj)}:
            raise KeyError(f"unknown config key {dotted!r}")
        current = getattr(obj, name)
        coerced = _coerce(value, type(current))
        setattr(obj, name, coerced)
        if dotted in self.env_keys:  # an explicit set is written to the file even where the environment overrides it
            self.env_keys.remove(dotted)
        return coerced

    def save(self, path: str | Path | None = None) -> Path:
        """Write the config file atomically. Values that came from the environment (the Helius key inside the RPC
        URLs, SNIPETECH_* overrides) are written back as the file had them, never as the environment set them."""
        p = Path(path) if path else (self.path or DEFAULT_PATH)
        data = self.to_dict()
        if self.env_keys:
            base = Config()
            if p.exists():
                with p.open("rb") as fh:
                    _apply(base, tomllib.load(fh))
            for dotted in self.env_keys:
                _set_nested(data, dotted, base.get(dotted))
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(dump_toml(data))
        os.replace(tmp, p)
        self.path = p
        return p


OVERLAY_SECTIONS = ("book", "qualify")
OVERLAY_FORBIDDEN_WORDS = ("enabled", "wallet", "key", "token", "secret", "url", "path")


def overlay_path_for(cfg: Config) -> Path:
    return (
        Path(cfg.autotune.overlay)
        if cfg.autotune.overlay
        else Path(cfg.storage.db_path).resolve().parent / "autotune.toml"
    )


def _apply_overlay(cfg: Config) -> None:
    """The strategist's changes (autotune.toml in the data directory) on top of the owner's file: [book] and [qualify]
    only; anything else in that file is ignored."""
    try:
        path = overlay_path_for(cfg)
        if not path.exists():
            return
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return
    for section in OVERLAY_SECTIONS:
        if isinstance(data.get(section), dict):
            # never from the overlay: switching the book on, wallets, keys (the owner's file decides those)
            safe = {k: v for k, v in data[section].items() if not any(w in k.lower() for w in OVERLAY_FORBIDDEN_WORDS)}
            _apply(getattr(cfg, section), safe)
    cfg.overlay_path = str(path)


def _set_nested(d: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def _coerce(value: Any, target: type) -> Any:
    if target is type(None):  # an optional field (e.g. oktech.compute_units): a number when the text is one
        if value is None or (isinstance(value, str) and value.strip().lower() in ("", "none", "null")):
            return None
        for cast in (int, float):
            try:
                return cast(value)
            except (TypeError, ValueError):
                continue
        return value
    if target in (list, tuple):
        if isinstance(value, (list, tuple)):
            return list(value)
        return [v.strip() for v in str(value).split(",") if v.strip()]
    if target is bool:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "yes", "on"}:
                return True
            if v in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"not a boolean: {value!r}")
        return bool(value)
    if target is int:
        return int(float(value))
    if target is float:
        return float(value)
    return str(value)


def _apply(obj: Any, data: dict[str, Any]) -> None:
    for key, value in data.items():
        if not hasattr(obj, key):
            continue
        current = getattr(obj, key)
        if dataclasses.is_dataclass(current) and isinstance(value, dict):
            _apply(current, value)
        elif isinstance(current, dict) and isinstance(value, dict):
            if isinstance(obj, AutotuneConfig) and key == "bounds":
                # per rule: a partial rule keeps the default's other limits; `off = true` (or an empty table) removes it
                for k, rule in value.items():
                    if not isinstance(rule, dict) or not rule or rule.get("off"):
                        current.pop(k, None)
                    else:
                        current[k] = {**current.get(k, {}), **rule}
            else:
                current.update(value)  # a dict field (e.g. operator.prices) is merged over its defaults
        else:
            setattr(obj, key, _coerce(value, type(current)))


_ENV = {
    "SNIPETECH_RPC_URL": "rpc.http_url",
    "SNIPETECH_WS_URL": "rpc.ws_url",
    "SNIPETECH_RPC_RPS": "rpc.max_rps",
    "SNIPETECH_DB": "storage.db_path",
    "SNIPETECH_RETAIN_DAYS": "storage.retain_days",
    "SNIPETECH_API_KEY": "api.api_key",
    "SNIPETECH_WEBHOOK_URL": "live.webhook_url",
    "SNIPETECH_MODEL": "agent.model",
}


def _apply_env(cfg: Config) -> None:
    for env, dotted in _ENV.items():
        if os.environ.get(env):
            cfg.set(dotted, os.environ[env])
            cfg.env_keys.append(dotted)
    # Convenience: HELIUS_API_KEY alone is enough to use Helius mainnet.
    if os.environ.get("HELIUS_API_KEY") and not os.environ.get("SNIPETECH_RPC_URL"):
        key = os.environ["HELIUS_API_KEY"]
        cfg.rpc.http_url = f"https://mainnet.helius-rpc.com/?api-key={key}"
        cfg.rpc.ws_url = f"wss://mainnet.helius-rpc.com/?api-key={key}"
        cfg.env_keys += ["rpc.http_url", "rpc.ws_url"]
        if cfg.rpc.max_rps <= 8 and not os.environ.get("SNIPETECH_RPC_RPS"):
            cfg.rpc.max_rps = 40
            cfg.env_keys.append("rpc.max_rps")


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(k: str) -> str:
    """A bare key when TOML allows it, else a quoted one (the [autotune] bounds are keyed by dotted config paths)."""
    return k if _BARE_KEY.match(k) else _toml_value(k)


def dump_toml(data: dict[str, Any], prefix: str = "") -> str:
    scalars = {
        k: v for k, v in data.items() if not isinstance(v, dict) and v is not None
    }  # TOML has no null: a None is left out
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    lines: list[str] = []
    if prefix and scalars:
        lines.append(f"[{prefix}]")
    for k, v in scalars.items():
        lines.append(f"{_toml_key(k)} = {_toml_value(v)}")
    if scalars:
        lines.append("")
    for k, v in tables.items():
        lines.append(dump_toml(v, f"{prefix}.{_toml_key(k)}" if prefix else _toml_key(k)))
    return "\n".join(lines)
