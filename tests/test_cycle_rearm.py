"""A send that bought nothing, read off the chain (23 Nov 2026, the smoke test of the audited runner).

With a market-cap ceiling most copies are refused on chain: 24 times in 32 minutes the runner said "OKTech had it
spent 30 s with nothing held (a send that bought nothing)", 47 to 62 s after the refused send, and the copied wallet's
next buys in that time went uncopied. The 30 s are right while a coin may still show (a sniper started again while it
holds a coin loses its exit). The chain says sooner what happened: a refused send is a FAILED transaction of the
wallet, and a buy is a successful one. So a sniper OKTech reads spent is armed again at the reading after that one
when the wallet's own newest transactions show a failed one since its last start and no buy since that start that no
sale followed. Anything else keeps the 30 s: no failed transaction, a buy whose coin has not shown, a transaction
the node does not serve yet, a list that does not reach back to the start, a read that fails.

A failed transaction cannot be told to one sniper, and neither can a buy (OKTech copies a copied wallet's failed sends
and create-bait: no copied buy stands before those). So each sniper is asked by its own last start: a buy of the
wallet since then that is still unsold may be its own, whoever sent the failed one.
"""

from tests.chain import Chain, key, noise, sig, swap
from tests.test_cycle_floor import T0, _run
from tests.test_oktech import SNIPER_IDS, W6

A, B = SNIPER_IDS
X = key("coin X")  # the coin a copy buys
REFUSED = {"InstructionError": [4, {"Custom": 6042}]}  # pump.fun's BuySlippageBelowMinTokensOut: the ceiling's refusal


class WalletChain(Chain):
    """The sniper wallet's transactions as the RPC lists and serves them on the runner's fake clock: one put with
    `at(t, tx)` is there once the clock has passed `t`, and `lag` seconds more for a node that is behind (`tip()`:
    the slot such a node has come to, which its readings of the SOL and of the coins name). The wallet has a history:
    one transaction a quarter of an hour before the run, unless `history` is False. `names_slot` False: the reading
    of the wallet's coins names no slot."""

    def __init__(self, lag: float = 0.0, behind: int = 0, history: bool = True):
        super().__init__(t0=int(T0), slot0=480_000_000)
        self.clock, self.lag, self.behind = {"t": T0}, lag, behind  # `behind`: the chain's clock against ours, s
        self.names_slot = True
        if history:
            self.at(T0 - 900, noise("an old transaction", W6))

    def ts(self, slot: int) -> int:
        return super().ts(slot) - self.behind

    def slot_at(self, t: float) -> int:
        return self.slot0 + int((t - self.t0) / 0.4)

    def tip(self) -> int:
        return self.slot_at(self.clock["t"] - self.lag)

    def at(self, t: float, tx: dict) -> None:
        self.put(self.slot_at(t), tx)

    def _all(self):
        tip = self.tip()
        return (row for row in super()._all() if row[0] <= tip)


def _refused(name: str, mint: str = X) -> dict:
    """A copy the chain refused: it paid its fee and moved nothing."""
    return swap(name, W6, mint, tokens=1_000_000, sol=1.0, native=True, priority=0.001, err=REFUSED)


def _copy(name: str, mint: str = X) -> dict:
    """A copy that bought, paid from wrapped SOL (a PumpSwap buy: the plain SOL does not move)."""
    return swap(name, W6, mint, tokens=1_000_000, sol=1.0, priority=0.001, tip=0.002)


def _exit(name: str, mint: str = X) -> dict:
    """The sale of all of it."""
    return swap(name, W6, mint, tokens=-1_000_000, sol=0.9, held=1_000_000, priority=0.001, tip=0.002)


def _coin(mint: str = X) -> dict:
    return {"mint": mint, "amount": 1.0, "raw": 1_000_000, "value_sol": 1.0, "priced": True}


def _finished(sends: dict[str, list[float]], after: float = 12.0):
    """OKTech reads a sniper finished from `after` seconds after each of its sends, until it is started again (on 23
    Nov a refused send read finished about 12 s later, the sniper's exit timer)."""

    def spent(i, now, last_start):
        return any(last_start < at <= now - after for at in sends.get(i, ()))

    return spent


def _starts(client, i, since):
    return [t for kind, j, t in client.timed if kind == "start" and j == i and t >= since]


def _touched(client, i, since, until):
    return [(kind, t - T0) for kind, j, t in client.timed if j == i and since < t < until]


def _lines(rep, i):
    return [e for e in rep["events"] if "re-armed" in e and i[-6:] in e]


def test_a_send_the_chain_shows_failed_arms_its_sniper_again_at_the_next_reading(monkeypatch):
    """(a) A's copy is refused 100 s in and OKTech reads A finished from 112 s. The runner reads OKTech's list at
    117 s; the reading after that (122 s) finds the failed send on the chain and nothing bought since A's start, and
    arms A again: about 25 s after the send, not 50 (the 30 s wait would have armed it at 147 s). B is left alone."""
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and T0 + 117 < again[0] < T0 + 130, [t - T0 for t in again]
    (line,) = _lines(rep, A)
    assert "failed send on chain" in line and "30 s" not in line, line
    assert not _starts(client, B, T0 + 10) and rep["restarts"] == 1


def test_a_spent_sniper_with_no_failed_send_on_the_chain_waits_its_30_s_as_before(monkeypatch):
    """OKTech reads A finished and the wallet made no transaction at all (a send that never landed, a task OKTech
    ended): nothing on the chain says its shot is used up, so A is armed again after the 30 s, as before."""
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=WalletChain(), spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]
    (line,) = _lines(rep, A)
    assert "spent 30 s with nothing held" in line, line


def test_a_buy_since_its_start_whose_coin_has_not_shown_holds_the_fast_rearm_back(monkeypatch):
    """(b) A send of the wallet's fails at 100 s and a buy lands at 101 s (paid from wrapped SOL, a coin the tape does
    not follow: only the chain's list knows of it); a slow node shows its coin from 126 s, its sniper sells it at
    150 s. A reads finished from 112 s. The failed send arms nothing: the buy since A's start may be A's own. No
    sniper is touched until the coin has shown and is sold."""
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused", key("another coin")))
    chain.at(T0 + 101, _copy("copy"))
    chain.at(T0 + 150, _exit("exit"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4,
                       coins=lambda now: [_coin()] if T0 + 126 <= now < T0 + 150 else [])  # fmt: skip
    assert not [(k, i[-6:], t - T0) for k, i, t in client.timed if T0 + 100 < t < T0 + 150], client.timed
    assert _starts(client, A, T0 + 150) and rep["fills"] == 1  # armed again once the coin was sold


def test_a_buy_whose_coin_never_shows_is_armed_again_after_the_30_s_as_before(monkeypatch):
    """(b), the other half: the coin never shows in the wallet. The failed send arms nothing while that buy is unsold
    on the chain's list, and once A has read spent 30 s with nothing held it is armed again, exactly as before. The
    buy's transaction is read once in the run, however often the list is looked at."""
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused", key("another coin")))
    chain.at(T0 + 101, _copy("copy"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]
    assert "spent 30 s with nothing held" in _lines(rep, A)[0]
    assert chain.calls.count(("getTransaction", sig("copy"))) == 1


def test_a_chain_that_cannot_be_read_keeps_the_30_s_wait_and_says_so_once(monkeypatch):
    """(c) The node refuses the list of the wallet's signatures: the 30 s rule as before, said once in the run."""
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused"))

    async def refuses(*a, **k):
        raise RuntimeError("HTTP 429 (rate limited)")

    chain.get_signatures = refuses
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]
    assert "spent 30 s with nothing held" in _lines(rep, A)[0]
    assert sum("failed send" in e and "could not be read" in e for e in rep["events"]) == 1, rep["events"]


def test_a_chain_that_does_not_answer_holds_up_no_tick(monkeypatch):
    """(c) The list of signatures never answers: the look gives up after its own limit, the 30 s rule holds, and the
    run goes on reading the wallet."""
    import asyncio

    import snipetech.service as S

    monkeypatch.setattr(S, "FAILED_LOOK_SECS", 0.05)
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused"))

    async def never(*a, **k):
        await asyncio.Event().wait()

    chain.get_signatures = never
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]
    assert rep["reason"].startswith("time limit") and rep["left_running"] == []


def test_a_transaction_the_node_does_not_serve_yet_keeps_the_30_s_wait(monkeypatch):
    """A transaction of the wallet's landed since A's start and the node cannot serve it yet: it may be a buy, so the
    failed send beside it arms nothing."""
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused", key("another coin")))
    chain.at(T0 + 101, _copy("copy"))
    served = chain.get_transaction

    async def not_yet(signature):
        return None if signature == sig("copy") else await served(signature)

    chain.get_transaction = not_yet
    _rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]


def test_a_list_that_does_not_reach_back_to_the_snipers_start_keeps_the_30_s_wait(monkeypatch):
    """The wallet made more transactions since A's start than the look lists (the owner trading by hand): a buy older
    than the list's last row would go unseen, so the failed send arms nothing."""
    chain = WalletChain()
    for k in range(60):
        chain.at(T0 + 30 + k, noise(f"hand {k}", W6))
    chain.at(T0 + 100, _refused("refused"))
    _rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]


def test_a_chain_whose_clock_is_behind_ours_frees_no_sniper_that_may_have_bought(monkeypatch):
    """A block's time is the chain's own clock, and it has run minutes behind before. The look asks the list back by
    time, so with the chain two minutes behind it stops at the newest row and never reads the buy at 101 s; what
    counts as since a start is told by slots, and a look that did not reach back past A's start frees nothing."""
    chain = WalletChain(behind=120)
    chain.at(T0 + 100, _copy("copy"))
    chain.at(T0 + 101, _refused("refused", key("another coin")))
    _rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]


def test_a_look_that_reaches_one_snipers_start_frees_it_beside_one_it_does_not_reach(monkeypatch):
    """Each sniper is asked by its own last start. The wallet makes 60 other transactions 3 to 18 s in, more than
    the look lists. A's first refused send (20 s) is then no proof (the list does not reach back to its start: 30 s
    rule, armed again at 73 s). Its second (100 s) is: the list reaches back past A's new start, though not past
    B's, which OKTech reads finished too and which waits its 30 s."""

    def spent(i, now, last_start):
        if i == B:
            return last_start < T0 + 10 and now >= T0 + 100
        return any(last_start < at <= now - 12 for at in (T0 + 20, T0 + 100))

    chain = WalletChain()
    for k in range(60):
        chain.at(T0 + 3 + k / 4, noise(f"hand {k}", W6))
    chain.at(T0 + 20, _refused("refused 1"))
    chain.at(T0 + 100, _refused("refused 2"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=spent, minutes=4)
    first, second = (t - T0 for t in _starts(client, A, T0 + 10))
    assert 70 < first < 80 and 117 < second < 135, (first, second)
    one, two = _lines(rep, A)
    assert "spent 30 s with nothing held" in one and "failed send on chain" in two, (one, two)
    (b_again,) = _starts(client, B, T0 + 10)
    assert b_again > second + T0 and "spent 30 s with nothing held" in _lines(rep, B)[0]


def test_a_failed_send_older_than_the_snipers_last_start_says_nothing_of_this_start(monkeypatch):
    """A's refused send at 40 s arms it again at once (63 s). OKTech then reads the new start finished from 100 s
    with no transaction of the wallet's since it: the old failed send is from before this start and frees nothing,
    so the 30 s rule holds."""

    def spent(i, now, last_start):
        return i == A and (last_start < T0 + 40 <= now - 12 or T0 + 60 < last_start < T0 + 100 <= now)

    chain = WalletChain()
    chain.at(T0 + 40, _refused("refused"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=spent, minutes=4)
    first, second = _starts(client, A, T0 + 10)
    assert T0 + 57 < first < T0 + 70 and second >= first + 60, (first - T0, second - T0)
    one, two = _lines(rep, A)
    assert "failed send on chain" in one and "spent 30 s with nothing held" in two, (one, two)


def test_a_second_send_on_its_way_is_never_met_by_a_rearm(monkeypatch):
    """OKTech's cap of one buy a start is not a hard limit (12 Nov: four buys on a cap of three; 23 Nov: a refused
    send and a buy 5 s apart from one start). A's send fails at 95 s, OKTech reads A finished at once, and a second
    send of A's, a buy, lands at 103.5 s; its coin shows from 108 s and is sold at 120 s. The reading that first
    sees A spent (102 s) arms nothing, whatever the chain shows then: the next one finds the buy. A sniper started
    again as its buy lands loses that position's exit."""
    chain = WalletChain()
    chain.at(T0 + 95, _refused("refused", key("another coin")))
    chain.at(T0 + 103.5, _copy("copy"))
    chain.at(T0 + 120, _exit("exit"))
    _rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 95]}, after=1.0),
                        minutes=4, coins=lambda now: [_coin()] if T0 + 108 <= now < T0 + 120 else [])  # fmt: skip
    assert not _touched(client, A, T0 + 95, T0 + 120), _touched(client, A, T0 + 95, T0 + 120)
    assert _starts(client, A, T0 + 120)


def test_of_two_snipers_only_the_one_started_after_the_buy_is_armed_on_a_failed_send(monkeypatch):
    """(d) Two snipers. 40 s in, in the same second, B's copy lands (paid from wrapped SOL, a coin the tape does not
    follow, and the node shows it only from 160 s) and A's copy is refused. Nothing tells the failed send to A: the
    buy is since A's start too. So A waits its 30 s as before (armed again at 88 s). A's next copy is refused at
    100 s: that buy is from before A's last start now, so it cannot be A's, and A is armed again at once. B reads
    finished from 130 s and the same failed sends are since its start, but so is the unsold buy: B is not touched
    until its coin has shown and is sold (175 s)."""

    def spent(i, now, last_start):
        if i == B:
            return last_start < T0 + 40 and now >= T0 + 130
        return any(last_start < at <= now - 12 for at in (T0 + 40.4, T0 + 100))

    chain = WalletChain()
    chain.at(T0 + 40, _copy("copy of B's"))
    chain.at(T0 + 40.4, _refused("refused 1", key("another coin")))
    chain.at(T0 + 100, _refused("refused 2", key("a third coin")))
    chain.at(T0 + 175, _exit("exit"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=spent, minutes=4,
                       coins=lambda now: [_coin()] if T0 + 160 <= now < T0 + 175 else [])  # fmt: skip
    first, second = (t - T0 for t in _starts(client, A, T0 + 10) if t < T0 + 160)
    assert 85 < first < 95 and 117 < second < 135, (first, second)
    one, two = _lines(rep, A)[:2]
    assert "spent 30 s with nothing held" in one and "failed send on chain" in two, (one, two)
    assert not _touched(client, B, T0 + 3, T0 + 175), _touched(client, B, T0 + 3, T0 + 175)


def test_a_list_the_look_cannot_make_sense_of_never_ends_the_run(monkeypatch):
    """The node lists a signature with no slot (an answer the look was not written for): nothing is told, the 30 s
    rule holds and the run goes on to its own end. An error out of the look would have ended the run."""
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused"))
    listed = chain.get_signatures

    async def odd(*a, **k):
        return [{key_: v for key_, v in row.items() if key_ != "slot"} for row in await listed(*a, **k)]

    chain.get_signatures = odd
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]
    assert rep["reason"].startswith("time limit") and rep["left_running"] == []


def test_a_signature_listed_without_its_time_yet_is_waited_for(monkeypatch):
    """The node lists a transaction of moments ago before it has its block time (a signature with no status yet).
    Until 130 s every row since A's start comes without one: nothing is told, quietly, and the reading that finds
    the time arms A again, still before the 30 s rule would have (147 s)."""
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused"))
    listed = chain.get_signatures

    async def untimed(*a, **k):
        rows = await listed(*a, **k)
        return [dict(row, blockTime=None) for row in rows] if chain.clock["t"] < T0 + 130 else rows

    chain.get_signatures = untimed
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and T0 + 130 < again[0] < T0 + 140, [t - T0 for t in again]
    assert "failed send on chain" in _lines(rep, A)[0]
    assert not any("could not be read" in e for e in rep["events"])


# -- the review of round 3 (23 Nov): how old the failed send must be, by the chain's own blocks -----------------------


class FastChain(WalletChain):
    """A chain whose slots take 0.2 s (23 Nov 2026: 0.215 s; 0.4 s in earlier years)."""

    SLOT_SECS = 0.2

    def ts(self, slot: int) -> int:
        return self.t0 + int((slot - self.slot0) * self.SLOT_SECS) - self.behind

    def slot_at(self, t: float) -> int:
        return self.slot0 + int((t - self.t0) / self.SLOT_SECS)


def test_a_node_that_is_behind_frees_no_sniper_whose_buy_it_has_not_seen(monkeypatch):
    """One RPC name is several machines, and one of them can be many seconds behind: its list of the wallet's
    signatures and its reading of the coins tell of the past alike. A's copy is refused at 100 s and A's second send
    buys at 105 s (23 Nov 03:31: a refused send and a buy 5 s apart from one start); OKTech never sells that copy and
    reads A finished from 117 s. The node is 20 s behind: at 122 s it lists the failed send and not the buy, and shows
    no coin until 125 s. The slot its reading of the coins names says how far it has come: 2 s past the failed send,
    not the 20 s a second send needs to land and show. A is never touched."""
    chain = WalletChain(lag=20)
    chain.at(T0 + 100, _refused("refused", key("another coin")))
    chain.at(T0 + 105, _copy("copy"))
    _rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 105]}), minutes=4,
                        coins=lambda now: [_coin()] if now >= T0 + 125 else [])  # fmt: skip
    assert not _touched(client, A, T0 + 100, T0 + 240), _touched(client, A, T0 + 100, T0 + 240)


def test_a_second_send_fourteen_seconds_after_a_refused_one_is_never_met_by_a_rearm(monkeypatch):
    """23 Nov 03:29:34 and 03:29:48: one start, two sends 14 s apart. Should OKTech read the sniper finished at once
    after the first, a look one reading later finds the failed send and nothing else, and the start that follows meets
    the second send as it lands. A failed send frees its sniper only once the wallet's coins were read, with nothing
    held, at a block 20 s after it. A's send fails at 96 s and its second buys at 110 s (the coin shows from 112 s and
    is sold at 130 s): nothing is touched until the coin is sold."""
    chain = WalletChain()
    chain.at(T0 + 96, _refused("refused", key("another coin")))
    chain.at(T0 + 110, _copy("copy"))
    chain.at(T0 + 130, _exit("exit"))
    _rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 96]}, after=0.5),
                        minutes=4, coins=lambda now: [_coin()] if T0 + 112 <= now < T0 + 130 else [])  # fmt: skip
    assert not _touched(client, A, T0 + 96, T0 + 130), _touched(client, A, T0 + 96, T0 + 130)
    assert _starts(client, A, T0 + 130)


def test_a_failed_send_frees_its_sniper_once_it_is_twenty_seconds_old(monkeypatch):
    """The same with no second send: OKTech reads A finished a second after its refused send at 100 s, and the runner
    sees that at 102 s. The readings at 107, 112 and 117 s arm nothing (the failed send is under 20 s old by the slot
    the coins' reading names); the one at 122 s arms A again, still ahead of the 30 s rule (132 s)."""
    chain = WalletChain()
    chain.at(T0 + 100, _refused("refused"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}, after=1.0),
                       minutes=4)  # fmt: skip
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and T0 + 120 < again[0] < T0 + 130, [t - T0 for t in again]
    assert "failed send on chain" in _lines(rep, A)[0]


def test_the_twenty_seconds_are_counted_in_the_chains_own_slots_whatever_a_slot_takes(monkeypatch):
    """How long a slot takes is read off the list itself (its newest and oldest rows, a minute or more apart), never
    taken as 0.4 s: on a chain whose slots take 0.2 s the failed send of 100 s is 20 s old at 122 s, as on any other
    (at 0.4 s a slot the 50 slots of 110 s would have passed for it)."""
    chain = FastChain()
    chain.at(T0 + 100, _refused("refused"))
    _rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}, after=1.0),
                        minutes=4)  # fmt: skip
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and T0 + 120 < again[0] < T0 + 130, [t - T0 for t in again]


def test_a_reading_of_the_coins_that_names_no_slot_keeps_the_30_s_wait(monkeypatch):
    """How far the node has come is told by the slot its reading of the wallet's coins names. An answer without one
    says nothing of that: the failed send arms nothing, and the 30 s rule holds."""
    chain = WalletChain()
    chain.names_slot = False
    chain.at(T0 + 100, _refused("refused"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]
    assert "spent 30 s with nothing held" in _lines(rep, A)[0]


def test_a_list_too_short_to_tell_how_long_a_slot_takes_keeps_the_30_s_wait(monkeypatch):
    """A wallet whose whole list is the one failed send tells nothing of how long a slot takes, so nothing of how
    old that send is by the chain's own blocks: the 30 s rule holds."""
    chain = WalletChain(history=False)
    chain.at(T0 + 100, _refused("refused"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and again[0] >= T0 + 147, [t - T0 for t in again]
    assert "spent 30 s with nothing held" in _lines(rep, A)[0]


def test_a_failed_transaction_older_than_the_snipers_own_shot_frees_nothing_a_node_behind_has_not_seen(monkeypatch):
    """The failed transaction the look finds need not be the sniper's own shot: the owner's hand trade that failed 40 s
    in (or a duplicate sale of OKTech's). A's copy buys at 90 s (paid from wrapped SOL, a coin the tape does not
    follow), OKTech never sells it and reads A finished from 102 s. Every node is 25 s behind: at 107 s the list shows
    the failed transaction and not the buy, the coins show nothing until 115 s, and the coins' reading is 42 s past
    the failed one. Every send of A's start came before OKTech was seen to read it spent (102 s), and the node had
    not come that far: A is never touched."""
    chain = WalletChain(lag=25)
    chain.at(T0 + 40, _refused("the owner's failed hand trade", key("an owner's coin")))
    chain.at(T0 + 90, _copy("copy"))
    _rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 90]}), minutes=4,
                        coins=lambda now: [_coin()] if now >= T0 + 115 else [])  # fmt: skip
    assert not _touched(client, A, T0 + 90, T0 + 240), _touched(client, A, T0 + 90, T0 + 240)


def test_a_chain_clock_a_few_seconds_behind_ours_still_frees_a_refused_sniper(monkeypatch):
    """The moment OKTech was seen to read A spent is on our clock, the node's place on the chain's. On 23 Nov the two
    agreed within 2 s (the run's sale on chain at 03:37:09, said by the run at 03:37:11). With the chain's clock 4 s
    behind ours the refused send of 100 s still arms A again at the reading of 122 s, ahead of the 30 s rule."""
    chain = WalletChain(behind=4)
    chain.at(T0 + 100, _refused("refused"))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: [T0 + 100]}), minutes=4)
    again = _starts(client, A, T0 + 100)
    assert len(again) == 1 and T0 + 117 < again[0] < T0 + 130, [t - T0 for t in again]
    assert "failed send on chain" in _lines(rep, A)[0]


def test_a_sniper_whose_sends_keep_failing_rests_instead_of_burning_a_fee_a_minute(monkeypatch):
    """23 Nov 15:23-15:31: Decu's sniper sent a copy about once a minute, each failing before any buy (buys of coins at
    the moment of their launch, which OKTech copies before they fail), and the runner armed it again after every one:
    0.0011 SOL a minute for nothing, about 0.07 an hour. After FAIL_STREAK_MAX sends in a row that bought nothing, each
    within FAIL_STREAK_FAST_SECS of its start, the sniper rests (FAIL_STREAK_REST_SECS) and the run says why; the
    other sniper is untouched. (Yugi's refusals at the ceiling that day came 4 to 20 minutes after each start: none
    of them would rest him.)"""
    from snipetech import service as S

    chain = WalletChain()
    sends = [T0 + 100, T0 + 160, T0 + 220, T0 + 280, T0 + 340]
    for k, at in enumerate(sends):
        chain.at(at, _refused(f"refused{k}", mint=key(f"coin {k}")))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: sends}), minutes=8)
    again = _starts(client, A, T0 + 100)
    assert len(again) == S.FAIL_STREAK_MAX - 1, [t - T0 for t in again]  # armed again after the first two only
    rest = [e for e in rep["events"] if "rests until" in e and A[-6:] in e]
    assert rest and f"{S.FAIL_STREAK_MAX} sends in a row bought nothing" in rest[0], rep["events"]
    assert not _starts(client, B, T0 + 10)


def test_a_fill_or_a_slow_failure_breaks_the_streak(monkeypatch):
    """A send that fails long after its start (a refusal at the ceiling minutes later, as Yugi's were) is no part of a
    streak: three such failures in a row still arm the sniper again each time."""
    from snipetech import service as S

    chain = WalletChain()
    gap = S.FAIL_STREAK_FAST_SECS + 60
    sends = [T0 + gap * (k + 1) for k in range(S.FAIL_STREAK_MAX)]
    for k, at in enumerate(sends):
        chain.at(at, _refused(f"slow{k}", mint=key(f"slow coin {k}")))
    rep, client = _run(monkeypatch, lambda now: 10.0, chain=chain, spent=_finished({A: sends}),
                       minutes=int(sends[-1] - T0) // 60 + 2)  # fmt: skip
    assert len(_starts(client, A, T0 + 100)) == S.FAIL_STREAK_MAX
    assert not [e for e in rep["events"] if "rests until" in e]
