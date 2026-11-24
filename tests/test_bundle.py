"""The Jito block-engine client: a bundle of at most five transactions, ours right behind the leader's a seat hands over.

The HTTP client is a fake that records every request and answers what a block engine answers: nothing here reaches a
network, and nothing lands."""

import asyncio
import base64
import json

import pytest

from snipetech.solana import bundle as J

SOL = 1_000_000_000
BLOCKHASH = "CdSs3nhUW2L4i9rDT8DnyGYA3b6fqHk9D4Ryv9bEQNPp"


class Answer:
    """An HTTP answer: its status, its text, and its text read as JSON (a ValueError when it is none, as httpx's)."""

    def __init__(self, status: int, body):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class Http:
    """Records each POST (url, body) and answers with `answer` (a block engine's JSON-RPC reply by default)."""

    def __init__(self, answer=None, status=200):
        self.posts: list[tuple[str, dict]] = []
        self.answer, self.status, self.closed = answer, status, False

    async def post(self, url, json=None, headers=None):
        self.posts.append((url, json))
        if isinstance(self.answer, Exception):
            raise self.answer
        body = self.answer if self.answer is not None else {"jsonrpc": "2.0", "id": json["id"], "result": "b1d"}
        return Answer(self.status, body)

    async def aclose(self):
        self.closed = True


def _signed(lamports: int = 1, kp=None):
    """A signed transfer from a throwaway key: any signed transaction stands in for one here."""
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    kp = kp or Keypair()
    ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=Keypair().pubkey(), lamports=lamports))
    msg = Message.new_with_blockhash([ix], kp.pubkey(), Hash.from_string(BLOCKHASH))
    return Transaction([kp], msg, Hash.from_string(BLOCKHASH))


def _b64(tx) -> str:
    return base64.b64encode(bytes(tx)).decode()


def test_the_regions_are_jitos_mainnet_block_engines():
    assert set(J.REGIONS) == {"amsterdam", "frankfurt", "ny", "tokyo", "slc"}
    for region, url in J.REGIONS.items():
        assert url == f"https://{region}.mainnet.block-engine.jito.wtf/api/v1/bundles"
    assert J.DEFAULT_REGION in J.REGIONS and J.MAX_TRANSACTIONS == 5


def test_send_bundle_posts_the_json_rpc_body_and_returns_the_bundle_id():
    """sendBundle with the transactions as base64, in the order given, and the encoding said: the block engine's
    answer is the bundle's id."""
    a, b = _signed(1), _signed(2)
    http = Http()
    out = asyncio.run(J.send_bundle([a, _b64(b)], J.REGIONS["frankfurt"], http=http))
    assert out == "b1d" and len(http.posts) == 1 and not http.closed  # a client handed in is the caller's to close
    url, body = http.posts[0]
    assert url == "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles"
    assert body == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendBundle",
        "params": [[_b64(a), _b64(b)], {"encoding": "base64"}],
    }


def test_a_bundle_holds_one_to_five_transactions():
    http = Http()
    for txs in ([], [_signed(i) for i in range(1, 7)]):
        with pytest.raises(J.BundleError, match="1 to 5 transactions"):
            asyncio.run(J.send_bundle(txs, J.REGIONS["ny"], http=http))
    with pytest.raises(J.BundleError, match="twice"):
        same = _signed(1)
        asyncio.run(J.send_bundle([same, same], J.REGIONS["ny"], http=http))
    assert http.posts == []  # refused before anything was posted
    asyncio.run(J.send_bundle([_signed(i) for i in range(1, 6)], J.REGIONS["ny"], http=http))
    assert len(http.posts[0][1]["params"][0]) == 5


def test_the_block_engines_refusal_is_said_in_its_own_words():
    refusal = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "bundle contains an already processed transaction"},
    }
    with pytest.raises(J.BundleError, match="already processed"):
        asyncio.run(J.send_bundle([_signed()], J.REGIONS["ny"], http=Http(refusal)))
    with pytest.raises(J.BundleError, match="already processed"):  # the engine's own no, whatever 4xx carries it
        asyncio.run(J.send_bundle([_signed()], J.REGIONS["ny"], http=Http(refusal, status=400)))
    for status in (400, 429):
        with pytest.raises(J.BundleError, match=str(status)):
            asyncio.run(J.send_bundle([_signed()], J.REGIONS["ny"], http=Http("rate limited", status=status)))


def test_an_answer_that_says_neither_yes_nor_no_is_not_known_never_a_refusal():
    """Review of 23 Nov: any status of 400 or more, or any answer that is no JSON, was a refusal, and the buyer said
    "nothing sent". A 5xx from the gateway in front of the block engine, a body that is no JSON-RPC answer, or a 2xx
    with no bundle id does not say the bundle was refused: it may have been taken, and may land. Those are
    BundleUnknown, never a BundleError."""
    for http in (Http("<html>502 Bad Gateway</html>", status=502), Http("<html>504 Gateway Time-out</html>", status=504),
                 Http({"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "internal"}}, status=503),
                 Http("<html>maintenance</html>", status=200), Http({"jsonrpc": "2.0", "id": 1}),
                 Http({"jsonrpc": "2.0", "id": 1, "result": None})):  # fmt: skip
        with pytest.raises(J.BundleUnknown) as e:
            asyncio.run(J.send_bundle([_signed()], J.REGIONS["ny"], http=http))
        assert not isinstance(e.value, J.BundleError) and len(http.posts) == 1
    assert not issubclass(J.BundleUnknown, J.BundleError) and not issubclass(J.BundleError, J.BundleUnknown)


def test_bundle_statuses_asks_for_each_id_and_returns_the_engines_list():
    status = {"bundle_id": "b1d", "transactions": ["s1"], "slot": 472790130, "confirmation_status": "confirmed",
              "err": {"Ok": None}}  # fmt: skip
    http = Http({"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 472790131}, "value": [status, None]}})
    out = asyncio.run(J.bundle_statuses(["b1d", "b2"], J.REGIONS["tokyo"], http=http))
    assert out == [status, None]
    assert http.posts[0] == (J.REGIONS["tokyo"], {"jsonrpc": "2.0", "id": 1, "method": "getBundleStatuses",
                                                  "params": [["b1d", "b2"]]})  # fmt: skip


def test_a_client_made_here_is_closed_here(monkeypatch):
    http = Http()
    monkeypatch.setattr(J, "http_client", lambda: http)
    asyncio.run(J.send_bundle([_signed()], J.REGIONS["slc"]))
    assert http.closed and len(http.posts) == 1


def test_behind_puts_the_leader_first_and_ours_right_after_it():
    """Our buy lands right after the leader's transaction, never in front of it: the bundle is [leader, ours]."""
    leader, ours = _signed(7), _signed(8)
    out = J.behind(_b64(leader), ours)
    assert out == [_b64(leader), _b64(ours)] and out[0] == _b64(leader)
    assert J.behind(_b64(leader), [ours, _signed(9)])[0] == _b64(leader)  # more of ours: still behind it
    with pytest.raises(J.BundleError, match="1 to 5 transactions"):
        J.behind(_b64(leader), [_signed(i) for i in range(10, 15)])  # the leader and five of ours: six
    with pytest.raises(J.BundleError, match="the leader's own"):
        J.behind(_b64(leader), leader)  # ours is not the leader's


def test_the_leader_must_be_a_signed_transaction():
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.transaction import Transaction

    unsigned = Transaction.new_unsigned(Message.new_with_blockhash([], Keypair().pubkey(), Hash.from_string(BLOCKHASH)))
    for bad, why in ((_b64(unsigned), "not signed"), ("bm90IGEgdHJhbnNhY3Rpb24=", "not a transaction"),
                     ("%%%", "not a transaction")):  # fmt: skip
        with pytest.raises(J.BundleError, match=why):
            J.leader(bad)
        with pytest.raises(J.BundleError, match=why):
            J.behind(bad, _signed())
    tx = J.leader(_b64(_signed(3)))
    assert tx.verify_with_results() == [True]


def test_a_leader_signed_wrongly_is_refused_too():
    """Review of 23 Nov: only an unsigned leader was tried, so a check that merely refused all-zero signatures passed.
    Every signature is verified: a well-formed signature over other bytes, or by another key, is refused as well."""
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    good = bytearray(bytes(_signed(4)))
    good[-1] ^= 1  # one byte of the message changed after it was signed (the transfer's amount)
    kp, other = Keypair(), Keypair()
    ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=Keypair().pubkey(), lamports=5))
    msg = Message.new_with_blockhash([ix], kp.pubkey(), Hash.from_string(BLOCKHASH))
    signed_by_another = Transaction.new_unsigned(msg)
    signed_by_another.signatures = [other.sign_message(bytes(msg))]  # well-formed, by the wrong key
    for bad in (base64.b64encode(bytes(good)).decode(), _b64(signed_by_another)):
        with pytest.raises(J.BundleError, match="not signed"):
            J.leader(bad)
        with pytest.raises(J.BundleError, match="not signed"):
            J.behind(bad, _signed())
