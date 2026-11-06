# Jev (TypeSafe System One) — status and the plan to use it well (6 Nov 2026)

**Status: not used yet.** The key is stored as `TYPESAFE_API_KEY` in `/etc/snipetech/env` and nothing calls it. The
25 $ of credit is untouched.

## What it is, in our terms

A judgment model, not a writer: you send a *state* (text or JSON) and typed questions, it returns probabilities in
about 100 ms. Three question types: **Noul** (probability a statement is true), **Choice** (one option from a list,
with probabilities and a confidence), **Score** (a weighted level on a scale you describe). Price 0.042 $ per million
input tokens, output free. HTTP: `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer <key>`;
the official SDK retries 429/529; body limit 32 MiB; no documented per-plan rate limit. We can call it with `httpx`
and need no new dependency.

TypeSafe's own design rules (docs, "How to build with System One"): keep deterministic work in code; one atomic
question per property; short question, context in `instructions`/`criteria`; send only the relevant state; start
thresholds near 0.7 yes / 0.3 no and tune on your own labelled examples; re-run to check self-consistency; use it
for judgments over unstructured data, never to pick its own next action.

## Where it fits snipetech — and where it does not

Every number that decides a trade here is already exact: price, slot, position behind the leader, fees, holder
counts, the tape. Jev adds nothing to those, and it does not change landing position. What it can judge cheaply is
the **unstructured part of a launch**: name, symbol, description, image, website and socials, the dev's history as
text. That is the one input the lab has today only as crude flags (has twitter, has website, tweet age).

Planned use, in this order, each gated by measurement before it touches a decision:

1. **Launch-quality factor for the lab (offline first).** For the last N days of launches, send `{name, symbol,
   description, website, twitter, telegram, image_url?, dev: {launches, rinse_rate}}` with atomic Nouls such as
   "the description is a coherent concept rather than filler", "the name/symbol copy a currently viral token",
   "the socials look created for this launch only", and a Choice over `{serious, lazy cash-grab, impersonation,
   bot-generated}`. Join the answers to the tape outcomes (rinse, runner, graduated) and the replayed PnL at +1 and
   +6. If the AUC against outcome is near 0.5, stop; if it separates, it becomes a filter the strategy lab can
   sweep. Cost: ~400 tokens a launch → 2 500 launches ≈ 0.04 $.
2. **Owner inbox triage for the operator.** A Noul per inbox file: "this note asks for an action" vs "this is
   information" so the operator knows what to do with `docs/inbox/`. Pennies a day.
3. **Not for:** anything time-critical on the trade path (OKTech executes, not us), ranking wallets (that is PnL
   arithmetic), or any step where the answer is computable.

## Guard rails

- Never send keys, wallet secrets or the owner's private files; state is token metadata and public on-chain facts.
- Log the exact request payload (TypeSafe's advice) so a surprising answer can be reproduced.
- A failed call is "unknown", never "no".
