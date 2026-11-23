"""Our own pump.fun buy: `buy_exact_quote_in_v3`, built from the curve buy our sniper wallet landed on 23 Nov 2026
(tests/fixtures/pump_buy_v3_template.json, taken out of tests/fixtures/buy_limits_23nov.json by its builder) and
re-derived per coin, never written from memory.

Everything here runs on recorded mainnet data and a fake chain: nothing reaches a network, nothing is sent, and the
wallet is a throwaway key made by each test."""

import asyncio
import base64
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest

from snipetech.solana import buy as B
from snipetech.solana.base58 import b58decode
from snipetech.solana.programs import (
    DEFAULT_PUBKEY,
    INITIAL_REAL_TOKEN_RESERVES,
    INITIAL_VIRTUAL_SOL_RESERVES,
    INITIAL_VIRTUAL_TOKEN_RESERVES,
    PUMP_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    TOKEN_TOTAL_SUPPLY,
    WSOL_MINT,
    decode_instruction,
)
from snipetech.solana.sell import (
    ATA_PROGRAM,
    BONDING_CURVE_DISCRIMINATOR,
    COMPUTE_BUDGET,
    SYSTEM_PROGRAM,
    ata,
    bonding_curve,
    sell_v3_accounts,
)
from snipetech.solana.tips import JITO_TIP_ACCOUNTS
from snipetech.solana.tx import _account_keys, extract_from_transaction, ix_data

FX = Path(__file__).parent / "fixtures"
TEMPLATE = json.loads((FX / "pump_buy_v3_template.json").read_text())
OUR_WALLET = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
MINT = TEMPLATE["mint"]  # G21H7ob4…pump, the coin of the landed buy
EVENT = TEMPLATE["trade_event"]
SPENT = TEMPLATE["args"]["spendable_quote_in"]  # 20,000,000 lamports: 0.02 SOL
BLOCKHASH = "CdSs3nhUW2L4i9rDT8DnyGYA3b6fqHk9D4Ryv9bEQNPp"
SOL = 1_000_000_000


def _builder():
    spec = importlib.util.spec_from_file_location("build_pump_buy_v3_template", FX / "build_pump_buy_v3_template.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _before(ev: dict) -> dict:
    """The curve just before a trade, from the TradeEvent the program logged after it: the reserves less the trade."""
    return {"vs": ev["virtual_sol_reserves"] - ev["sol_amount"], "vt": ev["virtual_token_reserves"] + ev["token_amount"],
            "rs": ev["real_sol_reserves"] - ev["sol_amount"], "rt": ev["real_token_reserves"] + ev["token_amount"]}  # fmt: skip


PRE = _before(EVENT)


def curve_bytes(vt=PRE["vt"], vs=PRE["vs"], rt=PRE["rt"], rs=PRE["rs"], *, complete=False, creator=EVENT["creator"],
                mayhem=False, cashback=False, quote_mint=DEFAULT_PUBKEY, holder=False) -> bytes:  # fmt: skip
    """A bonding curve account as the vendored IDL lays BondingCurve out (166 bytes, the size of the real one in
    tests/fixtures/pumpswap_pool_state.json)."""
    return (BONDING_CURVE_DISCRIMINATOR + struct.pack("<QQQQQ", vt, vs, rt, rs, TOKEN_TOTAL_SUPPLY) + bytes([complete])
            + b58decode(creator) + bytes([mayhem, cashback]) + b58decode(quote_mint) + struct.pack("<Q", 0)
            + bytes([0, holder]) + struct.pack("<QQ", 0, 0) + bytes([0]) + struct.pack("<QQQ", 30 * SOL, 0, 0))  # fmt: skip


def coin(**kw) -> "B.Coin":
    token_program = kw.pop("token_program", TOKEN_2022_PROGRAM)
    lamports = kw.pop("wallet_lamports", 1 * SOL)
    account = kw.pop("account_exists", False)
    return B.Coin(MINT, token_program, B.CoinCurve.decode(curve_bytes(**kw)), account, lamports)


@pytest.fixture
def key():
    from solders.keypair import Keypair

    kp = Keypair()
    return str(kp.pubkey()), str(kp)


# -- the template: the landed buy of our wallet --------------------------------------------------------------------------


def test_the_template_is_the_landed_buy_of_our_wallet_taken_out_by_its_builder():
    """The saved template is exactly what its builder takes out of the landed transaction: the buy's accounts with the
    message's flags, its data, the compute budget, the other instructions and the program's TradeEvent."""
    b = _builder()
    tx = b.source_transaction()
    assert tx["meta"]["err"] is None and tx["transaction"]["signatures"][0] == TEMPLATE["signature"]
    assert b.build(tx) == TEMPLATE
    assert TEMPLATE["wallet"] == OUR_WALLET and TEMPLATE["instruction"] == B.BUY_V3 == "buy_exact_quote_in_v3"
    assert TEMPLATE["utc"] == "2026-11-23 03:22:01 UTC" and TEMPLATE["token_program"] == TOKEN_2022_PROGRAM


def test_the_accounts_are_the_landed_buys_re_derived_for_its_own_wallet_and_coin():
    """Re-derived for the template's wallet and coin: the same 17 accounts in the same order, each with the signer and
    writable flags the landed transaction gave it."""
    ours = B.buy_v3_accounts(OUR_WALLET, MINT, TOKEN_2022_PROGRAM)
    assert ours == [(k, s, w) for _n, k, s, w in TEMPLATE["accounts"]]
    assert B.BUY_V3_ACCOUNT_NAMES == tuple(n for n, *_ in TEMPLATE["accounts"])  # the IDL's names, in its order
    # the landed sell of 5 Nov takes the very same accounts (sell_v3 names them alike in the IDL)
    assert ours == sell_v3_accounts(OUR_WALLET, MINT, TOKEN_2022_PROGRAM)


def _landed_txs():
    """Every landed transaction the fixtures hold as getTransaction returned it (json or jsonParsed encoding)."""
    for path in sorted(FX.rglob("*.json")):
        try:
            obj = json.loads(path.read_text())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        txs = obj.get("txs", {}).values() if isinstance(obj, dict) and "txs" in obj else [obj.get("result", obj)]
        for tx in txs:
            if isinstance(tx, dict) and isinstance(tx.get("meta"), dict) and tx["meta"].get("err") is None:
                yield path, tx


def _instructions(tx):
    """Every instruction of a transaction, top-level and called, as (program, accounts, data), in either encoding."""
    keys = _account_keys(tx)
    inner = {g["index"]: g["instructions"] for g in tx["meta"].get("innerInstructions") or []}
    for i, top in enumerate(tx["transaction"]["message"]["instructions"]):
        for ix in (top, *inner.get(i, ())):
            if "programIdIndex" in ix:
                program, accounts = keys[ix["programIdIndex"]], [keys[a] for a in ix["accounts"]]
            else:  # jsonParsed: the program and the accounts by address
                program, accounts = ix.get("programId"), list(ix.get("accounts") or [])
            if isinstance(ix.get("data"), str):
                yield program, accounts, ix_data(ix)


def _landed_v3_buys():
    """Every landed buy_exact_quote_in_v3 in the fixtures but the template's: (the transaction, whether the buy is one
    of its own instructions or called by a router, its user, its coin, its 17 accounts as landed)."""
    out = []
    for _path, tx in _landed_txs():
        if tx["transaction"]["signatures"][0] == TEMPLATE["signature"]:
            continue
        top = {ix_data(ix) for ix in tx["transaction"]["message"]["instructions"] if isinstance(ix.get("data"), str)}
        for program, accounts, data in _instructions(tx):
            if program == PUMP_PROGRAM and data[:8] == B.BUY_V3_DISCRIMINATOR:
                out.append((tx, data in top, accounts[8], accounts[1], accounts))
    return out


def _seed_accounts(tx) -> set[str]:
    """The accounts a transaction makes with the System program's CreateAccountWithSeed (OKTech's seed account: the
    6 Nov audit found every copy buying into one and switched it off, docs/oktech-audit-2026-11-06.md, item 1)."""
    return {accounts[1] for program, accounts, data in _instructions(tx)
            if program == SYSTEM_PROGRAM and data[:4] == (3).to_bytes(4, "little")}  # fmt: skip


def test_a_second_coin_changes_only_the_accounts_tied_to_the_coin_and_other_landed_buys_derive_alike():
    """For another coin only the coin, its curve, the curve's two token accounts and the wallet's account of the coin
    change; the wallet's own accounts and pump.fun's stay.

    And the same derivation gives every other landed buy_exact_quote_in_v3 in the fixtures (two more of our wallet,
    of 6 Nov, holder-reward coins among them; three of three other wallets, two of them called by a router), account
    for account and with the flags of our own, but two accounts that each transaction chooses:
    * the buyback fee recipient: pump.fun takes any of several, and each transaction names one (five different ones
      in these six buys). Ours names the one our 23 Nov buy landed with, as the sale names the one our 5 Nov sell did;
    * in our two buys of 6 Nov, the wallet's account of the coin: OKTech's seed account, made in the same transaction
      (switched off by the 6 Nov audit); our buy opens the wallet's own token account, as the 23 Nov buy did."""
    landed = _landed_v3_buys()
    assert sorted(user == OUR_WALLET for _tx, _top, user, _m, _a in landed) == [False, False, False, True, True]
    assert sum(not top for _tx, top, _u, _m, _a in landed) == 2
    second = landed[0][3]
    a, b = B.buy_v3_accounts(OUR_WALLET, MINT), B.buy_v3_accounts(OUR_WALLET, second)
    changed = [B.BUY_V3_ACCOUNT_NAMES[i] for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert changed == ["base_mint", "bonding_curve", "associated_base_bonding_curve", "associated_quote_bonding_curve",
                       "associated_base_user"]  # fmt: skip
    assert [(s, w) for _k, s, w in a] == [(s, w) for _k, s, w in b]
    names, flags_of = B.BUY_V3_ACCOUNT_NAMES, _builder().flags
    recipient = names.index("buyback_fee_recipient")
    assert a[recipient][0] == TEMPLATE["accounts"][recipient][1] == sell_v3_accounts(OUR_WALLET, MINT)[recipient][0]
    recipients, seeded = {a[recipient][0]}, 0
    for tx, top, user, mint, accounts in landed:
        ours = B.buy_v3_accounts(user, mint, TOKEN_2022_PROGRAM)
        differ = {names[i] for i, ((k, _s, _w), x) in enumerate(zip(ours, accounts)) if k != x}
        recipients.add(accounts[recipient])
        if accounts[names.index("associated_base_user")] in _seed_accounts(tx):
            seeded += 1
            differ.discard("associated_base_user")
        assert differ <= {"buyback_fee_recipient"}, (mint, differ)
        if user == OUR_WALLET and top:  # our own buy, one of its transaction's instructions: its flags are the buy's
            flags = dict(zip(_account_keys(tx), flags_of(tx)))
            assert [(s, w) for _k, s, w in ours] == [flags[k] for k in accounts], mint
    assert seeded == 2 and len(recipients) == 5


def test_the_data_is_the_idls_layout_and_the_landed_buys_bytes():
    """The IDL's discriminator (Anchor's: the first 8 bytes of sha256("global:buy_exact_quote_in_v3")), the two
    amounts as little-endian u64, then partial_fill as the IDL defines OptionBool (a struct of one bool: one byte).
    OKTech's landed buy leaves partial_fill out (24 bytes), and so does ours unless it is asked for."""
    landed = bytes.fromhex(TEMPLATE["data"])
    assert B.BUY_V3_DISCRIMINATOR == hashlib.sha256(b"global:buy_exact_quote_in_v3").digest()[:8] == landed[:8]
    assert B.buy_v3_data(20_000_000, 309_565_217_392) == landed and len(landed) == 24
    for flag, byte in ((True, 1), (False, 0)):
        data = B.buy_v3_data(20_000_000, 309_565_217_392, partial_fill=flag)
        assert data == landed + bytes([byte])
        name, _accounts, args = decode_instruction(PUMP_PROGRAM, data)
        assert (name, args["partial_fill"]) == ("buy_exact_quote_in_v3", (flag,))
    assert decode_instruction(PUMP_PROGRAM, landed)[2] == TEMPLATE["args"]


# -- the quote: pump.fun's own arithmetic, on the curve its TradeEvent recorded --------------------------------------------


def test_the_quote_is_the_landed_fill_to_the_coin():
    """The curve before the template's buy is its TradeEvent's reserves less the trade, and every curve's invariants
    hold on it (virtual less real: the 30 SOL and 279.9 million coins a curve starts with). On it the quote of
    0.02 SOL is exactly what landed: 19,753,085 lamports into the curve, fees of 95 and 30 basis points rounded up
    (187,655 + 59,260, together 0.02 SOL to the lamport) and 371,200,971,351 coins.

    The rounding: the SOL in is the most whose fees fit in what is spent (10,000/10,125 of it, rounded down, then one
    lamport less: 19,753,086 and its fees would spend 20,000,001), and the coins are priced on one lamport less than
    goes in. Priced on the whole 19,753,085 lamports the quote would be 18,783 coins too many."""
    vs, vt = PRE["vs"], PRE["vt"]
    assert vs - PRE["rs"] == INITIAL_VIRTUAL_SOL_RESERVES
    assert vt - PRE["rt"] == INITIAL_VIRTUAL_TOKEN_RESERVES - INITIAL_REAL_TOKEN_RESERVES
    q = B.quote_buy(SPENT, vs, vt)
    assert (q.net_sol, q.protocol_fee, q.creator_fee, q.tokens) == (19_753_085, 187_655, 59_260, 371_200_971_351)
    assert (q.net_sol, q.protocol_fee, q.creator_fee, q.tokens) == (EVENT["sol_amount"], EVENT["fee"],
                                                                    EVENT["creator_fee"], EVENT["token_amount"])  # fmt: skip
    assert q.net_sol + q.protocol_fee + q.creator_fee == SPENT == q.spendable
    assert q.net_sol * vt // (vs + q.net_sol) - q.tokens == 18_783
    assert B.reserves_before(EVENT) == (vs, vt)
    assert (B.CURVE_PROTOCOL_FEE_BPS, B.CURVE_CREATOR_FEE_BPS) == (
        EVENT["fee_basis_points"],
        EVENT["creator_fee_basis_points"],
    )
    assert B.quote_buy(0, vs, vt).tokens == 0 and B.quote_buy(1, vs, vt).tokens == 0


def _exact_in_buys():
    """Every other landed exact-SOL-in curve buy in the fixtures with its TradeEvent: (file, SOL spent, event). The
    buy that ends a curve (the graduating bundle of 7 Nov: 65 SOL offered, the curve's last coins taken) is left out:
    the buyer refuses such a buy."""
    names = {"buy_exact_sol_in": "spendable_sol_in", "buy_exact_quote_in_v2": "spendable_quote_in",
             "buy_exact_quote_in_v3": "spendable_quote_in"}  # fmt: skip
    out = []
    for path, tx in _landed_txs():
        if tx["transaction"]["signatures"][0] == TEMPLATE["signature"]:
            continue
        buys = [(decode_instruction(PUMP_PROGRAM, data), accounts) for program, accounts, data in _instructions(tx)
                if program == PUMP_PROGRAM]  # fmt: skip
        buys = [(d, accounts) for d, accounts in buys if d is not None and d[0] in names]
        if not buys:
            continue
        events = [e.fields for e in extract_from_transaction(tx).events if e.name == "TradeEvent"]
        for (name, account_names, args), accounts in buys:
            named = dict(zip(account_names, accounts))
            mint = named.get("base_mint") or named.get("mint")
            ev = [e for e in events if e["mint"] == mint and e["is_buy"]]
            if len(ev) == 1 and ev[0]["real_token_reserves"] > 0:
                out.append((path.name, args[names[name]], ev[0]))
    return out


def test_the_quote_is_every_other_landed_exact_in_buy_in_the_fixtures_to_the_coin():
    """Seven more landed buys of other wallets (buy_exact_sol_in, buy_exact_quote_in_v2 and v3; mayhem coins and
    holder-reward coins among them; 1.8 to 359 SOL of market cap), each from the curve its own TradeEvent recorded:
    the SOL in, both fees and the coins out are what the program logged, and SOL in plus fees is what was spent."""
    buys = _exact_in_buys()
    assert len(buys) == 7
    kinds = {(ev["mayhem_mode"], ev["holder_rewards_bps"] > 0) for _f, _s, ev in buys}
    assert kinds == {(False, False), (True, False), (False, True)}
    for name, spent, ev in buys:
        vs, vt = B.reserves_before(ev)
        q = B.quote_buy(spent, vs, vt, ev["fee_basis_points"], ev["creator_fee_basis_points"])
        assert (q.net_sol, q.protocol_fee, q.creator_fee, q.tokens) == (ev["sol_amount"], ev["fee"], ev["creator_fee"],
                                                                        ev["token_amount"]), name  # fmt: skip
        assert q.net_sol + q.protocol_fee + q.creator_fee == spent, name
        assert (ev["fee_basis_points"], ev["creator_fee_basis_points"]) == (95, 30), name


def test_the_least_coins_out_never_lets_the_price_rise_more_than_the_cap_above_the_quotes():
    """A cap c: the buy fails unless it gets at least quote / (1 + c) coins, rounded up, so its price (SOL in over
    coins out) is never more than c above the quote's. One coin fewer than the least is a price above the cap."""
    quote = EVENT["token_amount"]
    least = B.min_tokens_out(quote, 0.10)
    assert least == 337_455_428_501 == -(-quote * 10 // 11)
    assert least < quote  # a cap lowers the least coins taken, it never raises them above the quote
    spent = SPENT
    assert spent / least <= 1.10 * spent / quote  # the price at the least coins: at most 10 percent above the quote's
    assert spent / (least - 1) > 1.10 * spent / quote  # one coin fewer: above it, so the program refuses that fill
    assert B.min_tokens_out(quote, 0) == quote  # no slippage: the quote or nothing
    assert B.min_tokens_out(quote, 0.5) < B.min_tokens_out(quote, 0.25) < least  # a wider cap takes fewer coins
    assert B.min_tokens_out(1_000_000, 1.0) == 500_000  # the widest cap: twice the quote's price at the most
    for bad in (-0.01, 1.01, float("nan")):
        with pytest.raises(B.BuyRefused, match="slippage"):
            B.min_tokens_out(quote, bad)


# -- the curve account ----------------------------------------------------------------------------------------------------


def test_the_curve_is_read_with_the_idls_layout_and_a_real_completed_curve_reads_complete():
    """A curve account decodes into the sale's Curve and the flags a buy checks (mayhem mode, cashback, holder rewards,
    the quote mint), from the vendored IDL. The real account of 2Ngd…pump as the chain held it on 7 Nov (166 bytes)
    reads complete, a holder-reward coin, quoted in SOL."""
    c = B.CoinCurve.decode(curve_bytes())
    assert (c.virtual_quote_reserves, c.virtual_token_reserves, c.real_token_reserves) == (
        PRE["vs"],
        PRE["vt"],
        PRE["rt"],
    )
    assert (c.complete, c.is_mayhem_mode, c.is_cashback_coin, c.is_holder_reward) == (False, False, False, False)
    assert c.quote_mint == DEFAULT_PUBKEY and c.creator == EVENT["creator"]
    flags = B.CoinCurve.decode(curve_bytes(mayhem=True, cashback=True, holder=True, quote_mint=WSOL_MINT))
    assert (flags.is_mayhem_mode, flags.is_cashback_coin, flags.is_holder_reward, flags.quote_mint) == (
        True,
        True,
        True,
        WSOL_MINT,
    )
    state = json.loads((FX / "pumpswap_pool_state.json").read_text())
    real = dict(zip(state["addresses"], state["result"]["value"]))[
        bonding_curve("2NgdUjFYoU5TWvEMu91rw3oYegvmERtiPcYRKVcBpump")
    ]
    old = B.CoinCurve.decode(base64.b64decode(real["data"][0]))
    assert old.complete and old.is_holder_reward and not old.is_mayhem_mode and old.quote_mint == DEFAULT_PUBKEY
    short = B.CoinCurve.decode(curve_bytes()[:81])  # an account from before the flags: none of them is set
    assert (short.is_mayhem_mode, short.is_cashback_coin, short.is_holder_reward, short.quote_mint) == (
        False,
        False,
        False,
        DEFAULT_PUBKEY,
    )
    with pytest.raises(ValueError):
        B.CoinCurve.decode(bytes(166))


# -- the transaction --------------------------------------------------------------------------------------------------------


def _ixs(tx):
    keys = [str(k) for k in tx.message.account_keys]
    return [
        (keys[ix.program_id_index], bytes(ix.data), [keys[i] for i in ix.accounts]) for ix in tx.message.instructions
    ]


def _msg_flags(msg) -> dict[str, tuple[bool, bool]]:
    """Each account of a legacy message with its (signer, writable), read off the header as the builder reads the
    landed one."""
    h, keys = msg.header, [str(k) for k in msg.account_keys]
    n, signers = len(keys), h.num_required_signatures
    return {k: (i < signers, i < signers - h.num_readonly_signed_accounts if i < signers
                else i < n - h.num_readonly_unsigned_accounts) for i, k in enumerate(keys)}  # fmt: skip


def test_the_buy_is_the_templates_shape_with_the_tip_to_jito_as_its_last_instruction(key):
    """The compute budget as the landed buy set it (price, then limit), the wallet's account of the coin opened as the
    landed buy opened it, the buy itself, and the tip: a System transfer to one of Jito's tip accounts, last. Signed by
    the wallet alone, a legacy message under the size limit."""
    from solders.transaction import Transaction

    user, secret = key
    buyer = B.Buyer(object(), secret)
    tx = buyer.build(coin(), 0.02, 0.10, BLOCKHASH, tip_lamports=1_000_000)
    assert isinstance(tx, Transaction) and len(bytes(tx)) <= 1232
    assert str(tx.message.account_keys[0]) == user and tx.verify_with_results() == [True]
    ixs = _ixs(tx)
    assert [p for p, _d, _a in ixs] == [COMPUTE_BUDGET, COMPUTE_BUDGET, ATA_PROGRAM, PUMP_PROGRAM, SYSTEM_PROGRAM]
    assert [d.hex() for _p, d, _a in ixs[:2]] == [b["data"] for b in TEMPLATE["compute_budget"]]  # the landed budget
    opened = next(o for o in TEMPLATE["other_instructions"] if o["program"] == ATA_PROGRAM)
    assert ixs[2] == (ATA_PROGRAM, bytes.fromhex(opened["data"]), [user, ata(user, MINT, TOKEN_2022_PROGRAM), user, MINT,
                                                                   SYSTEM_PROGRAM, TOKEN_2022_PROGRAM])  # fmt: skip
    _p, data, accounts = ixs[3]
    assert accounts == [k for k, _s, _w in B.buy_v3_accounts(user, MINT, TOKEN_2022_PROGRAM)]
    name, _names, args = decode_instruction(PUMP_PROGRAM, data)
    assert (name, args) == ("buy_exact_quote_in_v3", {"spendable_quote_in": SPENT, "min_tokens_out": 337_455_428_501})
    _p, tip, to = ixs[4]
    assert to[0] == user and to[1] in JITO_TIP_ACCOUNTS and tip == struct.pack("<IQ", 2, 1_000_000)
    # the message gives each account of the buy the flags the landed transaction gave it
    flags = _msg_flags(tx.message)
    for k, s, w in B.buy_v3_accounts(user, MINT, TOKEN_2022_PROGRAM):
        assert flags[k] == (s, w), k
    plain = buyer.build(coin(), 0.02, 0.10, BLOCKHASH)  # no tip: no transfer at all
    assert [p for p, _d, _a in _ixs(plain)] == [COMPUTE_BUDGET, COMPUTE_BUDGET, ATA_PROGRAM, PUMP_PROGRAM]
    chosen = buyer.build(coin(), 0.02, 0.10, BLOCKHASH, tip_lamports=5_000, tip_account=sorted(JITO_TIP_ACCOUNTS)[3])
    assert _ixs(chosen)[-1][2][1] == sorted(JITO_TIP_ACCOUNTS)[3]
    with pytest.raises(B.BuyRefused, match="not one of Jito's tip accounts"):
        buyer.build(coin(), 0.02, 0.10, BLOCKHASH, tip_lamports=5_000, tip_account=OUR_WALLET)


def test_a_buy_behind_a_leader_is_quoted_on_the_curve_the_leader_leaves():
    """The quote and the least coins out follow the curve the buy meets: right behind a leader's buy, the curve after
    it (`Buyer.behind` reads it off the chain's simulation of the leader's transaction)."""
    lead = B.quote_buy(2 * SOL, PRE["vs"], PRE["vt"])
    after = coin(
        vs=PRE["vs"] + lead.net_sol, vt=PRE["vt"] - lead.tokens, rs=PRE["rs"] + lead.net_sol, rt=PRE["rt"] - lead.tokens
    )
    p0, p1 = (
        B.Buyer(object(), wallet=OUR_WALLET).plan(coin(), 0.02, 0.1),
        B.Buyer(object(), wallet=OUR_WALLET).plan(after, 0.02, 0.1),
    )
    assert p1.quote == B.quote_buy(SPENT, PRE["vs"] + lead.net_sol, PRE["vt"] - lead.tokens)
    assert p1.quote.tokens < p0.quote.tokens and p1.min_tokens_out == B.min_tokens_out(p1.quote.tokens, 0.1)


# -- refusals, each in plain words, before anything is built -----------------------------------------------------------------


def _refused(buyer, c, sol=0.02, slippage=0.10, signed=True):
    with pytest.raises(B.BuyRefused) as e:
        buyer.build(c, sol, slippage, BLOCKHASH, tip_lamports=1_000, signed=signed)
    return str(e.value)


def test_refusals(key):
    user, secret = key
    buyer = B.Buyer(object(), secret)
    assert "left its pump.fun curve" in _refused(buyer, coin(complete=True))
    assert "PumpSwap" in _refused(buyer, coin(complete=True))
    assert "mayhem" in _refused(buyer, coin(mayhem=True))
    assert "cashback" in _refused(buyer, coin(cashback=True))
    other = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    assert "priced in another token" in _refused(buyer, coin(quote_mint=other)) and other in _refused(
        buyer, coin(quote_mint=other)
    )
    assert "older token program" in _refused(buyer, coin(token_program=TOKEN_PROGRAM))
    assert "token program" in _refused(buyer, coin(token_program=SYSTEM_PROGRAM))
    assert "above the per-buy cap of 0.2 SOL" in _refused(buyer, coin(), sol=0.2001)
    assert "owner's decision" in _refused(buyer, coin(), sol=5)
    for nothing in (0, -0.1):
        assert "more than nothing" in _refused(buyer, coin(), sol=nothing)
    assert "slippage" in _refused(buyer, coin(), slippage=2)
    # a curve near its end (10,000 coins left, its SOL on the curve's own constant product): 0.02 SOL would take more
    near_end = coin(rt=10_000_000_000, vt=279_910_000_000_000, rs=85_000_000_000, vs=115_000_000_000)
    assert "last of the curve's coins" in _refused(buyer, near_end)
    holder = buyer.build(coin(holder=True), 0.02, 0.1, BLOCKHASH)  # holder-reward coins: landed v3 buys cover them
    assert _ixs(holder)[3][0] == PUMP_PROGRAM
    assert B.Buyer(object(), secret, max_sol=0.5).build(coin(), 0.3, 0.1, BLOCKHASH) is not None  # the caller's cap
    unsigned = B.Buyer(object(), wallet=user)  # no key: a buy can be built for the chain's simulation, never signed
    assert "SNIPER_SECRET" in _refused(unsigned, coin())
    tx = unsigned.build(coin(), 0.02, 0.1, BLOCKHASH, signed=False)
    assert str(tx.signatures[0]) == "1" * 64


def test_a_key_for_another_wallet_than_the_sniper_wallet_is_refused(key, monkeypatch):
    from solders.keypair import Keypair

    user, secret = key
    with pytest.raises(B.BuyRefused, match="not the sniper wallet"):
        B.Buyer(object(), secret, wallet=str(Keypair().pubkey()))
    assert B.Buyer(object(), secret, wallet=user).user == user
    monkeypatch.delenv("SNIPER_SECRET", raising=False)
    with pytest.raises(B.BuyRefused, match="SNIPER_SECRET"):
        B.Buyer(object())  # neither a key nor a wallet: nothing to buy for
    monkeypatch.setenv("SNIPER_SECRET", secret)
    assert B.Buyer(object()).user == user  # read from the environment as the sale reads it


# -- the chain, faked: it answers the reads and simulations from the template, and lands nothing -------------------------------

TEMPLATE_LOGS = _builder().source_transaction()["meta"]["logMessages"]


class Chain:
    """The chain as the buyer reads it: the template coin's curve just before its landed buy, the coin's mint (a
    Token-2022 account), the wallet holding `lamports`, the wallet's account of the coin missing. The simulation of a
    transaction the wallet signed answers with the landed buy's own log (its TradeEvent: the program's fill of 0.02
    SOL on this curve); a leader's (anyone else's) answers with the curve after `leader_spends` lamports bought."""

    commitment = "confirmed"

    def __init__(self, user, lamports=1 * SOL, curve=None, leader_spends=2 * SOL, leader_err=None, sim_err=None,
                 mint_owner=TOKEN_2022_PROGRAM, curve_owner=PUMP_PROGRAM):  # fmt: skip
        self.user, self.lamports, self.curve = user, lamports, curve or curve_bytes()
        self.leader_spends, self.leader_err, self.sim_err = leader_spends, leader_err, sim_err
        self.mint_owner, self.curve_owner = mint_owner, curve_owner
        self.calls: list[str] = []
        self.simulated: list[tuple[str, dict]] = []

    def _account(self, address):
        if address == bonding_curve(MINT):
            return {"data": [base64.b64encode(self.curve).decode(), "base64"], "owner": self.curve_owner, "lamports": 1}
        if address == MINT:
            return {"data": ["", "base64"], "owner": self.mint_owner, "lamports": 1}
        if address == self.user:
            return {"data": ["", "base64"], "owner": SYSTEM_PROGRAM, "lamports": self.lamports}
        return None

    async def call(self, method, params=None):
        from solders.transaction import VersionedTransaction

        self.calls.append(method)
        if method == "getMultipleAccounts":
            assert params[1]["encoding"] == "base64"
            return {"context": {"slot": 472790129}, "value": [self._account(a) for a in params[0]]}
        if method == "getLatestBlockhash":
            return {"value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 1}}
        if method == "simulateTransaction":
            tx = VersionedTransaction.from_bytes(base64.b64decode(params[0]))
            payer = str(tx.message.account_keys[0])
            self.simulated.append((payer, params[1]))
            if payer == self.user:
                return {
                    "value": {"err": self.sim_err, "logs": TEMPLATE_LOGS, "unitsConsumed": 82_941, "accounts": None}
                }
            q = B.quote_buy(self.leader_spends, PRE["vs"], PRE["vt"])
            after = curve_bytes(vs=PRE["vs"] + q.net_sol, vt=PRE["vt"] - q.tokens, rs=PRE["rs"] + q.net_sol,
                                rt=PRE["rt"] - q.tokens)  # fmt: skip
            return {"value": {"err": self.leader_err, "logs": [], "unitsConsumed": 70_000,
                              "accounts": [{"data": [base64.b64encode(after).decode(), "base64"], "owner": PUMP_PROGRAM}]}}  # fmt: skip
        raise AssertionError(f"the buyer asked the chain for {method}: it never sends through the RPC")


def test_the_coin_is_read_in_one_call(key):
    user, secret = key
    chain = Chain(user, lamports=3 * SOL)
    c = asyncio.run(B.Buyer(chain, secret).coin(MINT))
    assert chain.calls == ["getMultipleAccounts"]
    assert (c.mint, c.token_program, c.account_exists, c.wallet_lamports) == (MINT, TOKEN_2022_PROGRAM, False, 3 * SOL)
    assert c.curve.virtual_token_reserves == PRE["vt"] and not c.curve.complete
    for chain, why in ((Chain(user, curve_owner=SYSTEM_PROGRAM), "not pump.fun's"),
                       (Chain(user, curve=bytes(166)), "not a pump.fun bonding curve")):  # fmt: skip
        with pytest.raises(B.BuyRefused, match=why):
            asyncio.run(B.Buyer(chain, secret).coin(MINT))
    with pytest.raises(B.BuyRefused, match="no pump.fun curve"):
        asyncio.run(B.Buyer(Chain(user), secret).coin("So11111111111111111111111111111111111111112"))
    with pytest.raises(B.BuyRefused, match="not a coin's address"):
        asyncio.run(B.Buyer(Chain(user), secret).coin("not-a-mint"))


def _leader_tx(spends=2 * SOL):
    """A leader's signed buy of the coin: another wallet's (a throwaway key), built by the same builder."""
    from solders.keypair import Keypair

    tx = B.Buyer(object(), str(Keypair()), max_sol=100).build(coin(), spends / SOL, 0.5, BLOCKHASH)
    return base64.b64encode(bytes(tx)).decode(), str(tx.signatures[0])


def test_behind_a_leader_the_curve_is_the_one_its_simulation_leaves(key):
    """The leader's transaction is simulated as it is (its own signature and blockhash checked) with the coin's curve
    asked back: the quote stands on the curve after the leader's buy. A leader that fails in simulation, that does not
    buy this coin, or that is no signed transaction is refused: a bundle with it could only fail."""
    user, secret = key
    chain = Chain(user)
    buyer = B.Buyer(chain, secret)
    leader, sig = _leader_tx()
    after = asyncio.run(buyer.behind(asyncio.run(buyer.coin(MINT)), leader))
    lead = B.quote_buy(2 * SOL, PRE["vs"], PRE["vt"])
    assert after.curve.virtual_quote_reserves == PRE["vs"] + lead.net_sol
    assert after.leader == {"signature": sig, "sol_in_curve": lead.net_sol, "tokens": lead.tokens}
    payer, how = chain.simulated[-1]
    assert payer != user and how["sigVerify"] is True and how["replaceRecentBlockhash"] is False
    assert how["accounts"] == {"encoding": "base64", "addresses": [bonding_curve(MINT)]}
    for c, why in ((Chain(user, leader_err={"InstructionError": [3, {"Custom": 6042}]}), "fails in simulation"),
                   (Chain(user, leader_spends=0), "does not buy this coin")):  # fmt: skip
        b = B.Buyer(c, secret)
        with pytest.raises(B.BuyRefused, match=why):
            asyncio.run(b.behind(asyncio.run(b.coin(MINT)), leader))
    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.transaction import Transaction

    unsigned = base64.b64encode(bytes(Transaction.new_unsigned(Message.new_with_blockhash(
        [], Pubkey.from_string(user), Hash.from_string(BLOCKHASH))))).decode()  # fmt: skip
    for bad, why in ((unsigned, "not signed"), ("bm90IGEgdHJhbnNhY3Rpb24=", "not a transaction")):
        with pytest.raises(B.BuyRefused, match=why):
            asyncio.run(buyer.behind(asyncio.run(buyer.coin(MINT)), bad))


def test_a_dry_run_simulates_and_checks_the_arithmetic_against_the_programs_own_fill(key):
    """The simulation runs the built buy without the signature check and on the chain's own blockhash; the program's
    TradeEvent in its log is the fill, and the quote's arithmetic on the curve that event records gives the very same
    coins (`check` 0)."""
    user, secret = key
    chain = Chain(user)
    buyer = B.Buyer(chain, secret)
    tx = buyer.build(asyncio.run(buyer.coin(MINT)), 0.02, 0.1, BLOCKHASH, tip_lamports=1_000_000)
    sim = asyncio.run(buyer.simulate(tx))
    assert chain.simulated[-1][1]["sigVerify"] is False and chain.simulated[-1][1]["replaceRecentBlockhash"] is True
    assert sim["err"] is None and sim["units"] == 82_941
    assert sim["fill"] == {"tokens": 371_200_971_351, "sol_in_curve": 19_753_085, "spent": SPENT, "fee_bps": [95, 30],
                           "check": 0}  # fmt: skip
    failed = asyncio.run(B.Buyer(Chain(user, sim_err={"InstructionError": [4, {"Custom": 6042}]}), secret).simulate(tx))
    assert failed["err"] == {"InstructionError": [4, {"Custom": 6042}]} and failed["log"]


# -- the command: snipetech buy -------------------------------------------------------------------------------------------


class _Answer:
    status_code = 200

    def __init__(self, body):
        self._body, self.text = body, ""

    def json(self):
        return self._body


class Engine:
    """Jito's block engine, faked: it records each request and answers a bundle id (or `answer`)."""

    def __init__(self, answer=None):
        self.posts: list[tuple[str, dict]] = []
        self.answer = answer

    async def post(self, url, json=None, headers=None):
        self.posts.append((url, json))
        if isinstance(self.answer, Exception):
            raise self.answer
        return _Answer(self.answer or {"jsonrpc": "2.0", "id": json["id"], "result": "BUNDLE-1"})

    async def aclose(self):
        pass


@pytest.fixture
def cmd(tmp_path, monkeypatch, key):
    """`snipetech buy` against the fake chain and the fake block engine: the wallet's key in SNIPER_SECRET as on the
    server, the owner's wallet-wide guard clear (`env.guard[0]` sets what it says)."""
    from types import SimpleNamespace

    from snipetech import cli
    from snipetech.service import Service
    from snipetech.solana import bundle as J
    from snipetech.solana.rpc import SolanaRpc

    user, secret = key
    env = SimpleNamespace(user=user, secret=secret, chain=Chain(user), engine=Engine(), tmp=tmp_path, guard=[None],
                          asked_guard=None)  # fmt: skip

    async def call(self, method, params=None, final=frozenset()):
        return await env.chain.call(method, params)

    async def guard(self, address, plain_sol=None, held=None):
        env.asked_guard = address
        return env.guard[0]

    monkeypatch.setattr(SolanaRpc, "call", call)
    monkeypatch.setattr(Service, "start_refusal", guard)
    monkeypatch.setattr(J, "http_client", lambda: env.engine)
    monkeypatch.setenv("SNIPER_SECRET", secret)

    def run(*args, config=""):
        settings = tmp_path / "s.toml"
        settings.write_text(config)
        return cli.main(["--config", str(settings), "--db", str(tmp_path / "t.db"), "buy", MINT, *args])

    env.run = run
    return env


def _sent(env, i=0) -> list:
    from solders.transaction import Transaction

    return [Transaction.from_bytes(base64.b64decode(t)) for t in env.engine.posts[i][1]["params"][0]]


def test_without_send_the_command_is_a_dry_run_that_sends_nothing(cmd, capsys):
    """It reads the coin, quotes, builds and signs the buy and has the chain simulate it; it prints the quote, the
    least coins out, every account's role and the simulation, and the block engine hears nothing."""
    assert cmd.run("--sol", "0.02") == 0
    out = capsys.readouterr().out
    assert cmd.engine.posts == [] and cmd.chain.calls == [
        "getMultipleAccounts",
        "getLatestBlockhash",
        "simulateTransaction",
    ]
    assert "371,200,971,351" in out and "337,455,428,501" in out  # the quote and the least coins out
    assert all(name in out for name in B.BUY_V3_ACCOUNT_NAMES) and "signer, writable" in out
    assert "the chain's simulation: ok" in out and "exact" in out and "dry run: nothing sent" in out
    assert cmd.run("--sol", "0.02", "--json") == 0
    row = json.loads(capsys.readouterr().out)
    assert row["dry_run"] is True and row["signed"] is True and row["send_refusals"] == []
    assert (row["quote"]["tokens"], row["min_tokens_out"], row["simulation"]["fill"]["check"]) == (371_200_971_351,
                                                                                                    337_455_428_501, 0)  # fmt: skip
    assert [a["name"] for a in row["accounts"]] == list(B.BUY_V3_ACCOUNT_NAMES) and cmd.engine.posts == []
    assert cmd.chain.simulated[-1][1]["sigVerify"] is False and cmd.chain.simulated[-1][1]["replaceRecentBlockhash"]


def test_send_sends_one_bundle_with_the_signed_buy_and_says_its_id(cmd, capsys):
    from snipetech.solana import bundle as J

    assert cmd.run("--sol", "0.02", "--send", "--region", "frankfurt") == 0
    out = capsys.readouterr().out
    assert "BUNDLE-1" in out and len(cmd.engine.posts) == 1
    url, body = cmd.engine.posts[0]
    assert (
        url == J.REGIONS["frankfurt"] and body["method"] == "sendBundle" and body["params"][1] == {"encoding": "base64"}
    )
    (tx,) = _sent(cmd)
    assert tx.verify_with_results() == [True] and str(tx.message.account_keys[0]) == cmd.user
    assert str(tx.signatures[0]) in out
    ixs = _ixs(tx)
    assert ixs[3][0] == PUMP_PROGRAM and ixs[-1][0] == SYSTEM_PROGRAM and ixs[-1][2][1] in JITO_TIP_ACCOUNTS
    assert ixs[-1][1] == struct.pack("<IQ", 2, 1_000_000)  # the default tip, 0.001 SOL, last
    assert "simulateTransaction" not in cmd.chain.calls and cmd.asked_guard == cmd.user  # the owner's guard was asked


def test_behind_a_leader_the_bundle_is_the_leader_then_ours_priced_on_the_curve_it_leaves(cmd, capsys):
    leader, sig = _leader_tx()
    assert cmd.run("--sol", "0.02", "--behind", leader, "--send") == 0
    body = cmd.engine.posts[0][1]
    assert body["params"][0][0] == leader and len(body["params"][0]) == 2  # the leader first, ours right behind it
    assert sig in capsys.readouterr().out
    lead = B.quote_buy(2 * SOL, PRE["vs"], PRE["vt"])
    want = B.quote_buy(SPENT, PRE["vs"] + lead.net_sol, PRE["vt"] - lead.tokens)
    args = decode_instruction(PUMP_PROGRAM, _ixs(_sent(cmd)[1])[3][1])[2]
    assert args == {"spendable_quote_in": SPENT, "min_tokens_out": B.min_tokens_out(want.tokens, 0.10)}


def test_the_per_buy_cap_and_the_tip_are_checked_before_anything_is_read(cmd):
    from snipetech.config import Config

    for extra in ([], ["--send"]):
        with pytest.raises(SystemExit) as e:
            cmd.run("--sol", "0.25", *extra)
        assert "per-buy cap of 0.2 SOL" in str(e.value) and "owner's decision" in str(e.value)
    for tip in ("1", "-0.001"):
        with pytest.raises(SystemExit, match="tip"):
            cmd.run("--sol", "0.02", "--tip", tip)
    with pytest.raises(SystemExit, match="1,000 lamports"):
        cmd.run("--sol", "0.02", "--tip", "0", "--send")
    assert cmd.chain.calls == [] and cmd.engine.posts == []
    assert Config().operator.buy_max_sol == 0.2
    assert cmd.run("--sol", "0.25", config="[operator]\nbuy_max_sol = 0.3\n") == 0  # the owner's setting, raised


def test_the_owners_stop_refuses_a_send_before_anything_is_read(cmd, capsys):
    """The /stop flag the runner and the guard honour (operator/STOP beside the database): a send is refused before the
    chain is asked anything; a dry run still shows the buy and says a send would be refused."""
    stop = cmd.tmp / "operator" / "STOP"
    stop.parent.mkdir()
    stop.write_text("1795400000")
    with pytest.raises(SystemExit) as e:
        cmd.run("--sol", "0.02", "--send")
    assert "/stop" in str(e.value) and cmd.chain.calls == [] and cmd.engine.posts == []
    assert cmd.run("--sol", "0.02") == 0
    out = capsys.readouterr().out
    assert "a --send would be refused: the owner's /stop flag is set" in out and cmd.engine.posts == []


def test_the_owners_wallet_guard_refuses_a_send(cmd):
    cmd.guard[0] = "the owner's wallet-wide guard holds (today is 1.20 SOL down, at or past the 1 SOL day stop)"
    with pytest.raises(SystemExit, match="wallet-wide guard holds"):
        cmd.run("--sol", "0.02", "--send")
    assert cmd.engine.posts == [] and cmd.chain.calls == []


def test_a_wallet_that_cannot_pay_the_buy_its_fees_and_its_exit_is_refused_before_anything_is_built(cmd, capsys):
    cmd.chain.lamports = 20_000_000  # the buy's 0.02 SOL, and nothing for its fees, tip, account and sale
    with pytest.raises(SystemExit) as e:
        cmd.run("--sol", "0.02", "--send")
    assert "the wallet holds 0.0200 SOL" in str(e.value) and "0.0245 SOL" in str(e.value) and cmd.engine.posts == []
    assert "getLatestBlockhash" not in cmd.chain.calls  # nothing built
    assert cmd.run("--sol", "0.02") == 0
    assert "a --send would be refused: the wallet holds 0.0200 SOL" in capsys.readouterr().out


def test_without_the_key_a_dry_run_simulates_the_buy_unsigned_and_a_send_is_refused(cmd, capsys, monkeypatch):
    monkeypatch.delenv("SNIPER_SECRET")
    settings = f'[operator]\nwallet = "{cmd.user}"\n'
    assert cmd.run("--sol", "0.02", config=settings) == 0
    out = capsys.readouterr().out
    assert "built unsigned" in out and "a --send would be refused: SNIPER_SECRET is not set" in out
    assert cmd.chain.simulated[-1][0] == cmd.user and cmd.chain.simulated[-1][1]["sigVerify"] is False
    with pytest.raises(SystemExit, match="SNIPER_SECRET"):
        cmd.run("--sol", "0.02", "--send", config=settings)
    assert cmd.engine.posts == []


def test_a_refused_coin_or_a_refusing_engine_is_said_in_plain_words(cmd):
    cmd.chain.curve = curve_bytes(complete=True)
    with pytest.raises(SystemExit) as e:
        cmd.run("--sol", "0.02", "--send")
    assert str(e.value).startswith("buy: refused, nothing sent: ") and "PumpSwap" in str(e.value)
    cmd.chain.curve = curve_bytes()
    cmd.engine.answer = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "bundle contains an already processed transaction"},
    }
    with pytest.raises(SystemExit, match="already processed"):
        cmd.run("--sol", "0.02", "--send")
    cmd.engine.answer = ConnectionResetError("reset")
    with pytest.raises(SystemExit) as e:
        cmd.run("--sol", "0.02", "--send")
    assert "not known" in str(e.value) and "ConnectionResetError" in str(e.value)  # it may have been taken: say so


def test_the_secret_never_appears_in_any_output_or_error(cmd, capsys, monkeypatch):
    """Every path of the command, dry run and send, accepted and refused, prints nothing of the wallet's key in either
    form, and the block engine's requests carry signed transactions, never the key."""
    from solders.keypair import Keypair

    as_list = json.dumps(list(bytes(Keypair.from_base58_string(cmd.secret))))
    said: list[str] = []

    def run(*args):
        try:
            cmd.run(*args)
        except SystemExit as e:
            said.append(str(e.code))
        got = capsys.readouterr()
        said.extend([got.out, got.err])

    leader, _sig = _leader_tx()
    for args in (("--sol", "0.02"), ("--sol", "0.02", "--json"), ("--sol", "0.02", "--send"), ("--sol", "5"),
                 ("--sol", "0.02", "--behind", leader, "--send", "--json"), ("--sol", "0.02", "--behind", "bm8=")):  # fmt: skip
        run(*args)
    cmd.chain.curve = curve_bytes(mayhem=True)
    run("--sol", "0.02", "--send")
    text = "\n".join(said)
    assert "BUNDLE-1" in text and "mayhem" in text and "per-buy cap" in text
    for form in (cmd.secret, as_list, as_list.replace(" ", "")):
        assert form not in text and form not in json.dumps([b for _u, b in cmd.engine.posts])
    monkeypatch.setenv("SNIPER_SECRET", "x" + cmd.secret[1:])  # a slip of the paste: no key, and nothing of it repeated
    with pytest.raises(SystemExit) as e:
        cmd.run("--sol", "0.02")
    assert "not a valid secret key" in str(e.value) and cmd.secret[1:12] not in str(e.value)
    assert repr(B.Buyer(object(), cmd.secret)) == f"Buyer(wallet={cmd.user})"


def test_the_command_is_the_owners_and_says_so():
    """`buy` trades: no agent starts it (the owner's commands), the server's job list does not run it, and its help says
    that --send is a live trade and that raising the per-buy cap is the owner's decision."""
    import argparse

    from snipetech import ops
    from snipetech.cli import build_parser
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.solana import bundle as J

    assert "buy" in OWNER_COMMANDS and "buy" not in RESEARCH_COMMANDS and "buy" not in ops.ALLOWED
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices["buy"]
    text = " ".join(sub.format_help().split())
    assert "owner's decision" in text and "live trade" in text and "dry run" in text
    a = parser.parse_args(["buy", MINT, "--sol", "0.1"])
    assert (a.mint, a.sol, a.slippage, a.tip, a.behind, a.send, a.region) == (MINT, 0.1, 0.10, 0.001, None, False,
                                                                              J.DEFAULT_REGION)  # fmt: skip
