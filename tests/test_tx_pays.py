"""A transaction's other payments, read by the recorder's own pass (14 Nov).

The tape wrote a trade's tip from a fixed list of landing-service accounts. On 90 random market trades of 14 Nov, 19
of the 70 recorded with tip 0 sent SOL to an account the list does not know. `extract_from_transaction` now also
lists those payments (`TxEvents.pays`), so that the amounts can say which destinations are tip accounts
(`solana/pay_dests.py`), and counts a transfer to a learned tip account into the tip when it is handed them.

The transactions are real ones (`snipetech tx <sig>`): four wallets' own (AtifzK and FLPjyS through the Axiom router,
i3Sj7y on pump.fun itself, Decu through another router) and 90 random market trades of 14 Nov.
"""

import gzip
import json
from pathlib import Path

import pytest

from snipetech.solana.base58 import b58decode
from snipetech.solana.tips import RELAY_TIP_ACCOUNTS
from snipetech.solana.tx import extract_from_transaction
from snipetech.solana.txmeta import extract_tx_meta

FIX = Path(__file__).parent / "fixtures"
TIPS = FIX / "tips"

ATIFZK_FEE = "3Tu1Y9aNveLFN4WTAwnAwXL6tbUp5MMe3RxyybG4jTAS"  # 30,000,000 of a 2.933 SOL buy: 1.02 %
ATIFZK_TIP = "8d5jPhXwjEa9cPkQG2aFHw5cwE6KjytnPFJbmH1RgN1M"  # 796,456: with the priority fee of 3,544, 800,000
FLPJYS_FEE = "2ApLdwLrGayEmxgpLX9BTR47Q2QprfMg5SpjrLeaK8s7"  # 5,747,474 of a 0.5623 SOL buy: 1.02 %
FLPJYS_WSOL = "5NgNqgvm"  # the wallet's own wrapped-SOL account, 625,900,000 lamports wrapped into it
DECU = [
    ("jDdwqZpThntj3pLNoWeNgPViyi7RrZPJjXPhyNm2tG7", 5_944_420),  # 0.39 % of the 1.5435 SOL sale: the referral
    ("3P4hw2BxLCHMzcFPTWjQuLDVnna8RZ7R3wFyvhjwoRXG", 13_870_312),  # 0.90 %: the platform
    ("moonBtH9HvLHjLqi9ivyrMVKgFUsSfrz9BwQ9khhn1u", 1_750_000),  # flat: the tip
]
KNOWN_TIP = "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta"  # Helius Sender, on the fixed list
KNOWN_FEE = "KfHbT5Jrr5SV3CDkQVCLiSNWyVv5DmuVaCzbAHrzMFQ"  # the same transaction's 0.46 % to its router's platform


def load(name: str) -> dict:
    return json.loads((TIPS / f"{name}.json").read_text())


def all115() -> list[tuple[str, dict]]:
    with gzip.open(TIPS / "all115.jsonl.gz", "rt") as f:
        return [(row["name"], row["tx"]) for row in map(json.loads, f)]


def stream_form(tx: dict) -> dict:
    """The same transaction as the tape's stream hands it over (`laserstream.to_rpc_json(raw=True)`): instruction data
    as bytes, no message header, no balances."""

    def ix(i: dict) -> dict:
        return {"programIdIndex": i["programIdIndex"], "accounts": list(i["accounts"]), "data": b58decode(i["data"])}

    msg, meta = tx["transaction"]["message"], tx["meta"]
    return {
        "slot": tx["slot"],
        "blockTime": tx.get("blockTime"),
        "transaction": {
            "signatures": list(tx["transaction"]["signatures"]),
            "message": {"accountKeys": list(msg["accountKeys"]), "instructions": [ix(i) for i in msg["instructions"]]},
        },
        "meta": {
            "err": meta.get("err"),
            "fee": meta.get("fee"),
            "innerInstructions": [
                {"index": g["index"], "instructions": [ix(i) for i in g["instructions"]]}
                for g in meta.get("innerInstructions") or []
            ],
            "logMessages": list(meta.get("logMessages") or []),
            "loadedAddresses": meta.get("loadedAddresses") or {"writable": [], "readonly": []},
        },
    }


def test_the_atifzk_buy_pays_its_platform_and_a_tip_the_list_does_not_know():
    ev = extract_from_transaction(load("AtifzK_4gW26W2jtV"))
    assert ev.pays == [(ATIFZK_FEE, 30_000_000), (ATIFZK_TIP, 796_456)]
    assert ev.tip == 0 and ev.learned_tips == []  # as the tape has recorded it so far
    assert ev.priority + 796_456 == 800_000  # the round number the wallet's bot bids: fee and transfer together


def test_with_that_account_learned_the_transfer_is_the_transactions_tip():
    tx = load("AtifzK_4gW26W2jtV")
    for learned in ({ATIFZK_TIP: 1_794_600_000}, {ATIFZK_TIP}, frozenset({ATIFZK_TIP})):  # a dict or a set
        ev = extract_from_transaction(tx, tip_accounts=learned)
        assert ev.tip == 796_456
        assert ev.pays == [(ATIFZK_FEE, 30_000_000)]  # the platform's share stays a payment, never a tip
        assert ev.learned_tips == [(ATIFZK_TIP, 796_456)]  # what the learning goes on counting for that account
    assert extract_from_transaction(tx, tip_accounts={}).tip == 0  # nothing learned: as without
    assert extract_from_transaction(tx, "S", {ATIFZK_TIP}).signature == "S"  # the signature stays the second argument


def test_the_flpjys_wrap_is_not_a_payment_and_its_one_percent_is():
    """FLPjyS wraps 0.6259 SOL into its own wrapped-SOL account before the PumpSwap buy spends it: its own money."""
    ev = extract_from_transaction(load("FLPjyS_44HzhKyVMJ"))
    assert ev.pays == [(FLPJYS_FEE, 5_747_474)]
    assert not any(dest.startswith(FLPJYS_WSOL) for dest, _lamports in ev.pays)
    assert ev.tip == 0  # this wallet pays no tip at all: its 24,000 lamport priority fee is its whole bid


def test_a_plain_pump_fun_trade_has_no_payments():
    ev = extract_from_transaction(load("i3Sj7y_3Qtq4HhH2Q"))
    assert ev.events and ev.pays == [] and ev.tip == 0 and ev.learned_tips == []


def test_the_decu_sale_has_three_payments():
    ev = extract_from_transaction(load("Decu_2FEXpVUgPf"))
    assert ev.pays == DECU and ev.tip == 0


def test_a_transaction_with_a_known_relay_tip_is_unchanged():
    """The fixed list counts as before, with or without learned accounts; the same transaction's payment to its
    router's platform is listed beside it."""
    tx = load("market_2PBYiLE7TU6W")
    assert KNOWN_TIP in RELAY_TIP_ACCOUNTS
    ev = extract_from_transaction(tx)
    assert ev.tip == 1_000_000 and ev.pays == [(KNOWN_FEE, 450_000)]
    learned = extract_from_transaction(tx, tip_accounts={ATIFZK_TIP: 1})  # another account learned: no difference
    assert (learned.tip, learned.pays, learned.learned_tips) == (1_000_000, [(KNOWN_FEE, 450_000)], [])
    both = extract_from_transaction(tx, tip_accounts={KNOWN_FEE: 1})  # a learned account beside a listed one: the sum
    assert (both.tip, both.pays, both.learned_tips) == (1_450_000, [], [(KNOWN_FEE, 450_000)])


# Jupiter sends 1,346,200 lamports from the buyer to an account that pump.fun's buy then allocates and takes over (the
# buyer's volume record: its rent). The fingerprint lists it as a payment, because Jupiter made the transfer and not
# pump.fun; the recorder's rule leaves it out, because the account is one of the pump.fun instruction's own.
RENT_OF_PUMPS_OWN_ACCOUNT = {"2MECYAi9": [("JCQMbs6df4NnsZFLva83BjBiQhVLutFVxVSEsJrZCN9b", 1_346_200)]}


@pytest.mark.parametrize("path", sorted((FIX / "tx").glob("*.json")), ids=lambda p: p.stem[:8])
def test_the_older_real_transactions_keep_their_tip_and_list_the_fingerprints_payments(path):
    """The 26 transactions the fee records were built on (our own fills among them): the tip is the fingerprint's, as
    before, and the payments are the fingerprint's unknown transfers, but for one the fingerprint gets wrong."""
    tx = json.loads(path.read_text())
    want = extract_tx_meta(tx, path.stem)
    got = extract_from_transaction(tx, path.stem)
    if want.failed:
        assert got.failed and got.pays == []
        return
    assert got.tip == want.tip_lamports and got.priority == want.priority_lamports
    assert not {dest for dest, _lamports in got.pays} & (set(RELAY_TIP_ACCOUNTS) | set(want.signers))
    theirs = [(t["dst"], t["lamports"]) for t in want.transfers]
    extra = RENT_OF_PUMPS_OWN_ACCOUNT.get(path.stem[:8], [])
    assert got.pays == [t for t in theirs if t not in extra] and all(t in theirs for t in extra)


def test_a_wrap_for_a_route_that_never_hands_the_account_to_pump_is_not_a_payment():
    """The case the watched-accounts rule alone lets through: a router wraps the buyer's SOL into the buyer's own
    wrapped-SOL account and spends it on another exchange first (4iHFAXgL: 0.1 SOL through Raydium, then pump.fun), so
    no pump instruction ever names that account. A token program's instruction does (it syncs, spends or closes it):
    an account a token program works on is a token account, never somebody paid."""
    path = next((FIX / "tx").glob("4iHFAXgL*.json"))
    tx = json.loads(path.read_text())
    ev = extract_from_transaction(tx)
    assert ev.pays == [
        ("7sHXjs1j7sDJGVSMSPjD1b4v3FD6uRSvRWfhRdfv5BiA", 1_000_000),
        ("uXYxbbhcD9kHU1KzZM6ZNzPvieEvRcJCPHpqcnpwdcR", 10_000),
    ]
    assert ("C2TVFn1XwmQ7K5QRzEygc6XA5mH3ywwS94AG6fmc5qmX", 100_000_000) not in ev.pays  # the wrap
    assert extract_from_transaction(stream_form(tx)).pays == ev.pays


def test_the_fast_path_agrees_with_the_fingerprint_on_all_115_real_transactions():
    """`extract_tx_meta` (the slow study path) follows each inner instruction's call stack and reads the token
    balances; the recorder's pass knows neither. Its rule: a System transfer from a signer to a non-signer that is no
    account of any pump.fun, PumpSwap or LaunchLab instruction of the transaction. On these 115 they list the same
    payments, in the same order, and the same tip."""
    txs = all115()
    assert len(txs) == 115
    differences, with_pays, with_tip = [], 0, 0
    for name, tx in txs:
        want = extract_tx_meta(tx)
        got = extract_from_transaction(tx)
        assert not got.failed
        if got.pays != [(t["dst"], t["lamports"]) for t in want.transfers] or got.tip != want.tip_lamports:
            differences.append((name, got.pays, want.transfers, got.tip, want.tip_lamports))
        with_pays += bool(got.pays)
        with_tip += bool(got.tip)
    assert differences == []
    assert with_pays >= 40 and with_tip >= 15  # the comparison compares something


def test_the_signers_are_the_first_keys_in_both_forms():
    """getTransaction names the signers in the message header (numRequiredSignatures); the stream's form has no
    header. Both carry one signature a signer, and the signers are the first account keys."""
    for _name, tx in all115():
        header = tx["transaction"]["message"]["header"]
        assert header["numRequiredSignatures"] == len(tx["transaction"]["signatures"])
        assert "header" not in stream_form(tx)["transaction"]["message"]


def test_the_streams_form_gives_the_same_payments_as_the_json_form():
    """The recorder gets instruction data as bytes and no header; the same 115 transactions in that form."""
    learned = {ATIFZK_TIP: 1, DECU[2][0]: 1}
    for _name, tx in all115():
        text, raw = extract_from_transaction(tx), extract_from_transaction(stream_form(tx))
        assert (raw.pays, raw.tip, raw.priority, raw.learned_tips) == (text.pays, text.tip, text.priority, [])
        assert len(raw.events) == len(text.events)
        text, raw = (extract_from_transaction(t, tip_accounts=learned) for t in (tx, stream_form(tx)))
        assert (raw.pays, raw.tip, raw.learned_tips) == (text.pays, text.tip, text.learned_tips)


@pytest.mark.parametrize("name", sorted(p.name for p in (FIX / "laserstream").glob("*.bin")))
def test_the_laserstream_captures_read_the_same_in_both_forms(name):
    from snipetech.solana import laserstream as LS

    if not LS.available():
        pytest.skip("grpc/protobuf not installed")
    from snipetech.solana.ys import geyser_pb2

    u = geyser_pb2.SubscribeUpdate()
    u.ParseFromString((FIX / "laserstream" / name).read_bytes())
    info, slot = u.transaction.transaction, u.transaction.slot
    raw = extract_from_transaction(LS.to_rpc_json(info, slot, 1_800_000_000, raw=True))
    as_json = LS.to_rpc_json(info, slot, 1_800_000_000)
    text = extract_from_transaction(as_json)
    assert (raw.pays, raw.tip, raw.learned_tips) == (text.pays, text.tip, text.learned_tips)
    # and the fingerprint's unknown transfers: two of the seven wrap SOL for a route through another exchange
    # (p1: 0.1 SOL through Raydium; p3: 0.0104 SOL through Jupiter and Orca), which is nobody paid
    want = extract_tx_meta(as_json)
    assert raw.pays == [(t["dst"], t["lamports"]) for t in want.transfers] and raw.tip == want.tip_lamports
    assert len(raw.pays) == {"p1.bin": 2, "p3.bin": 1}.get(name, 0)


def test_a_failed_transaction_and_a_log_notification_have_no_payments():
    from snipetech.solana.tx import TxEvents

    tx = load("AtifzK_4gW26W2jtV")
    tx["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    failed = extract_from_transaction(tx, tip_accounts={ATIFZK_TIP})
    assert failed.failed and failed.pays == [] and failed.learned_tips == []
    logs_only = TxEvents("sig", 1, 0, "")
    assert logs_only.pays == [] and logs_only.learned_tips == []


def test_a_transfer_of_nothing_or_between_signers_is_no_payment():
    """A System transfer of 0 lamports (a bot's marker) and SOL moved to another signer of the same transaction (a
    bundle funding its own wallets) pay nobody."""
    from snipetech.solana.tx import _account_keys

    seen = {"zero": 0, "to_signer": 0}
    for _name, tx in all115():
        keys = _account_keys(tx)  # the message's own, then the lookup tables'
        signers = set(keys[: len(tx["transaction"]["signatures"])])
        ev = extract_from_transaction(tx)
        assert all(lamports > 0 and dest not in signers for dest, lamports in ev.pays)
        inner = [i for g in tx["meta"].get("innerInstructions") or [] for i in g["instructions"]]
        for i in tx["transaction"]["message"]["instructions"] + inner:
            if keys[i["programIdIndex"]] == "1" * 32 and len(i["accounts"]) >= 2:
                data = b58decode(i["data"])
                if data[:4] == b"\x02\0\0\0":
                    seen["zero"] += int.from_bytes(data[4:12], "little") == 0
                    seen["to_signer"] += keys[i["accounts"][1]] in signers
    assert seen["zero"] >= 1 and seen["to_signer"] >= 2  # the real transactions hold both cases


def test_an_instruction_whose_data_cannot_be_read_is_no_payment_and_costs_nothing_else():
    """Base58 text that is none (never seen from a node; a damaged file): that transfer is left out, the rest of the
    transaction is read as ever."""
    tx = load("AtifzK_4gW26W2jtV")
    whole = extract_from_transaction(tx)
    outer = tx["transaction"]["message"]["instructions"]
    tip_ix = next(i for i in outer if i["data"].startswith("3Bxs"))  # the System transfer to the tip account
    tip_ix["data"] = "0OIl"  # no base58
    ev = extract_from_transaction(tx)
    assert ev.pays == [(ATIFZK_FEE, 30_000_000)] and ev.tip == 0
    assert [e.name for e in ev.events] == [e.name for e in whole.events] and ev.priority == whole.priority


def test_a_failure_while_reading_the_payments_never_costs_the_transaction(monkeypatch, caplog):
    """Whatever goes wrong in the new reading: the transaction's events, its priority fee and its tip by the fixed
    list come out as before, without payments; it is counted, and logged the first time only."""
    import logging

    from snipetech.solana import tx as T

    def broken(*_a, **_k):
        raise RuntimeError("something nobody thought of")

    before = T.payment_failures()
    monkeypatch.setattr(T, "_payments", broken)
    monkeypatch.setattr(T, "_payment_failures", [0])
    with caplog.at_level(logging.WARNING, logger="snipetech.solana.tx"):
        for name in ("AtifzK_4gW26W2jtV", "market_2PBYiLE7TU6W", "Decu_2FEXpVUgPf"):
            ev = extract_from_transaction(stream_form(load(name)), tip_accounts={ATIFZK_TIP})
            assert ev.events and ev.pays == [] and ev.learned_tips == [] and ev.priority is not None
            assert ev.tip == (1_000_000 if name.startswith("market") else 0)  # the fixed list's tip, as before
    assert T.payment_failures() == 3 and len(caplog.records) == 1
    assert before == 0  # and nothing failed on any real transaction read so far in this process


def test_a_transaction_without_payments_builds_no_list_and_shares_nothing_that_can_change():
    """Two transactions in three pay nobody. Their `pays` and `learned_tips` are one empty list every such
    transaction shares, so the recorder builds none for them; it reads as an empty list and cannot be changed, so
    nothing one reader does shows up in another transaction. A transaction's payments are set, never appended."""
    from snipetech.solana.tx import TxEvents

    plain = extract_from_transaction(load("i3Sj7y_3Qtq4HhH2Q"))
    other = TxEvents("sig", 1, 0, "")
    assert plain.pays == [] == other.pays and plain.learned_tips == [] == other.learned_tips
    assert isinstance(plain.pays, list) and not plain.pays and list(plain.pays) == [] and len(plain.pays) == 0
    assert plain.pays is other.pays  # none built
    for change in (lambda: plain.pays.append(("X", 1)), lambda: plain.pays.extend([("X", 1)]),
                   lambda: plain.learned_tips.insert(0, ("X", 1)), lambda: plain.pays.__iadd__([("X", 1)])):  # fmt: skip
        with pytest.raises(TypeError):
            change()
    assert other.pays == []
    plain.pays = [("X", 1)]  # set: this transaction's own
    assert plain.pays == [("X", 1)] and other.pays == []
    paying = extract_from_transaction(load("AtifzK_4gW26W2jtV"))
    assert type(paying.pays) is list and paying.learned_tips == []
    paying.pays.append(("Y", 2))  # a transaction's own list is an ordinary one
    assert json.loads(json.dumps(plain.learned_tips)) == []
