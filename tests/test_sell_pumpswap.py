"""Our own PumpSwap sell: a coin that left its pump.fun curve is sold on its PumpSwap pool, from the account list of the
sale OKTech landed from our wallet (tests/fixtures/pumpswap_sell_template.json: coin 2NgdUjFY…pump, 6 Nov 05:15:55 UTC,
`sell_v2`) re-derived per pool and coin, never written from memory.

Everything here runs on recorded mainnet data: the template, the tape decoder's own sell_v2 fixture (another wallet's,
27 Oct, through Jupiter) and the coin's pool as the chain held it on 7 Nov (tests/fixtures/pumpswap_pool_state.json).
Nothing touches the network, and the fake chain below never lands anything."""

import asyncio
import base64
import json
import struct
from pathlib import Path

import pytest

from snipetech.models import Migration
from snipetech.solana import sell as S
from snipetech.solana.base58 import b58decode
from snipetech.solana.programs import (
    PUMP_AMM_PROGRAM,
    PUMP_FEES_PROGRAM,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    WSOL_MINT,
    amm_idl,
    decode_instruction,
    pump_pool,
)
from snipetech.solana.tx import extract_from_logs, extract_from_transaction
from snipetech.store import Store

FX = Path(__file__).parent / "fixtures"
TEMPLATE = json.loads((FX / "pumpswap_sell_template.json").read_text())
STATE = json.loads((FX / "pumpswap_pool_state.json").read_text())
JUPITER_SELL_V2 = json.loads(next((FX / "tx").glob("Q13fDdg52zbR*.json")).read_text())

OUR_WALLET = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
MINT = "2NgdUjFYoU5TWvEMu91rw3oYegvmERtiPcYRKVcBpump"  # the coin OKTech sold on PumpSwap for us (6 Nov 05:15)
POOL = "AHym5tFZM45gVWwe1DHthAPHD1V7SuCfppqKAiP4YJxA"
MINT2 = "CygfJt67TZBEqJnib8RfSoAgrf1v15ooJNFCFZt8pump"  # the decoder fixture's coin and pool (27 Oct)
POOL2 = "8YebdxK9SPVyBcaMTSG7b364DZGnrQHMVPjuYfwZcF9N"
HELD = 126_954_560_767  # what the template sold: the whole holding


def _template_sell():
    """The template's sell_v2 instruction (accounts, data) and each account's (signer, writable) in its message."""
    msg = TEMPLATE["transaction"]["message"]
    ix = next(i for i in msg["instructions"] if i["programId"] == PUMP_AMM_PROGRAM)
    flags = {k["pubkey"]: (k["signer"], k["writable"]) for k in msg["accountKeys"]}
    return ix, flags


def _jupiter_sell():
    """The decoder fixture's sell_v2: an inner instruction of a Jupiter route (a v0 message with lookup tables)."""
    msg, meta = JUPITER_SELL_V2["transaction"]["message"], JUPITER_SELL_V2["meta"]
    keys = list(msg["accountKeys"]) + meta["loadedAddresses"]["writable"] + meta["loadedAddresses"]["readonly"]
    for group in meta["innerInstructions"]:
        for ix in group["instructions"]:
            if keys[ix["programIdIndex"]] == PUMP_AMM_PROGRAM and b58decode(ix["data"])[:8] == S.SELL_V2_DISCRIMINATOR:
                return [keys[a] for a in ix["accounts"]], b58decode(ix["data"])
    raise AssertionError("no sell_v2 in the fixture")


def _sell_event(events):
    return next(e.fields for e in events if e.name == "SellEvent")


# -- the instruction: the landed template, re-derived ------------------------------------------------------------------


def test_the_sell_v2_instruction_is_the_landed_templates_byte_for_byte():
    """Re-derived for the template's own wallet, coin and pool, our instruction is the one that landed: the same 17
    accounts in the same order with the same signer and writable flags, the same program and the same data."""
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey

    ix, flags = _template_sell()
    ours = S.sell_v2_accounts(OUR_WALLET, MINT, POOL, TOKEN_2022_PROGRAM)
    assert [k for k, _s, _w in ours] == ix["accounts"]
    assert [(s, w) for _k, s, w in ours] == [flags[k] for k in ix["accounts"]]
    landed = Instruction(Pubkey.from_string(PUMP_AMM_PROGRAM), b58decode(ix["data"]),
                         [AccountMeta(Pubkey.from_string(k), *flags[k]) for k in ix["accounts"]])  # fmt: skip
    built = S.sell_v2_instruction(OUR_WALLET, MINT, POOL, TOKEN_2022_PROGRAM, HELD, 1)
    assert bytes(built) == bytes(landed)


def test_a_second_pool_and_coin_change_only_the_pool_and_coin_accounts():
    """For another coin and its pool, only the pool, the coin and the three token accounts tied to them change: the
    wallet's own accounts (its WSOL account, its volume accumulator) and PumpSwap's (global config, fee config, the
    buyback recipient's WSOL account, the event authority) stay as they landed. And the same derivation gives, account
    for account, the sell_v2 another wallet landed on that pool on 27 Oct."""
    ours = S.sell_v2_accounts(OUR_WALLET, MINT, POOL, TOKEN_2022_PROGRAM)
    second = S.sell_v2_accounts(OUR_WALLET, MINT2, POOL2, TOKEN_2022_PROGRAM)
    names = amm_idl().instructions[S.SELL_V2_DISCRIMINATOR][1]
    changed = [names[i] for i, (a, b) in enumerate(zip(ours, second)) if a != b]
    assert changed == [
        "pool",
        "base_mint",
        "user_base_token_account",
        "pool_base_token_account",
        "pool_quote_token_account",
    ]
    assert [(s, w) for _k, s, w in ours] == [(s, w) for _k, s, w in second]
    landed, data = _jupiter_sell()
    user = landed[names.index("user")]
    assert [k for k, _s, _w in S.sell_v2_accounts(user, MINT2, POOL2, TOKEN_2022_PROGRAM)] == landed
    assert data[:8] == S.SELL_V2_DISCRIMINATOR


def test_a_coin_of_the_older_token_program_takes_its_own_token_accounts():
    ours = S.sell_v2_accounts(OUR_WALLET, MINT, POOL, TOKEN_2022_PROGRAM)
    legacy = S.sell_v2_accounts(OUR_WALLET, MINT, POOL, TOKEN_PROGRAM)
    names = amm_idl().instructions[S.SELL_V2_DISCRIMINATOR][1]
    changed = [names[i] for i, (a, b) in enumerate(zip(ours, legacy)) if a != b]
    assert changed == ["user_base_token_account", "pool_base_token_account", "base_token_program"]


def test_the_constants_are_the_addresses_they_are_derived_from():
    """Each PumpSwap address the sale takes as a constant is the PDA or token account it stands for, so a change on
    pump.fun's side shows up here rather than as a refused sale."""
    assert S.PUMP_AMM_GLOBAL_CONFIG == S.pda([b"global_config"], PUMP_AMM_PROGRAM)
    assert S.PUMP_AMM_EVENT_AUTHORITY == S.pda([b"__event_authority"], PUMP_AMM_PROGRAM)
    assert S.PUMP_AMM_FEE_CONFIG == S.pda([b"fee_config", bytes(S._pk(PUMP_AMM_PROGRAM))], PUMP_FEES_PROGRAM)
    assert S.PUMP_AMM_BUYBACK_FEE_ACCOUNT == S.ata(S.PUMP_BUYBACK_FEE_RECIPIENT, WSOL_MINT, TOKEN_PROGRAM)
    pool = pump_pool(MINT)
    assert pool == POOL and S.pump_pool_authority(MINT) == "9e819dEJCG7ngLFP1HxBtiAX76rCtZW7h1bkkjpoqDVf"


def test_the_data_is_the_idls_sell_v2_with_the_amount_held_and_the_floor():
    floor = 27_451_883
    data = S.sell_v2_data(HELD, floor)
    assert data == bytes([93, 246, 130, 60, 231, 233, 64, 178]) + struct.pack("<QQ", HELD, floor)
    name, _accounts, args = decode_instruction(PUMP_AMM_PROGRAM, data)
    assert (name, args) == ("sell_v2", {"base_amount_in": HELD, "min_quote_amount_out": floor})
    ix, _ = _template_sell()
    assert S.sell_v2_data(HELD, 1) == b58decode(ix["data"])  # OKTech's own floor: 1 lamport


# -- the quote: PumpSwap's own arithmetic, on reserves a real sale recorded --------------------------------------------


def test_the_quote_is_what_two_real_sales_paid_to_the_lamport():
    """The SOL out of a sale, from the reserves and fee rates its own SellEvent recorded: our template (a boost pool with
    a virtual SOL reserve, 20/5/95 bps) and the decoder fixture's (2/93/30 bps). The swap rounds down and each fee
    rounds up, as the program does: the quote is exact, not within a few lamports."""
    for ev in (_sell_event(extract_from_logs(TEMPLATE["meta"]["logMessages"])),
               _sell_event(extract_from_transaction(JUPITER_SELL_V2).events)):  # fmt: skip
        fees = S.AmmFees(
            ev["lp_fee_basis_points"], ev["protocol_fee_basis_points"], ev["coin_creator_fee_basis_points"]
        )
        out = S.amm_sol_out(ev["base_amount_in"], ev["pool_base_token_reserves"], ev["pool_quote_token_reserves"], fees,
                            ev["virtual_quote_reserves"])  # fmt: skip
        assert out == ev["user_quote_amount_out"]
    assert S.amm_sol_out(0, 10**15, 10**11, S.AmmFees(20, 5, 95)) == 0


def test_the_fee_rates_are_the_tier_of_the_pools_market_cap():
    """PumpSwap charges a canonical pump pool by its market cap (pump-fees' fee tiers, read from the fee config account
    as it stood on 7 Nov): the template sold at a 437 SOL cap (20/5/95 bps) and the decoder fixture at 26 SOL
    (2/93/30), as each one recorded."""
    tiers = S.fee_tiers(base64.b64decode(STATE["result"]["value"][4]["data"][0]))
    assert len(tiers) >= 2 and tiers[0][0] == 0
    for ev in (_sell_event(extract_from_logs(TEMPLATE["meta"]["logMessages"])),
               _sell_event(extract_from_transaction(JUPITER_SELL_V2).events)):  # fmt: skip
        cap = (
            ev["base_supply"]
            * (ev["pool_quote_token_reserves"] + ev["virtual_quote_reserves"])
            // ev["pool_base_token_reserves"]
        )
        want = (ev["lp_fee_basis_points"], ev["protocol_fee_basis_points"], ev["coin_creator_fee_basis_points"])
        assert tuple(S.tier_fees(tiers, cap)) == want, cap


def _pool(**kw):
    tiers = S.fee_tiers(base64.b64decode(STATE["result"]["value"][4]["data"][0]))
    base = {"address": POOL, "base_mint": MINT, "quote_mint": WSOL_MINT, "creator": S.pump_pool_authority(MINT),
            "coin_creator": "7jP6tDvmbF7PcRYmKmJHxeQdGsiRKJAwSwE5sMNcJ8EM", "pool_base_token_account": "",
            "pool_quote_token_account": "", "virtual_quote_reserves": 17_507_362_053, "creator_fee_bps": 0,
            "is_mayhem_mode": False, "is_cashback_coin": False, "base_reserve": 200_401_019_125_048,
            "quote_reserve": 70_267_845_196, "supply": 998_332_549_368_214, "tiers": tiers}  # fmt: skip
    return S.AmmPool(**(base | kw))


def test_the_pool_quotes_the_template_sale_from_its_own_state():
    """The template's pool at the moment of its sale, with the fee tiers read from the chain: the quote is what the sale
    got (54,903,766 lamports for 126,954,560,767 base units)."""
    assert _pool().sol_out(HELD) == 54_903_766


def test_a_mayhem_pool_is_capped_on_the_full_supply_and_a_set_creator_rate_never_raises_the_quote():
    """A mayhem pool's market cap counts the whole 1e15 supply (pump-amm Pool::market_cap). A creator rate set on the
    pool replaces the tier's while a switch we do not read is on, so the higher of the two is charged: the quote can
    only come out at or under what the program pays, never over it."""
    assert _pool(is_mayhem_mode=True, supply=1).market_cap() == _pool(supply=10**15).market_cap()
    tier = _pool().fees()
    assert tuple(_pool(creator_fee_bps=225).fees()) == (tier.lp_bps, tier.protocol_bps, 225)
    assert _pool(creator_fee_bps=10).fees() == tier
    assert _pool(creator_fee_bps=225).sol_out(HELD) < _pool().sol_out(HELD)


# -- the sale: the fake chain answers from the recorded accounts, and lands nothing -------------------------------------


class Chain:
    """The chain as the seller reads it: the 7 Nov accounts of the template coin's pool, the coin held by the throwaway
    wallet, and every call recorded. sendTransaction keeps the transaction and answers a made-up signature."""

    commitment = "confirmed"

    def __init__(self, user: str, held: int | None = HELD, token_program: str = TOKEN_2022_PROGRAM, drop=(),
                 pool_patch=None):  # fmt: skip
        self.calls: list[str] = []
        self.sent: list[bytes] = []
        self.user, self.held, self.token_program = user, held, token_program
        self.accounts = dict(zip(STATE["addresses"], STATE["result"]["value"]))
        wsol = S.ata(user, WSOL_MINT, TOKEN_PROGRAM)
        self.accounts[wsol] = {"data": {"parsed": {"info": {"isNative": True, "mint": WSOL_MINT, "owner": user,
                                                            "state": "initialized",
                                                            "tokenAmount": {"amount": "0", "decimals": 9}},
                                                   "type": "account"}, "program": "spl-token", "space": 165},
                               "executable": False, "lamports": 1_488_440, "owner": TOKEN_PROGRAM, "space": 165}  # fmt: skip
        if pool_patch is not None:
            raw = bytearray(base64.b64decode(self.accounts[POOL]["data"][0]))
            pool_patch(raw)
            self.accounts[POOL] = self.accounts[POOL] | {"data": [base64.b64encode(bytes(raw)).decode(), "base64"]}
        for a in drop:
            self.accounts[a] = None

    async def get_account_data(self, address):
        self.calls.append("getAccountInfo")
        v = self.accounts.get(address)
        return None if v is None else base64.b64decode(v["data"][0])

    async def call(self, method, params=None):
        self.calls.append(method)
        if method == "getTokenAccountsByOwner":
            if self.held is None or params[1]["programId"] != self.token_program:
                return {"value": []}
            info = {"mint": MINT, "tokenAmount": {"amount": str(self.held), "decimals": 6}}
            return {"value": [{"pubkey": S.ata(self.user, MINT, self.token_program),
                               "account": {"data": {"parsed": {"info": info}}}}]}  # fmt: skip
        if method == "getMultipleAccounts":
            return {"context": {"slot": 466658553}, "value": [self.accounts.get(a) for a in params[0]]}
        if method == "getLatestBlockhash":
            return {"value": {"blockhash": "CdSs3nhUW2L4i9rDT8DnyGYA3b6fqHk9D4Ryv9bEQNPp"}}
        if method == "sendTransaction":
            self.sent.append(base64.b64decode(params[0]))
            return "SIG-PUMPSWAP"
        raise AssertionError(f"unexpected RPC call {method}")


@pytest.fixture
def key():
    from solders.keypair import Keypair

    kp = Keypair()
    return str(kp.pubkey()), str(kp)


def _store(tmp_path, pool=POOL):
    st = Store(tmp_path / "snipetech.db")
    if pool is not None:
        st.add_migrations([Migration(MINT, pool, 465_988_050, 1_793_942_146, "migrate-sig")])
    return st


def _holding(user, amount=HELD, token_program=TOKEN_2022_PROGRAM, account=None):
    return S.Holding(MINT, account or S.ata(user, MINT, token_program), amount, 6, token_program)


def _sent_sell(raw: bytes):
    """The instructions of a transaction the seller sent, as (program, data, accounts)."""
    from solders.transaction import Transaction

    tx = Transaction.from_bytes(raw)
    keys = [str(k) for k in tx.message.account_keys]
    return [
        (keys[ix.program_id_index], bytes(ix.data), [keys[i] for i in ix.accounts]) for ix in tx.message.instructions
    ]


def test_a_coin_off_its_curve_is_sold_on_its_pumpswap_pool(tmp_path, key):
    """One transaction: the same compute budget as a curve sale, then sell_v2 with the whole holding and the floor at
    half the quote; the row says it went to PumpSwap and which pool."""
    user, secret = key
    chain, st = Chain(user), _store(tmp_path)
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user), 0.5))
    finally:
        st.close()
    assert row["signature"] == "SIG-PUMPSWAP" and row["venue"] == "pumpswap" and row["pool"] == POOL
    assert chain.calls.count("sendTransaction") == 1 and len(chain.sent) == 1
    sent = _sent_sell(chain.sent[0])
    curve_sale = _sent_sell(
        bytes(S.Seller(Chain(user), secret).build(_holding(user), 5, "11111111111111111111111111111111"))
    )
    assert len(sent) == 3 and sent[:2] == curve_sale[:2]  # the curve sale's compute unit limit and price, nothing else
    sale = sent[2]
    assert sale[0] == PUMP_AMM_PROGRAM and sale[2] == [k for k, _s, _w in S.sell_v2_accounts(user, MINT, POOL)]
    name, _accounts, args = decode_instruction(PUMP_AMM_PROGRAM, sale[1])
    floor = int(row["floor_sol"] * 1e9 + 0.5)
    assert name == "sell_v2" and args == {"base_amount_in": HELD, "min_quote_amount_out": floor}
    assert floor == int(round(row["quote_sol"] * 1e9) * 0.5) and row["quote_sol"] > 0
    assert row["amount"] == HELD  # never more than held: the whole token account, read from the chain


def test_the_compute_budget_is_the_curve_sales(key):
    """The PumpSwap sale pays what a curve sale pays: the same compute unit limit and price, a plain legacy message
    signed by the wallet, no nonce and no tip."""
    from solders.hash import Hash

    user, secret = key
    seller = S.Seller(Chain(user), secret)
    bh = "CdSs3nhUW2L4i9rDT8DnyGYA3b6fqHk9D4Ryv9bEQNPp"
    curve = _sent_sell(bytes(seller.build(_holding(user), 5, bh)))
    amm = _sent_sell(bytes(seller.build_pumpswap(_holding(user), POOL, 5, bh)))
    assert curve[:2] == amm[:2] and [p for p, _d, _a in amm] == ["ComputeBudget111111111111111111111111111111"] * 2 + [
        PUMP_AMM_PROGRAM
    ]
    assert curve[2][0] == PUMP_PROGRAM
    tx = seller.build_pumpswap(_holding(user), POOL, 5, bh)
    assert tx.message.recent_blockhash == Hash.from_string(bh) and str(tx.message.account_keys[0]) == user


def test_a_dry_run_builds_the_sale_and_sends_nothing(tmp_path, key, monkeypatch):
    user, secret = key
    built: list[tuple] = []
    real = S.sell_v2_instruction

    def spy(*a):
        built.append(a)
        return real(*a)

    monkeypatch.setattr(S, "sell_v2_instruction", spy)
    chain, st = Chain(user), _store(tmp_path)
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user), 0.5, dry_run=True))
    finally:
        st.close()
    assert row["dry_run"] is True and row["venue"] == "pumpswap" and row["pool"] == POOL and "signature" not in row
    assert 0 < row["floor_sol"] < row["quote_sol"]
    assert built and built[0][:4] == (user, MINT, POOL, TOKEN_2022_PROGRAM)
    assert "sendTransaction" not in chain.calls and "getLatestBlockhash" not in chain.calls and chain.sent == []


def test_the_quote_is_the_pools_state_on_the_chain(tmp_path, key):
    """The quote reads the pool's two token accounts, its virtual SOL reserve, the coin's supply and the fee tier on
    the chain: here the 7 Nov state of the template coin's pool, priced by hand from the same numbers."""
    user, secret = key
    v = dict(zip(STATE["addresses"], STATE["result"]["value"]))
    base = int(v[STATE["addresses"][1]]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
    quote = int(v[STATE["addresses"][2]]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
    supply = int(v[MINT]["data"]["parsed"]["info"]["supply"])
    vq = 17_582_508_743  # the pool account's virtual_quote_reserves at that slot
    tiers = S.fee_tiers(base64.b64decode(v[S.PUMP_AMM_FEE_CONFIG]["data"][0]))
    want = S.amm_sol_out(HELD, base, quote, S.tier_fees(tiers, supply * (quote + vq) // base), vq)
    chain, st = Chain(user), _store(tmp_path)
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user), 0.25, dry_run=True))
    finally:
        st.close()
    assert row["quote_sol"] == want / 1e9 and row["floor_sol"] == int(want * 0.75) / 1e9
    assert chain.calls.count("getMultipleAccounts") == 1  # one read for the pool, its reserves, the supply and fees


def _skipped(tmp_path, key, *, store_pool=POOL, **chain_kw):
    user, secret = key
    chain, st = Chain(user, **chain_kw), _store(tmp_path, store_pool)
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user), 0.5))
    finally:
        st.close()
    assert "signature" not in row and chain.sent == [] and "sendTransaction" not in chain.calls, row
    assert str(row.get("skipped", "")).startswith("PumpSwap: "), row
    return row["skipped"]


def test_a_coin_whose_migration_the_tape_missed_is_sold_on_its_own_pool(tmp_path, key):
    """The tape misses a migration while it is down (7 Nov 14:03-15:48): the coin's own PumpSwap address, the pool
    pump.fun's migration creates, is then the only candidate, and the chain confirms it is this coin's pool (pump.fun's
    pool authority created it, for this coin and SOL) before anything is sent."""
    user, secret = key
    chain, st = Chain(user), _store(tmp_path, None)
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user), 0.5))
        assert row["signature"] == "SIG-PUMPSWAP" and row["pool"] == POOL
        chain = Chain(user)
        row = asyncio.run(S.Seller(chain, secret).sell(_holding(user), 0.5))  # no tape at all (the CLI without one)
        assert row["signature"] == "SIG-PUMPSWAP" and row["pool"] == POOL
    finally:
        st.close()


def test_a_coin_with_no_pool_yet_is_refused(tmp_path, key):
    """The curve completed but its pool does not exist yet (the move lands a slot or more later), with or without a
    migration on the tape: nothing is sold, and nothing is guessed."""
    why = _skipped(tmp_path / "a", key, drop=[POOL])
    assert "does not exist yet" in why
    why = _skipped(tmp_path / "b", key, store_pool=None, drop=[POOL])
    assert "does not exist yet" in why


def test_a_coin_with_two_candidate_pools_is_refused(tmp_path, key):
    """The tape's migration names a pool other than the coin's own PumpSwap address: two candidates, so nothing is
    sold rather than a guess between them."""
    other = "3UuCJ6PuXgqwq8P8M3Q5Q7qasXkFtobYMAEynTotd8uJ"
    why = _skipped(tmp_path, key, store_pool=other)
    assert "two" in why and other[:6] in why and POOL[:6] in why


def test_a_pool_that_is_not_the_coins_is_refused(tmp_path, key):
    def other_mint(raw: bytearray):  # base_mint sits after the discriminator, the bump, the index and the creator
        raw[8 + 1 + 2 + 32 : 8 + 1 + 2 + 64] = bytes(32)

    assert "not this coin" in _skipped(tmp_path, key, pool_patch=other_mint)


def test_a_cashback_coin_is_refused(tmp_path, key):
    """PumpSwap sells a cashback coin only through its older `sell`, and we hold no landed copy of one from our wallet."""

    def cashback(raw: bytearray):  # is_cashback_coin follows lp_supply, coin_creator and is_mayhem_mode
        raw[8 + 1 + 2 + 32 * 6 + 8 + 32 + 1] = 1

    assert "cashback" in _skipped(tmp_path, key, pool_patch=cashback)


def test_a_wallet_without_its_wsol_account_is_refused(tmp_path, key):
    """The pool pays out in WSOL into the wallet's WSOL account, as OKTech's own sale did; the sale adds no instruction
    the template did not carry, so a wallet without one is refused, not sent to fail."""
    user, _ = key
    assert "WSOL" in _skipped(tmp_path, key, drop=[S.ata(user, WSOL_MINT, TOKEN_PROGRAM)])


def test_a_coin_held_outside_the_wallets_own_token_account_is_refused(tmp_path, key):
    user, secret = key
    chain, st = Chain(user), _store(tmp_path)
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user, account=POOL2), 0.5))
    finally:
        st.close()
    assert "token account" in row["skipped"] and chain.sent == []


def test_a_pool_that_would_pay_nothing_is_refused(tmp_path, key):
    """An emptied pool (no SOL, and a virtual reserve at or under zero) quotes nothing: a floor of half of nothing would
    hand the coin over for a lamport, so it is refused instead."""

    def no_virtual_sol(raw: bytearray):  # virtual_quote_reserves (i128) follows is_cashback_coin
        off = 8 + 1 + 2 + 32 * 6 + 8 + 32 + 2
        raw[off : off + 16] = (-(10**9)).to_bytes(16, "little", signed=True)

    user, secret = key
    chain, st = Chain(user, pool_patch=no_virtual_sol), _store(tmp_path)
    pool_quote = STATE["addresses"][2]
    chain.accounts[pool_quote] = {"data": {"parsed": {"info": {"mint": WSOL_MINT, "owner": POOL,
                                                               "tokenAmount": {"amount": "0", "decimals": 9}},
                                                      "type": "account"}}, "owner": TOKEN_PROGRAM}  # fmt: skip
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user), 0.5))
    finally:
        st.close()
    assert row["skipped"].startswith("PumpSwap: ") and "pay nothing" in row["skipped"] and chain.sent == []


def test_a_holding_worth_less_than_the_fee_to_sell_it_is_left_alone(tmp_path, key):
    """Review finding (7 Nov evening): a leftover worth less than the sale's own fee (about 0.001 SOL: 140,000 CU at the
    sell's priority price plus the base fee) was sold at a loss by /sell all and the sweep: 40,000 tokens of the
    fixture coin quote 0.00086 SOL. It is left alone, with the reason, and nothing is sent."""
    user, secret = key
    chain, st = Chain(user), _store(tmp_path)
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user, amount=40_000_000_000), 0.5))
    finally:
        st.close()
    assert row["skipped"].startswith("PumpSwap: worth less than the fee to sell it") and chain.sent == []


def test_a_curve_holding_worth_less_than_the_fee_is_left_alone_too(tmp_path, key):
    user, secret = key
    live = (S.BONDING_CURVE_DISCRIMINATOR + struct.pack("<QQQQQ", 10**15, 40 * 10**9, 7 * 10**14, 10 * 10**9, 10**15)
            + bytes([0]) + bytes(32) + bytes(64))  # fmt: skip
    chain, st = Chain(user), _store(tmp_path)
    chain.accounts[S.bonding_curve(MINT)] = {"data": [base64.b64encode(live).decode(), "base64"]}
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user, amount=10_000_000_000), 0.5))
    finally:
        st.close()
    assert row["skipped"].startswith("worth less than the fee to sell it") and chain.sent == []


def test_nothing_held_is_never_sold(tmp_path, key):
    """The sweep sells only what the wallet's own token accounts hold: a coin asked for but not held is not tried."""
    user, secret = key
    st = _store(tmp_path)
    try:
        chain = Chain(user, held=None)
        out = asyncio.run(S.sweep(chain, [MINT], 0.5, secret_b58=secret, store=st))
        assert out["results"] == [] and chain.sent == [] and "getMultipleAccounts" not in chain.calls
        chain = Chain(user)
        out = asyncio.run(S.sweep(chain, [MINT2], 0.5, secret_b58=secret, store=st))  # held, but not the coin asked for
        assert out["results"] == [] and chain.sent == []
        out = asyncio.run(S.sweep(chain, [MINT], 0.5, secret_b58=secret, store=st))
        assert [r["signature"] for r in out["results"]] == ["SIG-PUMPSWAP"] and len(chain.sent) == 1
    finally:
        st.close()


def test_a_coin_still_on_its_curve_is_sold_there_as_before(tmp_path, key):
    """The curve path is untouched: a coin whose curve has not completed is sold with sell_v3 and the row carries no
    venue of its own (5 Nov's behaviour)."""
    user, secret = key
    live = (S.BONDING_CURVE_DISCRIMINATOR + struct.pack("<QQQQQ", 10**15, 40 * 10**9, 7 * 10**14, 10 * 10**9, 10**15)
            + bytes([0]) + bytes(32) + bytes(64))  # fmt: skip
    chain, st = Chain(user), _store(tmp_path)
    chain.accounts[S.bonding_curve(MINT)] = {"data": [base64.b64encode(live).decode(), "base64"]}
    try:
        row = asyncio.run(S.Seller(chain, secret, store=st).sell(_holding(user), 0.5))
    finally:
        st.close()
    assert row["signature"] == "SIG-PUMPSWAP" and "venue" not in row and "getMultipleAccounts" not in chain.calls
    assert _sent_sell(chain.sent[0])[2][0] == PUMP_PROGRAM


# -- the CLI's sweep (`snipetech sweep`): the same sale, from the command line ------------------------------------------


def test_the_cli_sweep_hands_the_tape_over_and_says_which_coins_went_to_pumpswap(tmp_path, monkeypatch, capsys):
    from snipetech import cli
    from snipetech.report import render_sweep

    seen: list[dict] = []

    async def fake_sweep(rpc, mints, slippage, dry_run, store=None):
        seen.append({"mints": mints, "slippage": slippage, "dry_run": dry_run, "store": store})
        return {"wallet": OUR_WALLET, "holdings": 0, "results": []}

    monkeypatch.setattr(S, "sweep", fake_sweep)
    assert cli.main(["--db", str(tmp_path / "t.db"), "sweep", "--dry-run", "--mint", MINT]) == 0
    assert len(seen) == 1 and seen[0]["mints"] == [MINT] and seen[0]["dry_run"] is True
    assert isinstance(seen[0]["store"], Store)  # the tape, where a migrated coin's pool is on record
    capsys.readouterr()
    out = render_sweep({"wallet": OUR_WALLET, "holdings": 1,
                        "results": [{"mint": MINT, "amount": HELD, "quote_sol": 0.0549, "floor_sol": 0.0274,
                                     "venue": "pumpswap", "pool": POOL, "dry_run": True}]})  # fmt: skip
    assert f"on PumpSwap (pool {POOL})" in out and "dry run" in out
