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

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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
]
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=5d"
YIELD_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?interval=1d&range=5d"
DXY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=5d"
NEWS_URL = "https://www.fxstreet.com/rss/news"


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

    comm_long = int(latest["comm_positions_long_all"])
    comm_short = int(latest["comm_positions_short_all"])
    commercial_net = comm_long - comm_short

    retail_long = int(latest["nonrept_positions_long_all"])
    retail_short = int(latest["nonrept_positions_short_all"])
    retail_net = retail_long - retail_short
    prev_retail_net = int(prev["nonrept_positions_long_all"]) - int(prev["nonrept_positions_short_all"])

    report_date = fmt_date(datetime.fromisoformat(latest["report_date_as_yyyy_mm_dd"]))

    return {
        "reportDate": report_date,
        "ncLong": nc_long,
        "ncShort": nc_short,
        "netLong": net_long,
        "weeklyChange": weekly_change,
        "commercialLong": comm_long,
        "commercialShort": comm_short,
        "commercialNet": commercial_net,
        "retailLong": retail_long,
        "retailShort": retail_short,
        "retailNet": retail_net,
        "retailWeeklyChange": retail_net - prev_retail_net,
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
        except Exception as e:
            print(f"WARNING: failed to fetch {url}: {e}")
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
            "note": f"Dampak tinggi · Prediksi {ev.get('forecast') or '-'} · Sebelumnya {ev.get('previous') or '-'}",
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


def get_macro():
    yld = fetch_json(YIELD_URL)["chart"]["result"][0]["meta"]
    dxy = fetch_json(DXY_URL)["chart"]["result"][0]["meta"]
    return {
        "yield10y": round(yld["regularMarketPrice"], 2),
        "yield10yChange": round(yld.get("regularMarketChangePercent", 0.0), 2),
        "dxy": round(dxy["regularMarketPrice"], 2),
        "dxyChange": round(dxy.get("regularMarketChangePercent", 0.0), 2),
    }


NEWS_KEYWORDS = [
    "gold", "xau", "silver", "fed", "fomc", "powell", "rate cut", "rate hike",
    "interest rate", "dollar", "dxy", "treasury", "yield", "inflation", "cpi",
    "ppi", "nonfarm", "payrolls", "jobless", "safe haven", "safe-haven",
    "geopolit", "tariff", "war", "middle east", "opec", "central bank",
    "recession", "jerome powell",
]


def tag_news(text):
    t = text.lower()
    if any(k in t for k in ("gold", "xau", "silver", "safe haven", "safe-haven")):
        return "Emas"
    if any(k in t for k in ("fed", "fomc", "powell", "rate cut", "rate hike", "interest rate", "central bank")):
        return "Bank Sentral"
    if any(k in t for k in ("dollar", "dxy", "treasury", "yield")):
        return "Dolar/Yield"
    if any(k in t for k in ("cpi", "ppi", "nonfarm", "payrolls", "jobless", "inflation", "recession")):
        return "Data Ekonomi"
    if any(k in t for k in ("geopolit", "tariff", "war", "middle east", "opec")):
        return "Geopolitik"
    return "Pasar"


TRANSLATE_URL = "https://api.mymemory.translated.net/get"


def translate_to_id(text):
    """Best-effort EN->ID translation via MyMemory's free public API.
    Falls back to the original text if the service is unreachable or
    the text is too long for one request."""
    if not text or len(text) > 480:
        return text
    try:
        url = TRANSLATE_URL + "?" + urllib.parse.urlencode({"q": text, "langpair": "en|id"})
        data = fetch_json(url)
        translated = data.get("responseData", {}).get("translatedText")
        return translated or text
    except Exception:
        return text


def get_news():
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(NEWS_URL, headers=UA), timeout=20
        ).read()
    except Exception:
        return []

    root = ET.fromstring(raw)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        desc = re.sub("<[^<]+?>", "", html.unescape(item.findtext("description") or "")).strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()

        haystack = f"{title} {desc}".lower()
        if not any(k in haystack for k in NEWS_KEYWORDS):
            continue

        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None

        summary_en = (desc[:180] + "…") if len(desc) > 180 else desc
        items.append({
            "titleEn": title,
            "summaryEn": summary_en,
            "link": link,
            "time": fmt_date(dt.astimezone(WIB), with_time=True) if dt else "-",
            "sortKey": dt or datetime(1970, 1, 1, tzinfo=timezone.utc),
            "tag": tag_news(haystack),
        })

    items.sort(key=lambda x: x["sortKey"], reverse=True)
    items = items[:8]
    for it in items:
        del it["sortKey"]
        it["titleId"] = translate_to_id(it["titleEn"])
        it["summaryId"] = translate_to_id(it["summaryEn"])
    return items


def build_positioning(cot):
    def net_label(net):
        if net > 0:
            return "NET LONG", "tag-bull"
        if net < 0:
            return "NET SHORT", "tag-bear"
        return "SEIMBANG", "tag-neutral"

    hedge_label, hedge_cls = net_label(cot["netLong"])
    retail_label, retail_cls = net_label(cot["retailNet"])
    comm_label, comm_cls = net_label(cot["commercialNet"])

    same_direction = (cot["netLong"] > 0) == (cot["retailNet"] > 0)
    alignment_note = (
        "Retail dan hedge fund/spekulan besar searah — tren yang sedang berjalan "
        "cenderung didukung mayoritas pelaku pasar, termasuk institusi besar."
        if same_direction else
        "Retail dan hedge fund/spekulan besar berlawanan arah. Secara historis, "
        "posisi retail sering jadi indikator kontrarian — saat retail net long besar-besaran "
        "sementara smart money mulai berkurang, itu sinyal kewaspadaan akan potensi pembalikan."
    )

    return {
        "hedgeFund": {
            "long": cot["ncLong"], "short": cot["ncShort"], "net": cot["netLong"],
            "label": hedge_label, "cls": hedge_cls,
        },
        "retail": {
            "long": cot["retailLong"], "short": cot["retailShort"], "net": cot["retailNet"],
            "weeklyChange": cot["retailWeeklyChange"], "label": retail_label, "cls": retail_cls,
        },
        "commercial": {
            "long": cot["commercialLong"], "short": cot["commercialShort"], "net": cot["commercialNet"],
            "label": comm_label, "cls": comm_cls,
        },
        "alignmentNote": alignment_note,
    }


def build_policy(macro, price):
    yield_dir = "naik" if macro["yield10yChange"] > 0 else "turun"
    dxy_dir = "menguat" if macro["dxyChange"] > 0 else "melemah"
    gold_dir = "naik" if price["changePct"] > 0 else "turun"

    today_note = (
        f"Hari ini yield US Treasury 10-tahun {yield_dir} ke {macro['yield10y']}% "
        f"({macro['yield10yChange']:+.2f}%) dan indeks dolar (DXY) {dxy_dir} ke {macro['dxy']} "
        f"({macro['dxyChange']:+.2f}%), sementara XAU/USD {gold_dir} {abs(price['changePct'])}%. "
        + (
            "Pola ini konsisten dengan hubungan klasik: yield/dolar naik menekan emas."
            if (macro["yield10yChange"] > 0 or macro["dxyChange"] > 0) and price["changePct"] < 0
            else "Pola ini konsisten dengan hubungan klasik: yield/dolar turun mengangkat emas."
            if (macro["yield10yChange"] < 0 or macro["dxyChange"] < 0) and price["changePct"] > 0
            else "Pergerakannya tidak sepenuhnya sejalan pola klasik — kemungkinan ada faktor lain "
                 "(geopolitik, arus safe-haven, atau positioning) yang lebih dominan hari ini."
        )
    )

    mechanisms = [
        {
            "title": "Suku bunga The Fed (FOMC)",
            "body": "Emas tidak memberi imbal hasil (non-yielding asset). Saat The Fed menaikkan suku bunga "
                    "atau bersikap hawkish, yield obligasi & deposito USD jadi lebih menarik dibanding emas → "
                    "dana mengalir keluar dari emas → harga tertekan. Sebaliknya, sikap dovish atau pemangkasan "
                    "suku bunga menurunkan biaya peluang memegang emas → harga cenderung naik.",
        },
        {
            "title": "Kekuatan Dolar AS (DXY)",
            "body": "Emas dihargai dalam USD di pasar global. Saat dolar menguat (DXY naik), emas jadi lebih "
                    "mahal bagi pemegang mata uang lain sehingga permintaan melemah → harga turun. Dolar yang "
                    "melemah membuat emas relatif lebih murah → permintaan & harga naik.",
        },
        {
            "title": "Quantitative Easing / Tightening",
            "body": "QE (bank sentral mencetak uang, membeli obligasi) membanjiri sistem dengan likuiditas dan "
                    "melemahkan mata uang → biasanya bullish untuk emas sebagai lindung nilai inflasi. QT "
                    "(mengurangi neraca) menarik likuiditas keluar → cenderung bearish untuk emas.",
        },
        {
            "title": "Pembelian emas bank sentral global",
            "body": "Beberapa tahun terakhir, bank sentral negara berkembang (China, India, Turki, Polandia, dll) "
                    "secara konsisten menjadi pembeli neto emas untuk diversifikasi cadangan devisa dari dolar. "
                    "Ini jadi faktor bullish struktural jangka panjang yang independen dari siklus suku bunga.",
        },
        {
            "title": "Kebijakan bank sentral lain (ECB, BOJ, PBOC)",
            "body": "Selisih suku bunga The Fed vs bank sentral lain menggerakkan pasangan mata uang utama, yang "
                    "pada akhirnya memengaruhi DXY. Contoh: BOJ yang mulai hawkish menguatkan Yen → menekan DXY → "
                    "cenderung mendukung harga emas meski tidak ada perubahan kebijakan dari The Fed.",
        },
    ]

    return {"todayNote": today_note, "mechanisms": mechanisms}


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

    bullish_pct = round((score + 100) / 2)
    bearish_pct = 100 - bullish_pct
    return score, label, bullish_pct, bearish_pct


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
    macro = get_macro()
    news = get_news()
    score, label, bullish_pct, bearish_pct = compute_bias(cot, price)
    narrative = build_narrative(cot, price, calendar, score, label)
    positioning = build_positioning(cot)
    policy = build_policy(macro, price)

    cot_tag = "Crowded Long" if cot["netLongPct"] > 55 else "Crowded Short" if cot["netLongPct"] < 30 else "Seimbang"
    cot_tag_class = "tag-bull" if cot["netLongPct"] > 55 else "tag-bear" if cot["netLongPct"] < 30 else "tag-neutral"

    cme_tag = "Menguat" if cot["oiChange"] > 0 else "Melemah"
    cme_tag_class = "tag-bull" if cot["oiChange"] > 0 else "tag-bear"

    out = {
        "updatedAt": fmt_date(datetime.now(WIB), with_time=True),
        "price": {"level": cot and price["level"]},
        "biasScore": score,
        "biasLabel": label,
        "bullishPct": bullish_pct,
        "bearishPct": bearish_pct,
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
        "positioning": positioning,
        "policy": policy,
        "macro": macro,
        "news": news,
        "sources": [
            {"label": "CFTC COT (data.gov terbuka)", "url": "https://publicreporting.cftc.gov/Market-Reports/Commitments-of-Traders/6dca-aqww"},
            {"label": "ForexFactory Calendar", "url": "https://www.forexfactory.com/calendar"},
            {"label": "Yahoo Finance GC=F", "url": "https://finance.yahoo.com/quote/GC=F/"},
            {"label": "FXStreet News", "url": "https://www.fxstreet.com/news"},
            {"label": "US 10Y Yield (^TNX)", "url": "https://finance.yahoo.com/quote/%5ETNX/"},
            {"label": "US Dollar Index (DXY)", "url": "https://finance.yahoo.com/quote/DX-Y.NYB/"},
        ],
    }

    DATA_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {DATA_PATH} — bias {score:+d} ({label})")


if __name__ == "__main__":
    main()
