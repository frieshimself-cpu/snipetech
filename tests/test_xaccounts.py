"""The X account behind a coin's link (owner, 10 Nov: "are there any other distinct features, like X is gold verified").
twitterapi.io answers one account per call for a fraction of a cent; the answer is kept, so an account is paid for once,
and a run never asks for more accounts than it was told."""

import asyncio

from snipetech import xaccounts as xa
from snipetech.store import Store

GOLD = {"status": "success", "msg": "success", "data": {
    "userName": "AcmeLabs", "isBlueVerified": True, "verifiedType": "Business", "followers": 48_200, "following": 12,
    "createdAt": "2021-03-04T10:00:00.000000Z", "statusesCount": 911}}  # fmt: skip
BLUE = {"status": "success", "msg": "success", "data": {
    "userName": "dev123", "isBlueVerified": True, "verifiedType": "", "followers": 340, "following": 900,
    "createdAt": "2026-11-01T00:00:00.000000Z", "statusesCount": 4}}  # fmt: skip
PLAIN = {"status": "success", "msg": "success", "data": {
    "userName": "nobody", "isBlueVerified": False, "verifiedType": None, "followers": 7, "following": 1,
    "createdAt": "2019-06-01T00:00:00.000000Z", "statusesCount": 20}}  # fmt: skip
GONE = {"status": "error", "msg": "user not found", "data": None}
SUSPENDED = {"status": "success", "msg": "success", "data": {
    "unavailable": True, "message": "User is suspended", "unavailableReason": "Suspended"}}  # fmt: skip


def test_an_answer_becomes_the_badge_the_followers_and_the_day_the_account_was_made():
    gold = xa.parse_user("AcmeLabs", GOLD)
    assert gold["handle"] == "acmelabs" and gold["found"] and gold["badge"] == "gold" and gold["followers"] == 48_200
    assert gold["created_ts"] == 1_614_852_000 and gold["statuses"] == 911
    assert xa.parse_user("dev123", BLUE)["badge"] == "blue" and xa.parse_user("nobody", PLAIN)["badge"] == "none"
    assert xa.parse_user("x", GOLD | {"data": GOLD["data"] | {"verifiedType": "Government"}})["badge"] == "grey"


def test_an_account_that_is_gone_or_suspended_is_kept_as_missing():
    gone, suspended = xa.parse_user("ghost", GONE), xa.parse_user("banned", SUSPENDED)
    assert not gone["found"] and gone["badge"] == "missing" and gone["followers"] is None
    assert not suspended["found"] and suspended["badge"] == "missing" and suspended["reason"] == "suspended"


def test_an_account_is_sorted_by_followers_and_by_its_age_when_the_coin_launched():
    gold, blue = xa.parse_user("AcmeLabs", GOLD), xa.parse_user("dev123", BLUE)
    launch = 1_794_300_000  # 10 Nov 2026
    assert xa.labels(gold, launch) == {"x_badge": "gold", "x_followers": "10k-100k", "x_age": "1y+"}
    assert xa.labels(blue, launch) == {"x_badge": "blue", "x_followers": "<1k", "x_age": "<30d"}
    assert xa.labels(xa.parse_user("ghost", GONE), launch) == {
        "x_badge": "missing", "x_followers": "missing", "x_age": "missing"}  # fmt: skip
    assert xa.labels(None, launch) == {"x_badge": "unknown", "x_followers": "unknown", "x_age": "unknown"}


class _Api:
    """Three accounts answer; the fourth call is out of credits."""

    def __init__(self):
        self.asked: list[str] = []

    async def user(self, handle):
        self.asked.append(handle)
        if len(self.asked) > 3:
            return 402, {"error": "no credits"}
        return 200, {"acmelabs": GOLD, "dev123": BLUE, "ghost": GONE}[handle]


def test_a_run_asks_only_for_accounts_not_kept_yet_and_never_more_than_its_limit(tmp_path):
    store = Store(tmp_path / "t.db")
    api = _Api()
    out = asyncio.run(xa.fetch_accounts(api, store, ["AcmeLabs", "dev123", "ghost", "acmelabs"], max_calls=2))
    assert (
        api.asked == ["acmelabs", "dev123"] and out["asked"] == 2 and out["stopped"] == "the run's limit of 2 accounts"
    )
    again = asyncio.run(xa.fetch_accounts(api, store, ["acmelabs", "dev123", "ghost", "late"], max_calls=10))
    assert api.asked == ["acmelabs", "dev123", "ghost", "late"]  # the two kept ones were not paid for again
    assert again["asked"] == 1 and "402" in again["stopped"]
    kept = store.x_accounts(["acmelabs", "dev123", "ghost", "late"])
    assert set(kept) == {"acmelabs", "dev123", "ghost"} and kept["acmelabs"]["badge"] == "gold"
    assert kept["ghost"]["badge"] == "missing"  # a missing account is an answer too, and is not asked for again
    store.close()


def test_the_handle_of_a_coin_is_the_account_its_link_names():
    assert xa.handle_of({"ok": True, "x_kind": "profile", "x_handle": "acmelabs"}) == "acmelabs"
    assert xa.handle_of({"ok": True, "x_kind": "tweet", "x_handle": "dev123"}) == "dev123"
    assert xa.handle_of({"ok": True, "x_kind": "community", "x_community": "1"}) is None
    assert xa.handle_of(None) is None and xa.handle_of({"ok": False}) is None
