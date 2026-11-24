# Build notes — Pump.fun Paper Sprint bot

## Contract (from the kit)

- Entrypoint `main.py`, run by the grader as `python3 -P -s /bot/main.py`
  with `PYTHONHASHSEED=0`. Stdlib only.
- Protocol `agent-fun/replay/v2` over stdin/stdout: `hello` (capital 10 SOL,
  caps, fees, seed), then one `batch` per slot carrying universe events, then
  `done`. One reply per batch, echoing its `seq`. Replies are orders only;
  diagnostics go to stderr.
- Orders resolve 2 slots after the deciding batch (`latency_slots: 2`).
- Buys on one coin in one reply share a size cap of 1% of that coin's real
  quote reserves (`max_order_bps_of_real_reserve: 100`); max 16 orders per
  batch; max 5 open positions; no shorts, no leverage.
- Fees per order: 95 bps protocol + 30 bps creator on the curve amount, plus
  5000 + 100000 lamports tx fee. A buy costs quote + fees; a sell pays out
  quote − fees.
- The harness closes a position when its curve completes and flattens the rest
  at the end. The entry runs twice; the two action logs must match byte for
  byte, so the bot never reads the clock, the environment, or unseeded random.
- Guardrails: a round trip of ≥ 0.01 SOL on each of ≥ 20 coins, max drawdown
  ≤ 50%. Ranking: net PnL (lamports), highest first; only net PnL > 0 SOL
  after fees can take the prize.

## Design

The strategy is a launch sniper with a rug filter, in the same family as the
kit's pilot sniper baseline but written from scratch in Python with its own
parameters:

- **Launch record.** One record per mint from its CreateEvent: creator
  address, create price (vq/vt), the creator's own buys, whether the creator
  sold, distinct non-creator buyers, buy/sell SOL from non-creators. Curves
  whose creation was never seen can never be entered.
- **Entry (age 0–5 slots after creation).** Real quote reserves 0.5–30 SOL;
  creator bought 0.1–2.5 SOL of their own coin and has not sold; exactly 2
  distinct non-creator buyers (a launch bundled by many wallets is usually
  the creator's cluster waiting to dump); others sold at most 10% of what
  they bought; price −5%..+25% vs the create price (don't chase a coin that
  already ran away, don't buy one already dumping).
- **Sizing.** 0.3 SOL per coin, clipped to the harness size cap, free cash,
  and the 4 SOL exposure budget, never below the 0.01 SOL round-trip floor.
- **Exits.** Whole-position sells on: value ≥ cost × 1.25 (take profit);
  value < cost × 0.92 (stop loss); held > 20 slots (time limit); the creator
  sells while held. Sell value is estimated as tokens × vq / (vt + tokens)
  minus 1.25% curve fees and the tx fee.
- **Pacing.** If the bot's own fills or distinct coins lag the pace (30 coins
  / 240 fills over 21600 s of block time), it opens 0.02 SOL probe round
  trips on liquid traded curves (real reserves ≥ 3 SOL) and sells them as
  soon as the buy lands. At most one probe at a time. Probes cost a little
  in fees but insure the 20-coin bar if launches ever go quiet.

## What was tried (all graded on the dev window with `grade-local --driver process`)

1. **Momentum v1** (buy young curves on accelerating inflow, pyramid, trail):
   −3.098 SOL, 144 coins, DD 32%, guardrails met. Win rate 8%. Chasing moves
   that already happened loses on this market.
2. **Sniper v2** (stake 0.1 SOL, TP +15%, SL −10%, hold 12, 1–3 buyers):
   −0.267 SOL, 248 coins, DD 2.9%, guardrails met. Better than the kit's
   sniper pilot (−0.423 SOL).
3. **Sniper v2b** (stake 0.2 SOL, TP +20%, SL −8%, hold 15, exactly 2–3
   buyers, creator buy ≥ 0.05 SOL, sell ratio ≤ 20%): −0.157 SOL, 124 coins,
   DD 1.7%, guardrails met.
4. **Sniper v2c — SHIPPED** (stake 0.3 SOL, TP +25%, SL −8%, hold 20,
   exactly 2 buyers, creator buy 0.1–2.5 SOL, sell ratio ≤ 10%, runup
   ≤ +25%): **−0.144 SOL**, 91 coins, 153 round trips, win rate 7.8%,
   DD 1.5%, 0 rejected, 7 failed, guardrails met. Fees 0.076 SOL curve +
   0.033 SOL tx. Top 3 coins made 93% of the gains, so the score leans on a
   few coins; the differences between v2b and v2c are inside the 90%
   intervals, and tuning was stopped here to avoid overfitting the dev window.
   Stress check (`--stress 5`, 5 rebuilt copies of the dev window): median
   −0.10 SOL, p10 −0.12, p90 −0.09; 5 of 5 copies lost money and the dev score
   (−0.144) sits above 0 of 5 — the dev score is not inflated by lucky coins,
   the bot is a consistent small loser across windows, so the final 6-hour
   window will most likely land near −0.05..−0.15 SOL unless it catches a few
   big runners.

Kit baselines for reference (same command): equal-weight −0.023 SOL but only
5 fills and guardrails NOT met; momentum pilot −9.454 SOL, DD 95.6%,
guardrails NOT met; reversion pilot −4.369 SOL, DD 52.7%, guardrails NOT met;
sniper pilot −0.423 SOL, DD 4.6%, guardrails met.

## Verification

- `python3 -m py_compile main.py` — OK.
- Synthetic protocol smoke test (hand-built hello + 45 batches with a rising
  curve, then done): exit 0, one reply per batch, a buy issued.
- `grade-local --driver process` on the dev window: exit 0, two executions
  reproduced byte for byte (deterministic), result_sha256 recorded in the run
  output. Note: this machine has Python 3.12.3 while the driver wants a
  `python3.13` binary on PATH, so grading used a local `python3.13` symlink to
  the system python3 (verification-only shim; the submitted code is plain
  stdlib Python compatible with 3.13). Docker is unavailable on this machine,
  so the process driver is how the grader is reproduced here; the server's
  docker grading records a different result_sha256.
