"""Rein-Python technische Indikatoren."""
from typing import List


def _sma(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    out = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(values[i - period + 1:i + 1]) / period)
    return out


def ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return [None] * len(values)
    k = 2.0 / (period + 1)
    out = [None] * (period - 1)
    # Seed mit SMA
    seed = sum(values[:period]) / period
    out.append(seed)
    for i in range(period, len(values)):
        val = values[i] * k + out[-1] * (1 - k)
        out.append(val)
    return out


def rsi(values: List[float], period: int = 14) -> List[float]:
    if len(values) <= period:
        return [None] * len(values)
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
    rsi_vals = [None] * (period) + [100 - (100 / (1 + rs))]

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        rsi_vals.append(100 - (100 / (1 + rs)))
    return rsi_vals


def macd(values: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = []
    # EMA-Lines haben anfangs None-Werte, daher index-sicher
    for i in range(len(values)):
        f = ema_fast[i]
        s = ema_slow[i]
        macd_line.append(f - s if f is not None and s is not None else None)

    # Signal = EMA des MACD (ohne None)
    clean_macd = [v for v in macd_line if v is not None]
    signal_ema_clean = ema(clean_macd, signal)
    # Zurück in Voll-Länge mappen
    signal_line = [None] * (len(macd_line) - len(signal_ema_clean)) + signal_ema_clean

    histogram = []
    for i in range(len(macd_line)):
        m = macd_line[i]
        s = signal_line[i]
        histogram.append(m - s if m is not None and s is not None else None)

    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    if len(closes) < 2:
        return [None] * len(closes)
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        trs.append(max(tr1, tr2, tr3))

    if len(trs) < period:
        return [None] * len(closes)

    atr_vals = [None] * (period - 1)
    seed = sum(trs[:period]) / period
    atr_vals.append(seed)
    for i in range(period, len(trs)):
        atr_vals.append((atr_vals[-1] * (period - 1) + trs[i]) / period)
    # Längen angleichen: fülle vorne mit None
    return [None] * (len(closes) - len(atr_vals)) + atr_vals


def bollinger(values: List[float], period: int = 20, std_dev: int = 2):
    middle = _sma(values, period)
    upper, lower = [], []
    for i in range(len(values)):
        if i + 1 < period:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i - period + 1:i + 1]
        m = middle[i]
        s = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        upper.append(m + std_dev * s)
        lower.append(m - std_dev * s)
    return {"upper": upper, "middle": middle, "lower": lower}


def volume_trend(volumes: List[float], period: int = 20) -> float:
    """Gibt Verhältnis letztes Volumen zum 20-Tage-Schnitt zurück."""
    if len(volumes) < period:
        return 1.0
    last = volumes[-1]
    avg = sum(volumes[-period:]) / period
    return last / avg if avg else 1.0
