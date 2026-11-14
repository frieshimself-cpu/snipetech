"""What the agents' tools take and give back (14 Nov 2026). On 108 paid turns of the research agents the tools made
them write and read far more text than the work needed: every turn sent its whole table again (5,500 characters, a
third of it new), a one-line JSON job log came back whole whatever was asked (two answers of 160,000 characters), the
idea list was 8,000 characters a call, 57 idea writes were refused for a field a few characters too long and written
again, and every write was echoed back. These tests hold the shorter forms, and that a shorter answer always says how
to read the rest."""

import json
import os
import re

import pytest

from snipetech.operator import learning
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.tools import ANSWER_CHARS, read_job_output

from . import test_jobs_tools as _base
from . import test_turn as _turn

_call, _queue = _base._call, _base._queue
jobs, rolebox, toolbox = _base.jobs, _base.rolebox, _base.toolbox  # the same fixtures as the tools' other tests
env = _turn.env  # a whole turn against a scripted model, as the turn's own tests run one

NOW = 1_793_923_200  # the tools' clock in these fixtures: 2026-11-06 00:00 UTC

TABLE = (
    "# Who is worth following (wallet scout), 14 Nov 08:05 UTC\n"
    "\n"
    "Two wallets clear every gate. New 08:05: I170 closed.\n"
    "\n"
    "| Wallet | Who, kind | Qualify (+3x) |\n"
    "|---|---|---|\n"
    "| FLPjyS: QUALIFIED | unnamed, early herd buyer | +0.0204 / 107 |\n"
    "| **AtifzK** | hand trader | candidate +0.0863 / 20 |\n"
    "| HjuSth | scalper | stopped by crowding |\n"
    "\n"
    "In one line: nothing is copyable yet.\n"
    "\n"
    "| Wallet | Finding |\n"
    "|---|---|\n"
    "| YJFQNK | 8 of 39 buys on LaunchLab |\n"
)
TITLE_NOW = "# Who is worth following (wallet scout), updated 6 Nov 00:00 UTC"


def _table(paths, text=TABLE, name="scout-facts"):
    """The role's table as an earlier turn left it, an hour old."""
    p = paths.root / f"{name}.md"
    p.write_text(text)
    os.utime(p, (NOW - 3600, NOW - 3600))
    return p


# -- the table by rows -------------------------------------------------------------------------------------------------


def test_update_table_changes_one_row_and_keeps_the_rest_byte_for_byte(rolebox):
    """A role sent its whole table every turn, 5,500 characters of which a third was new. It now sends the rows that
    changed: the key is a row's first cell, the row with that key is replaced, every other line stays as it was, and
    the clock in the title is the code's."""
    tools, paths, _ = rolebox
    p = _table(paths)
    out = _call(tools("scout")["update_table"],
                rows=[{"key": "AtifzK", "text": "| **AtifzK** | hand trader | candidate +0.09 / 25 |"}])  # fmt: skip
    old, new = TABLE.split("\n"), p.read_text().split("\n")
    assert len(new) == len(old) and new[0] == TITLE_NOW
    changed = [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]
    assert changed == [0, 7] and new[7] == "| **AtifzK** | hand trader | candidate +0.09 / 25 |"
    assert "\n" not in out and out.startswith("scout-facts.md: ") and "replaced 1 (AtifzK)" in out
    assert f"{len(p.read_text()):,} characters" in out and "4 rows" in out
    assert p.stat().st_mtime > NOW - 60  # written: the other roles see that the table changed


def test_update_table_without_a_change_does_not_touch_the_file(rolebox):
    """The file's time is what calls the other roles' turns ("the scout table changed"). A call that changes no row
    and no paragraph writes nothing: not even the title's clock moves."""
    tools, paths, _ = rolebox
    p = _table(paths)
    stamp = p.stat().st_mtime_ns
    out = _call(tools("scout")["update_table"],
                rows=[{"key": "HjuSth", "text": "| HjuSth | scalper | stopped by crowding |"}],
                above="Two wallets clear every gate. New 08:05: I170 closed.")  # fmt: skip
    assert p.read_text() == TABLE and p.stat().st_mtime_ns == stamp
    assert "\n" not in out and "not written" in out and "characters" in out
    assert "error" not in _call(tools("scout")["update_table"])  # nothing asked: nothing written, no refusal
    assert p.read_text() == TABLE and p.stat().st_mtime_ns == stamp


def test_a_new_key_is_added_at_the_end_of_the_table_or_behind_the_row_named(rolebox):
    """A key no row has is a new row. It goes to the end of the first table, so the order the role chose stays; with
    `after` it goes right behind that row, which is also how a row gets into a second table of the file."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    out = _call(update, rows=[{"key": "9L5aWu", "text": "| 9L5aWu | fresh wallet | candidate, n 18 |"}])
    lines = p.read_text().split("\n")
    assert lines[9] == "| 9L5aWu | fresh wallet | candidate, n 18 |" and lines[8].startswith("| HjuSth")
    assert lines[10] == "" and lines[11] == "In one line: nothing is copyable yet." and "added 1 (9L5aWu)" in out
    twin = {"key": "i3Sj7y", "text": "| i3Sj7y | FLPjyS's twin | candidate |", "after": "FLPjyS: QUALIFIED"}
    out = _call(update, rows=[twin, {"key": "ANTUPU", "text": "| ANTUPU | no stop scored |", "after": "YJFQNK"}])
    lines = p.read_text().split("\n")
    assert lines[6].startswith("| FLPjyS") and lines[7] == "| i3Sj7y | FLPjyS's twin | candidate |"
    assert lines[-3:] == ["| YJFQNK | 8 of 39 buys on LaunchLab |", "| ANTUPU | no stop scored |", ""]
    assert "added 2 (i3Sj7y; ANTUPU)" in out
    # the row's cells without its key are a row too: the key is put in front (the table has three columns)
    _call(update, rows=[{"key": "Byn68j", "text": "Binance-funded | forward 8 of 20"}])
    assert "| Byn68j | Binance-funded | forward 8 of 20 |" in p.read_text().split("\n")
    # and a key is the first cell however it is dressed: bold, spaces, capitals
    out = _call(update, rows=[{"key": "atifzk", "text": "| **AtifzK** | hand trader | retired |"}])
    assert "replaced 1" in out and p.read_text().count("AtifzK") == 1


def test_update_table_drops_rows_and_replaces_the_paragraphs(rolebox):
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    # round 2 (14 Nov): the paragraph above the table is `above` (a headline is the journal's note), and a drop of a
    # row that is not there is an answer of its own, behind "error" (test_what_update_table_did_not_do_...)
    out = _call(update, drop=["HjuSth"], above="One wallet clears every gate.", below="In one line: FLPjyS only.")
    assert p.read_text() == (TABLE.replace("| HjuSth | scalper | stopped by crowding |\n", "")
                             .replace("Two wallets clear every gate. New 08:05: I170 closed.",
                                      "One wallet clears every gate.")
                             .replace("In one line: nothing is copyable yet.", "In one line: FLPjyS only.")
                             .replace(TABLE.split("\n")[0], TITLE_NOW))  # fmt: skip
    assert out.startswith("scout-facts.md: dropped 1 (HjuSth); the paragraph above replaced; the paragraph below "
                          "replaced; ") and "\n" not in out  # fmt: skip


def test_a_row_over_the_cap_is_cut_and_the_answer_says_so(rolebox):
    """A row is one subject: over 1,200 characters it is cut there and kept (a refusal makes the model write all of it
    again), and the answer says which row and how long it was. So is a paragraph."""
    from snipetech.operator.tools import TABLE_ROW_CHARS

    tools, paths, _ = rolebox
    p = _table(paths)
    long = "| HjuSth | scalper | " + "seven days plus 65.8 SOL over 45 coins; " * 40 + "|"
    out = _call(tools("scout")["update_table"], rows=[{"key": "HjuSth", "text": long}])
    row = next(line for line in p.read_text().split("\n") if line.startswith("| HjuSth"))
    assert len(row) == TABLE_ROW_CHARS == 1200 and row.endswith("… |") and row.startswith(long[:1100])
    assert "cut at 1,200 characters: HjuSth" in out and f"({len(long):,})" in out and not out.startswith("error")
    out = _call(tools("scout")["update_table"], above="No wallet is copyable yet. " * 60)
    head = p.read_text().split("\n")[2]
    assert len(head) <= 1200 and head.endswith("…")
    assert "the paragraph above cut at 1,200 characters (it was 1,619)" in out


def test_a_role_cannot_touch_another_roles_table_through_update_table(rolebox):
    """The tool names no file: it writes the table of the role whose turn it is and nothing else. The analyst, which
    keeps no table, has no such tool."""
    tools, paths, _ = rolebox
    _table(paths)
    theirs = _table(paths, name="tester-rules")
    update = tools("scout")["update_table"]
    assert not {"name", "table", "path", "role"} & set(update.to_dict()["input_schema"]["properties"])
    with pytest.raises(ValueError, match="Invalid arguments"):  # no such argument: the call never runs
        _call(update, name="tester-rules", rows=[{"key": "HjuSth", "text": "| HjuSth | x | y |"}])
    _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth | x | y |"}])
    assert theirs.read_text() == TABLE and theirs.stat().st_mtime == NOW - 3600
    assert sorted(f.name for f in paths.root.glob("*.md")) == ["scout-facts.md", "tester-rules.md"]
    _call(tools("tester")["update_table"], drop=["HjuSth"])
    assert "HjuSth" not in theirs.read_text() and "| HjuSth | x | y |" in (paths.root / "scout-facts.md").read_text()
    assert "update_table" not in tools()
    for role in ("scout", "tester", "fees", "oktech"):
        assert "update_table" in tools(role)


def test_the_first_write_of_a_table_and_a_rebuild_go_whole(rolebox):
    """Rows need a table to go into. A table that does not exist yet is written whole (text); so is a rebuild. The
    title's clock is the code's there too, and a table that says the same but for that clock is not written again.
    Round 2 (14 Nov): a whole table sent without whole=true is taken, not refused (the refusal made the turn write
    it twice), and a text that is no whole table is taken as rows: no table is replaced by a fragment."""
    tools, paths, _ = rolebox
    update = tools("scout")["update_table"]
    p = paths.root / "scout-facts.md"
    out = _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth | x | y |"}])
    assert out.startswith("error") and "text" in out and "\n" not in out and not p.exists()
    out = _call(update, text=TABLE)
    assert p.read_text() == TABLE.replace(TABLE.split("\n")[0], TITLE_NOW)
    assert out.startswith("scout-facts.md: written whole") and "4 rows" in out and "\n" not in out
    os.utime(p, (NOW - 3600, NOW - 3600))
    other = TABLE.replace("| HjuSth | scalper | stopped by crowding |\n", "")
    out = _call(update, text="| HjuSth | scalper | retired |")
    assert "taken as rows" in out and "| FLPjyS: QUALIFIED |" in p.read_text() and "retired" in p.read_text()
    _call(update, text=TABLE, whole=True)
    os.utime(p, (NOW - 3600, NOW - 3600))
    assert _call(update, text=TABLE.replace("08:05 UTC", "09:20 UTC"), whole=True).count("not written") == 1
    assert p.stat().st_mtime == NOW - 3600  # the same table with another clock in its title: nothing moved
    out = _call(update, text=other, whole=True)
    assert "written whole" in out and "HjuSth" not in p.read_text() and p.stat().st_mtime > NOW - 60
    # a table with no title line gets none
    _call(tools("fees")["update_table"], text="| place | bid |\n|---|---|\n| +0 | 0.05 |")
    assert (paths.root / "fees-bids.md").read_text() == "| place | bid |\n|---|---|\n| +0 | 0.05 |"


def test_a_table_past_what_a_turn_carries_is_said(rolebox):
    """A turn's message carries the first 6,000 characters of the role's own table: the answer says when it is past."""
    tools, paths, _ = rolebox
    _table(paths)
    update = tools("scout")["update_table"]
    rows = [{"key": f"W{i}", "text": f"| W{i} | " + "a wallet with a long story; " * 12 + "| candidate |"}
            for i in range(20)]  # fmt: skip
    out = _call(update, rows=rows)
    assert "added 20" in out and "6,000" in out and "\n" not in out
    assert "6,000" not in _call(update, drop=[f"W{i}" for i in range(20)])


def test_write_report_still_writes_the_table_whole_and_says_how_to_change_rows(rolebox):
    """write_report wrote the table whole until 14 Nov, and the agents' instructions said so. It still does, so that
    a turn that sends its table the old way loses nothing and writes nothing twice (a refusal here would cost the
    whole table again); the answer is one line and says that rows go through update_table. The title's clock is
    the code's there too, and a table that says the same is not written again."""
    tools, paths, _ = rolebox
    report = tools("scout")["write_report"]
    assert _call(report, name="scout-facts", text=TABLE).startswith("scout-facts.md: written whole")
    p = paths.root / "scout-facts.md"
    assert p.read_text() == TABLE.replace(TABLE.split("\n")[0], TITLE_NOW)
    os.utime(p, (NOW - 3600, NOW - 3600))
    out = _call(report, name="scout-facts", text=TABLE.replace("08:05 UTC", "09:20 UTC"))
    assert "not written again" in out and p.stat().st_mtime == NOW - 3600  # only the clock differed
    out = _call(report, name="scout-facts", text=TABLE.replace("HjuSth", "HjuSth2"))
    assert out.startswith("scout-facts.md: written whole") and "update_table" in out and "\n" not in out
    assert "HjuSth2" in p.read_text() and p.stat().st_mtime > NOW - 60
    assert _call(report, name="scout-journal-2026-11-06", text="looked at winners", append=True) == (
        "wrote scout-journal-2026-11-06.md"
    )
    assert _call(report, name="scout-journal-2026-11-06", text="and at playbook", append=True).startswith("appended")


# -- reading a job's output --------------------------------------------------------------------------------------------


def _lab_result():
    return {
        "ok": True, "tape_hours": 137.1, "moments": 1786,
        "strategies": [{"trigger": f"mig w{w}", "exit": "stop -20 %", "test": {"n": 367 - w, "mean": -0.0151}}
                       for w in (60, 20)],
        "wallets": [{"wallet": f"W{i:03d}" + "x" * 38, "name": f"name {i}", "summary": {"pnl_sol": i / 10, "trips": i},
                     "trades": [{"mint": f"coin{j}", "sol": j} for j in range(30)]} for i in range(120)],
    }  # fmt: skip


def _one_line_log(paths, jid="gmgn1"):
    """A job's output as `gmgn ... --json` prints it: the whole result on ONE line."""
    out = _lab_result()
    text = json.dumps(out)
    (paths.jobs / f"{jid}.log").write_text(text + "\n")
    assert "\n" not in text and len(text) > 100_000
    return out, text


def test_a_one_line_json_log_is_read_from_the_top_and_takes_a_key(toolbox):
    """`gmgn wallet --json` prints its result on one line. The reader took a JSON result only when its last bracket
    stood on a line of its own, so such a log came back WHOLE whatever `tail` said (25 answers, 703,000 characters,
    two of 160,000) and `key=` answered "this log is not one JSON object". It is read like any JSON result."""
    tools, paths, _ = toolbox
    out, _ = _one_line_log(paths)
    got = _call(tools["read_job_log"], id="gmgn1")
    assert got.splitlines()[0].startswith("one JSON object") and '"moments": 1786' in got
    assert 'key="wallets"' in got and "120 items" in got and len(got) < 6000
    assert json.loads(_call(tools["read_job_log"], id="gmgn1", key="strategies.1")) == out["strategies"][1]
    assert json.loads(_call(tools["read_job_log"], id="gmgn1", key="wallets.7.summary.trips")) == 7
    assert "W007" in _call(tools["read_job_log"], id="gmgn1", key="wallets.7", tail=3)


def test_tail_on_a_one_line_json_log_never_returns_the_whole_line(toolbox):
    tools, paths, _ = toolbox
    _, text = _one_line_log(paths)
    for tail in (5, 40, 120, 6100):
        got = _call(tools["read_job_log"], id="gmgn1", tail=tail)
        assert len(got) <= ANSWER_CHARS < len(text) and text[:5000] not in got, tail
        assert got.splitlines()[0].startswith("one JSON object"), tail  # the outline, or the pretty form's lines
    short = _call(tools["read_job_log"], id="gmgn1", tail=5).splitlines()
    assert len(short) <= 5 + 5 and any('key="wallets"' in line for line in short)
    part = _call(tools["read_job_log"], id="gmgn1", key="wallets.3", tail=8).splitlines()
    assert part[1] == "{" and part[2].startswith(' "wallet": "W003') and len(part) == 10


def _read_on(tool, cap=ANSWER_CHARS, **kw):
    """Every answer's body, reading on with what each answer's last line says until nothing is left."""
    bodies, turns = [], 0
    while True:
        got = _call(tool, **kw)
        assert len(got) <= cap, len(got)
        lines = got.splitlines()
        more = lines[-1].startswith("… ") and re.search(r"read on with (.*)", lines[-1])
        assert more or turns, "the first answer was the whole: nothing to read on"
        bodies.append(lines[1 : -1 if more else None])  # the first line says how big the whole is
        if not more:
            return bodies
        said = more.group(1)
        for name in ("skip", "first"):
            m = re.search(rf"\b{name}=(\d+)", said)
            if m:
                kw[name] = int(m.group(1))
        turns += 1
        assert turns < 200


def test_an_answer_over_the_cap_is_cut_and_reading_on_returns_the_rest(toolbox):
    """Two answers were 160,000 characters each (`key="wallets"` with a tail of 6,000 lines). No answer is longer than
    12,000 characters now: it is cut at a line or an item, its first line says how big the whole is and its last line
    how to read on. Reading on with exactly what it says, the pieces are the whole: nothing became unreadable."""
    tools, paths, _ = toolbox
    read = tools["read_job_log"]
    out, _ = _one_line_log(paths)
    # a part of a JSON result, by lines
    whole = json.dumps(out["wallets"], indent=1).splitlines()
    first = _call(read, id="gmgn1", key="wallets", tail=6100)
    assert len(first) <= ANSWER_CHARS and re.match(r'"wallets": 120 items, [\d,]+ lines, [\d,]+ characters', first)
    assert f"{len(whole):,} lines" in first.splitlines()[0]
    assert 'key="wallets"' in first.splitlines()[-1] and "skip=" in first.splitlines()[-1]
    pieces = _read_on(read, id="gmgn1", key="wallets", tail=6100)
    assert len(pieces) > 5 and [line for piece in pieces for line in piece] == whole
    assert json.loads("\n".join(line for piece in pieces for line in piece)) == out["wallets"]
    # the same with a short tail: the line limit cuts before the cap does
    pieces = _read_on(read, id="gmgn1", key="wallets.0", tail=20)
    assert json.loads("\n".join(line for piece in pieces for line in piece)) == out["wallets"][0]
    assert all(len(piece) <= 20 for piece in pieces)
    # a plain log is read from its end: the earlier lines come next
    lines = [f"{i:05d} scoring coin {i} of 3000, " + "x" * 40 for i in range(3000)]
    (paths.jobs / "plain.log").write_text("\n".join(lines) + "\n")
    got = _call(read, id="plain", tail=5000)
    assert len(got) <= ANSWER_CHARS and got.splitlines()[-2] == lines[-1] and "3,000" in got.splitlines()[0]
    pieces = _read_on(read, id="plain", tail=5000)
    assert [line for piece in reversed(pieces) for line in piece] == lines
    # a list by items, one line each
    pieces = _read_on(read, id="gmgn1", key="wallets", fields=["wallet", "trades"])
    rows = [line for piece in pieces for line in piece]
    assert len(rows) == 120 and len(pieces) > 1
    for i, row in enumerate(rows):
        n, _, item = row.partition(" ")
        assert n == f"[{i}]" and json.loads(item) == {"wallet": out["wallets"][i]["wallet"],
                                                      "trades": out["wallets"][i]["trades"]}  # fmt: skip


def test_fields_of_a_list_of_objects_come_one_line_an_item(toolbox):
    """What a role wants of a list of 120 wallets is two or three numbers of each. `fields` gives exactly those, one
    compact line an item (a dotted name reads a part inside a part); `first` and `last` a range of items."""
    tools, paths, _ = toolbox
    read = tools["read_job_log"]
    out, _ = _one_line_log(paths)
    got = _call(read, id="gmgn1", key="wallets", fields=["name", "summary.pnl_sol"]).splitlines()
    assert got[0].startswith('"wallets": 120 items') and "name, summary.pnl_sol" in got[0] and len(got) == 121
    assert got[1] == '[0] {"name":"name 0","summary.pnl_sol":0.0}'
    assert got[120] == '[119] {"name":"name 119","summary.pnl_sol":11.9}'
    part = _call(read, id="gmgn1", key="wallets", fields=["name"], first=10, last=12).splitlines()
    assert part[1:] == ['[10] {"name":"name 10"}', '[11] {"name":"name 11"}', '[12] {"name":"name 12"}']
    assert "10-12 of 120" in part[0]
    # a range without fields is those items, whole
    two = _call(read, id="gmgn1", key="strategies", first=1, last=1)
    assert json.loads(two[two.index("\n") + 1 :]) == [out["strategies"][1]] and "1-1 of 2" in two.splitlines()[0]
    # a field that is itself long is named with the key that reads it, not printed
    wide = _call(read, id="gmgn1", fields=["moments", "wallets", "strategies.0.test"]).splitlines()
    assert '"moments": 1786' in wide and '"strategies.0.test": {"n":307,"mean":-0.0151}' in wide
    assert any(line.startswith('"wallets": ') and 'key="wallets"' in line and "120 items" in line for line in wide)
    # what cannot be done is said in one line, with what the place holds
    assert _call(read, id="gmgn1", key="tape_hours", first=2).startswith("error")
    assert 'no "nope"' in _call(read, id="gmgn1", key="wallets.0", fields=["nope", "name"])


def test_a_log_that_is_one_long_line_of_no_json_is_folded_and_never_over_the_cap(toolbox):
    """A job killed mid-print leaves half a JSON line. It is no JSON result; its one line is folded so that it still
    comes in pieces a turn can read on through."""
    tools, paths, _ = toolbox
    _, text = _one_line_log(paths)
    broken = text[:50_000]
    (paths.jobs / "killed.log").write_text(broken + "\nTerminated\n")
    got = _call(tools["read_job_log"], id="killed")
    assert len(got) <= ANSWER_CHARS and got.splitlines()[-2] == "Terminated" and "skip=" in got.splitlines()[-1]
    pieces = _read_on(tools["read_job_log"], id="killed")
    assert "".join(line for piece in reversed(pieces) for line in piece) == broken + "Terminated"
    assert _call(tools["read_job_log"], id="killed", key="wallets").startswith("error: this log is not one JSON")


def test_read_job_output_answers_within_the_cap_whatever_is_asked():
    out = _lab_result() | {"note": "n" * 30_000, "many": {f"k{i}": {"a": i, "b": [i] * 3} for i in range(900)}}
    text = json.dumps(out)
    for kw in ({}, {"tail": 5}, {"tail": 100_000}, {"key": "note"}, {"key": "many", "tail": 100_000},
               {"key": "wallets", "fields": ["trades", "summary", "name", "wallet"], "tail": 100_000},
               {"fields": ["note", "many", "wallets"]}, {"key": "wallets", "first": 3, "tail": 100_000}):  # fmt: skip
        got = read_job_output(text, **kw)
        assert len(got) <= ANSWER_CHARS and not got.startswith("error"), kw


# -- ideas: one line each, a field over its cap is cut and kept, a result of its own, no echo --------------------------


IDEA = {"title": "Copy only leader buys of 0.5 SOL or more", "why": "7 of 9 losses copied buys under 0.3 SOL (n 9)",
        "test": "lab run --by-wallet with min_copy_sol 0.5 on the last 72 h", "expected": "+0.004 SOL a trade",
        "kind": "research"}  # fmt: skip


def test_list_ideas_is_one_line_an_idea_unless_one_is_asked_whole(rolebox, jobs):
    """An idea is about 1,250 characters of JSON and a list was 8,000 characters a call (67 calls). The list is one
    line an idea: id, status, priority, queue, title and the start of its test; one idea, or all of them, comes whole
    when asked."""
    tools, _, _ = rolebox
    long = {"why": "w " * 250, "test": "lab with --copy-position +0 and --per-tx at the bid place-bids names; " * 5}
    mine = _queue(jobs, "scout", "who copies Cupsey", priority=5, **long)
    other = _queue(jobs, "scout", "hunt wallets", **long)
    theirs = _queue(jobs, "tester", "score a rule", **long)
    scout = tools("scout")
    got = _call(scout["list_ideas"]).splitlines()
    assert len(got) == 3 and all(len(line) <= 200 for line in got)
    assert got[0].startswith("queue scout: 2 open") and 'id="' in got[0] and "full=true" in got[0]
    assert got[1].startswith(f"{mine} [open, priority 5, scout] who copies Cupsey. Test: lab with --copy-position +0")
    assert got[2].startswith(f"{other} [open, priority 3, scout] hunt wallets.") and got[2].endswith("…")
    assert _call(scout["list_ideas"], queue="tester").splitlines()[1].startswith(f"{theirs} [open, priority 3, tester]")
    assert len(_call(scout["list_ideas"], queue="all").splitlines()) == 4
    whole = json.loads(_call(scout["list_ideas"], id=theirs))
    assert whole["ideas"][0]["test"] == " ".join(long["test"].split()) and whole["ideas"][0]["queue"] == "tester"
    two = json.loads(_call(scout["list_ideas"], id=f"{mine}, {theirs}, I999"))
    assert [i["id"] for i in two["ideas"]] == [mine, theirs] and two["unknown"] == ["I999"]
    full = json.loads(_call(scout["list_ideas"], full=True))
    assert [i["id"] for i in full["ideas"]] == [mine, other] and full["counts"] == {"open": 2}
    assert full["ideas"][0]["why"] == " ".join(long["why"].split())
    # the analyst's list is the same shape, with no queue
    analyst = tools()["list_ideas"]
    assert _call(analyst).splitlines()[0].startswith("3 open") and len(_call(analyst).splitlines()) == 4
    assert len(json.loads(_call(analyst, full=True))["ideas"]) == 3


def test_an_idea_field_over_its_cap_is_cut_and_kept(rolebox, jobs):
    """59 of 207 idea writes were refused, 57 for a field over its length: 41 times a test over 400 characters. The
    text was written, refused and written again. It is cut at the cap and kept, and the answer says which field and by
    how much. Round 2 (14 Nov): it says how many of the text's last characters are gone, and gives no advice."""
    tools, paths, _ = rolebox
    test = "lab at the bid place-bids names, then the same at +0 and at +6; " * 9  # 575 characters as stored
    why = "7 of 9 losses copied buys under 0.3 SOL (n 9); " * 14  # 657
    out = _call(tools("scout")["upsert_idea"], **(IDEA | {"test": test, "why": why}))
    assert not out.startswith("error") and "\n" not in out
    (idea,) = learning.list_ideas(paths, "all")
    assert 390 < len(idea["test"]) <= 400 and idea["test"].endswith("…") and idea["test"][:390] == test[:390]
    assert len(idea["why"]) <= 600 and idea["why"].endswith("…") and out.startswith(f"{idea['id']} created")
    assert "test was cut at 400 characters: its last " in out and "why was cut at 600 characters: its last " in out
    # the library says the same to its other callers
    done = learning.upsert_idea_detail(paths, {"id": idea["id"], "expected": "+0.004 SOL " * 30}, source="cloud")
    assert done["cut"] == {"expected": 329} and done["changed"] == ["expected"] and not done["created"]
    assert len(done["idea"]["expected"]) <= 300 and done["idea"]["expected"].endswith("…")
    # a secret is still refused, and so is an empty field: nothing is kept of those
    assert _call(tools("scout")["upsert_idea"], id=idea["id"], why="key sk-ant-api03-abcdefghij").startswith("error")


def test_an_idea_is_closed_with_its_result_and_the_test_stays_the_test(rolebox, jobs):
    """There was no field for what a test found, so roles kept the running result in `test` (which is why it grew past
    its cap). `result` holds it: an idea is closed with status and result, and its test is still how it was tested."""
    tools, paths, _ = rolebox
    scout = tools("scout")
    mine = _queue(jobs, "scout", "who copies Cupsey", test="place-bids --each over 72 h")
    out = _call(scout["upsert_idea"], id=mine, status="rejected",
                result="lower -0.021 at +3x over 133 copies (job 6c1b82): no copy pays")  # fmt: skip
    assert out == f"{mine} updated: result, status"
    (idea,) = learning.list_ideas(paths, "all")
    assert idea["status"] == "rejected" and idea["test"] == "place-bids --each over 72 h"
    assert idea["result"] == "lower -0.021 at +3x over 133 copies (job 6c1b82): no copy pays"
    line = _call(scout["list_ideas"], status="rejected").splitlines()[1]
    assert line.startswith(f"{mine} [rejected, priority 3, scout] who copies Cupsey. Result: lower -0.021")
    assert "result" in learning.IDEA_FIELDS and "result" not in learning.IDEA_TEXT  # a new idea needs none
    long = _call(scout["upsert_idea"], id=mine, result="lower -0.021 over 133 copies; " * 25)
    assert "result was cut at 600 characters: its last 150 characters are gone" in long
    assert _call(tools()["upsert_idea"], id=mine, result="the analyst's reading").endswith("updated: result")


def test_the_live_page_shows_an_ideas_result(tmp_path):
    from snipetech import ops as O
    from snipetech.dash import dash_state

    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main")
    paths = OperatorPaths(data).ensure()
    idea = learning.upsert_idea(paths, IDEA | {"status": "rejected", "result": "lower -0.021 over 133 copies"},
                                source="scout", now=NOW)  # fmt: skip
    shown = dash_state(ops, now=NOW)["ideas"]
    assert shown[0]["id"] == idea["id"] and shown[0]["result"] == "lower -0.021 over 133 copies"
    from snipetech.dash import dash_html

    html = dash_html()
    assert "i.result" in html and "update_table" in html  # the page words the new tool and shows the result


def test_the_writing_tools_answer_in_one_line_without_echoing(rolebox, jobs):
    """upsert_idea, add_lesson and start_job answered with what had just been written (181,000 characters back for
    151,000 written). They say what they did: the idea's id and the fields that changed, the lesson recorded, the
    job's id; a refusal still comes whole."""
    tools, paths, _ = rolebox
    scout = tools("scout")
    out = _call(scout["upsert_idea"], **IDEA)
    (idea,) = learning.list_ideas(paths, "all")
    assert out == f"{idea['id']} created: research, open, priority 3, queue scout"
    assert _call(scout["upsert_idea"], id=idea["id"], status="testing", priority=3) == f"{idea['id']} updated: status"
    assert _call(scout["upsert_idea"], id=idea["id"], status="testing").endswith("updated: nothing changed")
    handed = _call(scout["upsert_idea"], **(IDEA | {"title": "score copies of this group", "role": "tester"}))
    assert handed.endswith("created: research, open, priority 3, queue tester")
    owners = _call(scout["upsert_idea"], **(IDEA | {"title": "copy FLPjyS live at 0.1 SOL", "kind": "live-test"}))
    assert owners.endswith("created: live-test, open, priority 3, needs the owner's yes, in no queue")
    assert IDEA["why"] not in out + handed + owners
    lesson = _call(scout["add_lesson"], text="Crowded leaders lose at our landing", evidence="4 of 5 losses", n=5)
    assert lesson == "recorded" and "Crowded" in (paths.root / "lessons.md").read_text()
    assert (
        _call(scout["add_lesson"], text="Crowded leaders lose at our landing", evidence="x", n=5) == "already recorded"
    )
    started = _call(scout["start_job"], argv=["winners", "--hours", "72", "--json"], idea=idea["id"])
    assert re.fullmatch(r"started \d{8}-\d{6}-[0-9a-f]{6}", started) and "winners" not in started
    assert (paths.jobs / f"{started.split()[1]}.json").exists()
    refused = _call(scout["start_job"], argv=["lab", "--json"], idea=idea["id"])
    assert refused.startswith("error: ") and "not the wallet scout's to start" in refused
    plain = _call(tools()["start_job"], argv=["follow", "--kols"])
    assert re.fullmatch(r"started \d{8}-\d{6}-[0-9a-f]{6}", plain)


# -- the journal entry carries the note --------------------------------------------------------------------------------


def test_the_journal_headline_is_the_turns_note(rolebox):
    """One finding was written up to five times a turn, and 88 % of a note's numbers were already in the same turn's
    journal entry. The journal entry takes a headline: code writes it as the role's note exactly as write_note does
    (always "info", the role's name in front, one a turn), and the turn counts as having written its note."""
    tools, paths, sink = rolebox
    fees = tools("fees")
    out = _call(fees["write_report"], name="fees-journal-2026-11-06", append=True,
                text="## 00:00 UTC: I20\n\nThe lab at +0 against +6, 41 moments.",
                headline="place +0 behind Cupsey is worth 0.21 SOL at 2 SOL (n 41)")  # fmt: skip
    assert out == "wrote fees-journal-2026-11-06.md; the headline is written as this turn's note"  # the day's first
    assert paths.notes.read_text() == ("- 2026-11-06T00:00:00Z [info] fees: place +0 behind Cupsey is worth 0.21 SOL "
                                       "at 2 SOL (n 41)\n")  # fmt: skip
    assert sink.count == 1 and sink.decisions == []  # what turn.py counts: the turn wrote its note
    journal = (paths.root / "fees-journal-2026-11-06.md").read_text()
    assert journal == ("## 00:00 UTC: I20\n\nThe lab at +0 against +6, 41 moments.\n\n"
                       "In short: place +0 behind Cupsey is worth 0.21 SOL at 2 SOL (n 41)\n")  # fmt: skip
    # one note a turn, whichever tool wrote it (round 2, 14 Nov: said without "error", which asked for one more reply)
    assert _call(fees["write_note"], text="and one more").startswith("this turn's note is written already")
    again = _call(fees["write_report"], name="fees-journal-2026-11-06", append=True, text="more",
                  headline="a second headline")  # fmt: skip
    assert again.startswith("appended to fees-journal-2026-11-06.md") and "not written as a note" in again
    assert sink.count == 1 and paths.notes.read_text().count("\n") == 1
    assert (paths.root / "fees-journal-2026-11-06.md").read_text().endswith("more\n\nIn short: a second headline\n")
    # a journal entry without a headline is as before, and write_note still writes the note
    scout = tools("scout")
    assert _call(scout["write_report"], name="scout-journal-2026-11-06", append=True, text="x") == (
        "wrote scout-journal-2026-11-06.md")  # fmt: skip
    assert sink.count == 1
    assert _call(scout["write_note"], text="found 3 wallets") == "noted: this turn's note is written"
    assert paths.notes.read_text().endswith("[info] scout: found 3 wallets\n")
    # what the wallet can pay is no role's subject, in a headline as in a note: the entry is kept, the note is not
    tester = tools("tester")
    out = _call(tester["write_report"], name="tester-journal-2026-11-06", append=True, text="y",
                headline="the wallet can't pay a buy")  # fmt: skip
    assert out.startswith("error: not done: the headline was not written as this turn's note")  # round 2: shown
    assert (paths.root / "tester-journal-2026-11-06.md").read_text() == "y\n"
    assert sink.count == 2 and "can't pay" not in paths.notes.read_text()
    assert "headline" not in tools()["write_report"].to_dict()["input_schema"]["properties"]  # the analyst's: as it was


def test_a_turn_whose_journal_entry_carried_the_headline_has_written_its_note(env):
    """The turn runner counts a turn's notes through the note sink and writes "turn ended without a note" itself when
    there is none. A headline goes through the same sink, so a role's turn that wrote its journal with a headline and
    never called write_note counts as having written its note: nothing in the runner had to change."""
    from snipetech.operator.gate import Decision

    handler, _ = _turn._scripted(
        (
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "write_report",
                    "input": {
                        "name": "fees-journal-2026-11-06",
                        "append": True,
                        "text": "## 01:00 UTC: I20\n\nThe lab at +0 against +6.",
                        "headline": "place +0 behind Cupsey is worth 0.21 SOL at 2 SOL (n 41)",
                    },
                }
            ],
            "tool_use",
        ),
        ([{"type": "text", "text": "done"}], "end_turn"),
    )
    out = _turn._run(env, Decision(True, "fees", ["a job of yours ended"]), handler, role="fees")
    notes = env["paths"].notes.read_text()
    assert out["stopped"] == "end_turn" and out["notes"] == 1 and out["tool_calls"] == 1
    assert "[info] fees: place +0 behind Cupsey is worth 0.21 SOL at 2 SOL (n 41)" in notes
    assert "turn ended without a note" not in notes and env["sent"] == []


def test_a_row_is_found_by_the_start_of_its_first_cell_and_a_new_row_can_go_on_top(rolebox):
    """The real tables' first cells are long and change ("efe459e8a2 (frozen 13 Nov 21:31)" became "efe459e8a2"), and
    44 of 100 real table writes put a new row on top. A key is the first cell as it stands or enough of its start to
    tell the row from every other; a start that fits several rows changes none of them and says so; a new row goes on
    top with `before`; and a new row that starts like a row already there is pointed out, so no row is doubled
    unseen."""
    tools, paths, _ = rolebox
    p = _table(paths, TABLE.replace("| HjuSth |", "| HjuSth (I165, closed 06:31): a scalper |"))
    update = tools("scout")["update_table"]
    out = _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth | scalper | retired |"}])
    assert "replaced 1 (HjuSth)" in out and "| HjuSth | scalper | retired |" in p.read_text().split("\n")
    assert p.read_text().count("HjuSth") == 1
    # "Hju" is no whole word of that cell, and "FLPjyS: QUAL" neither: a start ends where a word ends
    out = _call(update, rows=[{"key": "Hju", "text": "| Hju | another wallet | candidate |"}])
    assert out.startswith("scout-facts.md: added 1 (Hju)") and "starts like" not in out
    # a start that fits two rows: neither is touched, the row is not added, and the answer names both
    _call(update, rows=[{"key": "FLPjyS twin", "text": "| FLPjyS twin i3Sj7y | same signal | candidate |"}])
    before = p.read_text()
    out = _call(update, rows=[{"key": "FLPjyS", "text": "| FLPjyS | unnamed | retired |"}], drop=["FLPjyS"])
    assert p.read_text() == before and out.startswith("error: not done: ")  # round 2 (14 Nov): behind "error"
    assert "FLPjyS fits 2 rows (FLPjyS: QUALIFIED; FLPjyS twin i3Sj7y): give more of the first cell" in out
    # a new row on top, and one that starts like a row that is there
    out = _call(update, rows=[{"key": "6Z91 copy bot", "text": "| 6Z91 copy bot | never a wallet to follow | - |",
                               "before": "FLPjyS: QUALIFIED"},
                              {"key": "YJFQNK (I164)", "text": "| YJFQNK (I164) | arbitrager | candidate |"}])  # fmt: skip
    lines = p.read_text().split("\n")
    assert lines[6] == "| 6Z91 copy bot | never a wallet to follow | - |"
    assert lines[7].startswith("| FLPjyS: QUALIFIED")
    assert "added 2 (6Z91 copy bot; YJFQNK (I164))" in out
    assert out.startswith("error: not done: YJFQNK (I164) was added as a NEW row and starts like the row YJFQNK")


def test_a_secret_is_scrubbed_before_a_job_log_is_cut_never_after(monkeypatch):
    """The scrub (ops.redact) takes out whole values of the environment's keys. Since 14 Nov a job's output is cut
    inside a line (a long line folded at 2,000 characters, a long JSON value cut at 300), and a key across such a cut
    was two harmless-looking pieces to a scrub that ran on the cut answer: it reached the model, the API and the
    owner's page. The text is scrubbed first, whole, and cut after."""
    from snipetech.operator.tools import FOLD_CHARS, VALUE_CHARS

    key = "hk_" + "Q7w9" * 9 + "Zz"
    monkeypatch.setenv("GMGN_API_KEY", key)
    pieces = lambda out: [key[i : i + 6] for i in range(len(key) - 5) if key[i : i + 6] in out.replace("\n", "")]
    folded = read_job_output("x" * (FOLD_CHARS - 15) + key + "y" * 500 + "\n", 200)
    assert not pieces(folded) and "<redacted>" in folded
    err = "pool: " + "e" * (VALUE_CHARS - 40) + f" url: /?api-key={key} (Caused by X)"
    result = {"error": err, "rows": [{"i": i, "v": "z" * 50} for i in range(400)]}
    assert not pieces(read_job_output(json.dumps(result, indent=1) + "\n", 40))
    lead = "warning: " + "w" * (VALUE_CHARS - 25) + key + " tail"
    assert not pieces(read_job_output(lead + "\n" + json.dumps({"a": 1}, indent=1) + "\n", 200))


def test_a_headline_that_starts_like_a_row_or_a_title_is_still_a_paragraph():
    """A headline beginning with "|" read as a table from then on (new rows landed above the real table and the next
    headline left it in place); one beginning with "#" as a title. Both are written as plain paragraphs."""
    from snipetech.operator.tools import change_table

    text, _, _ = change_table(TABLE, above="| not a row | just a headline |")
    text, _, _ = change_table(text, rows=[{"key": "NEWROW", "text": "| NEWROW | a | b |"}])
    lines = text.splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("|") and "---" in lines[i + 1])
    assert next(i for i, line in enumerate(lines) if "NEWROW" in line) > head  # inside the real table
    text, _, _ = change_table(text, above="# clean again")
    assert "just a headline" not in text and sum(line.startswith("#") for line in text.splitlines()) == 1


# -- round 2 (14 Nov, after four reviews): a role's turn now ends right after the reply that held its note, as soon as
# no answer of that reply starts with "error". An answer that says "not done" in other words is never read. -----------

TWINS = TABLE.replace("| HjuSth |", "| FLPjyS twin i3Sj7y | same signal | candidate |\n| HjuSth |")


def test_what_update_table_did_not_do_starts_its_answer_with_error(rolebox):
    """On the real fees table five first cells start with "FvHwFe": a row sent with that key changed nothing, the
    answer said so without the word "error", the journal call beside it wrote the note, and the turn ended: the row was
    lost and the model never read why. What was not done as asked now leads the answer, behind "error"; what could be
    done is still done and written. A row cut at its cap is done, with its notice: no error."""
    tools, paths, _ = rolebox
    p = _table(paths, TWINS)
    update = tools("scout")["update_table"]
    out = _call(update, rows=[{"key": "FLPjyS", "text": "| FLPjyS | unnamed | retired |"},
                              {"key": "HjuSth", "text": "| HjuSth | scalper | retired |"}])  # fmt: skip
    assert out.startswith("error: not done: FLPjyS fits 2 rows (FLPjyS: QUALIFIED; FLPjyS twin i3Sj7y): give more of")
    assert "; done: replaced 1 (HjuSth); " in out and "\n" not in out and out.endswith("5 rows")
    assert "| HjuSth | scalper | retired |" in p.read_text().split("\n") and "| FLPjyS | unnamed" not in p.read_text()
    os.utime(p, (NOW - 3600, NOW - 3600))
    before = p.read_text()
    for call, said in (({"rows": [{"key": "AtifzK"}]}, "a row has no key or no text"),
                       ({"drop": ["nobody"]}, "no row nobody to drop"),
                       ({"drop": ["FLPjyS"]}, "FLPjyS fits 2 rows")):  # fmt: skip
        out = _call(update, **call)
        assert out.startswith(f"error: not done: {said}") and "; done: nothing; " in out, out
        assert p.read_text() == before and p.stat().st_mtime == NOW - 3600  # nothing was written
    # a row that could not go where it was asked to is written at the table's end, and that is said as not done
    out = _call(update, rows=[{"key": "Z1", "text": "| Z1 | a | b |", "before": "no such row"}])
    assert out.startswith("error: not done: Z1 went to the end of the first table (no one row no such row)")
    assert "; done: added 1 (Z1); " in out and "| Z1 | a | b |" in p.read_text()
    # a new row that starts like the one row it may have been meant to replace: added, and shown
    out = _call(update, rows=[{"key": "YJFQNK (I164)", "text": "| YJFQNK (I164) | arbitrager | candidate |"}])
    assert out.startswith("error: not done: YJFQNK (I164) was added as a NEW row and starts like the row YJFQNK")
    assert "; done: added 1 (YJFQNK (I164)); " in out
    # a row with the wrong number of cells is written and shown too
    out = _call(update, rows=[{"key": "Z1", "text": "| Z1 | only two |"}])
    assert out.startswith("error: not done: Z1 has 2 cells, its table 3 columns") and "replaced 1 (Z1)" in out
    # a paragraph for a file that has no table
    prose = _table(paths, "# Fees and bids\n\nnothing measured yet\n", name="fees-bids")
    out = _call(tools("fees")["update_table"], above="All rows are paper.")
    assert out.startswith("error: not done: the paragraph above not replaced: the file has no table")
    assert prose.read_text() == "# Fees and bids\n\nnothing measured yet\n"
    # cut, but done: no error
    out = _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth | scalper | " + "seven days; " * 150 + "|"}])
    assert not out.startswith("error") and "cut at 1,200 characters: HjuSth" in out


def test_the_paragraph_above_the_table_is_called_above_and_a_headline_is_the_journals(rolebox):
    """Two arguments were called `headline` in adjacent sentences: update_table's was the paragraph above the table,
    write_report's is the note. A model that mixed them replaced the table's standing paragraph with the turn's news
    and wrote no note. The table's is `above` (next to `below`); update_table takes no headline at all."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    assert set(update.to_dict()["input_schema"]["properties"]) == {"rows", "drop", "above", "below", "text", "whole"}
    out = _call(update, above="One wallet clears every gate.")
    assert out == f"scout-facts.md: the paragraph above replaced; {len(p.read_text()):,} characters, 4 rows"
    assert p.read_text().split("\n")[2] == "One wallet clears every gate."
    with pytest.raises(ValueError, match="Invalid arguments"):
        _call(update, headline="W1 is Cupsey: 31 round trips")
    assert "Cupsey" not in p.read_text()


def test_write_report_with_the_tables_name_and_only_some_rows_changes_those_rows(rolebox):
    """The roles wrote the table with write_report in 105 of 105 turns and are now told "send only the rows that
    changed". The old tool with the table's name and one row replaced the whole table with that row (the real scout
    table: 5,607 characters to 54). A text is the table whole only when it has a "# " title line and a table header
    with its separator line; anything else is taken as rows, by their first cells, and the answer says so."""
    tools, paths, _ = rolebox
    report = tools("scout")["write_report"]
    for append in (False, True):
        p = _table(paths)
        out = _call(report, name="scout-facts", text="| AtifzK | hand trader | retired |", append=append)
        old, new = TABLE.split("\n"), p.read_text().split("\n")
        assert [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b] == [0, 7], append
        assert new[7] == "| AtifzK | hand trader | retired |" and new[0] == TITLE_NOW
        assert out.startswith("scout-facts.md: taken as rows (no whole table: no title line and header); replaced 1")
    # several rows, the header and its separator sent along, a new row: the header is no row
    p = _table(paths)
    out = _call(report, name="scout-facts", text="| Wallet | Who, kind | Qualify (+3x) |\n|---|---|---|\n"
                "| HjuSth | scalper | retired |\n| 9L5aWu | fresh wallet | candidate |")  # fmt: skip
    assert "replaced 1 (HjuSth); added 1 (9L5aWu)" in out and p.read_text().count("| Wallet |") == 2  # as before
    assert "| FLPjyS: QUALIFIED |" in p.read_text() and "| 9L5aWu | fresh wallet | candidate |" in p.read_text()
    # the table whole is still taken whole, with the pointer to rows
    out = _call(report, name="scout-facts", text=TABLE.replace("HjuSth", "HjuSth2"))
    assert out.startswith("scout-facts.md: written whole") and "Next time send only the rows that changed" in out
    assert "HjuSth2" in p.read_text() and "9L5aWu" not in p.read_text()
    # no row in it at all: nothing is done, and it says so behind "error"
    before = p.read_text()
    out = _call(report, name="scout-facts", text="FLPjyS is retired now.")
    assert out.startswith("error: not done: the text holds no table row") and p.read_text() == before


def test_update_table_takes_a_whole_table_without_the_flag_and_refuses_text_beside_rows(rolebox):
    """The whole table through update_table(text=...) without whole=true was refused and sent a second time; with
    `text` given, `rows` and `drop` were dropped without a word."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    out = _call(update, text=TABLE.replace("HjuSth", "HjuSth2"))
    assert out.startswith("scout-facts.md: written whole") and "Next time send only the rows that changed" in out
    assert "HjuSth2" in p.read_text()
    assert "Next time" not in _call(update, text=TABLE, whole=True)  # a rebuild that was meant
    before = p.read_text()
    out = _call(update, text=TABLE, rows=[{"key": "AtifzK", "text": "| AtifzK | CHANGED | - |"}], drop=["HjuSth"])
    assert out.startswith("error: not done: text is the table whole and stands alone") and p.read_text() == before
    # some rows as text are rows
    out = _call(update, text="| AtifzK | hand trader | retired |")
    assert "taken as rows" in out and "replaced 1 (AtifzK)" in out and "| FLPjyS: QUALIFIED |" in p.read_text()


def test_a_roles_journal_is_todays_always_added_to_and_never_replaced(rolebox):
    """A journal call without append=true replaced the day's journal, and that call now also ends the turn; a journal
    of another day was accepted; the refusal for a wrong name named the report as if it were the role."""
    tools, paths, _ = rolebox
    report = tools("scout")["write_report"]
    j = paths.root / "scout-journal-2026-11-06.md"
    j.write_text("## 00:15 UTC: earlier entry\n\nfirst finding kept here.\n")
    out = _call(report, name="scout-journal-2026-11-06", text="## 01:00 UTC\n\nread winners.")  # no append
    assert out == "appended to scout-journal-2026-11-06.md"
    assert j.read_text() == "## 00:15 UTC: earlier entry\n\nfirst finding kept here.\n\n## 01:00 UTC\n\nread winners.\n"
    refusal = ("error: write_report takes only your journal, scout-journal-2026-11-06; your table is update_table's; "
               "a finding for another role is an idea in its queue")  # fmt: skip
    for name in ("scout-journal-2026-10-01", "scout-journal-2026-11-07", "tester-journal-2026-11-06", "notes",
                 "analyst-2026-11-06", "scout-facts-old"):  # fmt: skip
        assert _call(report, name=name, text="x", headline="a finding") == refusal, name
        assert not (paths.root / f"{name}.md").exists() or name == "notes"
    assert not paths.notes.exists() or "a finding" not in paths.notes.read_text()


def test_a_headline_needs_no_text_and_the_note_it_writes_is_capped(rolebox):
    """write_report(headline=...) without `text` raised an opaque "Invalid arguments"; a headline had no cap (a note
    line of 5,786 characters in the reviewer's run)."""
    tools, paths, sink = rolebox
    report = tools("fees")["write_report"]
    out = _call(report, name="fees-journal-2026-11-06", headline="place +0 is worth 0.21 SOL at 2 SOL (n 41)")
    assert out == "wrote fees-journal-2026-11-06.md; the headline is written as this turn's note"
    assert (paths.root / "fees-journal-2026-11-06.md").read_text() == (
        "In short: place +0 is worth 0.21 SOL at 2 SOL (n 41)\n")  # fmt: skip
    long = ("a finding with numbers " * 250).strip()
    out = _call(tools("scout")["write_report"], name="scout-journal-2026-11-06", text="entry", headline=long)
    line = paths.notes.read_text().splitlines()[-1]
    assert len(line) <= 700 + 40 and line.endswith("…") and not out.startswith("error")
    assert f"the note was cut at 700 characters (the headline was {len(long):,})" in out
    assert _call(tools("tester")["write_note"], text=long).endswith(f"cut at 700 characters (it was {len(long):,})")
    assert sink.count == 3 and all(len(n) <= 740 for n in paths.notes.read_text().splitlines())


def test_a_second_note_is_answered_without_error_and_a_wallet_headline_with_it(rolebox):
    """write_note after the journal's headline answered "error: one note a turn ... put the rest into your journal":
    the word "error" asked for one more reply (the reply the change removed) and a model that obeyed wrote the finding
    into the journal twice. It is now told that the note is written, and nothing else. A headline that was NOT written
    as the note (what the wallet can pay is no role's subject) is not done as asked: that one starts with "error"."""
    tools, paths, sink = rolebox
    fees = tools("fees")
    _call(fees["write_report"], name="fees-journal-2026-11-06", text="entry", headline="place +0 is worth 0.21 SOL")
    out = _call(fees["write_note"], text="place +0 is worth 0.21 SOL")
    assert out == "this turn's note is written already (the journal's headline): nothing to add"
    assert sink.count == 1 and paths.notes.read_text().count("\n") == 1
    scout = tools("scout")
    _call(scout["write_note"], text="found 3 wallets")
    assert _call(scout["write_note"], text="again") == "this turn's note is written already: nothing to add"
    out = _call(scout["write_report"], name="scout-journal-2026-11-06", text="entry", headline="a headline")
    assert out.startswith("wrote scout-journal-2026-11-06.md") and "error" not in out  # the note stands: nothing lost
    tester = tools("tester")
    out = _call(
        tester["write_report"], name="tester-journal-2026-11-06", text="y", headline="the wallet can't pay a buy"
    )
    assert out.startswith("error: not done: the headline was not written as this turn's note") and "write_note" in out
    assert "do not send the entry again" in out and sink.count == 2
    assert (paths.root / "tester-journal-2026-11-06.md").read_text().startswith("y\n")  # the entry is kept
    # a headline sent with the table's name writes no note: said, with what was done to the table
    _table(paths, name="oktech-facts")
    out = _call(tools("oktech")["write_report"], name="oktech-facts", text="| HjuSth | scalper | retired |",
                headline="HjuSth is retired")  # fmt: skip
    assert out.startswith("error: not done: no note was written (a headline goes with your journal, oktech-journal-")
    assert "-2026-11-06, not your table); done: oktech-facts.md: taken as rows" in out
    assert sink.count == 2 and "retired" in (paths.root / "oktech-facts.md").read_text()
    assert _call(tools("oktech")["write_report"], name="oktech-journal-2026-11-06").startswith(
        "error: not done: nothing"
    )


def test_an_ideas_cut_says_what_is_gone_and_from_where(rolebox):
    """The cut takes the END of the text, where "what is missing" is appended, and its notice arrives after the turn
    ended in about half the turns. It says how much is gone and from where; no advice about the journal."""
    tools, paths, _ = rolebox
    test = "lab at the bid place-bids names, then the same at +0 and at +6; " * 9  # 575 characters as stored
    out = _call(tools("scout")["upsert_idea"], **(IDEA | {"test": test}))
    (idea,) = learning.list_ideas(paths, "all")
    gone = 575 - (len(idea["test"]) - 1)
    assert out == (f"{idea['id']} created: research, open, priority 3, queue scout; test was cut at 400 characters: "
                   f"its last {gone} characters are gone")  # fmt: skip
    assert "journal" not in out and not out.startswith("error")


def test_a_journal_entry_and_a_table_sent_whole_have_a_hard_cap(rolebox):
    """A journal entry of 300,000 characters and a table of 323,542 were accepted. An entry is cut at 12,000
    characters and a table at 20,000, at a line, with a notice; a cut is not an error. A table that rows would take
    past the cap is not written: nothing of what is there may be cut off by a row."""
    from snipetech.operator.tools import JOURNAL_ENTRY_CHARS, TABLE_MAX_CHARS

    tools, paths, _ = rolebox
    out = _call(tools("fees")["write_report"], name="fees-journal-2026-11-06", text="a line of the entry\n" * 15_000,
                headline="place +0 is worth 0.21 SOL")  # fmt: skip
    j = (paths.root / "fees-journal-2026-11-06.md").read_text()
    assert (
        JOURNAL_ENTRY_CHARS == 12_000 and len(j) <= 12_000 + 60 and j.endswith("In short: place +0 is worth 0.21 SOL\n")
    )
    assert not out.startswith("error") and "the entry was cut at 12,000 characters (it was 300,000)" in out
    assert "the headline is written as this turn's note" in out
    row = "| W{i} | a wallet with a long story | candidate |\n"
    big = TABLE.split("| FLPjyS")[0] + "".join(row.format(i=i) for i in range(8000))
    update = tools("scout")["update_table"]
    out = _call(update, text=big)
    t = (paths.root / "scout-facts.md").read_text()
    assert TABLE_MAX_CHARS == 20_000 and len(t) <= 20_000 and t.endswith("| candidate |\n")
    assert not out.startswith("error") and "cut at 20,000 characters" in out and "lines are gone" in out
    stamp = (paths.root / "scout-facts.md").stat().st_mtime_ns
    out = _call(update, rows=[{"key": f"N{i}", "text": f"| N{i} | " + "x" * 1000 + " | new |"} for i in range(3)])
    assert out.startswith("error: not done: the table would be") and "20,000" in out
    assert (paths.root / "scout-facts.md").stat().st_mtime_ns == stamp


def test_the_roles_writing_tools_say_each_thing_once(rolebox):
    """The reviewer measured +6,600 characters of tools and instructions a role, "by rows, never whole" said six times
    and "the headline is the note, no write_note" four times. Each tool says its own part once."""
    from snipetech.operator import prompts
    from snipetech.operator.roles import NAMES, TABLES

    tools, _, _ = rolebox
    for role in ("scout", "tester", "fees", "oktech"):
        t = tools(role)
        flat = lambda tool: " ".join(tool.to_dict()["description"].split())
        assert flat(t["write_report"]) == (
            "Add this turn's entry to your journal of the day: what the turn did and found, the longer record. With "
            "`headline` it also writes this turn's one note for the owner (operator/notes.md, always \"info\"), and "
            f"the turn ends. The {NAMES[role]}'s journal is {role}-journal-<YYYY-MM-DD>.")  # fmt: skip
        assert flat(t["write_note"]) == (
            "This turn's note for the owner, only for a turn that writes no journal entry (a journal entry's headline "
            "is the note). One or two plain sentences with the numbers that matter; always \"info\". It is the turn's "
            "last call.")  # fmt: skip
        assert flat(t["update_table"]) == (
            "Change your table by rows (the other roles' turns carry its first 2,000 characters: one row a subject, "
            "short). A row is known by its first cell, its key. Every other line stays as it is, the code stamps the "
            "time into the title, and a call that changes nothing writes nothing. The answer says what was done and "
            f"what was not: read it. The {NAMES[role]}'s table is operator/{TABLES[role]}.md.")  # fmt: skip
        args = t["write_report"].to_dict()["input_schema"]["properties"]
        assert " ".join(args["headline"]["description"].split()) == (
            "The finding in one or two plain sentences with the numbers that matter (n, the landing, the bid); not "
            "repeated in the text.")  # fmt: skip
        assert t["write_report"].to_dict()["input_schema"]["required"] == ["name"]
        said = prompts.ROLE_SYSTEM[role] + " ".join(json.dumps(x.to_dict()) for x in t.values())
        assert said.count("never the whole table") == 0 and said.count("no write_note") == 1
        said_log = json.dumps(t["read_job_log"].to_dict())
        assert "as listed in the message" in said_log and "snapshot" not in said_log
        for tool in t.values():  # a role writes no note beside its journal entry: no text tells it to
            assert "in a note" not in flat(tool) and "in your note" not in flat(tool), tool.name


def test_the_lab_slots_line_is_in_the_part_of_a_roles_message_that_changes_every_turn(env, monkeypatch):
    """The line moves with every lab that starts or ends: it stands in the last block of the message, behind the
    blocks the hour's cache reads, and a role that starts no lab gets none."""
    from snipetech.operator.gate import Decision

    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "2")
    for role, told in (("tester", True), ("oktech", False)):
        handler, requests = _turn._scripted(([{"type": "text", "text": "nothing new"}], "end_turn"))
        _turn._run(env, Decision(True, role, ["first turn"]), handler, role=role)
        blocks = requests[0]["messages"][0]["content"]
        line = "Lab slots: 2 of 2 free (a lab, a freeze and qualify each take one)."
        assert (line in blocks[-1]["text"].splitlines()) is told, role
        assert all("Lab slots" not in b["text"] for b in blocks[:-1]), role


def test_a_text_that_is_no_whole_table_never_replaces_one_even_with_the_flag(rolebox):
    """whole=true says "a rebuild", but one row sent with it is no rebuild of a table that has a title and a header:
    it is taken as rows too. And what such a text holds beside rows (a paragraph) is not dropped without a word."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    out = _call(update, text="| AtifzK | hand trader | retired |", whole=True)
    assert "taken as rows" in out and "| FLPjyS: QUALIFIED |" in p.read_text() and len(p.read_text()) > 400
    out = _call(update, text="One wallet clears every gate now.\n\n| HjuSth | scalper | retired |")
    assert out.startswith("error: not done: 1 line of the text is no table row and was left out")
    assert "above" in out and "; done: taken as rows" in out and "| HjuSth | scalper | retired |" in p.read_text()
    assert "Two wallets clear every gate." in p.read_text()
