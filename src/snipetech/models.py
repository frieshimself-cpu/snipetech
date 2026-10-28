"""Normalized records shared by ingestion, storage, simulation and analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

CURVE = "curve"  # pump.fun bonding curve
AMM = "amm"  # PumpSwap pool (post-migration), incl. the synthetic-migration pool part
LAUNCHLAB = "launchlab"  # Raydium LaunchLab bonding curve (bonk.fun and others)


@dataclass(slots=True)
class Launch:
    mint: str
    user: str  # signer that created the coin (usually the deployer wallet)
    creator: str  # fee/metadata creator (differs from `user` on delegated/free creation)
    name: str
    symbol: str
    uri: str
    bonding_curve: str
    slot: int
    ts: int
    sig: str
    token_program: str = ""
    mayhem: bool = False
    holder_reward: bool = False
    quote_mint: str = ""

    @property
    def deployer(self) -> str:
        """Primary identity: the creator (fee) wallet, which stays with the person even when a
        launch platform signs the create; falls back to the signer."""
        return self.creator or self.user

    def deployers(self) -> set[str]:
        return {a for a in (self.user, self.creator) if a}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Trade:
    """One swap on the bonding curve or a PumpSwap pool, normalized to SOL/lamports.

    `quote` is the constant-product amount (lamports into the reserves for a buy, out of
    them for a sell) *before* fees; the trader paid `quote + fee` on a buy and received
    `quote - fee` on a sell. `q_after`/`b_after` are the effective reserves after the trade
    (virtual reserves on the curve), which is all the simulator needs to price
    counterfactual trades at that point in time.
    """

    sig: str
    ev: int  # event index within the transaction
    slot: int
    pos: int  # position of the transaction within its slot (block order)
    ts: int
    mint: str
    venue: str
    user: str
    is_buy: bool
    quote: int
    base: int
    fee: int
    fee_bps: int
    q_after: int
    b_after: int
    r_base_after: int = 0  # real token reserves left on the curve (0 for AMM)
    ix: str = ""
    quote_mint: str = ""  # "" = SOL; otherwise amounts are in this token's base units (kept apart from SOL trades)

    @property
    def order(self) -> tuple[int, int, int]:
        return (self.slot, self.pos, self.ev)

    @property
    def signed_quote(self) -> int:
        """Lamports the trader's wallet moved: negative on a buy (paid), positive on a sell."""
        return -(self.quote + self.fee) if self.is_buy else self.quote - self.fee


@dataclass(slots=True)
class Completion:
    mint: str
    slot: int
    ts: int
    sig: str


@dataclass(slots=True)
class Migration:
    mint: str
    pool: str
    slot: int
    ts: int
    sig: str


@dataclass(slots=True)
class Normalized:
    launches: list[Launch]
    trades: list[Trade]
    completions: list[Completion]
    migrations: list[Migration]
    pools: dict[str, tuple[str, str]]  # pool -> (base_mint, quote_mint)
    unresolved_pools: set[str]
    foreign: list[Trade] = field(default_factory=list)  # trades quoted in another token (normalize(foreign=True))
