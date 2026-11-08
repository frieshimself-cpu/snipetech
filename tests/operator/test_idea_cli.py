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
