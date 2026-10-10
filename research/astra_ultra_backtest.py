"""Standalone, public-data-only research; never imports app/broker modules.

Frozen candidate v1 versus a controlled LONG-only baseline approximation.
Run --download once; thereafter runs are offline. JSON artifacts stay outside repo.
No optimization, actual orders, account access, EUR conversion or funding model.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOUR = 3_600_000
DAY = 24 * HOUR
SYMBOLS = ("BTC", "ETH", "SOL")  # Stable priority when capacity binds.
REPO = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = REPO.parent / "astra-research"
PINNED_REVISION = "e8ff09c"
RULES = {"version": "astra-ultra-v1", "symbols": SYMBOLS, "bar": "1H",
         "entry_channel": 24, "trend_ema": 200, "exit_channel": 12,
         "atr_period": 14, "stop_atr": 2.0, "max_hold_hours": 120,
         "leverage": 1, "risk_fraction": .005, "aggregate_risk_fraction": .01,
         "max_notional_fraction": .4, "max_positions": 2,
         "initial_capital_usdt": 1000.0, "fee_per_side": .002,
         "slippage_per_side": .0005, "entry_priority": SYMBOLS}


def utc(ts):
    return datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()


def public_get(path, params=None):
    if not path.startswith(("/api/v5/public/", "/api/v5/market/")):
        raise ValueError("Only public market/time endpoints permitted")
    url = "https://www.okx.com" + path
    if params:
        url += "?" + urlencode(params)
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": "AstraPaperResearch/1"}), timeout=25) as response:
                payload = json.load(response)
            if payload.get("code") != "0":
                raise RuntimeError(str(payload))
            return payload["data"]
        except Exception:
            if attempt == 3:
                raise
            time.sleep(.5 * (attempt + 1))


def download(cache, days=470):
    cache.mkdir(parents=True, exist_ok=True)
    server_ts = int(public_get("/api/v5/public/time")[0]["ts"])
    floor = server_ts - days * DAY
    metadata = {"server_time": utc(server_ts), "server_ts": server_ts,
                "requested_days_with_warmup": days, "symbols": {}}
    for symbol in SYMBOLS:
        path = cache / f"{symbol}_1H.json"
        existing = json.loads(path.read_text()) if path.exists() else None
        rows = {int(r[0]): r for r in existing["rows"]} if existing else {}
        # Seed previously downloaded public data, avoiding duplicate 1-year traffic.
        # That archive lacks per-row confirm flags; use only bars ending before its fetch timestamp.
        seed_path = REPO.parent / "research" / f"crypto_{symbol}_1H.json"
        seed_used = bool(existing and existing.get("seed_archive"))
        if not rows and seed_path.exists():
            seed = json.loads(seed_path.read_text())
            fetched_ms = int(float(seed["fetched_at"]) * 1000)
            for i, ts in enumerate(seed["timestamps"]):
                if floor <= ts and ts + HOUR <= fetched_ms:
                    rows[ts] = [ts, seed["opens"][i], seed["highs"][i], seed["lows"][i],
                                seed["closes"][i], seed["volumes"][i], 0,
                                seed.get("quote_volumes", seed["volumes"])[i], "1"]
            seed_used = bool(rows)
        previous_max = max(rows) if rows else None
        cursor = None
        requests = 0
        while True:
            params = {"instId": symbol + "-USDT", "bar": "1H", "limit": 300}
            if cursor is not None:
                params["after"] = cursor
            batch = public_get("/api/v5/market/history-candles", params)
            requests += 1
            if not batch:
                break
            for row in batch:
                ts = int(row[0])
                if row[8] == "1" and floor <= ts and ts + HOUR <= server_ts:
                    rows[ts] = row
            oldest = min(int(r[0]) for r in batch)
            if oldest <= floor or (previous_max is not None and oldest <= previous_max):
                break
            if cursor is not None and oldest >= cursor:
                raise RuntimeError("Pagination did not advance")
            cursor = oldest
            time.sleep(.12)
        # A refreshed archive may need older data for a slower timeframe's warmup.
        # Backfill separately; refreshing its recent edge must not silently imply
        # that a newly requested earlier interval was also obtained.
        cursor = min(rows) if rows else None
        while cursor is not None and cursor > floor:
            batch = public_get("/api/v5/market/history-candles",
                {"instId": symbol + "-USDT", "bar": "1H", "limit": 300, "after": cursor})
            requests += 1
            if not batch:
                break
            for row in batch:
                ts = int(row[0])
                if row[8] == "1" and floor <= ts and ts + HOUR <= server_ts:
                    rows[ts] = row
            oldest = min(int(r[0]) for r in batch)
            if oldest >= cursor:
                raise RuntimeError("Backfill did not advance")
            cursor = oldest
            time.sleep(.12)
        ordered = [rows[t] for t in sorted(rows) if t >= floor]
        payload = {"source": "OKX public SPOT history-candles", "symbol": symbol,
                   "server_ts": server_ts, "seed_archive": str(seed_path) if seed_used else None,
                   "seed_confirmation": "closed before original fetched_at; original confirm unavailable" if seed_used else None,
                   "rows": ordered}
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        metadata["symbols"][symbol] = {"bars": len(ordered), "requests": requests,
            "first": utc(int(ordered[0][0])), "last_open": utc(int(ordered[-1][0])),
            "seed_used": seed_used, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        print(symbol, metadata["symbols"][symbol], flush=True)
    (cache / "download_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def pinned_indicators():
    # Reuse only pure indicator functions from the deployed revision, never dirty worktree/app imports.
    source = subprocess.check_output(["git", "show", f"{PINNED_REVISION}:analyzer/indicators.py"], cwd=REPO, text=True)
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, (ast.ImportFrom, ast.FunctionDef, ast.Expr)):
            raise ValueError("Unexpected top-level indicator code")
    namespace = {}
    exec(compile(tree, "pinned_indicators", "exec"), namespace)
    return namespace


def load_data(cache):
    data = {}
    for symbol in SYMBOLS:
        path = cache / f"{symbol}_1H.json"
        raw = json.loads(path.read_text())
        rows = sorted(raw["rows"], key=lambda r: int(r[0]))
        if any(str(r[8]) != "1" for r in rows):
            raise ValueError("Unconfirmed bar in archive")
        bars = {int(r[0]): {"o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                            "c": float(r[4]), "qv": float(r[7])} for r in rows}
        if len(bars) != len(rows):
            raise ValueError("Duplicate timestamp")
        for t, b in bars.items():
            if t % HOUR or not all(math.isfinite(v) and v > 0 for k, v in b.items() if k != "qv"):
                raise ValueError("Invalid bar")
            if not b["l"] <= min(b["o"], b["c"]) <= max(b["o"], b["c"]) <= b["h"]:
                raise ValueError("OHLC inconsistent")
        timestamps = sorted(bars)
        if any(b - a != HOUR for a, b in zip(timestamps, timestamps[1:])):
            raise ValueError(f"{symbol}: hourly gaps; refuse fabricated continuity")
        data[symbol] = bars
    common = sorted(set.intersection(*(set(d) for d in data.values())))
    if any(b - a != HOUR for a, b in zip(common, common[1:])):
        raise ValueError("Shared calendar has gaps")
    return common, {s: [data[s][t] for t in common] for s in SYMBOLS}


def features(bars, ind, rolling500=False):
    c, h, l = ([b[k] for b in bars] for k in ("c", "h", "l"))
    m = ind["macd"](c)
    bb = ind["bollinger"](c, 20, 2)
    f = {"ema200": ind["ema"](c, 200), "atr": ind["atr"](h, l, c, 14)}
    if rolling500:
        # Final selected 4H hypothesis explicitly defines finite-window indicators,
        # enabling deterministic parity with a live500-completed-bar snapshot.
        f["ema200"] = [None] * len(bars)
        f["atr"] = [None] * len(bars)
        for i in range(499, len(bars)):
            section = slice(i - 499, i + 1)
            f["ema200"][i] = ind["ema"](c[section], 200)[-1]
            f["atr"][i] = ind["atr"](h[section], l[section], c[section], 14)[-1]
    e20, e50, rsi, adx = ind["ema"](c, 20), ind["ema"](c, 50), ind["rsi"](c), ind["adx"](h, l, c)
    f["adx"] = adx
    f["supertrend"] = ind["supertrend"](h, l, c, period=10, multiplier=3.0)["trend"]
    f["score"] = [None] * len(bars)
    for i in range(60, len(bars)):
        score = 50 + (15 if e20[i] > e50[i] else -5)
        score += 12 if rsi[i] < 40 else (-15 if rsi[i] > 68 else 0)
        score += 10 if m["macd"][i] > m["signal"][i] else -10
        score += 10 if c[i] <= bb["lower"][i] else (-10 if c[i] >= bb["upper"][i] else 0)
        if adx[i] is not None:
            if adx[i] < 20:
                score = 50 + (score - 50) * .4
            elif adx[i] >= 25:
                score += (1 if score >= 50 else -1) * min((adx[i] - 25) * .2, 8)
        f["score"][i] = max(0, min(100, score))
    return f


def loss_per_coin(entry, stop, fee, slip):
    stop_fill = stop * (1 - slip)
    return entry * (1 + fee) - stop_fill * (1 - fee)


def replay(ts, data, feat, start, end, strategy, fee=.002, slip=.0005, retain=False,
           execution="standing_stop", entry_bars=24, exit_bars=12,
           stop_atr=2.0, max_hold_hours=120, bar_ms=HOUR):
    """Common risk/execution: 1x LONG, identical capital, fees, universe and scheduling.

    Baseline_control preserves score/ATR-target/trailing/time-exit only. Its live
    leverage, intrabar inputs, universe and 2H/day-window schedule are NOT replayed.
    """
    if execution not in ("standing_stop", "bar_open_observation"):
        raise ValueError("Unknown execution model")
    cash = 1000.0
    positions, trades, curve = {}, [], []
    peak, maxdd, totalfees, totalslip = cash, 0.0, 0.0, 0.0
    exposure_sum = 0.0
    indices = [i for i, t in enumerate(ts) if start <= t < end]
    if not indices or indices[0] < 500 or len(indices) != (end - start) // bar_ms:
        raise ValueError("Insufficient exact window or 500-bar EMA warmup")

    def equity(marks):
        return cash + sum(p["qty"] * marks[s] * (1 - slip) * (1 - fee) for s, p in positions.items())

    def close(s, raw, i, reason):
        nonlocal cash, totalfees, totalslip
        p = positions.pop(s)
        fill = raw * (1 - slip)
        fee_paid = p["qty"] * fill * fee
        cash += p["qty"] * fill - fee_paid
        pnl = p["qty"] * (fill - p["entry"]) - p["entry_fee"] - fee_paid
        totalfees += fee_paid
        totalslip += p["qty"] * raw * slip
        trades.append({"symbol": s, "entry_at": utc(ts[p["i"]]), "exit_bar": utc(ts[i]),
            "entry": p["entry"], "exit": fill, "qty": p["qty"], "net_pnl": pnl,
            "risk_budget_used": p["risk"], "r_multiple": pnl / p["risk"],
            "fees": p["entry_fee"] + fee_paid, "reason": reason})

    for i in indices:
        exited = set()
        opens = {s: data[s][i]["o"] for s in SYMBOLS}
        # Orders from completed i-1 candles and previously active stops, at i open.
        for s, p in list(positions.items()):
            prev = data[s][i - 1]
            why = None
            if opens[s] <= p["stop"]:
                why = "stop_gap"
            elif strategy == "astra" and (feat[s]["exit_signal"][i - 1] if "exit_signal" in feat[s] else prev["c"] < min(b["l"] for b in data[s][i - 1 - exit_bars:i - 1])):
                why = "channel_exit"
            elif strategy == "astra" and (i - p["i"]) * bar_ms >= max_hold_hours * HOUR:
                why = "time_exit"
            elif strategy == "supertrend_control" and feat[s]["supertrend"][i - 1] == -1:
                why = "supertrend_down"
            elif strategy == "baseline_control" and i - p["i"] >= 72 and (prev["c"] / p["entry"] - 1) < .015:
                why = "time_exit"
            elif p.get("target") and opens[s] >= p["target"]:
                why = "target_gap_capped"
            if why:
                raw = p["target"] if why == "target_gap_capped" else opens[s]
                close(s, raw, i, why)
                exited.add(s)
        # All signal decisions use i-1. No same-bar re-entry after closing.
        candidates = []
        for s in SYMBOLS:
            j = i - 1
            if s in positions or s in exited:
                continue
            if strategy == "astra":
                eligible = feat[s]["enter_signal"][j] if "enter_signal" in feat[s] else data[s][j]["c"] > max(b["h"] for b in data[s][j - entry_bars:j]) and data[s][j]["c"] > feat[s]["ema200"][j]
            elif strategy == "supertrend_control":
                eligible = feat[s]["supertrend"][j] == 1 and feat[s]["supertrend"][j - 1] == -1 and feat[s]["adx"][j] >= 20
            else:
                btc = data["BTC"]
                eligible = feat[s]["score"][j] >= 66 and btc[j]["c"] / btc[j - 24]["c"] - 1 >= -.04 and sum(b["qv"] for b in data[s][j - 23:j + 1]) >= 5_000_000
            if eligible:
                candidates.append(s)
        if strategy == "baseline_control":
            candidates.sort(key=lambda s: -feat[s]["score"][i - 1])
        for s in candidates:
            if len(positions) >= 2:
                break
            eq = equity(opens)
            risk_left = .01 * eq - sum(p["risk"] for p in positions.values())
            risk_budget = min(.005 * eq, max(0, risk_left))
            entry = opens[s] * (1 + slip)
            dist = feat[s]["atr"][i - 1] * (1.1 if strategy == "baseline_control" else stop_atr)
            stop = entry - dist
            if stop <= 0 or risk_budget <= 0:
                continue
            unitrisk = loss_per_coin(entry, stop, fee, slip)
            qty = min(risk_budget / unitrisk, .4 * eq / entry, cash / (entry * (1 + fee)))
            if qty * entry < 10:
                continue
            entryfee = qty * entry * fee
            cash -= qty * entry + entryfee
            totalfees += entryfee
            totalslip += qty * opens[s] * slip
            positions[s] = {"entry": entry, "qty": qty, "entry_fee": entryfee, "i": i,
                "stop": stop, "dist": dist, "risk": qty * unitrisk,
                "target": entry + 1.4 * dist if strategy == "baseline_control" else None}
            assert qty * unitrisk <= .005 * eq + 1e-8
            assert cash >= -1e-8
        # Intrabar stops/targets: both touched => stop first; stop gaps handled above.
        # Alternative intentionally samples once/bar. It is a sensitivity, NOT a bound
        # or exact reproduction of live seconds/minutes polling.
        if execution == "standing_stop":
            for s, p in list(positions.items()):
                bar = data[s][i]
                if bar["l"] <= p["stop"]:
                    close(s, p["stop"], i, "hard_stop")
                elif p["target"] and bar["h"] >= p["target"]:
                    close(s, p["target"], i, "target")
        closes = {s: data[s][i]["c"] for s in SYMBOLS}
        eq = equity(closes)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak)
        exposure_sum += sum(p["qty"] * closes[s] for s, p in positions.items()) / eq
        curve.append([ts[i] + bar_ms, eq])
        # Trailing from close becomes active ONLY on next bar.
        if strategy == "baseline_control":
            for s, p in positions.items():
                if closes[s] / p["entry"] - 1 >= .04:
                    p["stop"] = max(p["stop"], closes[s] - .85 * p["dist"])
    for s in list(positions):
        close(s, data[s][indices[-1]]["c"], indices[-1], "end_liquidation")
    assert math.isclose(cash, 1000 + sum(t["net_pnl"] for t in trades), abs_tol=1e-7)
    assert math.isclose(curve[-1][1], cash, abs_tol=1e-7)
    positive = sum(max(t["net_pnl"], 0) for t in trades)
    negative = -sum(min(t["net_pnl"], 0) for t in trades)
    bysymbol = {s: {"trades": sum(t["symbol"] == s for t in trades), "net_pnl": sum(t["net_pnl"] for t in trades if t["symbol"] == s)} for s in SYMBOLS}
    result = {"start": utc(start), "end_exclusive": utc(end), "hours": len(indices) * bar_ms / HOUR,
        "execution": execution,
        "return_pct": (cash / 1000 - 1) * 100, "final_usdt": cash,
        "max_equity_drawdown_pct": maxdd * 100, "equity_sample_hours": bar_ms / HOUR,
        "trades": len(trades), "wins": sum(t["net_pnl"] > 0 for t in trades),
        "profit_factor": positive / negative if negative else None,
        "fees_usdt": totalfees, "slippage_usdt": totalslip,
        "mean_sampled_exposure_pct": exposure_sum / len(indices) * 100,
        "mean_r": sum(t["r_multiple"] for t in trades) / len(trades) if trades else None,
        "by_symbol": bysymbol, "end_liquidations": sum(t["reason"] == "end_liquidation" for t in trades)}
    if retain:
        result.update(trades_detail=trades, hourly_curve=curve)
    return result


def resample_four_hours(ts, data):
    chunks = {}
    for i, t in enumerate(ts):
        chunks.setdefault(t // (4 * HOUR) * (4 * HOUR), []).append(i)
    complete = {t: ix for t, ix in chunks.items() if len(ix) == 4 and ts[ix[0]] == t and ts[ix[-1]] == t + 3 * HOUR}
    out = {}
    for s in SYMBOLS:
        out[s] = [{"o": data[s][ix[0]]["o"], "h": max(data[s][i]["h"] for i in ix),
                   "l": min(data[s][i]["l"] for i in ix), "c": data[s][ix[-1]]["c"],
                   "qv": sum(data[s][i]["qv"] for i in ix)} for ix in complete.values()]
    return list(complete), out


def four_hour_signals_on_hourly(ts, fourts, fourdata, fourfeat):
    """Map only newly completed4H signals to1H calendar, no forward fill of entries."""
    end_indices = {t + 4 * HOUR: i for i, t in enumerate(fourts)}
    out = {}
    for s in SYMBOLS:
        f = {"enter_signal": [False] * len(ts), "exit_signal": [False] * len(ts), "atr": [None] * len(ts)}
        for j, t in enumerate(ts):
            i = end_indices.get(t + HOUR)
            if i is None or i < 499:
                continue
            b = fourdata[s]
            f["atr"][j] = fourfeat[s]["atr"][i]
            f["enter_signal"][j] = b[i]["c"] > max(v["h"] for v in b[i-24:i]) and b[i]["c"] > fourfeat[s]["ema200"][i]
            f["exit_signal"][j] = b[i]["c"] < min(v["l"] for v in b[i-12:i])
        out[s] = f
    return out


def candidate_family(ts, data, feat, ind, cache):
    """Small explicitly enumerated exploration, added AFTER v1 loss was observed.

    No claim that these data are held out. No optimizer or automatic selection.
    """
    family = {
        "astra_1h_72_24_atr3_240h": {"entry_bars": 72, "exit_bars": 24, "stop_atr": 3., "max_hold_hours": 240},
        "astra_1h_168_48_atr3_336h": {"entry_bars": 168, "exit_bars": 48, "stop_atr": 3., "max_hold_hours": 336},
        "astra_4h_24_12_atr2_336h": {"entry_bars": 24, "exit_bars": 12, "stop_atr": 2., "max_hold_hours": 336, "bar_ms": 4 * HOUR},
        "supertrend_control_1h_10_3_adx20": {"stop_atr": 2.},
    }
    out = {"declared_after_v1_review": True, "selection_data_not_holdout": True,
           "cash_benchmark_return_pct": 0, "families": {}}
    ledgers = {}
    fourts, fourdata = resample_four_hours(ts, data)
    fourfeat = {s: features(b, ind, rolling500=True) for s, b in fourdata.items()}
    hybridfeat = four_hour_signals_on_hourly(ts, fourts, fourdata, fourfeat)
    shared_end = fourts[-1] + 4 * HOUR
    out["common_end_exclusive"] = utc(shared_end)
    for name, params in family.items():
        isfour = params.get("bar_ms") == 4 * HOUR
        t, d, f = (fourts, fourdata, fourfeat) if isfour else (ts, data, feat)
        bar = params.get("bar_ms", HOUR)
        end = shared_end
        runs = {}
        periods = [(f"last_{days}d", end - days * DAY, end) for days in (90, 180, 360)]
        periods += [(f"quarter_{n+1}_oldest_first", end - (360 - 90*n) * DAY, end - (270 - 90*n) * DAY) for n in range(4)]
        strat = "supertrend_control" if name.startswith("supertrend") else "astra"
        for label, start, finish in periods:
            if start - t[0] < 500 * bar:
                runs[label] = {"unavailable": "Insufficient500-bar warmup", "data_start": utc(t[0]), "requested_start": utc(start)}
                continue
            detail = replay(t, d, f, start, finish, strat, retain=True, **params)
            ledger = {k: detail.pop(k) for k in ("trades_detail", "hourly_curve")}
            ledgers[name + "/" + label] = ledger
            runs[label] = detail
        sensitivity_days = 360 if "return_pct" in runs["last_360d"] else 180
        start = end - sensitivity_days * DAY
        stress = {
            "fee_030_slip_005": replay(t, d, f, start, end, strat, fee=.003, **params),
            "fee_020_slip_010": replay(t, d, f, start, end, strat, slip=.001, **params),
            "bar_open_observation": replay(t, d, f, start, end, strat, execution="bar_open_observation", **params),
        }
        if isfour:
            stress["four_hour_signals_hourly_ohlc_execution"] = replay(ts, data, hybridfeat, start, end,
                "astra", max_hold_hours=336)
        ledger = ledgers[name + f"/last_{sensitivity_days}d"]["trades_detail"]
        profits = sorted((trade["net_pnl"] for trade in ledger), reverse=True)
        concentration = {"period_days": sensitivity_days, "largest_trade_net_usdt": profits[0] if profits else 0,
            "sum_net_without_largest_trade_usdt": sum(profits[1:]),
            "sum_net_without_largest_three_usdt": sum(profits[3:]),
            "note": "Arithmetic contribution only, not reinvestment replay or alternative historical path"}
        out["families"][name] = {"parameters": params, "results": runs,
                                  "indicator_definition": "exact_rolling500_completed_bars" if isfour else "full_prefix_history",
                                  "sensitivity_days": sensitivity_days, "sensitivity": stress,
                                  "concentration": concentration}
        print(name, {k: round(v["return_pct"], 4) if "return_pct" in v else "unavailable" for k, v in runs.items()}, flush=True)
        print("sensitivity", {k: round(v["return_pct"], 4) for k, v in stress.items()}, "concentration", concentration, flush=True)
    out["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    out["file_hashes"] = {s: hashlib.sha256((cache / f"{s}_1H.json").read_bytes()).hexdigest() for s in SYMBOLS}
    (cache / "candidate_family.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    (cache / "candidate_family_ledgers.json").write_text(json.dumps(ledgers, separators=(",", ":")), encoding="utf-8")


def self_test():
    ind = pinned_indicators()
    data = [{"o": 100., "h": 101., "l": 99., "c": 100., "qv": 1e7} for _ in range(700)]
    data[600] = {"o": 100., "h": 103., "l": 100., "c": 102., "qv": 1e7}
    data[601] = {"o": 110., "h": 112., "l": 109., "c": 111., "qv": 1e7}
    data[602] = {"o": 95., "h": 96., "l": 94., "c": 95., "qv": 1e7}
    allbars = {s: data for s in SYMBOLS}
    f = {s: features(data, ind) for s in SYMBOLS}
    ts = [i * HOUR for i in range(700)]
    result = replay(ts, allbars, f, 600 * HOUR, 604 * HOUR, "astra", retain=True)
    first = result["trades_detail"][0]
    assert first["entry"] == 110 * 1.0005  # Signal close is 102: no lookahead fill.
    assert first["exit"] == 95 * .9995 and first["reason"] == "stop_gap"
    assert first["net_pnl"] < -first["risk_budget_used"]  # Gaps may exceed risk budget.
    earlier = features(data[:601], ind)
    assert earlier["ema200"][-1] == f["BTC"]["ema200"][600]
    assert earlier["atr"][-1] == f["BTC"]["atr"][600]
    unitloss = loss_per_coin(100, 98, .001, .0005)
    assert unitloss > 2
    # Once a position is entered, high/low ambiguity cannot grant the favorable target.
    ambiguous = [dict(b) for b in data]
    ambiguous[601] = {"o": 102., "h": 125., "l": 85., "c": 110., "qv": 1e7}
    af = {s: features(ambiguous, ind) for s in SYMBOLS}
    for s in SYMBOLS:
        af[s]["score"] = [0] * 700
        af[s]["score"][600] = 70
    ar = replay(ts, {s: ambiguous for s in SYMBOLS}, af, 600 * HOUR, 604 * HOUR,
                "baseline_control", retain=True)
    assert ar["trades_detail"][0]["reason"] == "hard_stop"
    assert ar["trades_detail"][0]["net_pnl"] < 0
    print("Self-checks passed: next-open fill, adverse gap, uncapped gap loss, causal EMA/ATR, risk includes costs, cash reconciliation, ambiguous-bar stop first.")


def verify_live_signals(ts, data, ind, cache):
    """Read-only pure-function parity; never invokes engine start/cycle/network."""
    engine_path = REPO / "analyzer" / "astra_ultra.py"
    source = engine_path.read_text(encoding="utf-8")
    namespace = {"__name__": "astra_pure_parity", "__file__": str(engine_path)}
    exec(compile(source, str(engine_path), "exec"), namespace)
    if namespace["RULES"].get("bar_seconds") != 4 * HOUR / 1000 or namespace["RULES"].get("warmup_bars") != 500:
        raise ValueError("Live rules do not match frozen4H500bar contract")
    fourts, bars = resample_four_hours(ts, data)
    count, maxema, maxatr, mismatches = 0, 0., 0., []
    for s in SYMBOLS:
        f = features(bars[s], ind, rolling500=True)
        for i in range(499, len(fourts)):
            rows = [[str(fourts[j]), str(bars[s][j]["o"]), str(bars[s][j]["h"]),
                     str(bars[s][j]["l"]), str(bars[s][j]["c"]), "0", "0", "0", "1"] for j in range(i - 499, i + 1)]
            signal = namespace["signal"](rows, (fourts[i] + 4 * HOUR) / 1000 + 1)
            enter = bars[s][i]["c"] > max(b["h"] for b in bars[s][i - 24:i]) and bars[s][i]["c"] > f["ema200"][i]
            leave = bars[s][i]["c"] < min(b["l"] for b in bars[s][i - 12:i])
            maxema = max(maxema, abs(signal["ema200"] - f["ema200"][i]))
            maxatr = max(maxatr, abs(signal["atr14"] - f["atr"][i]))
            if signal["enter"] != enter or signal["exit"] != leave:
                mismatches.append([s, fourts[i]])
            count += 1
    result = {"windows_checked": count, "entry_exit_mismatches": len(mismatches),
              "max_ema_absolute_error": maxema, "max_atr_absolute_error": maxatr,
              "engine_version": namespace["VERSION"],
              "engine_sha256": hashlib.sha256(source.encode()).hexdigest(),
              "execution_parity_claimed": False}
    (cache / "signal_parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert not mismatches and maxema < 1e-7 and maxatr < 1e-9, result
    print("Pure signal parity:", result, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--family", action="store_true")
    parser.add_argument("--verify-live-signals", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        if not args.download and not (args.cache / "BTC_1H.json").exists():
            return
    if args.download:
        download(args.cache)
    ts, data = load_data(args.cache)
    ind = pinned_indicators()
    if args.verify_live_signals:
        verify_live_signals(ts, data, ind, args.cache)
    feat = {s: features(b, ind) for s, b in data.items()}
    if args.family:
        candidate_family(ts, data, feat, ind, args.cache)
    end = ts[-1] + HOUR
    results = {"generated_at": datetime.now(timezone.utc).isoformat(), "rules": RULES,
        "indicators_revision": PINNED_REVISION, "data_start": utc(ts[0]), "data_end_exclusive": utc(end),
        "shared_bars": len(ts), "coverage_gaps": 0, "windows": {},
        "limitations": ["No forward evidence; current fixed universe selected without point-in-time liquidity universe.",
            "Baseline is risk-matched LONG-only signal/exit approximation, not deployed-account replay.",
            "Hourly OHLC assumes standing stops; live polling can have delayed/missed intrabar exits.",
            "0.20% fee models regular EEA spot-only tariff, not verified account fee; sensitivity uses0.10%.",
            "All figures are USDT; no EUR currency conversion. Overlapping windows are not independent.",
            "Historical rules were specified before this replay, but related history was examined previously."]}
    ledgers = {}
    windows = [(f"last_{d}d", end - d * DAY, end) for d in (90, 180, 360)]
    windows += [(f"quarter_{n+1}_oldest_first", end - (360 - 90*n) * DAY, end - (270 - 90*n) * DAY) for n in range(4)]
    for name, start, finish in windows:
        if start - ts[0] < 500 * HOUR:
            results["windows"][name] = {"unavailable": "Less than500 warmup bars"}
            continue
        window = {}
        for strategy in ("astra", "baseline_control"):
            detail = replay(ts, data, feat, start, finish, strategy, retain=True)
            ledgers[name + "/" + strategy] = {k: detail.pop(k) for k in ("trades_detail", "hourly_curve")}
            window[strategy] = detail
            window[strategy + "_lower_fee_sensitivity"] = replay(ts, data, feat, start, finish, strategy, fee=.001)
        window["astra_hourly_observation_sensitivity"] = replay(ts, data, feat, start, finish, "astra", execution="bar_open_observation")
        i = ts.index(start)
        j = ts.index(finish - HOUR)
        window["btc_buy_hold_net_pct"] = (data["BTC"][j]["c"] * (1-.0005) * (1-.002) / (data["BTC"][i]["o"] * (1+.0005) * (1+.002)) - 1) * 100
        results["windows"][name] = window
        print(name, {k: {a: round(v[a], 4) if isinstance(v[a], float) else v[a] for a in ("return_pct", "max_equity_drawdown_pct", "trades", "profit_factor")} for k, v in window.items() if isinstance(v, dict)}, flush=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    results["file_hashes"] = {s: hashlib.sha256((args.cache / f"{s}_1H.json").read_bytes()).hexdigest() for s in SYMBOLS}
    results["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (args.cache / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (args.cache / "trade_ledgers.json").write_text(json.dumps(ledgers, separators=(",", ":")), encoding="utf-8")
    print("Results:", args.cache / "results.json")


if __name__ == "__main__":
    main()
