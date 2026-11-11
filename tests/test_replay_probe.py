"""What a replaying LaserStream subscription hands over, measured (11 Nov).

The recorder resumes after a drop from "where the last subscription stopped handing over". Whether a slot tick says
that (the PumpSwap side took it so since 7 Nov) or only a delivered transaction does (Helius's SDK issue 115: ticks are
"not ordered with payload delivery"; during a replay they run at the live head) is the provider's behaviour: the probe
subscribes from some slots back and writes down the order things came in.
"""

from snipetech.solana.laserstream import ReplayProbe


def test_ticks_running_ahead_of_the_transactions_are_measured():
    p = ReplayProbe(start=100, head=130)
    p.tick(130, at=0.0)  # a tick of the live head before any replayed transaction
    for slot in (100, 100, 101, 103):
        p.tx(slot, bytes([slot, p.txs]), at=0.1)
    p.tick(131, at=0.2)
    out = p.summary()
    assert out["first_tx_slot"] == 100 and out["first_tick_slot"] == 130
    assert out["tick_ahead_max"] == 30  # tick 130 while no transaction, from slot 100 on, had come: 130 - 100
    assert out["txs"] == 4 and out["ticks"] == 2


def test_transactions_of_an_earlier_slot_after_a_later_one_are_measured():
    p = ReplayProbe(start=100, head=100)
    for slot in (100, 101, 102, 101, 103, 100):
        p.tx(slot, bytes([slot, p.txs]), at=0.0)
    out = p.summary()
    assert out["tx_back"] == 2 and out["tx_back_max"] == 3  # slot 100 after slot 103


def test_transactions_of_a_slot_after_its_tick_are_measured():
    p = ReplayProbe(start=100, head=100)
    p.tx(100, b"a", at=0.0)
    p.tick(101, at=0.0)  # "everything before slot 101 has been handed over" would be wrong:
    p.tx(100, b"b", at=0.0)
    p.tx(101, b"c", at=0.0)  # its own slot's transactions after its tick
    out = p.summary()
    assert out["tx_before_tick"] == 1 and out["tx_before_tick_max"] == 1
    assert out["tx_at_tick"] == 1


def test_a_transaction_handed_over_twice_is_counted():
    p = ReplayProbe(start=100, head=100)
    p.tx(100, b"a", at=0.0)
    p.tx(100, b"a", at=0.0)
    assert p.summary()["duplicates"] == 1


def test_the_probe_says_where_the_replay_began_and_when_it_reached_the_head():
    p = ReplayProbe(start=100, head=120)
    p.tx(104, b"a", at=1.0)  # the server began four slots after the one asked for
    p.tx(119, b"b", at=2.0)
    p.tx(120, b"c", at=3.5)
    p.tx(121, b"d", at=3.6)
    out = p.summary()
    assert out["asked_from"] == 100 and out["first_tx_slot"] == 104
    assert out["reached_head_secs"] == 2.5  # from its first message to the first transaction at the head
