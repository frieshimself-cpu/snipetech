"""The cloud session hands the server's agents a piece of research as an idea (operator/ideas.json): the agents run
it on the owner's Anthropic API credits, not on the cloud session's usage limit (owner, 8 Nov 20:40)."""

import pytest

from snipetech import cli
from snipetech.operator.learning import list_ideas
from snipetech.operator.paths import OperatorPaths


def _cli(tmp_path, *args):
    return cli.main(["--db", str(tmp_path / "data" / "snipetech.db"), "operate", *args])


def test_operate_idea_adds_an_open_research_idea_the_agents_read(tmp_path, capsys):
    rc = _cli(tmp_path, "idea", "--title", "Fees to land ahead of other copy traders", "--why", "we land last in the slot",
              "--test", "read the in-slot auction with sql", "--expected", "a per-leader bid or a no", "--kind",
              "research", "--priority", "5")  # fmt: skip
    assert rc == 0
    ideas = list_ideas(OperatorPaths(tmp_path / "data"))
    assert len(ideas) == 1 and ideas[0]["title"] == "Fees to land ahead of other copy traders"
    assert ideas[0]["status"] == "open" and ideas[0]["priority"] == 5 and ideas[0]["kind"] == "research"
    assert ideas[0]["id"] in capsys.readouterr().out


def test_operate_idea_refuses_an_incomplete_idea(tmp_path):
    with pytest.raises(SystemExit, match="not added"):
        _cli(tmp_path, "idea", "--title", "half an idea")
    assert list_ideas(OperatorPaths(tmp_path / "data")) == []


def test_operate_idea_updates_an_idea_by_id_with_its_status(tmp_path):
    _cli(tmp_path, "idea", "--title", "Cap", "--why", "w", "--test", "t", "--expected", "e", "--kind", "research")
    (idea,) = list_ideas(OperatorPaths(tmp_path / "data"))
    assert _cli(tmp_path, "idea", "--id", idea["id"], "--status", "adopted", "--why", "owner's yes 21:05") == 0
    (after,) = list_ideas(OperatorPaths(tmp_path / "data"), status=None)
    assert after["status"] == "adopted" and after["why"] == "owner's yes 21:05" and after["title"] == "Cap"


def test_idea_cli_sets_the_role(tmp_path):
    """The cloud session puts an idea in a role's queue (`operate idea --role scout`), and moves an old one there."""
    from snipetech.operator.learning import role_of

    _cli(tmp_path, "idea", "--title", "Who copies Cupsey", "--why", "w", "--test", "t", "--expected", "e", "--kind",
         "research", "--role", "scout")  # fmt: skip
    _cli(tmp_path, "idea", "--title", "Old", "--why", "w", "--test", "t", "--expected", "e", "--kind", "research")
    # in the order they came: the open list puts the newer idea first whenever the two fall into different seconds
    # (14 Nov: this failed once in a full run for that reason alone)
    scout, old = list_ideas(OperatorPaths(tmp_path / "data"), status=None)
    assert role_of(scout) == "scout" and role_of(old) == "tester"
    assert _cli(tmp_path, "idea", "--id", old["id"], "--role", "fees") == 0
    assert [role_of(i) for i in list_ideas(OperatorPaths(tmp_path / "data"), status=None)] == ["scout", "fees"]
    with pytest.raises(SystemExit):
        _cli(tmp_path, "idea", "--id", old["id"], "--role", "analyst")


def test_operate_idea_closes_an_idea_with_its_result_and_says_what_was_cut(tmp_path, capsys):
    """An idea is closed with what its test found (`--result`); a text over its cap is cut there and kept, and the
    command's answer names the field and how long it was, so the cloud session sees that it was cut."""
    import json

    _cli(tmp_path, "idea", "--title", "Cap", "--why", "w", "--test", "lab over 72 h", "--expected", "e", "--kind",
         "research")  # fmt: skip
    (idea,) = list_ideas(OperatorPaths(tmp_path / "data"))
    capsys.readouterr()
    assert _cli(tmp_path, "idea", "--id", idea["id"], "--status", "rejected", "--result",
                "lower -0.021 over 133 copies (job 6c1b82)") == 0  # fmt: skip
    (after,) = list_ideas(OperatorPaths(tmp_path / "data"), status=None)
    assert after["status"] == "rejected" and after["result"] == "lower -0.021 over 133 copies (job 6c1b82)"
    assert after["test"] == "lab over 72 h" and "cut" not in json.loads(capsys.readouterr().out)
    assert _cli(tmp_path, "idea", "--id", idea["id"], "--test", "the same lab at +0 and at +6; " * 20) == 0
    assert json.loads(capsys.readouterr().out)["cut"] == {"test": 599}
    (after,) = list_ideas(OperatorPaths(tmp_path / "data"), status=None)
    assert len(after["test"]) <= 400 and after["test"].endswith("…")
