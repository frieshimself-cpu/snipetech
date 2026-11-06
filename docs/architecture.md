# snipetech — the loop, in one picture

Obsidian renders this diagram; the boxes link to the documents that define them.

```mermaid
flowchart LR
  subgraph Sources
    H[Helius LaserStream<br/>every pump.fun trade]
    ST[SolanaTracker<br/>30/90-day boards, first buyers]
    BE[Birdeye<br/>gainers, top traders, wallet PnL]
    VY[Vybe<br/>30-day board]
    PF[pump.fun feed<br/>KOTH, live, trending]
    TG[Telegram channels<br/>callouts via Jev]
  end

  H --> T[(tape: trades, launches)]
  PF --> E[(events)]
  TG --> E
  H --> E

  ST --> D[discover<br/>hourly]
  BE --> D
  VY --> D
  T --> D
  D --> C[(candidates)]

  C --> Q[qualify<br/>gates at the measured landing<br/>one lab at a time]
  T --> Q
  E --> Q
  Q --> V[(verdicts)]

  V --> B[book<br/>every 5 min when enabled<br/>bounded by the owner's numbers]
  B --> OK[OKTech snipers<br/>bid, tip, CU, send count]
  OK --> CH[Solana]
  CH --> M[measure<br/>position in slot, gap, fee, PnL]
  M --> F[(fills, bid model, live stats)]
  F --> Q
  F --> B

  F --> O[operator every 15 min<br/>analyst daily, strategist weekly]
  V --> O
  E --> O
  O --> N[notes.md + reports]
  O --> TGB[Telegram: decisions, alerts, digest<br/>commands /status /stop /resume]
  O --> R[resources: credits, CU, requests, SOL, disk]
```

Documents: [[roadmap]] (where we stand), [[live-recipe]] (every live test), the design in
`superpowers/specs/2026-11-06-learning-loop-design.md`, the plans under `superpowers/plans/`, [[birdeye]], [[jev]],
[[oktech-features]], [[oktech-settings]]. Live state: the dashboard at `/dash` on the server (phase 1, task 6).
