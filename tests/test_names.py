"""The owner's wallet tracker (Axiom export, 10 Nov: 2,630 wallets, "a lot of them I js track for fun or are friends")
names many wallets of one team alike: 38 "Doji bundle", 31 "AtlasDrill", 46 "Faucet", "COMMUNITY TT 8" beside "COMMUNITY
Bundle 2". Counting such wallets as separate known wallets turns one team's bundle into "the second known wallet is in".
The name gives the wallet's kind (trader, sniper, dev, bundle ...) and its entity, the part of the name its wallets share."""

import pytest

from snipetech.analysis.names import category, entity


@pytest.mark.parametrize(
    "name, kind",
    [
        ("Me", "own"),
        ("MY NEW SNIPER", "own"),
        ("Faucet", "infra"),
        ("Coinbase", "infra"),
        ("Liquidity pool", "infra"),
        ("Doji bundle", "bundle"),
        ("COMMUNITY Bundle 2", "bundle"),
        ("KORI Dev", "dev"),
        ("Gud devver", "dev"),
        ("DEV DEPLOY", "dev"),
        ("Mintech Sniper 2", "sniper"),
        ("Frontrunner of Doom", "sniper"),
        ("CalmestScraper..", "sniper"),
        ("Stream Quant 2", "sniper"),
        ("Colewhrld Insider 3", "insider"),
        ("Irish Cabal 4", "insider"),
        ("TOM CRIME", "insider"),
        ("RUG 5", "insider"),
        ("ORANGE TT4", "top"),
        ("uranus TH 2", "top"),
        ("White Whale $2M", "whale"),
        ("XQ | Chinese KOL", "kol"),
        ("Cupsey Side 2", "trader"),
        ("1 | ohzarke side", "trader"),
        ("dv side", "trader"),  # "dv" is a person, not a dev
        ("Devistiny", "trader"),
        ("", "trader"),
    ],
)
def test_the_name_says_what_kind_of_wallet_it_is(name, kind):
    assert category(name) == kind


@pytest.mark.parametrize(
    "names",
    [
        ("Doji bundle", "Doji Bundle", "DOJI bundle", "Doji bunde", "Doji wallet"),
        ("AtlasDrill", "Atlasdrill"),
        ("COMMUNITY TT 8", "COMMUNITY Bundle 2", "COMMUNITY Dev", "COMMUNITY Insider", "Community Cabal"),
        ("1 | ohzarke side 2", "ohzarke side", "ohzarke Bags dev", "1 | ohzarke"),
        ("Irish Cabal", "Irish Cabal 3", "Irish Cabal Dev", "Irish Cabal Side"),
        ("noodles12", "Noodles13", "Noodles2"),
        ("White Whale $2M 2", "White Whale", "White Whale Side?"),
        ("Crime", "crime", "CRIME"),
        ("INSIDER", "Insider", "insider"),
        ("Faucet", "Faucet"),
        ("Cupsey Side 2", "Cupsey New", "Cupsey Dev", "cupsey bundle"),
        ("xet side", "xet dev wal", "xet"),
    ],
)
def test_one_teams_wallets_share_an_entity(names):
    assert len({entity(n) for n in names}) == 1 and entity(names[0])


def test_different_people_keep_different_entities():
    people = ["Cupsey", "Cented", "Orangie", "James Wynn", "james_amethyst", "Jack Duval", "jack curry?", "Gake",
              "Theo", "CryptoHawk 1", "CryptoD | Chinese KOL", "Cryptodegen2017", "KAPPA Dev", "COMMUNITY Dev"]  # fmt: skip
    assert len({entity(n) for n in people}) == len(people)


def test_a_nameless_wallet_is_its_own_entity():
    assert entity("", "Abc123") == "Abc123" and entity("...", "Xyz") == "Xyz" and entity("5", "W5") == "W5"
    assert entity("cand:Abc123", "Abc123") == "Abc123" and entity("found:YARSci", "YARSciFull") == "YARSciFull"


def test_the_owners_own_wallets_can_be_named_without_being_leaders(tmp_path):
    """10 Nov: the tracker export names the bot's own wallet "Me", and the server had it in the tracked list, so the
    labs counted our own buys as a known wallet's. Kind `own` names a wallet and puts it in no leader group."""
    from snipetech import cli
    from snipetech.store import Store

    from .synth import addr

    db = tmp_path / "snipetech.db"
    Store(db).close()
    assert cli.main(["--db", str(db), "label", addr("mine"), "Me", "--kind", "wallet"]) == 0
    assert cli.main(["--db", str(db), "label", addr("mine"), "Me", "--kind", "own"]) == 0
    st = Store(db)
    try:
        assert st.labels("own") == {addr("mine"): "Me"} and addr("mine") not in st.labels("wallet")
    finally:
        st.close()
