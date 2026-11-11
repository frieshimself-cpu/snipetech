"""`snipetech --help` and every command's own help must print. argparse formats a help text with `%`, so one
"60 % from" in a command's description took the whole help screen down (11 Nov: the `dip-buys` command, a day after
it shipped; nothing ran `--help`, so nothing noticed)."""

import argparse

import pytest

from snipetech.cli import build_parser


def _parsers(parser: argparse.ArgumentParser, name: str = "snipetech"):
    yield name, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub_name, sub in action.choices.items():
                yield from _parsers(sub, f"{name} {sub_name}")


PARSERS = dict(_parsers(build_parser()))


def test_there_are_commands_to_check():
    assert len(PARSERS) > 50  # the top-level help and each command's


@pytest.mark.parametrize("name", sorted(PARSERS))
def test_help_prints(name):
    assert PARSERS[name].format_help().startswith("usage:")
