# Fee records on the tape: who paid a trade's transaction and what it bid

11 Nov 2026. Audit row 64, open item 17.

## Why

The tape writes every trade and nothing of its transaction's fees. A fee record (`tx_meta`) exists only for a
transaction some study fetched over the RPC afterwards: none of the last 51,356 trades had one. Three things rest on
it and get a sliver:

- `playbook`'s "what it bids for its place" is empty for most bots.
- What bid buys which place behind a copied buy (CLAUDE.md rule 2: the place in the leader's block is a fee auction;
  ideas I20, I7) cannot be read off the tape.
- `fomo-wallets` finds FOMO's users by the wallet that pays their fees. It sees about 20 active users in three days;
  the owner asked for all of them (8 Nov).

## What

The recorder already holds each transaction it records, whole. From it, for every trade row:

| column on `trades` | what | from |
|---|---|---|
| `payer` (TEXT, NULL when the trader paid) | the wallet that paid the transaction's fee | the first account key (`TxEvents.fee_payer`, already decoded) |
| `priority` (INTEGER, lamports) | the priority fee | `meta.fee` less 5,000 lamports a signature (exact; no instruction is read for it) |
| `tip` (INTEGER, lamports) | what the transaction sent to a landing service | its System transfers, outer and inner, to a known tip account (`RELAY_TIP_ACCOUNTS`: Jito, Nozomi, Helius Sender, Astralane, bloXroute, Flash) |

A transaction with several trades (a create bundle) repeats its values on each row: a sum over rows counts a
transaction once per `sig`.

Rows written before the change, and rows that come from the log subscriptions (no transaction, only its logs), hold
NULL in `priority` and `tip`: unknown, never zero.

## Where

1. `solana/tips.py` (new): the tip accounts, moved out of `txmeta.py` so that `tx.py` can read them without a
   circular import. `txmeta.RELAY_TIP_ACCOUNTS` stays as a name.
2. `solana/tx.py`: `TxEvents.priority`, `TxEvents.tip` (None when unknown). `extract_from_transaction` fills them in
   the walk over the instructions it already does: one comparison for an instruction that is not the System
   program's.
3. `models.Trade`: `payer`, `priority`, `tip` at the end, with defaults. `solana/normalize.py` sets them on each
   trade it makes.
4. `store.py`: three entries in `_ADDED_COLUMNS` (an `ALTER TABLE ... ADD COLUMN` changes the schema only; the 47 GB
   table is not rewritten). `add_trades` writes them. Trades are read as before (`_TRADE_COLS` is unchanged): a lab
   reads no byte more. No index and no new table: nothing to prune.
5. The copy the labs read lacks the columns until the next copy. They are not needed to read the tape, so
   `schema_gaps` does not count them and the labs keep the copy; a study that wants them from a copy without them
   gets "unknown".
6. `playbook`: a bot's bid from its trade row; the old fee records where the row has none.
   `fomo-wallets`: the trades whose `payer` is FOMO's wallet, read by time (a daily batch job on the copy), together
   with the old fee records.
7. The agents' query tool lists the new columns by itself (its table layout is read off the schema); its note on
   `trades` says what they are.

Not in this change: a table of bid against place behind a copied buy. It needs a few days of rows first.

## Checks

- The recorder's processor share before and after (`remote status`: `units_cpu_secs`; coverage `tape:work`). It was
  brought to 0.06-0.1 of a core on 11 Nov and must stay there.
- A recorded transaction of ours with a known bid (`fills`: `priority_lamports`, `tip_lamports` from the measure
  stage) reads the same from its trade row.
- The size of a day of rows.

## Risks

- The first process to open the database after the deploy adds the columns: three schema writes, each under the
  write lock for an instant, through the store's retrying write.
- A tip account not on the list reads as no tip. The list is the one the measure stage uses for our own fills.
