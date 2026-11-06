"""How tests/fixtures/copy_fills_6nov.json and copy_fills_6nov_tape.tsv.gz were built (kept for the record; the raw
files it reads were exported from the server on 6 Nov 2026 and are not in the repository).

Inputs: the measure stage's fills (our 144 rows of 5–6 Nov) and the tape of the 35 coins we traded, both as TSV.
Output: the 12 copy fills of 6 Nov that OKTech sold (2Ngd left out: it moved to PumpSwap 3 s after our buy; DTAU left
out: OKTech never sold it), with the tape of each coin from 120 s before our first buy in it to 330 s after our last,
our own trades removed and the reserves put back to what they would have been without us (the curve's invariant
q·b is constant on these coins, so a state with our h tokens out of the pool is b + h tokens and k / (b + h) SOL).

Run: python3 tests/fixtures/build_copy_fills_6nov.py <fills.tsv> <tape.tsv> <out dir>
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

ME = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"  # the sniper wallet
HOLD60_LEADERS = {
    "4yo9CUuTBbds9NFhZd4MzPiZZkUvveXdTnAH8qMsE8ku"
}  # Gfree ran the "hold60" preset; every other copy "lab"
SKIP = {"2NgdUjFYoU5TWvEMu91rw3oYegvmERtiPcYRKVcBpump", "DTAUfLFo92uUbKGWWCkCZMfCAvLXkHmP31SRdAyipump"}
# what each sell was, read off the tape by the 6 Nov exits audit (the sell's slot against the leader's sells, the
# pauses and the price): stale = OKTech's dead exit, copy_exit = the leader sold, stop_loss, time = the timer
LIVE_EXIT = {"au6j": "copy_exit", "H4D5": "stale", "A7Ys": "stale", "3BrE": "copy_exit", "7bDN": "time",
             "9yCW": "copy_exit", "Cp2v": "stale", "Cmg7": "stale", "7Rwm": "stop_loss", "FqK7@1793984705": "stale",
             "FqK7@1793984799": "stale", "otdL": "copy_exit"}  # fmt: skip
BEFORE, AFTER = 120, 330


def _read_tsv(path: str) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def main(fills_path: str, tape_path: str, out_dir: str) -> None:
    tape = _read_tsv(tape_path)
    for r in tape:
        for k in ("ts", "slot", "pos", "is_buy", "quote", "base", "q_after", "b_after"):
            r[k] = int(r[k])
    by: dict[str, list[dict]] = defaultdict(list)
    for r in tape:
        by[r["mint"]].append(r)
    for rows in by.values():
        rows.sort(key=lambda r: (r["slot"], r["pos"]))
    fills = _read_tsv(fills_path)
    buys = [f for f in fills if f["side"] == "buy" and f["wallet_copied"] and f["mint"] not in SKIP]
    out_fills, windows = [], {}
    for f in buys:
        rows = by[f["mint"]]
        i = next(k for k, r in enumerate(rows) if r["user"] == ME and r["is_buy"] and r["slot"] == int(f["slot"]))
        buy = rows[i]
        sell = next(r for r in rows[i + 1 :] if r["user"] == ME and not r["is_buy"])
        sell_fill = next(
            s for s in fills if s["side"] == "sell" and s["mint"] == f["mint"] and int(s["slot"]) == sell["slot"]
        )
        short = f["mint"][:4]
        name = short if sum(1 for b in buys if b["mint"] == f["mint"]) == 1 else f"{short}@{f['ts']}"
        out_fills.append({
            "name": name, "mint": f["mint"], "leader": f["wallet_copied"],
            "preset": "hold60" if f["wallet_copied"] in HOLD60_LEADERS else "lab", "arm": f["bid_arm"],
            "buy": {"slot": buy["slot"], "pos": buy["pos"], "ts": buy["ts"], "sol": float(f["sol"]), "tokens": buy["base"]},
            "sell": {"slot": sell["slot"], "pos": sell["pos"], "ts": sell["ts"], "sol": float(sell_fill["sol"])},
            "live_exit": LIVE_EXIT[name],
        })  # fmt: skip
        lo, hi = windows.get(f["mint"], (buy["ts"] - BEFORE, buy["ts"] + AFTER))
        windows[f["mint"]] = (min(lo, buy["ts"] - BEFORE), max(hi, buy["ts"] + AFTER))
    lines = ["mint\tslot\tpos\tts\tleader\tis_buy\tquote\tbase\tq_after\tb_after"]
    leaders = {f["mint"]: {x["leader"] for x in out_fills if x["mint"] == f["mint"]} for f in out_fills}
    for mint, (lo, hi) in windows.items():
        held = 0
        for r in by[mint]:
            if r["user"] == ME:
                held += r["base"] if r["is_buy"] else -r["base"]
                continue
            if not lo <= r["ts"] <= hi:
                continue
            k = r["q_after"] * r["b_after"]
            b = r["b_after"] + held  # the pool without our tokens out of it
            q = round(k / b)
            lines.append("\t".join(str(x) for x in (mint, r["slot"], r["pos"], r["ts"], int(r["user"] in leaders[mint]),
                                                    r["is_buy"], r["quote"], r["base"], q, b)))  # fmt: skip
    out = Path(out_dir)
    about = ("The 12 copy fills of 6 Nov 2026 that OKTech sold, with the preset each ran and the exit read off the tape by "
             "the exits audit. Tape rows: our own trades removed, reserves without our tokens out of the pool. "
             "Built by tests/fixtures/build_copy_fills_6nov.py.")  # fmt: skip
    (out / "copy_fills_6nov.json").write_text(json.dumps({"about": about, "fills": out_fills}, indent=1) + "\n")
    with gzip.GzipFile(out / "copy_fills_6nov_tape.tsv.gz", "wb", compresslevel=9, mtime=0) as fh:  # byte-stable
        fh.write(("\n".join(lines) + "\n").encode())


if __name__ == "__main__":
    main(*sys.argv[1:4])
