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
    """A key no row has is a new row when the call says so (round 3, 14 Nov: "new": true, or a place). It goes to the
    end of the first table that has its number of cells, so the order the role chose stays; with `after` it goes
    right behind that row, which is also how a row gets into a second table of the file."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    out = _call(update, rows=[{"key": "9L5aWu", "text": "| 9L5aWu | fresh wallet | candidate, n 18 |", "new": True}])
    lines = p.read_text().split("\n")
    assert lines[9] == "| 9L5aWu | fresh wallet | candidate, n 18 |" and lines[8].startswith("| HjuSth")
    assert lines[10] == "" and lines[11] == "In one line: nothing is copyable yet." and "added 1 (9L5aWu)" in out
    twin = {"key": "i3Sj7y", "text": "| i3Sj7y | FLPjyS's twin | candidate |", "after": "FLPjyS: QUALIFIED",
            "new": True}  # fmt: skip
    out = _call(update, rows=[twin, {"key": "ANTUPU", "text": "| ANTUPU | no stop scored |", "after": "YJFQNK",
                                     "new": True}])  # fmt: skip
    lines = p.read_text().split("\n")
    assert lines[6].startswith("| FLPjyS") and lines[7] == "| i3Sj7y | FLPjyS's twin | candidate |"
    assert lines[-3:] == ["| YJFQNK | 8 of 39 buys on LaunchLab |", "| ANTUPU | no stop scored |", ""]
    assert "added 1 (i3Sj7y) to the first table (Wallet); added 1 (ANTUPU) to the second table (Wallet)" in out
    # the row's cells without its key are no row (round 4: the key was put in front, and a rename one cell short was
    # stored a column to the right): nothing is written and the answer asks for the row whole
    before = p.read_text()
    out = _call(update, rows=[{"key": "Byn68j", "text": "Binance-funded | forward 8 of 20", "new": True}])
    assert out.startswith("error: not done: Byn68j: the row's first cell is Binance-funded, and a new row's key is its "
                          "first cell or the start of it: nothing was written; send its first cell as the "
                          "key")  # fmt: skip
    assert p.read_text() == before
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
    assert f"the row HjuSth was cut at 1,200 characters (it was {len(long):,})" in out and not out.startswith("error")
    out = _call(tools("scout")["update_table"], above="No wallet is copyable yet. " * 60)
    head = p.read_text().split("\n")[2]
    assert len(head) <= 1200 and head.endswith("…")
    assert "the paragraph above was cut at 1,200 characters (it was 1,619)" in out


def test_a_role_cannot_touch_another_roles_table_through_update_table(rolebox):
    """The tool names no file: it writes the table of the role whose turn it is and nothing else. The analyst, which
    keeps no table, has no such tool."""
    tools, paths, _ = rolebox
    _table(paths)
    theirs = _table(paths, name="tester-rules")
    update = tools("scout")["update_table"]
    assert not {"name", "table", "path", "role"} & set(update.to_dict()["input_schema"]["properties"])
    # no such argument: the call never runs (round 4: answered in words, not with the SDK's ValueError)
    out = _call(update, name="tester-rules", rows=[{"key": "HjuSth", "text": "| HjuSth | x | y |"}])
    assert out.startswith("error: not done: ") and "update_table takes no name" in out
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
    assert _call(update, text=other).startswith("error: not done: the text is a whole table without 1 of")  # round 3
    out = _call(update, text="| HjuSth | scalper | retired |")
    assert "taken as rows" in out and "| FLPjyS: QUALIFIED |" in p.read_text() and "retired" in p.read_text()
    _call(update, text=TABLE, whole=True)
    os.utime(p, (NOW - 3600, NOW - 3600))
    assert _call(update, text=TABLE.replace("08:05 UTC", "09:20 UTC"), whole=True).count("not written") == 1
    assert p.stat().st_mtime == NOW - 3600  # the same table with another clock in its title: nothing moved
    out = _call(update, text=other, whole=True)
    assert "written whole" in out and "HjuSth" not in p.read_text() and p.stat().st_mtime > NOW - 60
    # round 3 (14 Nov): a first table needs its title line (test_a_first_table_needs_a_title_a_header_and_rows_...)
    out = _call(tools("fees")["update_table"], text="| place | bid |\n|---|---|\n| +0 | 0.05 |")
    assert out.startswith("error: not done: there is no table yet") and not (paths.root / "fees-bids.md").exists()


def test_a_table_past_what_a_turn_carries_is_said(rolebox):
    """A turn's message carries the first 6,000 characters of the role's own table: the answer says when it is past."""
    tools, paths, _ = rolebox
    _table(paths)
    update = tools("scout")["update_table"]
    rows = [{"key": f"W{i}", "text": f"| W{i} | " + "a wallet with a long story; " * 12 + "| candidate |", "new": True}
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
    out = _call(report, name="scout-facts", text=TABLE.replace("stopped by crowding", "retired"))
    assert out.startswith("scout-facts.md: written whole") and "update_table" in out and "\n" not in out
    assert "| HjuSth | scalper | retired |" in p.read_text() and p.stat().st_mtime > NOW - 60
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
    # round 4 (14 Nov): the caps from what was really written (test 404-654 for 400, why 603-682, expected 305-372)
    assert learning.IDEA_TEXT == {"title": 120, "why": 700, "test": 650, "expected": 400}
    test = "lab at the bid place-bids names, then the same at +0 and at +6; " * 11  # 703 characters as stored
    why = "7 of 9 losses copied buys under 0.3 SOL (n 9); " * 16  # 751
    out = _call(tools("scout")["upsert_idea"], **(IDEA | {"test": test, "why": why}))
    assert not out.startswith("error") and "\n" not in out
    (idea,) = learning.list_ideas(paths, "all")
    assert 640 < len(idea["test"]) <= 650 and idea["test"].endswith("…") and idea["test"][:640] == test[:640]
    assert len(idea["why"]) <= 700 and idea["why"].endswith("…") and out.startswith(f"{idea['id']} created")
    assert "test was cut at 650 characters: its last " in out and "why was cut at 700 characters: its last " in out
    # the library says the same to its other callers
    done = learning.upsert_idea_detail(paths, {"id": idea["id"], "expected": "+0.004 SOL " * 40}, source="cloud")
    assert done["cut"] == {"expected": 439} and done["changed"] == ["expected"] and not done["created"]
    assert len(done["idea"]["expected"]) <= 400 and done["idea"]["expected"].endswith("…")
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


def test_a_row_is_found_by_its_whole_first_cell_and_a_new_row_can_go_on_top(rolebox):
    """The real tables' first cells are long and change ("efe459e8a2 (frozen 13 Nov 21:31)" became "efe459e8a2"), and
    44 of 100 real table writes put a new row on top. Round 6: a key is the row's whole first cell (bold, spacing and
    capitals aside); the start of one finds no row, and the answer names the rows it starts, whole; a new row goes on
    top with `before`; and a new row that starts like a row already there is pointed out, so no row is doubled
    unseen."""
    tools, paths, _ = rolebox
    p = _table(paths, TABLE.replace("| HjuSth |", "| HjuSth (I165, closed 06:31): a scalper |"))
    update = tools("scout")["update_table"]
    # round 6: a key is the row's whole first cell; the start of it finds no row, and the answer names the row whole
    out = _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth (retired) | scalper | retired |"}])
    assert out.startswith('error: not done: no row HjuSth. Rows whose first cell starts with it: "HjuSth (I165, '
                          'closed 06:31): a scalper"')  # fmt: skip
    out = _call(update, rows=[{"key": "hjusth (i165, closed 06:31): A SCALPER",
                               "text": "| HjuSth (retired) | scalper | retired |"}])  # fmt: skip
    # round 4: the answer names the row as it stood, its whole first cell
    assert "replaced 1 (HjuSth (I165, closed 06:31): a scalper)" in out
    assert "| HjuSth (retired) | scalper | retired |" in p.read_text().split("\n")
    assert p.read_text().count("HjuSth") == 1
    # "Hju" is no whole word of that cell, and "FLPjyS: QUAL" neither: a start ends where a word ends
    out = _call(update, rows=[{"key": "Hju", "text": "| Hju | another wallet | candidate |", "new": True}])
    assert out.startswith("scout-facts.md: added 1 (Hju)")
    # a start that fits two rows: neither is touched, the row is not added, and the answer names both
    _call(
        update, rows=[{"key": "FLPjyS twin", "text": "| FLPjyS twin i3Sj7y | same signal | candidate |", "new": True}]
    )
    before = p.read_text()
    out = _call(update, rows=[{"key": "FLPjyS", "text": "| FLPjyS | unnamed | retired |"}], drop=["FLPjyS"])
    assert p.read_text() == before and out.startswith("error: not done: ")  # round 2 (14 Nov): behind "error"
    assert 'no row FLPjyS. Rows whose first cell starts with it: "FLPjyS: QUALIFIED"; "FLPjyS twin i3Sj7y"' in out
    # a new row on top, and one that starts like a row that is there
    out = _call(update, rows=[{"key": "6Z91 copy bot", "text": "| 6Z91 copy bot | never a wallet to follow | - |",
                               "before": "FLPjyS: QUALIFIED", "new": True},
                              {"key": "YJFQNK (I164)", "text": "| YJFQNK (I164) | arbitrager | candidate |"}])  # fmt: skip
    lines = p.read_text().split("\n")
    assert lines[6] == "| 6Z91 copy bot | never a wallet to follow | - |"
    assert lines[7].startswith("| FLPjyS: QUALIFIED")
    # round 3 (14 Nov): a key that fits no row and is not said to be new is not written, whatever starts like it
    assert "added 1 (6Z91 copy bot)" in out and "YJFQNK (I164)" not in p.read_text()
    assert out.startswith('error: not done: no row YJFQNK (I164) (rows that start alike: "YJFQNK"): nothing was '
                          "written")  # fmt: skip


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
    text, _, _ = change_table(text, rows=[{"key": "NEWROW", "text": "| NEWROW | a | b |", "new": True}])
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
    assert out.startswith('error: not done: no row FLPjyS. Rows whose first cell starts with it: "FLPjyS: '
                          'QUALIFIED"; "FLPjyS twin i3Sj7y": to change, move or drop one give its whole first cell')
    assert "; done: replaced 1 (HjuSth); " in out and "\n" not in out and out.endswith("5 rows")
    assert "| HjuSth | scalper | retired |" in p.read_text().split("\n") and "| FLPjyS | unnamed" not in p.read_text()
    os.utime(p, (NOW - 3600, NOW - 3600))
    before = p.read_text()
    for call, said in (({"rows": [{"key": "AtifzK"}]}, "a row has no key or no text"),
                       ({"drop": ["nobody"]}, "no row nobody to drop"),
                       ({"drop": ["FLPjyS"]}, "no row FLPjyS to drop. Rows whose first cell starts")):  # fmt: skip
        out = _call(update, **call)
        assert out.startswith(f"error: not done: {said}") and "; done: nothing; " in out, out
        assert p.read_text() == before and p.stat().st_mtime == NOW - 3600  # nothing was written
    # round 3 (14 Nov): a row that cannot go where it was asked to, a row for no row that is there, and a row with
    # the wrong number of cells are not written at all (round 2 wrote them and said so)
    out = _call(update, rows=[{"key": "Z1", "text": "| Z1 | a | b |", "before": "no such row"}])
    assert out.startswith("error: not done: no row no such row to put Z1 before: nothing was written; give "
                          '"before" the first cell of a row that is there')  # fmt: skip
    out = _call(update, rows=[{"key": "YJFQNK (I164)", "text": "| YJFQNK (I164) | arbitrager | candidate |"}])
    assert out.startswith('error: not done: no row YJFQNK (I164) (rows that start alike: "YJFQNK"): nothing was '
                          "written")  # fmt: skip
    out = _call(update, rows=[{"key": "AtifzK", "text": "| AtifzK | only two |"}])
    assert out.startswith("error: not done: AtifzK has 2 cells, its table has 3 columns")
    assert "Z1" not in p.read_text() and "I164" not in p.read_text() and "only two" not in p.read_text()
    # a paragraph for a file that has no table
    prose = _table(paths, "# Fees and bids\n\nnothing measured yet\n", name="fees-bids")
    out = _call(tools("fees")["update_table"], above="All rows are paper.")
    assert out.startswith("error: not done: the paragraph above not replaced: the file has no table")
    assert prose.read_text() == "# Fees and bids\n\nnothing measured yet\n"
    # cut, but done: no error
    out = _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth | scalper | " + "seven days; " * 150 + "|"}])
    assert not out.startswith("error") and "the row HjuSth was cut at 1,200 characters" in out


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
    # round 3 (14 Nov): answered in words, not with the SDK's "Invalid arguments"
    assert _call(update, headline="W1 is Cupsey: 31 round trips").startswith("error: not done: update_table has no")
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
    assert "replaced 1 (HjuSth)" in out and p.read_text().count("| Wallet |") == 2  # as before
    # round 3 (14 Nov): a row for no row that is there is not added through a text (it cannot say that it is new)
    assert out.startswith("error: not done: no row 9L5aWu: nothing was written") and "9L5aWu" not in p.read_text()
    assert "| FLPjyS: QUALIFIED |" in p.read_text()
    # the table whole is still taken whole, with the pointer to rows
    out = _call(report, name="scout-facts", text=TABLE.replace("stopped by crowding", "retired again"))
    assert out.startswith("scout-facts.md: written whole") and "Next time send only the rows that changed" in out
    assert "retired again" in p.read_text()
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
    out = _call(update, text=TABLE.replace("stopped by crowding", "retired"))
    assert out.startswith("scout-facts.md: written whole") and "Next time send only the rows that changed" in out
    assert "| HjuSth | scalper | retired |" in p.read_text()
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
    # round 5: shortened, not "cut": a note is written, and a further reply can do nothing about its length
    assert f"the note was shortened to 700 characters (the headline was {len(long):,})" in out
    assert _call(tools("tester")["write_note"], text=long).endswith(f"to 700 characters (it had {len(long):,})")
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
    test = "lab at the bid place-bids names, then the same at +0 and at +6; " * 11  # 703 characters as stored
    out = _call(tools("scout")["upsert_idea"], **(IDEA | {"test": test}))
    (idea,) = learning.list_ideas(paths, "all")
    gone = 703 - (len(idea["test"]) - 1)
    assert out == (f"{idea['id']} created: research, open, priority 3, queue scout; test was cut at 650 characters: "
                   f"its last {gone} characters are gone")  # fmt: skip
    assert "journal" not in out and not out.startswith("error")


def test_a_journal_entry_and_a_table_sent_whole_have_a_hard_cap(rolebox):
    """A journal entry of 300,000 characters and a table of 323,542 were accepted. An entry is cut at 12,000
    characters, at a line, with a notice; a cut is not an error. A table over 20,000 is not written."""
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
    # round 3 (14 Nov): a table over the cap is refused, not cut: test_a_whole_table_over_the_cap_is_refused_...
    row = "| W{i} | a wallet with a long story | candidate |\n"
    big = TABLE.split("| FLPjyS")[0] + "".join(row.format(i=i) for i in range(8000))
    out = _call(tools("scout")["update_table"], text=big)
    assert TABLE_MAX_CHARS == 20_000 and out.startswith("error: not done: the table sent is")
    assert not (paths.root / "scout-facts.md").exists()


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
            "repeated in the text (the note is cut at 700 characters).")  # fmt: skip
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
    round 4 (14 Nov) refuses it (round 3 took it as rows, and a prune sent without its title line removed nothing,
    answered without "error"). And what a text without the flag holds beside rows (a paragraph) is not dropped
    without a word."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    out = _call(update, text="| AtifzK | hand trader | retired |", whole=True)
    assert out.startswith("error: not done: whole=true needs the whole table") and p.read_text() == TABLE
    out = _call(update, text="One wallet clears every gate now.\n\n| HjuSth | scalper | retired |")
    assert out.startswith("error: not done: 1 line of the text is no table row and was left out")
    assert "above" in out and "; done: taken as rows" in out and "| HjuSth | scalper | retired |" in p.read_text()
    assert "Two wallets clear every gate." in p.read_text()


# -- round 3 (14 Nov, a fresh reviewer's proofs turned round): update_table writes a row only when it is certain where
# the row belongs and what it replaces; in every other case it writes nothing for that row and says, behind "error",
# exactly what to send. A reply to repair costs cents; a misplaced, doubled or lost row in a table three other agents
# read costs research. -------------------------------------------------------------------------------------------------

FAMILY = TABLE.replace(
    "| HjuSth |",
    "| FvHwFe first buy, 0.5 SOL | +2 | 0.036 |\n| FvHwFe: who is first | itself | 0.015 |\n"
    "| FvHwFe: limits | - | - |\n| HjuSth |",
)


def _rows(p):
    return [line for line in p.read_text().split("\n") if line.startswith("|")]


def test_a_new_row_goes_to_the_table_its_cells_fit_and_a_row_that_fits_no_table_is_not_written(rolebox):
    """F1, F5. The real oktech table has two tables (4 columns, then 3). A new row for the second landed at the end of
    the first, was answered "it is written so, send it again", and sending it again gave the same answer on every
    reply. A new row goes to the end of the one table that has its number of cells; a row whose cells fit no table,
    new or existing, is not written at all, so the repair finds the table as it was."""
    tools, paths, _ = rolebox
    p = _table(paths)  # three columns, then a second table of two
    update = tools("scout")["update_table"]
    # round 5: a new row without a place goes to the first table only; a row for the second gives "after"
    out = _call(update, rows=[{"key": "ANTUPU", "text": "| ANTUPU | no stop scored |", "new": True}])
    assert out.startswith("error: not done: ANTUPU has 2 cells, the first table has 3 columns (Wallet | Who, kind | "
                          "Qualify (+3x)): nothing was written; it has the columns of the second table (Wallet): for "
                          'the second table (Wallet) give "after" with a key of that table')  # fmt: skip
    out = _call(update, rows=[{"key": "ANTUPU", "text": "| ANTUPU | no stop scored |", "new": True, "after": "YJFQNK"}])
    assert "added 1 (ANTUPU) to the second table (Wallet)" in out and _rows(p)[-1] == "| ANTUPU | no stop scored |"
    before = p.read_text()
    out = _call(update, rows=[{"key": "N1", "text": "| N1 | a | b | c | d |", "new": True}])
    assert out.startswith("error: not done: N1 has 5 cells, the first table has 3 columns (Wallet | Who, kind | "
                          "Qualify (+3x)): nothing was written; send 3 cells: a | inside a cell is written \\|")
    # an existing row with a cell too few, with and without its key cell: nothing written, the repair leaves one row
    for text in ("| HjuSth | only two |", "| scalper only |", "| HjuSth | a | b | c |"):
        out = _call(update, rows=[{"key": "HjuSth", "text": text}])
        assert out.startswith("error: not done: HjuSth has ") and "its table has 3 columns" in out, out
        assert "nothing was written" in out and "; done: nothing; " in out and p.read_text() == before
    assert _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth | scalper | retired |"}]).startswith(
        "scout-facts.md"
    )
    assert sum(1 for row in _rows(p) if "scalper" in row) == 1 and len(_rows(p)) == len(before.split("\n|")) - 1
    # a place in the second table is a key of that table; three cells do not go there
    out = _call(update, rows=[{"key": "Z2", "text": "| Z2 | x |", "after": "YJFQNK", "new": True},
                              {"key": "Z3", "text": "| Z3 | x | y |", "after": "YJFQNK", "new": True}])  # fmt: skip
    assert out.startswith("error: not done: Z3 has 3 cells, the table of YJFQNK has 2 columns (Wallet | Finding)")
    assert "added 1 (Z2)" in out and "| Z2 | x |" in _rows(p) and "Z3" not in p.read_text()
    assert 'a row for another table: give "after" with a key of that table' in " ".join(
        update.to_dict()["input_schema"]["properties"]["rows"]["description"].split()
    )


def test_a_whole_shaped_text_with_only_some_rows_never_replaces_the_table(rolebox):
    """F2. "Send only the rows that changed" met the habit of sending the table as markdown: title, header, the
    changed row. It was "a whole table": the real scout table went from 18 rows to 1, answered "written whole". A
    whole-shaped text that lacks rows of the table changes nothing unless whole=true says the others are to go."""
    tools, paths, _ = rolebox
    part = "\n".join(TABLE.split("\n")[:6]) + "\n| AtifzK | hand trader | QUALIFIED +0.09 / 31 |\n"
    for tool, kw, way in (("update_table", {}, "this text with whole=true"),
                          ("write_report", {"name": "scout-facts"}, "update_table(text, whole=true)")):  # fmt: skip
        p = _table(paths)
        out = _call(tools("scout")[tool], text=part, **kw)
        assert out.startswith("error: not done: the text is a whole table without 3 of the table's 4 rows (FLPjyS: "
                              "QUALIFIED; HjuSth; YJFQNK): nothing was changed; send only the"), out  # fmt: skip
        assert way in out and p.read_text() == TABLE and p.stat().st_mtime == NOW - 3600
    out = _call(tools("scout")["update_table"], text=part, whole=True)
    assert out.startswith("scout-facts.md: written whole") and len(_rows(p)) == 3
    # a text that keeps every key is the table, written as before
    p = _table(paths)
    out = _call(tools("scout")["update_table"], text=TABLE.replace("stopped by crowding", "retired"))
    assert out.startswith("scout-facts.md: written whole") and "Next time send only the rows" in out
    whole = tools("scout")["update_table"].to_dict()["input_schema"]["properties"]["whole"]["description"]
    assert "rows of the table that the text does not hold are removed" in " ".join(whole.split())


def test_a_row_whose_key_fits_no_row_is_written_only_when_the_call_says_it_is_new(rolebox):
    """F3, F4. A row keyed by its CHANGED first cell was added and the stale row stayed: without a word when several
    rows start alike (five "FvHwFe..." rows on the real fees table), behind "error" when one did; and the right
    rename, a new row beside the old one's drop in one call, was answered "error" though all of it was done. One
    rule: a key that fits no row is a new row only when the call says so ("new": true, with or without a place)."""
    tools, paths, _ = rolebox
    p = _table(paths, FAMILY)
    update = tools("scout")["update_table"]
    out = _call(
        update, rows=[{"key": "FvHwFe: who is first (14 Nov)", "text": "| FvHwFe: who is first (14 Nov) | x | y |"}]
    )
    assert out.startswith('error: not done: no row FvHwFe: who is first (14 Nov) (rows that start alike: "FvHwFe first '
                          'buy, 0.5 SOL"; "FvHwFe: who is first"; "FvHwFe: limits"): nothing was written; to add it '
                          'send it with "new": true (and "after"/"before" for a place); to change or move a row give '
                          "its first cell as it is now")  # fmt: skip
    assert p.read_text() == FAMILY and p.stat().st_mtime == NOW - 3600
    out = _call(update, rows=[{"key": "Z9", "text": "| Z9 | a | b |"}])
    assert out.startswith("error: not done: no row Z9: nothing was written; to add it") and p.read_text() == FAMILY
    # the right rename: the new row where it belongs, the old one dropped, in one call, and no "error"
    out = _call(update, rows=[{"key": "AtifzK: QUALIFIED", "text": "| AtifzK: QUALIFIED | hand trader | +0.09 / 31 |",
                               "before": "FvHwFe first buy, 0.5 SOL", "new": True}], drop=["AtifzK"])  # fmt: skip
    assert out.startswith("scout-facts.md: added 1 (AtifzK: QUALIFIED) to the first table (Wallet); dropped 1 "
                          "(AtifzK)")  # fmt: skip
    assert [row for row in _rows(p) if "AtifzK" in row] == ["| AtifzK: QUALIFIED | hand trader | +0.09 / 31 |"]
    # "new" beside a key that is a row's first cell is two statements that disagree: nothing is written. A key that
    # is only the start of a row's first cell is a new row (round 4: "A1eN4E" beside "A1eN4E + HVM33j (I122)")
    before = p.read_text()
    out = _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth | a | b |", "new": True}])
    assert out.startswith("error: not done: HjuSth is said to be new but there is a row") and p.read_text() == before
    out = _call(update, rows=[{"key": "FLPjyS", "text": "| FLPjyS | a | b |", "new": True}])
    assert out.startswith("scout-facts.md: added 1 (FLPjyS)") and "| FLPjyS: QUALIFIED |" in p.read_text()
    assert _call(update, rows=[{"key": "Z9", "text": "| Z9 | a | b |", "new": True}]).startswith(
        "scout-facts.md: added"
    )


def test_a_whole_table_over_the_cap_is_refused_and_a_table_over_it_can_be_shortened(rolebox):
    """F6. A whole table over 20,000 characters lost its last rows, or all of them (a paragraph of 20,500 characters
    left the title and no row), without "error". It is refused like rows that would pass the cap: nothing changes.
    A table that is over the cap on disk can still be made shorter."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    head = "\n".join(TABLE.split("\n")[:6]) + "\n"
    for big in (head + "".join(f"| r{i} | {'value ' * 60}| x |\n" for i in range(60)),
                head.replace("Two wallets", "word " * 4100) + "| r1 | v | x |\n"):  # fmt: skip
        out = _call(update, text=big, whole=True)
        assert out.startswith(f"error: not done: the table sent is {len(big):,} characters, the limit is 20,000")
        assert "nothing was changed" in out and p.read_text() == TABLE
    p.write_text(head + "".join(f"| r{i} | {'value ' * 60}| x |\n" for i in range(60)))  # as an older write left it
    size = len(p.read_text())
    out = _call(update, drop=["r1", "r2"])
    assert out.startswith("scout-facts.md: dropped 2 (r1; r2)") and 20_000 < len(p.read_text()) < size
    out = _call(update, rows=[{"key": "r3", "text": "| r3 | " + "longer " * 80 + "| x |"}])
    assert out.startswith("error: not done: the table would be") and "20,000" in out


def test_update_table_answers_its_old_argument_headline_in_words(rolebox, env):
    """F7. `headline` was update_table's name for the paragraph above the table until round 2; a model that still
    sends it read "Invalid arguments for function update_table" and nothing else. It is told which argument is
    which; nothing is changed, and the argument is not offered in the tool's description."""
    from snipetech.operator.gate import Decision

    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    said = ('error: not done: update_table has no headline: the paragraph above the table is "above", the turn\'s note '
            "is write_report's headline; nothing was changed")  # fmt: skip
    assert _call(update, rows=[{"key": "HjuSth", "text": "| HjuSth | x | y |"}], headline="What holds.") == said
    assert p.read_text() == TABLE and "headline" not in update.to_dict()["input_schema"]["properties"]
    # and through the turn's own runner, as the model reads it
    (env["paths"].root / "scout-facts.md").write_text(TABLE)
    handler, requests = _turn._scripted(
        _turn._calls(("update_table", {"headline": "What holds for every row."})),
        ([{"type": "text", "text": "closing"}], "end_turn"),
    )
    _turn._run(env, Decision(True, "scout", ["x"]), handler, role="scout")
    shown = [b for b in requests[1]["messages"][-1]["content"] if b.get("type") == "tool_result"]
    assert str(shown[0]["content"]) == said


def test_a_row_that_exists_is_moved_by_before_and_after_and_a_key_in_rows_and_drop_changes_nothing(rolebox):
    """F8. before/after on a row that exists were dropped without a word, and a "move" written as the same key in
    rows and in drop lost the row."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    line = "| HjuSth | scalper | stopped by crowding |"
    out = _call(update, rows=[{"key": "HjuSth", "text": line, "before": "FLPjyS: QUALIFIED"}])
    assert out.startswith("scout-facts.md: moved 1 (HjuSth)") and _rows(p)[2:5] == [line, _rows(p)[3], _rows(p)[4]]
    assert _rows(p)[3].startswith("| FLPjyS: QUALIFIED") and p.read_text().count("HjuSth") == 1
    out = _call(update, rows=[{"key": "HjuSth", "text": line.replace("stopped", "STOPPED"), "after": "AtifzK"}])
    assert "moved 1 (HjuSth)" in out and _rows(p)[4] == line.replace("stopped", "STOPPED")
    before = p.read_text()
    out = _call(update, rows=[{"key": "HjuSth", "text": line, "before": "FLPjyS: QUALIFIED"}], drop=["HjuSth"])
    assert out.startswith("error: not done: HjuSth is in rows and in drop: nothing was changed for it")
    assert p.read_text() == before
    out = _call(update, rows=[{"key": "HjuSth", "text": line, "after": "no such row"}])
    assert out.startswith("error: not done: no row no such row to put HjuSth after: nothing was written")
    assert p.read_text() == before


def test_a_header_a_separator_or_another_rows_first_cell_is_never_written_as_a_row(rolebox):
    """F9. The header or the separator line sent among `rows` became a data row ("added 2 (Wallet; ---)"), and a row
    replaced under a first cell that ANOTHER row has left two rows with one key."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    head, sep = _rows(p)[0], _rows(p)[1]
    out = _call(update, rows=[{"key": "Wallet", "text": head, "new": True}, {"key": "---", "text": sep, "new": True},
                              {"key": "AtifzK", "text": "| AtifzK | hand trader | retired |"}])  # fmt: skip
    assert out.startswith("error: not done: Wallet is a header line of the table, not a row: nothing was written; "
                          "leave it out; --- is a separator line, not a row: nothing was written; leave it out; done: "
                          "replaced 1 (AtifzK)")  # fmt: skip
    assert _rows(p).count(head) == 1 and sum(1 for row in _rows(p) if row.startswith("|---")) == 2
    before = p.read_text()
    out = _call(update, rows=[{"key": "HjuSth", "text": "| AtifzK | scalper | stopped |"}])
    assert out.startswith('error: not done: HjuSth: the first cell "AtifzK" is another row\'s already: nothing was '
                          "written")  # fmt: skip
    # round 4: a new row's key is its first cell or the start of it, so this is refused before its first cell is
    # looked up; the refusal of a taken first cell holds for a rename (above)
    out = _call(update, rows=[{"key": "Z1", "text": "| HjuSth | a | b |", "new": True}])
    assert out.startswith("error: not done: Z1: the row's first cell is HjuSth") and p.read_text() == before


def test_an_empty_note_and_an_empty_headline_are_refused(rolebox):
    """F10. write_note(text=" ") wrote "scout:" as the note and ended the turn."""
    tools, paths, sink = rolebox
    scout = tools("scout")
    assert _call(scout["write_note"], text=" ").startswith("error: not done: the note is empty: nothing was written")
    out = _call(scout["write_report"], name="scout-journal-2026-11-06", text="## entry", headline="  ")
    assert out.startswith("error: not done: the headline is empty: nothing was written")
    assert sink.count == 0 and not paths.notes.exists() and not (paths.root / "scout-journal-2026-11-06.md").exists()
    assert _call(scout["write_report"], name="scout-journal-2026-11-06", text="## entry") == (
        "wrote scout-journal-2026-11-06.md"
    )


def test_a_first_table_needs_a_title_a_header_and_rows_that_fit_it(rolebox):
    """With no table yet, rows alone were written as the table (no title, no header), and a whole table with a row
    of the wrong number of cells went in unchecked: every later call then worked on a file no reader could read."""
    tools, paths, _ = rolebox
    update = tools("scout")["update_table"]
    p = paths.root / "scout-facts.md"
    shape = ('a title line starting with "# ", a paragraph, then the table: its header line, the separator line '
             "(|---|---|) and the rows, each with as many cells as the header")  # fmt: skip
    for text in ("| W1 | herd buyer | +0.02 / 107 |\n| W2 | scalper | stopped |", "# Title\n\nno table here",
                 "| a | b |\n|---|---|\n| r1 | v |"):  # fmt: skip
        out = _call(update, text=text, whole=True)
        assert out.startswith("error: not done: there is no table yet and the text is no whole table: send " + shape)
        assert "nothing was written" in out and not p.exists()
    out = _call(update, text=TABLE.replace("| HjuSth | scalper | stopped by crowding |", "| HjuSth | scalper |"))
    assert out.startswith("error: not done: HjuSth has 2 cells, its table has 3 columns (Wallet | Who, kind | Qualify "
                          "(+3x)): nothing was written")  # fmt: skip
    assert not p.exists()
    assert _call(update, text=TABLE).startswith("scout-facts.md: written whole") and p.exists()
    # a rebuild is checked the same way
    out = _call(
        update, text=TABLE.replace("| HjuSth | scalper | stopped by crowding |", "| HjuSth | scalper |"), whole=True
    )
    assert (
        out.startswith("error: not done: HjuSth has 2 cells")
        and "| HjuSth | scalper | stopped by crowding |" in p.read_text()
    )


def test_the_lab_slots_line_says_what_start_job_will_do(rolebox, jobs, monkeypatch):
    """F12. Two slots, one held by a lab that is no job of the agents', and qualify's mark that it waits: the line
    told the tester "qualify ... gets it before a lab of yours" while start_job started the tester's lab all the
    same (the roles give way only while their own running labs are at their share of the slots)."""
    from snipetech.analysis import lab_lock

    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "2")
    _, paths, _ = rolebox
    kol = '{"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "known": 2}'
    with lab_lock.lab_lock(paths.data, wait=False):
        lab_lock.want(paths.data, "qualify")
        assert learning.lab_slots_line(paths, "tester") == (
            "Lab slots: 1 of 2 free (a lab, a freeze and qualify each take one); qualify waits for a slot too."
        )
        started = jobs.start(["lab", "--trigger", kol, "--hours", "24", "--json"], role="tester",
                             idea=_queue(jobs, "tester"))  # fmt: skip
        assert started["id"]  # as the line said: nothing stood in its way
        lab_lock.want(paths.data, "qualify")
        # now a lab of the agents' runs: they are at their share (one of two), and the next free slot is qualify's
        assert learning.lab_slots_line(paths, "fees").endswith(
            "; qualify waits for the next free one and gets it before a lab of yours."
        )
        assert learning.lab_slots_line(paths, "scout").endswith("gets it before a lab-slot job of yours.")
        with pytest.raises(PermissionError, match="asked for a slot"):
            jobs.start(
                ["lab", "--trigger", kol, "--copy-position", "+0", "--hours", "48", "--json"],
                role="fees",
                idea=_queue(jobs, "fees"),
            )


def test_the_words_left_over_after_round_two_agree_with_the_code(rolebox):
    """A refusal told a role to "say so in a note" (a role writes its note as its journal's headline); the tester and
    the fee analyst were told "two labs run at a time" beside "one lab at a time on a machine under 24 GB" and the
    message's own Lab slots line."""
    import inspect

    from snipetech.operator import jobs as jobs_module

    tools, _, _ = rolebox
    refusal = re.sub(r'"\s*"', "", inspect.getsource(jobs_module.OperatorJobs._check_repeat))  # its strings, joined
    assert "say so in an idea with owner_needed" in refusal and "in a note" not in refusal
    for role in ("tester", "fees"):
        said = " ".join(tools(role)["start_job"].to_dict()["description"].split())
        assert "two labs run at a time" not in said
        assert ("as many labs run at a time as the machine has lab slots (the Lab slots line of a role's message), so "
                "queue the next as soon as one ends") in said  # fmt: skip


def test_a_row_in_rows_and_in_drop_stays_as_it_is_whatever_else_the_call_moves(rolebox):
    """The rows of one call are done in order and the table moves under them: a row that is also in `drop` is found
    again after an earlier row of the call went in above it, and still nothing is changed for it."""
    tools, paths, _ = rolebox
    p = _table(paths)
    update = tools("scout")["update_table"]
    out = _call(update, rows=[{"key": "Z0", "text": "| Z0 | a | b |", "before": "FLPjyS: QUALIFIED", "new": True},
                              {"key": "HjuSth", "text": "| HjuSth | scalper | CHANGED |"}],
                drop=["HjuSth", "AtifzK"])  # fmt: skip
    assert out.startswith("error: not done: HjuSth is in rows and in drop: nothing was changed for it")
    assert "; done: added 1 (Z0) to the first table (Wallet); dropped 1 (AtifzK); " in out
    rows = _rows(p)
    assert rows[2] == "| Z0 | a | b |" and "| HjuSth | scalper | stopped by crowding |" in rows
    assert not any("AtifzK" in row or "CHANGED" in row for row in rows)


# -- round 4 (14 Nov, 22:00): no guess left in the table tool -------------------------------------------------------
# The tables below keep the shapes of the agents' real tables of 14 Nov that the reviewer's proofs used: a scout table
# of seven columns with a row for two wallets, an oktech table whose second table has three columns, and a fee table
# whose rows say "same, 2 SOL" under a parent row.

SCOUT7 = (
    "# Who is worth following (wallet scout), 14 Nov 08:05 UTC\n"
    "\n"
    "A wallet's own profit is made at its own place in the block.\n"
    "\n"
    "| Wallet | Who, kind | Own result | Who stands in its block | Companions | Qualify (+3x) | Source |\n"
    "|---|---|---|---|---|---|---|\n"
    "| FvHwFe: QUALIFIED | own-signal bot of 4 wallets | 72 h +153 SOL / 212 coins | crowded | 3 "
    "| QUALIFIED +0.02 / 177 | SQL 07:10 |\n"
    "| AtifzK | unnamed hand trader | 2.4 d +38.7 SOL / 23 coins | outsider 48 % of 23 | none tracked "
    "| candidate +0.0863 / 20 | SQL 06:40 |\n"
    "| A1eN4E + HVM33j (I122) | unnamed twins | - | fees-bids | each other | A1eN4E +0.0151 / 31 | SQL 06:31 |\n"
    "| HjuSth | unnamed scalper | 7 d +65.8 SOL / 45 coins | gap 18.3 % | not checked | candidate +0.0083 / 27 "
    "| SQL 09:31 |\n"
)
OKTECH = (
    "# OKTech features (features analyst), 14 Nov 07:50 UTC\n"
    "\n"
    "No rule has passed a forward reading.\n"
    "\n"
    "| Feature | Verified? | Changes what | Cheapest test |\n"
    "|---|---|---|---|\n"
    "| Copy landing | quiet 0-1 behind (n 37) | FvHwFe is crowded | none |\n"
    "| FvHwFe copy at our bid (I156) | paper: 10 coins are 67 % of the gain | qualify | I156 |\n"
    "| Timer | 300 s verified | f98f hold | I81 |\n"
    "\n"
    "Candidates (tape, nothing live).\n"
    "\n"
    "| Wallet | Finding | Idea |\n"
    "|---|---|---|\n"
    "| FvHwFe + 3 companions | crowded | I153 |\n"
    "| HjuSth | re-enters | I149 |\n"
    "| YJFQNK | 21 % of buys on LaunchLab | I163 |\n"
)
FEES = (
    "# Fees and bids (fee and place analyst), 14 Nov 08:05 UTC\n"
    "\n"
    "All rows are paper.\n"
    "\n"
    "| Trade, size | Place | Bid | Wins the place | What it makes there | n, job |\n"
    "|---|---|---|---|---|---|\n"
    "| **FvHwFe first buy, 0.5 SOL (I20)** | +2 ASSUMED | 0.036 charged | not known | +0.05 | 69 test |\n"
    "| same, 0.5 SOL | +10 ASSUMED | 0.005 charged | none | +0.119 | 76 test |\n"
    "| same, 1 SOL | +10 ASSUMED | 0.005 charged | same | +0.245 | 76 test |\n"
    "| same, 2 SOL | +10 ASSUMED | 0.005 charged | same | +0.446, lower +0.244 | 76 test |\n"
    "| **FvHwFe: what a place is worth** | 0.5 SOL: +2 to +6 | 0.031 more | - | a place | 177 tape |\n"
    "| Leck, 2 SOL | +0 assumed | 0.05 | quiet | -0.114 | 72088a |\n"
)


def _rows4(p):
    return [line for line in p.read_text().split("\n") if line.startswith("|")]


def test_a_row_one_cell_short_is_refused_and_never_gets_the_key_put_in_front(rolebox):
    """Proof P1 of round 4: a rename (the key the old first cell, the text the row with its new one) with one cell
    forgotten was taken for "the row without its key", the key was put in front and every cell stood one heading to
    the right, answered "replaced 1". A row is now written only as it was sent: one cell short is refused and the
    answer asks for the row whole."""
    tools, paths, _ = rolebox
    p = _table(paths, SCOUT7)
    row = ("| AtifzK: QUALIFIED | unnamed hand trader | 2.4 d +38.7 SOL | outsider 48 % | QUALIFIED +0.09 / 31 "
           "| SQL 09:10 |")
    out = _call(tools("scout")["update_table"], rows=[{"key": "AtifzK", "text": row}])
    assert out.startswith("error: not done: AtifzK has 6 cells, its table has 7 columns (Wallet | Who, kind")
    assert "send all 7 cells of the row, its first cell first" in out and p.read_text() == SCOUT7
    # the same with the seventh cell is a rename, written as sent
    out = _call(tools("scout")["update_table"], rows=[{"key": "AtifzK", "text": row[:-1] + "| none tracked |"}])
    assert "replaced 1 (AtifzK)" in out and row[:-1] + "| none tracked |" in _rows4(p)


def test_a_new_row_goes_to_the_end_of_the_table_its_cells_fit_as_it_was_sent(rolebox):
    """Proof P1b: a new candidate of three cells for the second table (three columns), key the wallet and first cell
    the wallet with a word more, landed in the FIRST table (four columns) with the key put in front. It now stands as
    sent at the end of the table it is placed in (round 5: a row for the second table says "after" with a key of it;
    without a place it is refused, test_a_new_row_without_a_place_goes_only_to_the_first_table)."""
    tools, paths, _ = rolebox
    p = _table(paths, OKTECH, "oktech-facts")
    new = "| 9L5aWu (fresh wallet) | sells in one sale within 40 s | I173 |"
    out = _call(tools("oktech")["update_table"], rows=[{"key": "9L5aWu", "text": new, "new": True, "after": "YJFQNK"}])
    assert "added 1 (9L5aWu) to the table Wallet" in out and not out.startswith("error")
    lines = p.read_text().split("\n")
    assert lines.index(new) == lines.index("| YJFQNK | 21 % of buys on LaunchLab | I163 |") + 1
    assert not any(line.startswith("| 9L5aWu | 9L5aWu") for line in lines)


def test_a_key_written_with_its_pipes_is_the_first_cell_and_leaves_no_empty_first_cell(rolebox):
    """Proof P1c: a key written "| HjuSth |" with a row one cell short and "new": true left a row of nine cells in a
    table of seven with an empty first cell, and the old row stayed. The pipes around a key are no part of it: the
    key is HjuSth, which is a row already, and nothing is written."""
    tools, paths, _ = rolebox
    p = _table(paths, SCOUT7)
    update = tools("scout")["update_table"]
    short = "| HjuSth | unnamed scalper | 7 d +65.8 SOL / 45 coins | gap 18.3 % | candidate +0.0083 / 27 | SQL 09:31 |"
    out = _call(update, rows=[{"key": "| HjuSth |", "text": short, "new": True}])
    assert out.startswith("error: not done: ") and p.read_text() == SCOUT7
    assert not any(line.startswith("| |") for line in _rows4(p))
    whole = short[:-1] + "| CHANGED |"
    out = _call(update, rows=[{"key": "| **HjuSth** |", "text": whole}])
    assert "replaced 1 (HjuSth)" in out and whole in _rows4(p)


def test_a_new_row_whose_first_cell_starts_another_rows_is_added_and_never_replaces_that_row(rolebox):
    """Proof P2: the scout table has "A1eN4E + HVM33j (I122)". A row of its own for A1eN4E with "new": true was
    refused with advice to send what had been sent; with "after" it replaced the twins' row (HVM33j's numbers gone,
    "moved 1"). A row said to be new is matched by its whole first cell only; a place without "new" on a key that is
    only the start of a row writes nothing and says both ways out."""
    tools, paths, _ = rolebox
    p = _table(paths, SCOUT7)
    new = {"key": "A1eN4E", "text": "| A1eN4E | unnamed | - | fees-bids | HVM33j | QUALIFIED +0.03 / 41 | SQL 09:46 |"}
    out = _call(tools("scout")["update_table"], rows=[dict(new, after="FvHwFe: QUALIFIED")])
    assert out.startswith('error: not done: no row A1eN4E. Rows whose first cell starts with it: "A1eN4E + HVM33j '
                          '(I122)": to change, move or drop one give its whole first cell as the key; to add a new row '
                          'A1eN4E send "new": true (with "after" for a place)')  # fmt: skip
    assert p.read_text() == SCOUT7
    out = _call(tools("scout")["update_table"], rows=[dict(new, new=True, after="FvHwFe: QUALIFIED")])
    assert "added 1 (A1eN4E)" in out and not out.startswith("error")
    rows = _rows4(p)
    assert rows[3] == new["text"] and any(r.startswith("| A1eN4E + HVM33j (I122) |") for r in rows)
    assert len(rows) - 2 == 5
    # "new" alone: to the end of the table
    p = _table(paths, SCOUT7)
    out = _call(tools("scout")["update_table"], rows=[dict(new, new=True)])
    assert "added 1 (A1eN4E)" in out and _rows4(p)[-1] == new["text"] and len(_rows4(p)) - 2 == 5


def test_a_new_row_whose_first_cell_starts_two_rows_is_a_new_row(rolebox):
    """Proof P2b: a new candidate FvHwFe beside "FvHwFe copy at our bid (I156)" and "FvHwFe + 3 companions" was
    refused three times with "give more of the first cell": there is no more to give. With "new": true it is new;
    without it the answer says how to add it."""
    tools, paths, _ = rolebox
    p = _table(paths, OKTECH, "oktech-facts")
    new = {"key": "FvHwFe", "text": "| FvHwFe | alone, without its companions | I180 |"}
    out = _call(tools("oktech")["update_table"], rows=[dict(new, after="HjuSth")])
    assert out.startswith('error: not done: no row FvHwFe. Rows whose first cell starts with it: "FvHwFe copy at our '
                          'bid (I156)"; "FvHwFe + 3 companions"')  # fmt: skip
    assert 'to add a new row FvHwFe send "new": true' in out and p.read_text() == OKTECH
    out = _call(tools("oktech")["update_table"], rows=[dict(new, new=True, after="HjuSth")])
    assert "added 1 (FvHwFe)" in out
    lines = p.read_text().split("\n")
    assert lines[lines.index("| HjuSth | re-enters | I149 |") + 1] == new["text"]


def test_a_first_cell_that_stands_twice_on_disk_is_answered_with_the_one_way_out(rolebox):
    """Proof P4: "same, 2 SOL" under two parent rows (the agents use ditto cells; an older write took a whole table
    unchecked). A change was refused with no way out, again on the next reply, and a drop removed the first of the two
    silently. Both are now refused with the one call that works: the table whole, each row its own first cell."""
    tools, paths, _ = rolebox
    twin = "| same, 2 SOL | +2 ASSUMED | 0.036 charged | same | running | 9e9 |"
    text = FEES.replace("| **FvHwFe: what a place is worth**", twin + "\n| **FvHwFe: what a place is worth**", 1)
    p = _table(paths, text, "fees-bids")
    update = tools("fees")["update_table"]
    say = ('two rows have the first cell "same, 2 SOL" (lines 10 and 11): send the table whole with '
           "update_table(text, whole=true) and give each its own first cell")  # fmt: skip
    change = [{"key": "same, 2 SOL", "text": "| same, 2 SOL | +2 ASSUMED | 0.036 charged | same | +0.31 | 9e9 |"}]
    out = _call(update, rows=change)
    assert out.startswith(f"error: not done: {say}") and p.read_text() == text
    out = _call(update, drop=["same, 2 SOL"])
    assert out.startswith(f"error: not done: {say}") and p.read_text() == text
    out = _call(update, rows=[dict(change[0], new=True)])
    assert out.startswith(f"error: not done: {say}") and p.read_text() == text
    # the way out works
    fixed = text.replace(twin, twin.replace("same, 2 SOL", "same at +2, 2 SOL"))
    out = _call(update, text=fixed, whole=True)
    assert out.startswith("fees-bids.md: written whole") and "| same at +2, 2 SOL |" in p.read_text()


def test_a_row_the_call_never_named_is_never_removed(rolebox):
    """Proof P8: rows to drop were kept by line number. A key that fits a dropped row only after earlier rows of the
    call renamed its look-alikes moved it, and the drop then took the line that slid into its place ("**FvHwFe: what
    a place is worth**", named by no key). A row of the call that finds a row to drop now writes nothing."""
    tools, paths, _ = rolebox
    p = _table(paths, FEES, "fees-bids")
    lost = "| **FvHwFe: what a place is worth** | 0.5 SOL: +2 to +6 | 0.031 more | - | a place | 177 tape |"
    rows = [{"key": "same, 0.5 SOL", "text": "| FvHwFe 0.5 at +10 | +10 ASSUMED | 0.005 charged | - | +0.119 | 76 |"},
            {"key": "same, 1 SOL", "text": "| FvHwFe 1 at +10 | +10 ASSUMED | 0.005 charged | none | +0.245 | 76 |"}]
    moved = {"key": "same", "text": "| same | +10 ASSUMED | 0.005 charged | none | +0.446 | 76 test |"}
    out = _call(tools("fees")["update_table"], rows=[*rows, dict(moved, after="Leck, 2 SOL")], drop=["same, 2 SOL"])
    assert out.startswith("error: not done: ") and lost in _rows4(p)
    assert "replaced 2" in out and "dropped 1 (same, 2 SOL)" in out
    assert not any(r.startswith(("| same |", "| same, 2 SOL")) for r in _rows4(p))
    # the same without a place: the row found is one to drop, so nothing is changed for it
    p = _table(paths, FEES, "fees-bids")
    out = _call(tools("fees")["update_table"], rows=[*rows, moved], drop=["same, 2 SOL"])
    assert out.startswith("error: not done: ") and lost in _rows4(p)
    assert not any(r.startswith("| same |") for r in _rows4(p)) and "dropped 1 (same, 2 SOL)" in out


def test_whole_true_with_a_text_that_is_no_whole_table_is_refused(rolebox):
    """Proof P7: a prune sent as header, separator and the rows to keep, without the "# " title line, with whole=true:
    taken as rows, "the same already", nothing removed and no error. whole=true needs the whole table."""
    from snipetech.operator.tools import TABLE_SHAPE

    tools, paths, _ = rolebox
    p = _table(paths, SCOUT7)
    keep = "\n".join(_rows4(p)[:4])
    out = _call(tools("scout")["update_table"], text=keep, whole=True)
    assert out == (f"error: not done: whole=true needs the whole table ({TABLE_SHAPE}): nothing was changed; send the "
                   "table whole, or its changed rows in rows without whole")  # fmt: skip
    assert p.read_text() == SCOUT7


def test_a_journal_entry_sent_again_is_not_written_twice(rolebox):
    """Proof P5: reply 1 held a refused row and the journal entry with its headline; the model sent its reply again
    with the row repaired, and the entry stood in the journal twice. A journal that ends with this entry already is
    not written again, and the answer says so without "error" (the turn goes its way)."""
    tools, paths, _ = rolebox
    tester = tools("tester")
    name = "tester-journal-2026-11-06"
    entry = {"name": name, "text": "## 01:00 UTC\n\nRead lab 9c6a6e: lower -0.031 (n 44).",
             "headline": "the finding: +0.2 SOL a copy (n 41)"}  # fmt: skip
    first = _call(tester["write_report"], **entry)
    assert "the headline is written as this turn's note" in first
    again = _call(tester["write_report"], **entry)
    assert again.startswith("the journal has this entry already: not written again") and "error" not in again
    assert (paths.root / f"{name}.md").read_text().count("Read lab 9c6a6e") == 1
    assert paths.notes.read_text().count("+0.2 SOL a copy") == 1
    # a new turn that sends it again with no note written yet: not written again, and the note is written
    other = tools("tester")
    out = _call(other["write_report"], **entry)
    assert out.startswith("the journal has this entry already: not written again")
    assert "the headline is written as this turn's note" in out
    # another entry is written as always, and so is one that is only the end of the last line
    assert _call(other["write_report"], name=name, text="## 02:00 UTC\n\nall done").startswith("appended to")
    assert _call(other["write_report"], name=name, text="done").startswith("appended to")


def test_a_wrong_argument_type_is_answered_in_words(rolebox):
    """Proof P6: `rows` as JSON text, `drop` as one string, an argument of write_report's on update_table: the model
    read only the SDK's "Invalid arguments for function update_table". It reads what to send."""
    tools, paths, _ = rolebox
    p = _table(paths, SCOUT7)
    update = tools("scout")["update_table"]
    row = [{"key": "HjuSth", "text": "| HjuSth | a | b | c | d | e | f |"}]
    for kw in ({"rows": json.dumps(row)}, {"drop": "HjuSth"}, {"name": "scout-facts", "rows": row}):
        out = _call(update, **kw)
        assert out.startswith("error: not done: rows is a list of {\"key\", \"text\"} objects, drop is a list of keys")
        assert out.endswith("nothing was changed") and "Invalid arguments" not in out
    assert "update_table takes no name" in _call(update, name="scout-facts", rows=row)
    assert p.read_text() == SCOUT7
    # the other writing tools a model reaches the same way
    out = _call(tools("scout")["upsert_idea"], title="t", priority="high")
    assert out.startswith("error: not done: ") and "priority" in out and "Invalid arguments" not in out
    out = _call(tools("scout")["write_report"], name=["scout-journal-2026-11-06"], text="x")
    assert out.startswith("error: not done: ") and "name" in out and "Invalid arguments" not in out
    out = _call(tools()["upsert_idea"], title="t", priority="high")
    assert out.startswith("error: not done: ") and "priority" in out


def test_the_same_row_twice_in_one_call_is_not_written(rolebox):
    """R9: the same key twice in one call wrote the last and said "replaced 2 (K; K)". Two rows of a call for one row
    of the table write nothing for it; a key that is not short is said to be what it is."""
    tools, paths, _ = rolebox
    p = _table(paths, OKTECH, "oktech-facts")
    update = tools("oktech")["update_table"]
    a = {"key": "Timer", "text": "| Timer | 300 s verified | a | b |"}
    b = {"key": "TIMER", "text": "| Timer | 300 s verified | c | d |"}
    out = _call(update, rows=[a, b])
    assert out.startswith("error: not done: Timer is sent twice in this call: nothing was written for it")
    assert p.read_text() == OKTECH
    c = {"key": "**fvhwfe COPY at our bid (I156)**", "text": "| FvHwFe copy at our bid (I156) | x | y | z |"}
    d = {"key": "FvHwFe copy at our bid (I156)", "text": "| FvHwFe copy at our bid (I156) | x | y | w |"}
    out = _call(update, rows=[c, d, {"key": "HjuSth", "text": "| HjuSth | re-enters twice | I149 |"}])
    assert out.startswith("error: not done: ") and "sent twice in this call" in out and "replaced 1 (HjuSth)" in out
    assert "| FvHwFe copy at our bid (I156) | paper: 10 coins are 67 % of the gain | qualify | I156 |" in _rows4(p)
    long_key = "K" * 1300
    out = _call(update, rows=[{"key": long_key, "text": f"| {long_key} | a | b | c |", "new": True}])
    assert out.startswith("error: not done: ") and "first cell is 1,300 characters" in out
    assert "cut at 1,200" not in out and long_key not in p.read_text()


# -- round 5 (14 Nov, 22:50): the last read of round 4, on the agents' own four tables ----------------------------
# tests/fixtures/agent_tables_14nov holds the four tables as the server had them on 14 Nov (research rows, no secret).

REAL4 = os.path.join(os.path.dirname(__file__), "..", "fixtures", "agent_tables_14nov")
REAL_NAMES = {"scout": "scout-facts", "tester": "tester-rules", "fees": "fees-bids", "oktech": "oktech-facts"}
REAL_COLS = {"scout": 7, "tester": 10, "fees": 6, "oktech": 4}


def _real(paths, role):
    """The role's real table of 14 Nov as the file of this test."""
    with open(os.path.join(REAL4, f"{REAL_NAMES[role]}.md")) as f:
        return _table(paths, f.read(), REAL_NAMES[role])


def _quiet(out):
    from snipetech.operator.tools import CUT_NOTE

    return not out.lower().startswith("error") and CUT_NOTE not in out


def test_a_new_row_without_a_place_goes_only_to_the_first_table(rolebox):
    """F1: a feature row one cell short (three cells in a table of four) was filed silently in the oktech file's
    second table, the candidates (three columns), answered "added 1 (Guard v2)". A new row without before/after goes
    to the first table only; a row whose cells fit only another table is refused with the place that puts it there.
    Every "added" of a file with more than one table names the table."""
    tools, paths, _ = rolebox
    p = _real(paths, "oktech")
    before = p.read_text()
    update = tools("oktech")["update_table"]
    out = _call(update, rows=[{"key": "Guard v2", "text": "| Guard v2 | not verified | FvHwFe |", "new": True}])
    assert out.startswith("error: not done: Guard v2 has 3 cells, the first table has 4 columns (Feature")
    assert 'for the table Wallet give "after" with a key of that table' in out and p.read_text() == before
    out = _call(update, rows=[{"key": "Guard v2", "text": "| Guard v2 | not verified | FvHwFe |", "new": True,
                               "after": "HjuSth"}])  # fmt: skip
    assert "added 1 (Guard v2) to the table Wallet" in out
    out = _call(update, rows=[{"key": "Guard v3", "text": "| Guard v3 | not verified | FvHwFe | none |", "new": True}])
    assert "added 1 (Guard v3) to the table Feature" in out
    lines = p.read_text().split("\n")
    assert lines.index("| Guard v3 | not verified | FvHwFe | none |") < lines.index("| Wallet | Finding | Idea |")
    rows = " ".join(update.to_dict()["input_schema"]["properties"]["rows"]["description"].split())
    assert '{"key", "text", "new": true} goes to the end of the first table' in rows
    assert 'a row for another table: give "after" with a key of that table' in rows


@pytest.mark.parametrize("role,key,gone", [
    ("scout", "A1eN4E", "A1eN4E + HVM33j (I122)"), ("scout", "7aPo3n", "7aPo3n, AG89fn"),
    ("scout", "ATDYz6", "ATDYz6 + DdHH2n + Edeuun"), ("tester", "396fa05089", "396fa05089; 4d81cdb14a"),
    ("tester", "efe459e8a2", "efe459e8a2; 2ebf04efe8"), ("oktech", "Platforms", "Platforms vs LaunchLab"),
    ("oktech", "66hAs8", "66hAs8 (copy bot, not a leader)")])  # fmt: skip
def test_a_row_named_by_the_start_of_another_rows_first_cell_never_replaces_it(rolebox, role, key, gone):
    """F2: a key that is only the start of ONE row's first cell, sent without "new" with a text whose first cell is
    that key whole, replaced the row ("A1eN4E + HVM33j (I122)": HVM33j's numbers gone). Round 6: such a key finds no
    row at all; the answer names the row whole and both ways out."""
    tools, paths, _ = rolebox
    p = _real(paths, role)
    before = p.read_text()
    n = 3 if key == "66hAs8" else REAL_COLS[role]
    out = _call(tools(role)["update_table"], rows=[{"key": key, "text": _r(n, key)}])
    assert out.startswith(f'error: not done: no row {key}. Rows whose first cell starts with it: "{gone}')
    assert f'to add a new row {key} send "new": true' in out and p.read_text() == before


def test_a_row_is_changed_and_renamed_only_by_its_whole_first_cell(rolebox):
    """Round 5 kept two cases of a key that is the start of a first cell as legitimate (the text's whole first cell,
    and a rename); round 6 (A) refuses both: the start of a first cell finds no row. Keyed by the whole first cell,
    the change and the rename are done."""
    tools, paths, _ = rolebox
    p = _real(paths, "tester")
    before = p.read_text()
    update = tools("tester")["update_table"]
    whole = "efe459e8a2; 2ebf04efe8 (+5 same-signal records)"
    for text in (_r(10, whole, "y"), _r(10, "efe459e8a2 (frozen)", "y")):
        out = _call(update, rows=[{"key": "efe459e8a2", "text": text}])
        assert out.startswith(f'error: not done: no row efe459e8a2. Rows whose first cell starts with it: "{whole}"')
        assert p.read_text() == before
    out = _call(update, rows=[{"key": whole, "text": _r(10, whole, "y")}])
    assert out.startswith(f"tester-rules.md: replaced 1 ({whole[:39]}")
    out = _call(update, rows=[{"key": whole, "text": _r(10, "efe459e8a2 (frozen)", "y")}])
    assert "replaced 1" in out and not out.startswith("error") and "| efe459e8a2 (frozen) |" in p.read_text()


@pytest.mark.parametrize("rows", [
    [{"key": "ANTUPU", "text": "| ANTUPU | a | b | c | d | e | f |", "new": True},
     {"key": "ANTUPU", "text": "| ANTUPU (I122) upd | a | b | c | d | e | f |"}],
    [{"key": "HjuSth", "text": "| HjuSth v2 | a | b | c | d | e | f |"},
     {"key": "HjuSth v2", "text": "| HjuSth v2 | z | z | z | z | z | z |"}],
])  # fmt: skip
def test_a_later_row_of_the_call_never_overwrites_a_row_the_call_just_wrote(rolebox, rows):
    """F3: the loop looked every key up again on the table as it changed: a later row of the call found the row an
    earlier one had just added and overwrote it ("replaced 1 (ANTUPU); added 1 (ANTUPU)")."""
    tools, paths, _ = rolebox
    p = _real(paths, "scout")
    out = _call(tools("scout")["update_table"], rows=rows)
    assert out.startswith("error: not done: ")
    text = p.read_text()
    assert ("ANTUPU (I122) upd" not in text) and ("| z | z |" not in text)
    if rows[0]["key"] == "HjuSth":  # round 6: a rename, then a row keyed by the new first cell
        assert rows[0]["text"] in text and "HjuSth v2 finds the row this call just wrote: send it once" in out
    else:
        assert "replaced" not in out


def test_one_key_twice_in_a_call_is_refused_also_where_it_fits_several_rows(rolebox):
    """F3b: "One row once a call" held only for a key that fits one row."""
    tools, paths, _ = rolebox
    p = _real(paths, "oktech")
    before = p.read_text()
    out = _call(tools("oktech")["update_table"], rows=[{"key": "Copy", "text": "| Copy | A | B | C |", "new": True},
                                                       {"key": "Copy", "text": "| Copy | D | E | F |"}])  # fmt: skip
    assert out.startswith("error: not done: Copy is sent twice in this call") and p.read_text() == before
    assert "a change and a new row with one key: one call each" in out


def test_a_row_with_an_empty_first_cell_is_refused(rolebox):
    """F4: a change whose first cell was empty was written, and the row could then be reached by no key."""
    tools, paths, _ = rolebox
    p = _real(paths, "scout")
    before = p.read_text()
    update = tools("scout")["update_table"]
    out = _call(update, rows=[{"key": "HjuSth", "text": "|  | unnamed scalper | 7 d | gap | nc | cand | SQL |"}])
    assert out.startswith("error: not done: HjuSth: the row's first cell is empty") and p.read_text() == before
    out = _call(update, rows=[{"key": "HjuSth", "text": "| ** | unnamed scalper | 7 d | gap | nc | cand | SQL |"}])
    assert out.startswith("error: not done: HjuSth: the row's first cell is empty") and p.read_text() == before


def test_an_empty_paragraph_is_no_paragraph_and_removes_nothing(rolebox):
    """F5: above="" or below="" removed the paragraph, answered "replaced"; an empty text is "not given" elsewhere."""
    tools, paths, _ = rolebox
    p = _real(paths, "oktech")
    before = p.read_text()
    out = _call(tools("oktech")["update_table"], above="", below="  ")
    assert not out.startswith("error") and p.read_text() == before and "replaced" not in out


def test_a_new_row_sent_again_as_it_stands_is_the_same_already(rolebox):
    """F6: a repair reply that sends the whole call again got an error for the new row that stood as sent."""
    tools, paths, _ = rolebox
    p = _real(paths, "scout")
    update = tools("scout")["update_table"]
    row = {"key": "NEWW", "text": "| NEWW | a | b | c | d | e | f |", "new": True}
    assert "added 1 (NEWW)" in _call(update, rows=[row])
    stamp = p.read_text()
    out = _call(update, rows=[row])
    assert _quiet(out) and "the same already 1 (NEWW)" in out and p.read_text() == stamp
    prefixed = {"key": "NEWX", "text": "| NEWX (I200) | a | b | c | d | e | f |", "new": True}
    assert "added 1 (NEWX)" in _call(update, rows=[prefixed])
    out = _call(update, rows=[prefixed])
    assert _quiet(out) and "the same already 1 (NEWX (I200))" in out


def test_a_journal_entry_sent_again_without_its_headline_is_not_written_twice(rolebox):
    """F7: the journal ended with the entry and its "In short: <note>" line, so the entry sent again without the
    headline did not match and stood twice."""
    tools, paths, _ = rolebox
    scout = tools("scout")
    name = "scout-journal-2026-11-06"
    _call(scout["write_report"], name=name, text="## 01:00\nwhat the turn did.", headline="finding A (n 4)")
    out = _call(scout["write_report"], name=name, text="## 01:00\nwhat the turn did.")
    assert out.startswith("the journal has this entry already: not written again")
    assert (paths.root / f"{name}.md").read_text().count("what the turn did.") == 1


@pytest.mark.parametrize("role,name,kw", [
    ("scout", "write_note", {"text": "n", "level": "info"}),
    ("scout", "start_job", {"argv": "lab --json", "idea": "I1"}),
    ("scout", "add_lesson", {"text": "t", "evidence": "e", "n": "41 trades"}),
    (None, "write_note", {"text": ["n"]}), (None, "add_lesson", {"text": "t", "evidence": "e", "n": "x"}),
    (None, "write_report", {"name": ["analyst-2026-11-06"], "text": "x"}),
    (None, "start_job", {"argv": "lab --json"})])  # fmt: skip
def test_every_writing_tool_answers_a_wrong_argument_in_words(rolebox, role, name, kw):
    """F9: write_note with `level`, start_job with argv as one string, add_lesson with n as "41 trades": the model
    read only the SDK's "Invalid arguments". Every tool of turn.WRITE_TOOLS, for a role and for the analyst."""
    from snipetech.operator.turn import WRITE_TOOLS

    tools, _, _ = rolebox
    made = tools(role)
    out = _call(made[name], **kw)
    assert out.startswith("error: not done: ") and "Invalid arguments" not in out
    for tool in made.values():
        if tool.name in WRITE_TOOLS:
            assert getattr(tool, "answers_bad_arguments", False), tool.name


def test_a_cut_note_or_headline_is_said_without_the_cut_phrase(rolebox):
    """F10: a note is written when it is cut; a further reply can do nothing about it, so its notice does not hold
    CUT_NOTE (which keeps a turn open)."""
    from snipetech.operator.tools import CUT_NOTE

    tools, _, _ = rolebox
    long = "a finding with numbers " * 50
    out = _call(tools("scout")["write_note"], text=long)
    assert "shortened to 700 characters" in out and CUT_NOTE not in out
    out = _call(tools("fees")["write_report"], name="fees-journal-2026-11-06", text="entry", headline=long)
    assert "the note was shortened to 700 characters" in out and CUT_NOTE not in out


def _r(n, first, word="x"):
    return "| " + " | ".join([first] + [word] * (n - 1)) + " |"


def _first_cells(text):
    from snipetech.operator.tools import table_keys

    return table_keys(text)


def _twins_fees(text):
    return text.replace("| **FvHwFe: what a place is worth**",
                        "| same, 2 SOL | +2 ASSUMED | 0.036 charged | same | running | 9e9 |\n"
                        "| **FvHwFe: what a place is worth**", 1)  # fmt: skip


def _no_table(_):
    return "# Fees and bids\n\nnothing measured yet\n"


def _of(text, n):
    """The first cell of the first row of a table of `text` that has n columns."""
    from snipetech.operator.tools import _blocks, _cells

    lines = text.split("\n")
    return next(_cells(lines[f])[0] for s, f, e in _blocks(lines) if f < e and len(_cells(lines[s])) == n)


# Each error update_table gives, with ONE advice for its cause, and how that advice is followed to the letter:
# (what the answer says, the fix of the call: (kwargs, table text) -> kwargs). The order matters where two match.
def _drop_key(kw, k):
    return {**kw, "drop": [d for d in kw["drop"] if d != k]} if kw.get("drop") else kw


def _rows_set(kw, **each):
    return {**kw, "rows": [{**r, **each} for r in kw["rows"]]}


def _fix_count(kw, text):
    from snipetech.operator.tools import _cells

    m = int(re.search(r"has (\d+) columns", kw["_answer"]).group(1))
    out = []
    for r in kw["rows"]:
        c = _cells(r["text"])
        c = c + ["x"] * (m - len(c)) if len(c) < m else c[: m - 1] + ["\\|".join(c[m - 1 :])]
        out.append({**r, "text": "| " + " | ".join(c) + " |"})
    return {**kw, "rows": out}


def _named(kw):
    """The first whole first cell an answer names in double quotes."""
    return re.search(r'starts with it: "([^"]*)"', kw["_answer"]).group(1)


def _fix_place(kw, text):
    n = int(re.search(r"has (\d+) cells", kw["_answer"]).group(1))
    return _rows_set(kw, after=_of(text, n))  # "give after", nothing else: a new row stays new, a move a move


ADVICE = [
    (r"give each row its key and its text", lambda kw, t: {"rows": [{"key": "AtifzK", "text": next(
        line for line in t.split("\n") if line.startswith("| AtifzK")).replace("hand", "HAND")}]}),
    (r"is a (separator|header) line.*leave it out", lambda kw, t: {"rows": kw["rows"][1:]}),
    (r"first cell is empty: nothing was written; give the row its first cell, its key",
     lambda kw, t: {"rows": [{**r, "text": r["text"].replace("|  |", f"| {r['key']} |", 1)} for r in kw["rows"]]}),
    (r"send the table whole with update_table\(text, whole=true\) and give each its own first cell",
     lambda kw, t: {"text": t.replace("| same, 2 SOL | +2 A", "| same at +2, 2 SOL | +2 A"), "whole": True}),
    (r"Rows whose first cell starts with it: .*: give the whole first cell of the row to put it",
     lambda kw, t: _rows_set(kw, after=_named(kw))),
    (r"Rows whose first cell starts with it: .*: to change, move or drop one give its whole first cell as the key",
     lambda kw, t: {**kw, "rows": [{**r, "key": _named(kw)} for r in kw["rows"]]}),
    (r"to drop\. Rows whose first cell starts with it: .*: to drop one give its whole first cell as the key",
     lambda kw, t: {"drop": [_named(kw)]}),
    (r"no row .+? to drop: nothing to do for it; leave it out", lambda kw, t: {**kw, "drop": []}),
    (r"in drop.*send it in rows to change it, or in drop to remove it",
     lambda kw, t: {"rows": kw["rows"]}),
    (r"finds the row this call just wrote: send it once", lambda kw, t: {"rows": kw["rows"][:1]}),
    (r"sent twice in this call: nothing was written for it; send it once", lambda kw, t: {"rows": kw["rows"][:1]}),
    (r'give "(after|before)" the first cell of a row that is there', lambda kw, t: _rows_set(kw, after="HjuSth")),
    (r'is said to be new but there is a row .*leave "new" out',
     lambda kw, t: {"rows": [{k: v for k, v in r.items() if k != "new"} for r in kw["rows"]]}),
    (r'it has the columns of .*give "after" with a key of that table', _fix_place),
    (r"send all \d+ cells of the row|a \| inside a cell is written", _fix_count),
    (r"give it a short first cell", lambda kw, t: {"rows": [{"key": "Kshort", "text": _r(7, "Kshort"), "new": True}]}),
    (r"is another row's already: nothing was written; give the row a first cell of its own",
     lambda kw, t: {"rows": [{**r, "text": re.sub(r"^\| ([^|]+?) \|", r"| \1 v2 |", r["text"])} for r in kw["rows"]]}),
    (r'no row \S+.*to add it send it with "new": true', lambda kw, t: _rows_set(kw, new=True)),
    (r"send its first cell as the key", lambda kw, t: {"rows": [{**r, "key": r["text"].split("|")[1].strip()}
                                                                 for r in kw["rows"]]}),
    (r"the file has no table.*send the table whole \(text\)", lambda kw, t: {"text": kw["_real"]}),
    (r"drop or shorten rows first", lambda kw, t: {"rows": kw["rows"][:1]}),
    (r"send rows, drop, above and below without text, or the whole table without them",
     lambda kw, t: {k: v for k, v in kw.items() if k != "text"}),
    (r"whole=true needs the whole table.*send the table whole", lambda kw, t: {"text": t, "whole": True}),
    (r"has \d+ cells, its table has \d+ columns.*: nothing was written; send a title line",
     lambda kw, t: {"text": kw["_real"]}),
    (r"the limit is 20,000: nothing was changed; shorten or leave out rows", lambda kw, t: {"text": kw["_real"]}),
    (r"or this text with whole=true to remove the others", lambda kw, t: {**kw, "whole": True}),
    (r"send the rows that changed in rows, or the table whole", lambda kw, t: {"text": t}),
    (r"no table row and was left out \(the paragraph above the table is `above`",
     lambda kw, t: {"text": "\n".join(x for x in kw["text"].split("\n") if x.startswith("|"))}),
    (r"is not written yet: send it whole first", lambda kw, t: {"text": kw["_real"]}),
    (r"update_table has no headline: the paragraph above the table is \"above\"",
     lambda kw, t: {"above": kw["headline"]}),
    (r"update_table takes no", lambda kw, t: {k: v for k, v in kw.items() if k == "rows"}),
    (r'rows is a list of \{"key", "text"\} objects, drop is a list of keys',
     lambda kw, t: {"rows": json.loads(kw["rows"])} if isinstance(kw.get("rows"), str) else {"drop": [kw["drop"]]}),
]

_HUGE = [{"key": f"Big{i}", "text": _r(7, f"Big{i}", "y" * 160), "new": True} for i in range(16)]
# (role, how the file is made from the real table, the call): each makes one error of its own cause
CASES = [
    ("scout", None, {"rows": [{"key": "AtifzK"}]}),
    ("scout", None, {"rows": [{"key": "---", "text": "|---|---|---|---|---|---|---|"}]}),
    ("scout", None, {"rows": [{"key": "Wallet", "text": "| Wallet | Who, kind | Own result (own place) | Who stands in "
                                                        "its block | Companions | Qualify (+3x) | Source |"}]}),
    ("scout", None, {"rows": [{"key": "HjuSth", "text": "|  | a | b | c | d | e | f |"}]}),
    ("fees", _twins_fees, {"rows": [{"key": "same, 2 SOL", "text": _r(6, "same, 2 SOL")}]}),
    ("fees", _twins_fees, {"drop": ["same, 2 SOL"]}),
    ("fees", _twins_fees, {"rows": [{"key": "Zz", "text": _r(6, "Zz"), "new": True, "after": "same, 2 SOL"}]}),
    ("fees", None, {"rows": [{"key": "Zz", "text": _r(6, "Zz"), "new": True, "after": "same"}]}),
    ("fees", None, {"rows": [{"key": "FvHwFe", "text": _r(6, "FvHwFe")}]}),
    ("fees", None, {"drop": ["same"]}),
    ("scout", None, {"drop": ["Nobody"]}),
    ("scout", None, {"rows": [{"key": "HjuSth", "text": _r(7, "HjuSth")}], "drop": ["HjuSth"]}),
    ("fees", None, {"rows": [{"key": "same, 0.5 SOL", "text": _r(6, "FvHwFe 0.5 at +10")},
                             {"key": "same, 1 SOL", "text": _r(6, "FvHwFe 1 at +10")},
                             {"key": "same", "text": _r(6, "same")}], "drop": ["same, 2 SOL"]}),
    ("scout", None, {"rows": [{"key": "AtifzK", "text": _r(7, "AtifzK")},
                              {"key": "AtifzK", "text": _r(7, "AtifzK", "y")}]}),
    ("scout", None, {"rows": [{"key": "HVM33j (I122)", "text": _r(7, "HVM33j (I122)"), "new": True},
                              {"key": "HVM33j", "text": _r(7, "HVM33j (I122)", "z")}]}),
    ("scout", None, {"rows": [{"key": "HjuSth", "text": _r(7, "HjuSth v2")},
                              {"key": "HjuSth v2", "text": _r(7, "HjuSth v2", "z")}]}),
    ("scout", None, {"rows": [{"key": "Zz", "text": _r(7, "Zz"), "after": "no such row"}]}),
    ("scout", None, {"rows": [{"key": "HjuSth", "text": _r(7, "HjuSth"), "new": True}]}),
    ("scout", None, {"rows": [{"key": "A1eN4E", "text": _r(7, "A1eN4E")}]}),
    ("scout", None, {"rows": [{"key": "A1eN4E", "text": _r(7, "A1eN4E"), "after": "HjuSth"}]}),
    ("oktech", None, {"rows": [{"key": "Guard v2", "text": "| Guard v2 | not verified | FvHwFe |", "new": True}]}),
    ("oktech", None, {"rows": [{"key": "YJFQNK", "text": "| YJFQNK | f | i |", "after": "Timer"}]}),
    ("scout", None, {"rows": [{"key": "AtifzK", "text": _r(6, "AtifzK")}]}),
    ("scout", None, {"rows": [{"key": "HjuSth", "text": "| HjuSth | unnamed | 7 d | gap | nc | bids | 0.1 | SQL |"}]}),
    ("scout", None, {"rows": [{"key": "K" * 1300, "text": _r(7, "K" * 1300), "new": True}]}),
    ("scout", None, {"rows": [{"key": "HjuSth", "text": _r(7, "AtifzK")}]}),
    ("scout", None, {"rows": [{"key": "7aPo3n", "text": _r(7, "7aPo3n, AG89fn", "q"), "new": True}]}),
    ("scout", None, {"rows": [{"key": "Zed", "text": _r(7, "Zed")}]}),
    ("scout", None, {"rows": [{"key": "A1eN4", "text": _r(7, "A1eN4E alone"), "new": True}]}),
    ("fees", _no_table, {"rows": [{"key": "Zz", "text": _r(6, "Zz"), "new": True}]}),
    ("fees", _no_table, {"above": "All rows are paper."}),
    ("scout", None, {"rows": _HUGE}),
    ("scout", "text", {"rows": [{"key": "HjuSth", "text": _r(7, "HjuSth")}]}),
    ("scout", None, {"text": _r(7, "HjuSth"), "whole": True}),
    ("scout", "bad-whole", {}),
    ("scout", "huge-whole", {}),
    ("scout", "fragment", {}),
    ("scout", None, {"text": "just a sentence about the table"}),
    ("scout", None, {"text": "One wallet clears every gate now.\n\n" + _r(7, "HjuSth", "w")}),
    ("scout", "absent", {"rows": [{"key": "HjuSth", "text": _r(7, "HjuSth")}]}),
    ("scout", None, {"headline": "What holds for every row."}),
    ("scout", None, {"name": "scout-facts", "rows": [{"key": "HjuSth", "text": _r(7, "HjuSth", "w")}]}),
    ("scout", None, {"rows": json.dumps([{"key": "HjuSth", "text": _r(7, "HjuSth", "w")}])}),
    ("scout", None, {"drop": "LISTS"}),
]


def _make(paths, role, how):
    """The file of a case: the real table, a variant of it, or none; and the real table's text."""
    p = _real(paths, role)
    real = p.read_text()
    if callable(how):
        p.write_text(how(real))
    elif how == "absent":
        os.replace(p, p.with_name("gone.md"))
    return p, real


def _call_of(kw, how, real):
    lines = real.split("\n")
    if how == "text":
        return {**kw, "text": real}
    if how == "bad-whole":
        return {"text": real.replace("| HjuSth |", "| HjuSth | extra |", 1)}
    if how == "huge-whole":
        return {"text": real + "\n".join(_r(7, f"Big{i}", "y" * 300) for i in range(60)) + "\n"}
    if how == "fragment":
        return {"text": "\n".join([lines[0], "", lines[4], lines[5], _r(7, "HjuSth", "w")]) + "\n"}
    return kw


def test_every_error_of_update_table_has_one_advice_and_following_it_changes_the_answer(rolebox):
    """F8 (round 5): "send the row whole, with its first cell" was said to rows that were whole, and sending them again
    gave the same answer byte for byte. Every error update_table gives on the four real tables names ONE thing to do
    for its cause (the ADVICE list above knows each), and the call that does it gets another answer."""
    tools, paths, _ = rolebox
    seen = set()
    for role, how, kw in CASES:
        p, real = _make(paths, role, how)
        call = _call_of(kw, how, real)
        a1 = _call(tools(role)["update_table"], **call)
        assert a1.startswith("error: not done: "), (role, call, a1)
        hit = [n for n, (said, _) in enumerate(ADVICE) if re.search(said, a1)]
        assert hit, f"an error with no known advice: {a1}"
        n = hit[0]
        seen.add(n)
        table = p.read_text() if p.exists() else real
        fixed = {k: v for k, v in ADVICE[n][1]({**call, "_answer": a1, "_real": real}, table).items()
                 if not k.startswith("_")}
        _make(paths, role, how)
        a2 = _call(tools(role)["update_table"], **fixed)
        assert a2 != a1, (role, call, a1)
        said = a1.split("; done:")[0]
        assert not any("…" in q for q in re.findall(r'"([^"]*)"', said)), a1  # a first cell asked for is uncut
        assert not re.search(ADVICE[n][0], a2), (role, fixed, a2)  # the advice followed, its cause is gone
    assert seen == set(range(len(ADVICE))), sorted(set(range(len(ADVICE))) - seen)  # every error was driven


# -- round 6 (14 Nov, 23:30): no row is ever found by a part of its first cell ---------------------------------------


@pytest.mark.parametrize("role,key,first,gone", [
    ("scout", "A1eN4E", "A1eN4E (I130)", "A1eN4E + HVM33j (I122)"),
    ("oktech", "A1eN4E", "A1eN4E (I130)", "A1eN4E, Byn68j, AtifzK"),
    ("oktech", "66hAs8", "66hAs8 (I172)", "66hAs8 (copy bot, not a leader)"),
    ("tester", "efe459e8a2", "efe459e8a2 (frozen)", "efe459e8a2; 2ebf04efe8 (+5 same-signal records)"),
    ("scout", "ATDYz6", "ATDYz6 (I173)", "ATDYz6 + DdHH2n + Edeuun")])  # fmt: skip
def test_a_key_that_is_only_the_start_of_a_first_cell_finds_no_row(rolebox, role, key, first, gone):
    """Round 6, A: a key that was only the start of one row's first cell still replaced that row when the text gave
    it another first cell ("A1eN4E" with "| A1eN4E (I130) |" took the place of "A1eN4E, Byn68j, AtifzK"). A key
    finds a row only when it is that row's whole first cell. The answer names every row it is the start of, whole,
    and both ways out; following the first (the whole first cell as the key) changes that row."""
    tools, paths, _ = rolebox
    p = _real(paths, role)
    before = p.read_text()
    update = tools(role)["update_table"]
    n = 3 if role == "oktech" else REAL_COLS[role]
    out = _call(update, rows=[{"key": key, "text": _r(n, first)}])
    assert out.startswith(f'error: not done: no row {key}. Rows whose first cell starts with it: "{gone}"')
    assert ("to change, move or drop one give its whole first cell as the key; to add a new row "
            f'{key} send "new": true (with "after" for a place)') in out  # fmt: skip
    assert p.read_text() == before
    out = _call(update, rows=[{"key": gone, "text": _r(n, first)}])
    assert "replaced 1" in out and _r(n, first) in p.read_text() and gone not in p.read_text()


def test_a_drop_by_the_start_of_a_first_cell_drops_nothing(rolebox):
    """Round 6, A: drop ["7aPo3n"] dropped the row "7aPo3n, AG89fn"."""
    tools, paths, _ = rolebox
    p = _real(paths, "scout")
    before = p.read_text()
    out = _call(tools("scout")["update_table"], drop=["7aPo3n"])
    assert out.startswith('error: not done: no row 7aPo3n to drop. Rows whose first cell starts with it: "7aPo3n, '
                          'AG89fn": to drop one give its whole first cell as the key')  # fmt: skip
    assert p.read_text() == before
    assert "dropped 1 (7aPo3n, AG89fn)" in _call(tools("scout")["update_table"], drop=["7aPo3n, AG89fn"])


def test_a_place_by_the_start_of_a_first_cell_is_no_place(rolebox):
    """Round 6, A: a before/after target is a whole first cell too."""
    tools, paths, _ = rolebox
    p = _real(paths, "scout")
    before = p.read_text()
    out = _call(tools("scout")["update_table"], rows=[{"key": "Zz", "text": _r(7, "Zz"), "new": True,
                                                       "after": "A1eN4E"}])  # fmt: skip
    assert out.startswith('error: not done: no row A1eN4E to put Zz after. Rows whose first cell starts with it: '
                          '"A1eN4E + HVM33j (I122)": give the whole first cell of the row to put it after')  # fmt: skip
    assert p.read_text() == before


def test_a_row_is_added_only_when_the_call_says_it_is_new(rolebox):
    """The read of round 6: "before"/"after" on a key that names no row, without "new", added a row. A move meant for
    "7aPo3n, AG89fn" and keyed "7aPo3n + AG89fn" was answered "added 1" and the wallet pair stood twice. Every new row
    says that it is new; the refusal names the rows that start alike and both ways out."""
    tools, paths, _ = rolebox
    p = _real(paths, "scout")
    before = p.read_text()
    row = {"key": "7aPo3n + AG89fn", "text": _r(7, "7aPo3n + AG89fn"), "before": "FLPjyS: QUALIFIED"}
    out = _call(tools("scout")["update_table"], rows=[row])
    assert out.startswith('error: not done: no row 7aPo3n + AG89fn (rows that start alike: "7aPo3n, AG89fn")')
    assert 'to add it send it with "new": true' in out and "to change or move a row give its first cell" in out
    assert p.read_text() == before
    out = _call(tools("scout")["update_table"], rows=[{**row, "new": True}])  # the first way out
    assert _quiet(out) and "added 1 (7aPo3n + AG89fn)" in out


def test_a_placed_new_row_sent_again_as_it_stands_is_the_same_already(rolebox):
    """Round 6, B (F6 left open): a new row sent with "after" and sent again by a repair got an error."""
    tools, paths, _ = rolebox
    p = _real(paths, "oktech")
    call = {"rows": [{"key": "NewWal", "text": "| NewWal | finding | idea |", "new": True, "after": "HjuSth"}]}
    assert "added 1 (NewWal) to the table Wallet" in _call(tools("oktech")["update_table"], **call)
    stamp = p.read_text()
    out = _call(tools("oktech")["update_table"], **call)
    assert _quiet(out) and "the same already 1 (NewWal)" in out and p.read_text() == stamp


def test_an_advice_that_asks_for_a_whole_first_cell_names_it_uncut(rolebox):
    """Round 6, C: the advice named the cell cut at 40 characters ("Launch sniper, MIG, momentum hold, maxS…"), and
    that name sent as the key found no row."""
    tools, paths, _ = rolebox
    p = _real(paths, "oktech")
    whole = "Launch sniper, MIG, momentum hold, maxSol, minMcapUsd, cooldown, consensus"
    update = tools("oktech")["update_table"]
    out = _call(update, rows=[{"key": "Launch", "text": "| Launch | v | c | t |"}])
    assert f'"{whole}"' in out and "…" not in out.split("; done:")[0]
    out = _call(update, rows=[{"key": whole, "text": f"| {whole} | v | c | t |"}])
    assert "replaced 1" in out and f"| {whole} | v | c | t |" in p.read_text()


def test_tables_are_named_by_their_whole_header_cell(rolebox):
    """Round 6, D: two tables whose long header cells begin alike were named as one; "the table number 4 table"."""
    tools, paths, _ = rolebox
    head = "Wallet or bot with a very long header first cell here"
    text = (f"# T\n\np\n\n| {head} | a |\n|---|---|\n| W1 | x |\n\n"
            f"| {head} plus | a | b |\n|---|---|---|\n| C1 | z | q |\n")  # fmt: skip
    _table(paths, text, "oktech-facts")
    out = _call(tools("oktech")["update_table"], rows=[{"key": "W2", "text": "| W2 | x |", "new": True},
                                                       {"key": "C2", "text": "| C2 | z | q |", "new": True,
                                                        "after": "C1"}])  # fmt: skip
    assert "added 1 (W2) to the first table (Wallet or bot" in out and "added 1 (C2) to the second table (Wallet" in out
    assert "added 2" not in out
    four = "# T\n\np\n\n" + "".join(f"| Same | a{'| x ' * i}|\n|---|---{'|---' * i}|\n| R{i} | y{'| z ' * i}|\n\n"
                                    for i in range(4))  # fmt: skip
    _table(paths, four, "oktech-facts")
    out = _call(tools("oktech")["update_table"], rows=[{"key": "Q", "text": "| Q | y | z | z | z |", "new": True,
                                                        "after": "R3"}])  # fmt: skip
    assert "added 1 (Q) to the fourth table (Same)" in out


def test_a_row_for_an_empty_other_table_is_told_to_send_the_table_whole(rolebox):
    """Round 6, E: "give after with a key of that table" for a table that has no row cannot be followed."""
    tools, paths, _ = rolebox
    _table(paths, "# T\n\np\n\n| Feature | a | b |\n|---|---|---|\n| F1 | a | b |\n\nC.\n\n| Wallet | c |\n|---|---|\n",
           "oktech-facts")  # fmt: skip
    out = _call(tools("oktech")["update_table"], rows=[{"key": "C1", "text": "| C1 | z |", "new": True}])
    assert "the table Wallet has no row: send the table whole with update_table(text, whole=true)" in out
    assert 'give "after"' not in out


def test_a_journal_entry_resent_with_another_headline_is_not_written_twice(rolebox):
    """Round 6, F: a repair that sent the entry again with a reworded headline wrote it twice; so did an entry sent
    again after its headline when the turn's note had come from write_note."""
    tools, paths, _ = rolebox
    name = "scout-journal-2026-11-06"
    scout = tools("scout")
    entry = "## 10:00\nFound X: n 17."
    _call(scout["write_report"], name=name, text=entry, headline="H one.")
    out = _call(scout["write_report"], name=name, text=entry, headline="H one, corrected.")
    assert out.startswith("the journal has this entry already: not written again")
    assert (paths.root / f"{name}.md").read_text().count("Found X") == 1
    other = tools("tester")
    tname = "tester-journal-2026-11-06"
    _call(other["write_note"], text="Note first.")
    _call(other["write_report"], name=tname, text=entry, headline="H one.")
    out = _call(other["write_report"], name=tname, text=entry)
    assert out.startswith("the journal has this entry already: not written again")
    assert (paths.root / f"{tname}.md").read_text().count("Found X") == 1


def test_a_new_journal_entry_that_repeats_the_journals_last_lines_is_written(rolebox):
    """The read of round 6: an entry counted as already written whenever its text, without its headline, was the
    journal's last lines, also lines of an earlier turn or of another entry of this turn: a short new entry ("Nothing
    else changed.") was skipped and its "In short" line never reached the journal. Only an entry this turn wrote, or
    the whole entry standing as the journal's last entry, is "already written"."""
    tools, paths, _ = rolebox
    name = "scout-journal-2026-11-06"
    journal = paths.root / f"{name}.md"
    journal.write_text("## 09:00 scout\nchecked HjuSth.\nNothing else changed.\n")  # an earlier turn's, no headline
    scout = tools("scout")
    out = _call(scout["write_report"], name=name, text="Nothing else changed.", headline="H new.")
    assert out.startswith("appended to") and journal.read_text().endswith("Nothing else changed.\n\nIn short: H new.\n")
    # within one turn: an entry without a headline, then a one-line entry that repeats its last line
    other, tname = tools("tester"), "tester-journal-2026-11-06"
    assert _call(other["write_report"], name=tname, text="## 12:00\nA.\nNo other change.").startswith("wrote")
    out = _call(other["write_report"], name=tname, text="No other change.", headline="H2.")
    assert out.startswith("appended to")
    assert (paths.root / f"{tname}.md").read_text() == "## 12:00\nA.\nNo other change.\n\nNo other change.\n\nIn short: H2.\n"
    # an entry of this turn sent again after another one was written in between is still not written twice
    out = _call(other["write_report"], name=tname, text="## 12:00\nA.\nNo other change.")
    assert out.startswith("the journal has this entry already: not written again")
    assert (paths.root / f"{tname}.md").read_text().count("## 12:00") == 1


def test_a_cut_journal_entry_is_let_stand_and_the_rest_is_a_second_entry(rolebox):
    """Round 6, G: the rules said to shorten a cut text, and a shortened entry sent again stood beside the cut one.
    A cut entry stands as it is; its answer and the rules say to write the rest as a second entry."""
    from snipetech.operator import prompts

    tools, _, _ = rolebox
    out = _call(tools("scout")["write_report"], name="scout-journal-2026-11-06",
                text="## 10:00\n" + "a line n 17.\n" * 1200, headline="H.")  # fmt: skip
    assert "the entry was cut at 12,000 characters" in out
    assert "it stands as it is: write the rest as a second entry" in out
    rules = " ".join(prompts.ROLE_RULES.split())
    assert "a journal entry that was cut stands as it is, write the rest as a second entry" in rules
