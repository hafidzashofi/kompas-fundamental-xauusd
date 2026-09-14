"""Build monthly seasonality stats for Gold (COMEX GC=F) from Yahoo Finance
max-range monthly history, and write seasonality.json.

Unlike update_data.py (COT/calendar/price, refreshed 2x/day), this uses
long-run historical data that barely changes -- run it manually or on a
monthly schedule (see .github/workflows/seasonality.yml).
"""
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "Mozilla/5.0 (compatible; kompas-xauusd-bot/1.0)"}
WIB = timezone(timedelta(hours=7))
SYMBOL = "GC=F"
YAHOO_URL = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval=1mo&range=max"

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

# Thresholds to classify a month's historical tendency.
BULL_AVG = 0.3    # avg monthly return >= this AND winRate >= BULL_WIN -> Bullish
BULL_WIN = 55
BEAR_AVG = -0.3   # avg monthly return <= this AND winRate <= BEAR_WIN -> Bearish
BEAR_WIN = 45


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_monthly_closes():
    data = fetch_json(YAHOO_URL)
    result = data["chart"]["result"][0]
    ts = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    rows = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        rows.append((dt.year, dt.month, c))
    return rows


def build_monthly_returns(rows):
    """rows: list of (year, month, close), one per calendar month, in order.
    Returns dict: month(1-12) -> list of {year, returnPct} across all years."""
    by_month = {m: [] for m in range(1, 13)}
    for i in range(1, len(rows)):
        py, pm, pclose = rows[i - 1]
        y, m, close = rows[i]
        if pclose in (None, 0):
            continue
        ret_pct = (close - pclose) / pclose * 100
        by_month[m].append({"year": y, "returnPct": round(ret_pct, 2)})
    return by_month


def classify(avg_return, win_rate):
    if avg_return >= BULL_AVG and win_rate >= BULL_WIN:
        return "Bullish", "bull"
    if avg_return <= BEAR_AVG and win_rate <= BEAR_WIN:
        return "Bearish", "bear"
    return "Sideways", "neutral"


def build_seasonality():
    rows = get_monthly_closes()
    by_month = build_monthly_returns(rows)

    years_covered = sorted({y for m in by_month.values() for e in m for y in [e["year"]]})
    months_out = []
    for m in range(1, 13):
        entries = by_month[m]
        n = len(entries)
        avg_return = round(sum(e["returnPct"] for e in entries) / n, 2) if n else 0.0
        wins = sum(1 for e in entries if e["returnPct"] > 0)
        win_rate = round(wins / n * 100, 1) if n else 0.0
        best = max(entries, key=lambda e: e["returnPct"]) if entries else None
        worst = min(entries, key=lambda e: e["returnPct"]) if entries else None
        label, cls = classify(avg_return, win_rate)
        months_out.append({
            "month": m,
            "name": MONTH_NAMES[m - 1],
            "avgReturnPct": avg_return,
            "winRatePct": win_rate,
            "sampleYears": n,
            "best": best,
            "worst": worst,
            "label": label,
            "cls": cls,
        })

    # Cumulative average seasonal path: start an index at 100 on Jan 1 and
    # compound each month's average return in calendar order, so the line
    # traces the "typical" path gold takes through a year (Jan..Dec).
    cumulative = [{"label": "Awal Tahun", "index": 100.0}]
    idx = 100.0
    for mo in months_out:
        idx *= (1 + mo["avgReturnPct"] / 100)
        cumulative.append({"label": mo["name"], "index": round(idx, 2)})

    return {
        "generatedAt": datetime.now(WIB).strftime("%d %b %Y, %H:%M WIB"),
        "symbol": SYMBOL,
        "yearsCovered": f"{years_covered[0]}-{years_covered[-1]}" if years_covered else "-",
        "sampleCount": len(years_covered),
        "months": months_out,
        "cumulative": cumulative,
        "note": (
            "Dihitung dari return bulanan historis GC=F (COMEX Gold futures, "
            f"data Yahoo Finance sejak {years_covered[0] if years_covered else '-'}). "
            "Statistik masa lalu, bukan jaminan pergerakan di masa depan."
        ),
    }


def main():
    out = build_seasonality()
    with open("seasonality.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote seasonality.json ({out['yearsCovered']}, {out['sampleCount']} tahun)")


if __name__ == "__main__":
    main()
