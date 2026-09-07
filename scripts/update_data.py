"""
Update data.json for Kompas Fundamental XAUUSD.

Pulls three public, free, no-key-required sources:
  - CFTC Socrata Open Data: weekly Commitment of Traders (COT) report
    for the standard 100oz COMEX Gold contract.
  - ForexFactory's public calendar feed (used by their own website widgets):
    high-impact USD events for this week and next week.
  - Yahoo Finance chart API: live-ish GC=F (COMEX gold futures) price.

Writes the combined, summarized result to data.json at the repo root,
which index.html fetches and renders. Run manually with `python
scripts/update_data.py`, or on a schedule via the GitHub Actions
workflow in .github/workflows/update.yml.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data.json"

UA = {"User-Agent": "Mozilla/5.0 (compatible; KompasXAUUSD/1.0)"}
WIB = timezone(timedelta(hours=7))


def fmt_date(dt, with_time=False):
    """Portable equivalent of strftime('%-d %b %Y[, %H:%M WIB]')."""
    base = f"{dt.day} {dt.strftime('%b %Y')}"
    if with_time:
        base += dt.strftime(", %H:%M WIB")
    return base

COT_MARKET = "GOLD - COMMODITY EXCHANGE INC."
COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json?" + urllib.parse.urlencode({
    "$where": f"market_and_exchange_names='{COT_MARKET}'",
    "$order": "report_date_as_yyyy_mm_dd DESC",
    "$limit": 2,
})
FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=5d"


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def get_cot():
    rows = fetch_json(COT_URL)
    latest, prev = rows[0], rows[1]

    nc_long = int(latest["noncomm_positions_long_all"])
    nc_short = int(latest["noncomm_positions_short_all"])
    net_long = nc_long - nc_short
    prev_net_long = int(prev["noncomm_positions_long_all"]) - int(prev["noncomm_positions_short_all"])
    weekly_change = net_long - prev_net_long

    open_interest = int(latest["open_interest_all"])
    oi_change = int(latest["change_in_open_interest_all"])
    commercial_net = int(latest["comm_positions_long_all"]) - int(latest["comm_positions_short_all"])

    report_date = fmt_date(datetime.fromisoformat(latest["report_date_as_yyyy_mm_dd"]))

    return {
        "reportDate": report_date,
        "ncLong": nc_long,
        "ncShort": nc_short,
        "netLong": net_long,
        "weeklyChange": weekly_change,
        "commercialNet": commercial_net,
        "openInterest": open_interest,
        "oiChange": oi_change,
        "netLongPct": round(net_long / open_interest * 100, 1),
    }


# Events where a HIGHER-than-forecast number means a WEAKER economy
# (unemployment/claims data) -- everything else defaults to "growth":
# a higher-than-forecast number means a STRONGER economy.
SLACK_KEYWORDS = ("unemployment rate", "jobless claims", "continuing claims", "unemployment claims")
FOMC_KEYWORDS = ("fomc", "federal funds rate", "interest rate decision", "fed interest rate", "rate decision")


def classify_event(title):
    t = title.lower()
    if any(k in t for k in FOMC_KEYWORDS):
        return "fomc"
    if any(k in t for k in SLACK_KEYWORDS):
        return "slack"
    return "growth"


def build_scenario(title):
    kind = classify_event(title)

    if kind == "fomc":
        return {
            "strong": {"label": "Dovish (pangkas / sinyal pangkas suku bunga)", "impact": "Bullish", "cls": "tag-bull",
                       "reason": "USD melemah karena imbal hasil turun → emas jadi lebih menarik."},
            "weak": {"label": "Hawkish (tahan / naikkan suku bunga)", "impact": "Bearish", "cls": "tag-bear",
                     "reason": "USD & yield naik → biaya peluang memegang emas meningkat."},
        }
    if kind == "slack":
        # e.g. Unemployment Rate, Jobless Claims: a HIGH reading = weak labor market
        return {
            "strong": {"label": "Lebih tinggi dari forecast (pasar kerja melemah)", "impact": "Bullish", "cls": "tag-bull",
                       "reason": "The Fed berpeluang lebih dovish → USD melemah → emas naik."},
            "weak": {"label": "Lebih rendah dari forecast (pasar kerja menguat)", "impact": "Bearish", "cls": "tag-bear",
                     "reason": "The Fed cenderung hawkish/tahan suku bunga → USD menguat → emas tertekan."},
        }
    # default "growth" type: CPI, PPI, NFP, Retail Sales, GDP, PMI, etc.
    return {
        "strong": {"label": "Lebih tinggi dari forecast (data lebih kuat)", "impact": "Bearish", "cls": "tag-bear",
                   "reason": "Memperkuat ekspektasi The Fed hawkish → USD & yield naik → emas tertekan."},
        "weak": {"label": "Lebih rendah dari forecast (data lebih lemah)", "impact": "Bullish", "cls": "tag-bull",
                 "reason": "Membuka peluang The Fed lebih dovish → USD melemah → emas naik."},
    }


def get_calendar():
    events = []
    for url in FF_URLS:
        try:
            events.extend(fetch_json(url))
        except Exception:
            continue

    now = datetime.now(timezone.utc)
    picked = []
    for ev in events:
        if ev.get("country") != "USD" or ev.get("impact") != "High":
            continue
        try:
            dt = datetime.fromisoformat(ev["date"])
        except ValueError:
            continue
        if dt < now - timedelta(hours=6):
            continue
        picked.append((dt, ev))

    picked.sort(key=lambda x: x[0])
    out = []
    for dt, ev in picked[:6]:
        dt_wib = dt.astimezone(WIB)
        out.append({
            "date": fmt_date(dt_wib, with_time=True),
            "event": ev["title"],
            "note": f"High impact · forecast {ev.get('forecast') or '-'} · previous {ev.get('previous') or '-'}",
            "scenario": build_scenario(ev["title"]),
        })
    return out


def get_price():
    data = fetch_json(YAHOO_URL)
    meta = data["chart"]["result"][0]["meta"]
    return {
        "level": f"${meta['regularMarketPrice']:,.2f}",
        "changePct": round(meta.get("regularMarketChangePercent", 0.0), 2),
        "dayHigh": meta.get("regularMarketDayHigh"),
        "dayLow": meta.get("regularMarketDayLow"),
        "week52High": meta.get("fiftyTwoWeekHigh"),
        "week52Low": meta.get("fiftyTwoWeekLow"),
    }


def compute_bias(cot, price):
    # Heuristic composite score, -100 (bearish) .. +100 (bullish).
    # Not a trading signal by itself -- it blends speculative positioning
    # momentum, crowdedness, and today's price momentum.
    momentum_signal = max(-40, min(40, cot["weeklyChange"] / 400.0))
    crowd_signal = (cot["netLongPct"] - 50) * 0.8  # positioning skew vs neutral 50/50
    price_signal = max(-30, min(30, price["changePct"] * 10))
    score = round(max(-100, min(100, momentum_signal + crowd_signal + price_signal)))

    if score >= 30:
        label = "BULLISH"
    elif score >= 10:
        label = "NETRAL condong BULLISH"
    elif score <= -30:
        label = "BEARISH"
    elif score <= -10:
        label = "NETRAL condong BEARISH"
    else:
        label = "NETRAL"
    return score, label


def build_narrative(cot, price, calendar, score, label):
    direction = "naik" if cot["weeklyChange"] > 0 else "turun"
    crowd_note = (
        "sangat crowded (rawan aksi profit taking)" if cot["netLongPct"] > 55
        else "relatif seimbang" if cot["netLongPct"] > 40
        else "condong short"
    )
    price_note = (
        f"menguat {abs(price['changePct'])}% pada sesi terakhir" if price["changePct"] > 0
        else f"melemah {abs(price['changePct'])}% pada sesi terakhir"
    )
    next_event = calendar[0] if calendar else None

    paras = [
        f"Harga XAU/USD saat ini berada di area <span class=\"accent\">{price['level']}</span>, {price_note}, "
        f"dengan rentang 52 minggu ${price['week52Low']:,.0f}–${price['week52High']:,.0f}.",
        f"Posisi non-commercial (spekulan besar) pada laporan COT per {cot['reportDate']} tercatat net long "
        f"<span class=\"accent\">{cot['netLong']:,}</span> kontrak ({cot['netLongPct']}% dari open interest) — "
        f"{crowd_note}. Net long ini {direction} {abs(cot['weeklyChange']):,} kontrak dibanding pekan sebelumnya.",
        f"Open interest COMEX tercatat {cot['openInterest']:,} kontrak "
        f"({'naik' if cot['oiChange'] > 0 else 'turun'} {abs(cot['oiChange']):,}), mengindikasikan "
        f"{'minat baru masuk pasar' if cot['oiChange'] > 0 else 'sebagian posisi ditutup'}.",
    ]
    if next_event:
        paras.append(
            f"Katalis terdekat: <b>{next_event['event']}</b> pada {next_event['date']} — event high-impact "
            f"yang berpotensi memicu volatilitas jangka pendek pada USD dan gold."
        )
    paras.append(
        f"<b>Kesimpulan:</b> skor komposit {score:+d}/100 → bias <b>{label}</b>. "
        f"Ini gabungan sinyal dari momentum posisi spekulan, tingkat crowding, dan momentum harga terkini — "
        f"bukan sinyal entry, melainkan konteks fundamental untuk melengkapi analisis teknikal kamu."
    )
    return paras


def main():
    cot = get_cot()
    calendar = get_calendar()
    price = get_price()
    score, label = compute_bias(cot, price)
    narrative = build_narrative(cot, price, calendar, score, label)

    cot_tag = "Crowded Long" if cot["netLongPct"] > 55 else "Crowded Short" if cot["netLongPct"] < 30 else "Seimbang"
    cot_tag_class = "tag-bull" if cot["netLongPct"] > 55 else "tag-bear" if cot["netLongPct"] < 30 else "tag-neutral"

    cme_tag = "Menguat" if cot["oiChange"] > 0 else "Melemah"
    cme_tag_class = "tag-bull" if cot["oiChange"] > 0 else "tag-bear"

    out = {
        "updatedAt": fmt_date(datetime.now(WIB), with_time=True),
        "price": {"level": cot and price["level"]},
        "biasScore": score,
        "biasLabel": label,
        "narrative": narrative,
        "cot": {
            "reportDate": cot["reportDate"],
            "ncLong": cot["ncLong"],
            "ncShort": cot["ncShort"],
            "netLong": cot["netLong"],
            "weeklyChange": cot["weeklyChange"],
            "commercialNet": cot["commercialNet"],
            "openInterest": cot["openInterest"],
            "tagLabel": cot_tag,
            "tagClass": cot_tag_class,
        },
        "cme": {
            "openInterest": cot["openInterest"],
            "oiChange": cot["oiChange"],
            "rangeLow": f"${price['week52Low']:,.0f}",
            "rangeHigh": f"${price['week52High']:,.0f}",
            "structureNote": "OI & harga bergerak searah" if (cot["oiChange"] > 0) == (price["changePct"] > 0) else "OI & harga berlawanan arah",
            "tagLabel": cme_tag,
            "tagClass": cme_tag_class,
            "footnote": f"Open interest kontrak Gold 100oz COMEX per laporan CFTC {cot['reportDate']}; rentang dari harga 52 minggu terakhir",
        },
        "calendar": calendar or [{
            "date": "-", "event": "Tidak ada event high-impact USD terjadwal", "note": "Cek kembali menjelang akhir pekan"
        }],
        "sources": [
            {"label": "CFTC COT (data.gov terbuka)", "url": "https://publicreporting.cftc.gov/Market-Reports/Commitments-of-Traders/6dca-aqww"},
            {"label": "ForexFactory Calendar", "url": "https://www.forexfactory.com/calendar"},
            {"label": "Yahoo Finance GC=F", "url": "https://finance.yahoo.com/quote/GC=F/"},
        ],
    }

    DATA_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {DATA_PATH} — bias {score:+d} ({label})")


if __name__ == "__main__":
    main()
