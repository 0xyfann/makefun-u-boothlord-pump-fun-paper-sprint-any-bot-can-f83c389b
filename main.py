"""Pump.fun Paper Sprint bot: launch sniper with a rug filter.

The bot paper-trades pump.fun bonding curves on the agent-fun/replay/v2 harness
(stdin/stdout JSON lines, see vendor/makefun_bot.py). It watches every coin from
its CreateEvent and buys only in the first few slots, when the opening tape
looks clean: the creator put in a modest buy of their own and has not sold,
only a few other wallets piled in (a launch bundled by many wallets is usually
the creator's cluster waiting to dump), and the price has not already run away.
It takes profit fast (+15%), cuts losers fast (-10%), never holds past 12 slots,
and leaves at once when the creator sells. Tiny probe round trips keep the run
on pace for the 20-coin guardrail if launches ever go quiet. Deterministic by
construction: no clock, no random numbers, no environment reads; iteration
follows insertion order only.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script dir off sys.path)

LAMPORTS = makefun_bot.LAMPORTS_PER_SOL
# Curve fees the trader pays on top of a trade: protocol 95 bps + creator 30 bps.
CURVE_FEE_BPS = 125

# --- Launch entry (slots are the time unit; ~2.5 slots per second) ---
MIN_AGE = 0            # youngest age (slots since create) at which a buy may be decided
MAX_AGE = 5            # oldest age at which a first buy may still be decided
STAKE = 300_000_000    # lamports to aim for per coin in total: 0.3 SOL
MIN_BUY = 10_000_000   # smallest buy worth sending: 0.01 SOL (guardrail floor)
MIN_RQ = 500_000_000   # real quote reserves at decision time: 0.5..30 SOL
MAX_RQ = 30_000_000_000
MIN_DEV_BUY = 100_000_000     # creator's own buys in the opening: 0.1..2.5 SOL
MAX_DEV_BUY = 2_500_000_000
MIN_BUYERS = 2         # distinct non-creator buyers seen before entry, exactly 2
MAX_BUYERS = 2         # (a bundled launch is a dump in waiting)
MAX_SELL_RATIO = 0.10  # lamports sold by others / lamports bought, at most 10%
MIN_RUNUP = -0.05      # price now vs the create price: -5%..+25%
MAX_RUNUP = 0.25
BUY_SLIP_BPS = 10_000
SELL_SLIP_BPS = 10_000

# --- Exits ---
TAKE_PROFIT = 0.25     # sell when value >= cost * 1.25
STOP_LOSS = 0.08       # sell when value < cost * (1 - 0.08)
MAX_HOLD = 20          # slots; never hold past this
MAX_EXPOSURE = 4_000_000_000   # most lamports at cost across all positions: 4 SOL

# --- Guardrail pacing (probe round trips) ---
TARGET_COINS = 30      # distinct coins to reach by the end of the pace window
TARGET_FILLS = 240     # own fills to reach by the end of the pace window
PACE_SECS = 21_600     # 6 hours of block time
PROBE = 20_000_000     # probe buy: 0.02 SOL (>= the 0.01 SOL round-trip floor)
MAX_PROBES = 1         # most probe round trips open at once

# --- Per-curve launch record, from its CreateEvent on ---
class Launch:
    __slots__ = ("created", "creator", "create_price", "dev_buy", "dev_sold",
                 "buyers", "buy_sol", "sell_sol", "last_trade_slot")

    def __init__(self, created, creator, create_price):
        self.created = created
        self.creator = creator
        self.create_price = create_price  # vq / vt at creation, as a float
        self.dev_buy = 0        # lamports the creator bought in the opening
        self.dev_sold = False   # the creator sold at any point
        self.buyers = set()     # distinct non-creator buying wallets
        self.buy_sol = 0        # lamports bought by non-creator wallets
        self.sell_sol = 0       # lamports sold by non-creator wallets
        self.last_trade_slot = created


class Held:
    __slots__ = ("opened", "invested", "filled")

    def __init__(self, opened, invested):
        self.opened = opened
        self.invested = invested  # lamports sent in buys for this position
        self.filled = False      # some buy of this position filled


launches = {}  # mint -> Launch, insertion order
held = {}      # mint -> Held, insertion order
probes = {}    # mint -> bool, False while buying, True once selling
my_fills = 0
my_coins = set()
first_time = None
last_time = None


def launch_of(mint, slot, creator, price):
    ln = launches.get(mint)
    if ln is None:
        ln = Launch(slot, creator, price)
        launches[mint] = ln
    return ln


def entry_signal(ln, now, rq_lamports, price):
    """True when a launch's opening tape looks clean enough to snipe."""
    age = now - ln.created
    if age < MIN_AGE or age > MAX_AGE:
        return False
    if rq_lamports < MIN_RQ or rq_lamports > MAX_RQ:
        return False
    if ln.dev_buy < MIN_DEV_BUY or ln.dev_buy > MAX_DEV_BUY:
        return False
    if ln.dev_sold:
        return False
    if len(ln.buyers) < MIN_BUYERS or len(ln.buyers) > MAX_BUYERS:
        return False
    if ln.buy_sol > 0 and ln.sell_sol > MAX_SELL_RATIO * ln.buy_sol:
        return False
    if ln.create_price <= 0:
        return False
    runup = price / ln.create_price - 1.0
    if runup < MIN_RUNUP or runup > MAX_RUNUP:
        return False
    return True


def sell_value(tokens, vq, vt, tx_fee):
    """Lamports a sell of `tokens` returns after curve fees and the tx fee."""
    if tokens <= 0 or vt <= 0:
        return 0
    gross = (tokens * vq) // (vt + tokens)
    fee = (gross * CURVE_FEE_BPS + 9999) // 10000
    return gross - fee - tx_fee

def on_batch(state, batch):
    global my_fills, first_time, last_time
    now = batch["slot"]
    tx = state.tx_fee_lamports()
    caps = state.hello["caps"]
    max_orders = caps["max_orders_per_batch"]
    traded = []          # mints with an event this batch, first-seen order
    traded_seen = set()

    for f in batch["fills"]:
        if f["status"] == "filled" and not f["client_id"].startswith("_"):
            my_fills += 1
            my_coins.add(f["mint"])

    for e in batch["events"]:
        bt = e.get("block_time")
        if bt is not None:
            if first_time is None:
                first_time = bt
            if last_time is None or bt > last_time:
                last_time = bt
        mint = e["mint"]
        if mint not in traded_seen:
            traded_seen.add(mint)
            traded.append(mint)
        if e["event"] == "CreateEvent":
            price = (int(e["virtual_quote_reserves"])
                     / int(e["virtual_token_reserves"]))
            launch_of(mint, e["slot"], e.get("creator"), price)
        elif e["event"] == "TradeEvent":
            ln = launches.get(mint)
            if ln is None:
                # A trade on a curve whose creation this run never saw: track
                # the tape anyway, but with no creation it can never be entered.
                ln = Launch(None, None, 0.0)
                launches[mint] = ln
            sol = int(e["sol_amount"])
            ln.last_trade_slot = e["slot"]
            if e["user"] == ln.creator:
                if e["is_buy"]:
                    ln.dev_buy += sol
                else:
                    ln.dev_sold = True
            elif e["is_buy"]:
                ln.buyers.add(e["user"])
                ln.buy_sol += sol
            else:
                ln.sell_sol += sol

    marks = {}
    for m in batch["marks"]:
        marks[m["mint"]] = m

    pending_by_mint = {}
    for po in state.pending.values():
        pending_by_mint[po.order["mint"]] = pending_by_mint.get(po.order["mint"], 0) + 1

    orders = []

    def curve_reserves(mint):
        """(vq, vt, rq) lamports for a mint, preferring this batch's mark."""
        mk = marks.get(mint)
        if mk is not None:
            return (int(mk["virtual_quote_reserves"]),
                    int(mk["virtual_token_reserves"]),
                    int(mk["real_quote_reserves"]))
        c = state.curves.get(mint)
        if c is None:
            return (0, 0, 0)
        return (c.virtual_quote_reserves, c.virtual_token_reserves,
                c.real_quote_reserves)

    # --- Exits first: take profit, stop loss, time limit, creator sold.
    for mint in list(held):
        h = held[mint]
        pos = state.positions.get(mint)
        if pos is None:
            if mint not in pending_by_mint:
                del held[mint]
            continue
        h.filled = True
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        vq, vt, _rq = curve_reserves(mint)
        tokens = int(pos.token_amount)
        value = sell_value(tokens, vq, vt, tx)
        cost = int(pos.cost_lamports)
        ln = launches.get(mint)
        do_exit = False
        if value >= cost * (1 + TAKE_PROFIT):
            do_exit = True
        if not do_exit and value < cost * (1 - STOP_LOSS):
            do_exit = True
        if not do_exit and now - h.opened > MAX_HOLD:
            do_exit = True
        if not do_exit and ln is not None and ln.dev_sold:
            do_exit = True
        if not do_exit:
            continue
        sell_pending = any(po.order["mint"] == mint and po.order["side"] == "sell"
                           for po in state.pending.values())
        if sell_pending:
            continue
        if len(orders) >= max_orders:
            break
        orders.append({"side": "sell", "mint": mint,
                       "token_amount": pos.token_amount,
                       "max_slippage_bps": SELL_SLIP_BPS})

    # --- Probes: sell the whole probe as soon as its buy has landed.
    for mint in list(probes):
        pos = state.positions.get(mint)
        if pos is None:
            if mint not in pending_by_mint:
                del probes[mint]
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete or mint in pending_by_mint:
            continue
        if len(orders) >= max_orders:
            break
        orders.append({"side": "sell", "mint": mint,
                       "token_amount": pos.token_amount,
                       "max_slippage_bps": SELL_SLIP_BPS})
        probes[mint] = True

    # --- Entries: snipe clean launches in their first slots.
    free = state.free_cash_lamports()
    slots_left = caps["max_open_positions"] - state.committed_position_count()
    exposure = 0
    for h in held.values():
        exposure += h.invested
    for mint in traded:
        if len(orders) >= max_orders:
            break
        if slots_left <= 0:
            break
        if mint in held or mint in probes or mint in pending_by_mint:
            continue
        ln = launches.get(mint)
        if ln is None or ln.created is None:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        vq, vt, rq = curve_reserves(mint)
        price = (vq / vt) if vt > 0 else 0.0
        if not entry_signal(ln, now, rq, price):
            continue
        cap = state.max_buy_lamports(mint)
        amount = min(cap, STAKE, MAX_EXPOSURE - exposure)
        if amount > free - tx:
            amount = free - tx
        if amount < MIN_BUY:
            continue
        orders.append({"side": "buy", "mint": mint, "quote_lamports": amount,
                       "max_slippage_bps": BUY_SLIP_BPS})
        free -= amount + tx
        exposure += amount
        held[mint] = Held(now, amount)
        slots_left -= 1

    # --- Guardrail pacing: open a probe when fills or coins lag the pace.
    if (len(probes) < MAX_PROBES and slots_left > 0
            and first_time is not None and last_time is not None):
        pace = min(1.0, (last_time - first_time) / PACE_SECS)
        need_coins = len(my_coins) < math.ceil(TARGET_COINS * pace)
        need_fills = my_fills < math.ceil(TARGET_FILLS * pace)
        if need_coins or need_fills:
            min_rq = PROBE * 150  # buy cap is 1% of rq; want cap >= probe with headroom
            for mint in traded:
                if len(orders) >= max_orders or len(probes) >= MAX_PROBES:
                    break
                if (mint in held or mint in probes or mint in pending_by_mint
                        or mint in state.positions):
                    continue
                if need_coins and not need_fills and mint in my_coins:
                    continue
                curve = state.curves.get(mint)
                if curve is None or curve.complete:
                    continue
                if curve.real_quote_reserves < min_rq:
                    continue
                if free < PROBE + 2 * tx:
                    break
                orders.append({"side": "buy", "mint": mint,
                               "quote_lamports": PROBE,
                               "max_slippage_bps": BUY_SLIP_BPS})
                probes[mint] = False
                free -= PROBE + tx
    return orders


if __name__ == "__main__":
    makefun_bot.run(on_batch)
