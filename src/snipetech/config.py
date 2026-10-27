"""Configuration: a TOML file (default ./snipetech.toml) plus environment overrides.

Every number that changes a profitability verdict lives here so you can calibrate it to
*your* sniper: landing latency, sizes, priority fees / tips, bot fees. Run
`snipetech calibrate <your-sniper-wallet>` to measure latency from your own history.
"""

from __future__ import annotations

import dataclasses
import os
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


@dataclass
class AgentConfig:
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_tokens: int = 16000


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
    agent: AgentConfig = field(default_factory=AgentConfig)
    path: Path | None = field(default=None, compare=False, repr=False)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path) if path else DEFAULT_PATH
        cfg = cls()
        if p.exists():
            with p.open("rb") as fh:
                _apply(cfg, tomllib.load(fh))
        cfg.path = p
        _apply_env(cfg)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d.pop("path", None)
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
        return coerced

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path) if path else (self.path or DEFAULT_PATH)
        p.write_text(dump_toml(self.to_dict()))
        self.path = p
        return p


def _coerce(value: Any, target: type) -> Any:
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
        else:
            setattr(obj, key, _coerce(value, type(current)))


_ENV = {
    "SNIPETECH_RPC_URL": "rpc.http_url",
    "SNIPETECH_WS_URL": "rpc.ws_url",
    "SNIPETECH_RPC_RPS": "rpc.max_rps",
    "SNIPETECH_DB": "storage.db_path",
    "SNIPETECH_API_KEY": "api.api_key",
    "SNIPETECH_WEBHOOK_URL": "live.webhook_url",
    "SNIPETECH_MODEL": "agent.model",
}


def _apply_env(cfg: Config) -> None:
    for env, dotted in _ENV.items():
        if os.environ.get(env):
            cfg.set(dotted, os.environ[env])
    # Convenience: HELIUS_API_KEY alone is enough to use Helius mainnet.
    if os.environ.get("HELIUS_API_KEY") and not os.environ.get("SNIPETECH_RPC_URL"):
        key = os.environ["HELIUS_API_KEY"]
        cfg.rpc.http_url = f"https://mainnet.helius-rpc.com/?api-key={key}"
        cfg.rpc.ws_url = f"wss://mainnet.helius-rpc.com/?api-key={key}"
        if cfg.rpc.max_rps <= 8:
            cfg.rpc.max_rps = 40


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dump_toml(data: dict[str, Any], prefix: str = "") -> str:
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    lines: list[str] = []
    if prefix and scalars:
        lines.append(f"[{prefix}]")
    for k, v in scalars.items():
        lines.append(f"{k} = {_toml_value(v)}")
    if scalars:
        lines.append("")
    for k, v in tables.items():
        lines.append(dump_toml(v, f"{prefix}.{k}" if prefix else k))
    return "\n".join(lines)
