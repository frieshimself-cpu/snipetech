"""Token metadata parsing: X link kinds, tweet creation time, launch tool, IPFS routing."""

import datetime

from snipetech.meta import IPFS_GATEWAYS, parse_socials, resolve_uri, tweet_time


def test_tweet_id_encodes_its_creation_time():
    # a real status id from a launch's metadata (posted 2026-10-28 00:12:47 UTC)
    ts = tweet_time("2115235291325698133")
    assert datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d %H:%M:%S") == "2026-10-28 00:12:47"


def test_x_link_kinds_and_platform():
    real = {"createdOn": "pump.fun", "description": "Deployed using https://j7tracker.io",
            "twitter": "https://x.com/gettindevvy/status/2115235291325698133", "website": ""}  # fmt: skip
    s = parse_socials(real, "https://metadata.j7tracker.io/metadata/e6c350511bf34aeb.json")
    assert s["x_kind"] == "tweet" and s["x_handle"] == "gettindevvy" and s["tweet_id"] == "2115235291325698133"
    assert s["platform"] == "j7tracker.io" and s["website"] == "" and s["tweet_ts"] > 1.7e9
    assert parse_socials({"twitter": "https://x.com/i/communities/1912"})["x_kind"] == "community"
    assert parse_socials({"twitter": "https://twitter.com/SomeDev"})["x_handle"] == "somedev"
    plain = parse_socials({"name": "MIMI", "createdOn": "https://pump.fun"}, "https://ipfs.io/ipfs/QmUqdoC1fJwipxwH1bnwxazNrCJG1NtxpTf1XQtryvrJqm")
    assert plain["x_kind"] is None and plain["platform"] == "pump.fun"
    assert parse_socials({}, "https://meta.uxento.io/x.json")["platform"] == "uxento.io"


def test_ipfs_uris_go_through_working_gateways():
    cid = "bafkreieilrbztyntumtifzerr2runt74c36zdizhfhnsq43pefk5zl3k2u"
    assert resolve_uri(f"https://ipfs.io/ipfs/{cid}") == [g + cid for g in IPFS_GATEWAYS]
    assert resolve_uri(f"ipfs://{cid}")[0].endswith(cid)
    assert resolve_uri("https://metadata.j7tracker.io/metadata/x.json") == ["https://metadata.j7tracker.io/metadata/x.json"]


def test_a_coin_whose_metadata_address_is_not_a_web_address_is_kept_as_unreadable_not_a_crash():
    """10 Nov: a scan of 16 days of bought history ended on one coin's uri with a second "https:" where the port
    goes (httpx.InvalidURL is not an HTTPError); 25 minutes of reading were lost. Creators type anything there."""
    import asyncio

    from snipetech.meta import fetch_meta
    from snipetech.store import Store

    st = Store(":memory:")
    try:
        bad = [("M1", "https://ipfs.io:https://x/meta.json"), ("M2", "http://[not-an-address/meta.json")]
        assert asyncio.run(fetch_meta(st, bad)) == 2
        assert st.token_meta("M1")["ok"] is False and st.token_meta("M2")["ok"] is False
    finally:
        st.close()
