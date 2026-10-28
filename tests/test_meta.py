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
