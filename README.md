[![make.fun · u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b](https://make.fun/badge/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b.svg)](https://make.fun/bounty/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b?utm_source=github&utm_medium=readme&utm_campaign=u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b)
[![rank](https://make.fun/badge/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/0xyfann/makefun-u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/rank.svg)](https://make.fun/bounty/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b)
[![reward](https://make.fun/badge/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/0xyfann/makefun-u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/reward.svg)](https://make.fun/bounty/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b?utm_source=github&utm_medium=readme&utm_campaign=u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b)

# Pump.fun Paper Sprint: Any Bot Can Win

A paper-trading bot for the make.fun `pumpfun-replay/v2` harness
(`agent-fun/replay/v2`, competition
`u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b`).

## Strategy: launch sniper with a rug filter

The bot watches every coin from its CreateEvent and buys only in its first
few slots, when the opening tape looks clean: the creator put in a modest buy
of their own (0.1–2.5 SOL) and has not sold, exactly 2 other wallets piled in
(a launch bundled by many wallets is usually the creator's cluster waiting to
dump), almost nothing has been sold yet (≤ 10% of what was bought), and the
price has not already run away (−5%..+25% vs the create price).

- **Sizing:** 0.3 SOL per coin, clipped by the harness's 1%-of-real-reserves
  size limit and free cash, never below the 0.01 SOL round-trip floor.
- **Exits (sell whole):** take profit +25%; stop loss −8%; never hold past 20
  slots (~8 seconds); leave at once when the creator sells.
- **Guardrail pacing:** if the bot's own fills or distinct coins lag the pace
  (30 coins / 240 fills over 6 hours of block time), it opens tiny 0.02 SOL
  probe round trips on liquid curves and sells them as soon as they land, so
  the run clears the 20-coin / 0.01-SOL round-trip minimum bar.

The bot is deterministic: no clock, no randomness, no environment reads;
iteration follows insertion order only. It runs offline (stdlib only) as
`python3 -P -s main.py` with `PYTHONHASHSEED=0`, speaking the replay
protocol on stdin/stdout through `vendor/makefun_bot.py` (the kit's Python
shim, vendored as the kit instructs).

## Local verification (dev window, `grade-local --driver process`)

| Bot | Net PnL | Coins | Max DD | Guardrails |
|---|---|---|---|---|
| This bot (final) | **−0.144 SOL** | 91 | 1.5% | met |
| This bot (earlier momentum version) | −3.098 SOL | 144 | 32% | met |
| Kit sniper pilot | −0.423 SOL | 343 | 4.6% | met |
| Kit momentum pilot | −9.454 SOL | 46 | 95.6% | NOT met |
| Kit reversion pilot | −4.369 SOL | 195 | 52.7% | NOT met |
| Kit equal-weight baseline | −0.023 SOL | 5 | 1.0% | NOT met |

Two executions reproduced byte for byte (deterministic). The dev window is a
24-hour sample and every baseline loses on it; this bot loses the least.
The final 6-hour window is a fresh sample, so the dev score is not a forecast.

## Files

- `main.py` — the bot (entrypoint).
- `vendor/makefun_bot.py` — the kit's agent-fun/replay/v2 shim (vendored).
- `README.md`, `BUILD-NOTES.md`, `prompt.txt` — this file, build notes, the
  prompt the bot was built from.
- `LICENSE` — MIT.

## Local verification command

From the kit directory (the `grade-local` command runs the bot twice on the
dev window and requires the two action logs to match byte for byte):

```
node cli/makefun.mjs grade-local --kit . --bot <this repo> --driver process --worktree
```

<sub>Entered on [make.fun](https://make.fun/?ref=p1x954ca&utm_source=github&utm_medium=readme&utm_campaign=u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b)</sub>
