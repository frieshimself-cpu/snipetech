"""Qualify: push every candidate wallet through the gates at the landing our copies get and write a verdict.

Since research round 2 (7 Nov 2026, docs/research/2026-11-07-round-2.md) a verdict is qualified only when:

* the lab copies the wallet where live copies land: behind the 3rd buy after the leader's, counted across slots
  ("+3x", run.qualify_landing; only a measured landing or [qualify] landing_pos further back moves it, never closer),
  selling with OKTech's lab exit, its 1 s dead exit and its 50 % copy exit after the 1.3 s watch;
* under 10 % of its first buys sat in a coin's create slot, where no copy lands (gates.create_slot_share);
* its own copies on the tape after it was first picked pass too (gates.forward_gate): 20 or more over 6 h or more of
  later tape, their lower bound above 0. A mean below 0 there retires it; until it passes it is "forward pending", a
  candidate the book does not copy. The numbers are on the verdict (`forward`).

The book (book/) is unchanged: it copies qualified wallets only. A wallet it copied before these rules is never
stopped by them (gates.keep_armed): its verdict stays the one the book armed it on, says first that it no longer
qualifies and why, and `kept_armed` holds what these rules found; whether it goes is the owner's decision.
"""
