#!/usr/bin/env python3
"""Ranní briefing pro obchodování Nasdaqu: pre-market, nálada trhu, kalendář z Forex Factory."""
import json
import urllib.request
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

ROOT = Path(__file__).parent
PRAGUE = ZoneInfo("Europe/Prague")
NB = "\u00a0"
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def num(x, d=2):
    return f"{x:,.{d}f}".replace(",", NB).replace(".", ",")


def pc(x):
    return ("+" if x >= 0 else "\u2212") + num(abs(x)) + NB + "%"


def tone(x):
    return "up" if x >= 0 else "down"


def quotes(cfg):
    names = {**cfg["futures"], **cfg["macro"]}
    data = yf.download(list(names), period="5d", interval="1d", group_by="ticker", progress=False)
    rows = []
    for s, name in names.items():
        try:
            c = data[s]["Close"].dropna()
            rows.append({"sym": s, "name": name, "last": float(c.iloc[-1]),
                         "chg": float((c.iloc[-1] / c.iloc[-2] - 1) * 100)})
        except Exception as e:
            print("Bez dat:", s, e)
    return rows


def premarket(cfg):
    rows = []
    for s, name in cfg["stocks"].items():
        try:
            info = yf.Ticker(s).info
            pre = info.get("preMarketPrice")
            prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
            if pre and prev:
                rows.append({"sym": s, "name": name, "last": float(pre),
                             "chg": (pre / prev - 1) * 100})
        except Exception as e:
            print("Bez pre-marketu:", s, e)
    return sorted(rows, key=lambda r: -r["chg"])


def calendar():
    try:
        req = urllib.request.Request(FF_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            items = json.load(r)
    except Exception as e:
        print("Kalendář se nepodařilo stáhnout:", e)
        return None
    today = datetime.now(PRAGUE).date()
    out = []
    for e in items:
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        t = datetime.fromisoformat(e["date"]).astimezone(PRAGUE)
        if t.date() == today:
            out.append({"time": t.strftime("%H:%M"), "title": e.get("title", ""),
                        "impact": "vysoký" if e["impact"] == "High" else "střední",
                        "forecast": e.get("forecast") or "–", "previous": e.get("previous") or "–"})
    return sorted(out, key=lambda x: x["time"])


def bias(rows):
    f = [r["chg"] for r in rows if r["sym"] in ("NQ=F", "ES=F")]
    if not f:
        return "Bez dat", None
    a = sum(f) / len(f)
    for lim, label in ((0.6, "Bullish"), (0.15, "Mírně bullish"), (-0.15, "Neutrální"), (-0.6, "Mírně bearish")):
        if a >= lim:
            return label, a
    return "Bearish", a


MESICE = ["lednu", "únoru", "březnu", "dubnu", "květnu", "červnu", "červenci", "srpnu", "září", "říjnu", "listopadu", "prosinci"]


def seasonality():
    """Vlastní jednoduchá sezónnost: jak se Nasdaq 100 choval v tomto měsíci v minulých letech."""
    try:
        s = yf.download("^NDX", period="15y", interval="1d", progress=False)["Close"].squeeze().dropna()
        m = s.groupby([s.index.year, s.index.month]).last().pct_change().dropna()
        now = datetime.now(PRAGUE)
        x = [v * 100 for (y, mo), v in m.items() if mo == now.month and y != now.year]
        if len(x) < 5:
            return None
        return (f"Sezónnost: v {MESICE[now.month - 1]} Nasdaq 100 za posledních {len(x)} let průměrně "
                f"{pc(sum(x) / len(x))} a rostl v {num(sum(1 for v in x if v > 0) / len(x) * 100, 0)}{NB}% let.")
    except Exception as e:
        print("Sezónnost se nepodařilo spočítat:", e)
        return None


def scenario(label, share):
    low = label.lower()
    if "bullish" in low and share >= 0.6:
        return "Čekané otevření: pokračování nahoru. Sleduj, jestli Nasdaq po otevření drží nad otevírací cenou."
    if "bearish" in low and share <= 0.4:
        return "Čekané otevření: pokračování dolů. Sleduj, jestli Nasdaq po otevření drží pod otevírací cenou."
    return "Futures a akcie spolu úplně nesouhlasí, takže otevření je nejisté. Počkej na první reakci trhu."


WIDGETS = """<div id="tv"></div><script>
const dark = true;
[["technical-analysis", {symbol: "CME_MINI:NQ1!", interval: "15m", height: 420, showIntervalTabs: true, displayMode: "single"}, "Technický signál pro NQ (nákup nebo prodej)"],
 ["stock-heatmap", {exchanges: [], dataSource: "NASDAQ100", grouping: "sector", blockSize: "market_cap_basic", blockColor: "change", hasTopBar: false, isZoomEnabled: true, hasSymbolTooltip: true, height: 480}, "Mapa akcií Nasdaq 100 (zelená roste, červená klesá)"],
 ["timeline", {feedMode: "market", market: "stock", displayMode: "regular", height: 480}, "Aktuální zprávy"]
].forEach(([name, cfg, title]) => {
  const sec = document.createElement("section");
  sec.innerHTML = "<h2></h2><div class='tradingview-widget-container'></div>";
  sec.querySelector("h2").textContent = title;
  const sc = document.createElement("script");
  sc.src = "https://s3.tradingview.com/external-embedding/embed-widget-" + name + ".js";
  sc.async = true;
  sc.text = JSON.stringify({colorTheme: dark ? "dark" : "light", width: "100%", locale: "cs", isTransparent: false, ...cfg});
  sec.lastChild.appendChild(sc);
  document.getElementById("tv").appendChild(sec);
});
</script><p class="empty">Rámečky výše poskytuje TradingView. Pokud se nenačtou, zkontroluj, jestli je neblokuje doplněk prohlížeče.</p>"""


def gauge(avg):
    ang = max(-1.0, min(1.0, (avg or 0) / 1.5)) * 80
    return ('<svg class="gauge" viewBox="0 0 200 110" role="img" aria-label="Ukazatel nálady trhu"><defs><linearGradient id="gg">'
            '<stop offset="0" stop-color="#FF5C7A"/><stop offset=".5" stop-color="#8A94AD"/><stop offset="1" stop-color="#19D3A2"/></linearGradient></defs>'
            '<path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="url(#gg)" stroke-width="14" stroke-linecap="round"/>'
            f'<g transform="rotate({ang:.1f} 100 100)"><line x1="100" y1="100" x2="100" y2="34" stroke="#E8ECF6" stroke-width="3" stroke-linecap="round"/></g>'
            '<circle cx="100" cy="100" r="6" fill="#E8ECF6"/></svg>')


def nq_series():
    try:
        d = yf.download("NQ=F", period="5d", interval="1h", progress=False)["Close"].squeeze().dropna()
        return {"l": [t.tz_convert("Europe/Prague").strftime("%-d.%-m. %H:%M") for t in d.index],
                "v": [round(float(x), 2) for x in d]}
    except Exception as e:
        print("Graf futures se nepodařilo stáhnout:", e)
        return None


CHARTS_JS = """
Chart.defaults.color = "#8A94AD"; Chart.defaults.borderColor = "rgba(255,255,255,.07)"; Chart.defaults.font.family = '"IBM Plex Sans",system-ui,sans-serif';
if (window.Chart && DATA.nq) new Chart(document.getElementById("c-nq"), {type: "line",
  data: {labels: DATA.nq.l, datasets: [{data: DATA.nq.v, borderColor: "#7C6CFF", borderWidth: 2, tension: .25, fill: true, pointRadius: 0, pointHoverRadius: 5,
    backgroundColor: x => { const g = x.chart.ctx.createLinearGradient(0, 0, 0, x.chart.height); g.addColorStop(0, "rgba(124,108,255,.35)"); g.addColorStop(1, "rgba(124,108,255,0)"); return g; }}]},
  options: {maintainAspectRatio: false, interaction: {mode: "index", intersect: false}, plugins: {legend: {display: false}},
    scales: {x: {ticks: {maxTicksLimit: 6}, grid: {display: false}}, y: {ticks: {callback: v => v.toLocaleString("cs-CZ")}}}}});
if (window.Chart && DATA.pre && DATA.pre.length) new Chart(document.getElementById("c-pre"), {type: "bar",
  data: {labels: DATA.pre.map(r => r.s), datasets: [{data: DATA.pre.map(r => r.c), backgroundColor: DATA.pre.map(r => r.c >= 0 ? "#19D3A2" : "#FF5C7A"), borderRadius: 4}]},
  options: {indexAxis: "y", maintainAspectRatio: false, plugins: {legend: {display: false},
    tooltip: {callbacks: {title: i => DATA.pre[i[0].dataIndex].n, label: c => (c.parsed.x >= 0 ? "+" : "") + c.parsed.x.toFixed(2) + " %"}}},
    scales: {x: {ticks: {callback: v => v + " %"}}, y: {grid: {display: false}}}}});
"""


CSS = """
:root{--bg:#EEF2F5;--ink:#14202B;--muted:#5B6B79;--rule:#D3DCE3;--up:#0B7A6B;--down:#B3303C;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0F1A22;--ink:#E6EDF2;--muted:#93A4B2;--rule:#243442;--up:#3CC4AE;--down:#F0707B;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 "IBM Plex Sans",system-ui,sans-serif}
main{max-width:880px;margin:0 auto;padding:32px 20px 64px}
nav{display:flex;gap:20px;margin-bottom:40px}nav a{color:var(--muted);text-decoration:none;padding:4px 0}
nav a[aria-current]{color:var(--ink);border-bottom:2px solid var(--ink)}
.lede{font:400 clamp(30px,5.5vw,48px)/1.15 "Newsreader",Georgia,serif;margin:0 0 12px}
.sub{color:var(--muted);margin:0 0 48px;max-width:56ch}
h2{font-size:18px;font-weight:600;margin:0 0 12px}
section{margin-bottom:44px}
.wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:11px 8px;border-bottom:1px solid var(--rule);text-align:left;font-weight:400;white-space:nowrap}
thead th{color:var(--muted);font-size:14px}tbody th{font-weight:500}
.n{text-align:right}.chg{font-weight:600}.up{color:var(--up)}.down{color:var(--down)}
.empty{color:var(--muted)}
footer{color:var(--muted);font-size:14px;max-width:62ch}footer a,section a{color:inherit}
a:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
"""
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?'
         'family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">')


def quote_table(title, rows, empty):
    if not rows:
        return f'<section><h2>{title}</h2><p class="empty">{empty}</p></section>'
    body = "".join(
        f'<tr><th scope="row">{escape(r["name"])}</th><td class="n">{num(r["last"])}</td>'
        f'<td class="n chg {tone(r["chg"])}">{pc(r["chg"])}</td></tr>' for r in rows)
    return (f'<section><h2>{title}</h2><div class="wrap"><table><thead><tr><th scope="col">Název</th>'
            f'<th class="n" scope="col">Cena</th><th class="n" scope="col">Změna</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div></section>')


def cal_section(cal):
    t = "Dnešní zprávy z USA (Forex Factory)"
    if cal is None:
        return f'<section><h2>{t}</h2><p class="empty">Kalendář se dnes nepodařilo stáhnout. Otevři <a href="https://www.forexfactory.com/calendar">Forex Factory</a>.</p></section>'
    if not cal:
        return f'<section><h2>{t}</h2><p class="empty">Dnes nejsou žádné důležité zprávy pro USD.</p></section>'
    body = "".join(
        f'<tr><td>{e["time"]}</td><th scope="row">{escape(e["title"])}</th><td>{e["impact"]}</td>'
        f'<td class="n">{escape(e["forecast"])}</td><td class="n">{escape(e["previous"])}</td></tr>' for e in cal)
    return (f'<section><h2>{t}</h2><div class="wrap"><table><thead><tr><th scope="col">Čas</th><th scope="col">Událost</th>'
            f'<th scope="col">Dopad</th><th class="n" scope="col">Předpověď</th><th class="n" scope="col">Minule</th>'
            f'</tr></thead><tbody>{body}</tbody></table></div></section>')


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    import shutil
    (ROOT / "docs").mkdir(exist_ok=True)
    for f in ("journal.html", "style.css"):   # pokud jsou v hlavní složce, zkopíruje je do docs
        if (ROOT / f).exists():
            shutil.copy(ROOT / f, ROOT / "docs" / f)
    q, pre, cal = quotes(cfg), premarket(cfg), calendar()
    label, avg = bias(q)
    vix = next((r for r in q if r["sym"] == "^VIX"), None)
    sub = []
    if avg is not None:
        sub.append(f"Futures na Nasdaq a S&P 500 jsou v průměru {pc(avg)}.")
    if vix:
        sub.append(f"VIX je na {num(vix['last'])} ({pc(vix['chg'])}), " +
                   ("strach na trhu roste." if vix["chg"] > 5 else "strach na trhu klesá." if vix["chg"] < -5 else "nálada je bez velkých změn."))
    if pre:
        up = sum(1 for r in pre if r["chg"] >= 0)
        sub.append(f"V pre-marketu je v plusu {up} z {len(pre)} největších akcií.")
        sub.append(scenario(label, up / len(pre)))
    sea = seasonality()
    if sea:
        sub.append(sea)
    if cal:
        sub.append(f"Důležité zprávy z USA dnes: {len(cal)}.")
    x = " ".join(f'<a href="https://x.com/{escape(h)}">@{escape(h)}</a>' for h in cfg["x_accounts"])
    nq = nq_series()
    low = label.lower()
    lede_cls = "up" if "bullish" in low else "down" if "bearish" in low else ""
    facts = "".join(f"<li>{escape(t)}</li>" for t in sub)
    data = {"nq": nq, "pre": [{"s": r["sym"], "n": r["name"], "c": round(r["chg"], 2)} for r in pre]}
    charts_js = f"<script>const DATA={json.dumps(data, ensure_ascii=False)};</script><script>{CHARTS_JS}</script>"
    cards = "".join(
        f'<a class="twitter-timeline" data-theme="dark" data-height="420" data-chrome="noheader nofooter transparent" href="https://twitter.com/{escape(h)}">@{escape(h)}</a>'
        for h in cfg["x_accounts"])
    xsec = ('<section><h2>Příspěvky z X</h2><div class="xgrid">' + cards + '</div>'
            '<p class="empty">Pokud se příspěvky nezobrazí, X je někdy nepustí bez přihlášení. Odkaz na účet vždy funguje.</p>'
            '<script async src="https://platform.twitter.com/widgets.js"></script></section>')
    now = datetime.now(PRAGUE).strftime("%-d. %-m. %Y v %H:%M")
    html = f"""<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ranní briefing</title>{FONTS}<link rel="stylesheet" href="style.css"><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script></head>
<body><main>
<nav><a href="index.html" aria-current="page">Ranní briefing</a><a href="journal.html">Deník obchodů</a></nav>
<section class="hero"><div><p class="lede {lede_cls}">{escape(label)}</p><ul class="facts">{facts}</ul></div>{gauge(avg)}</section>
{'<section><h2>Nasdaq futures za posledních 5 dní (najeď myší na graf)</h2><div class="chartbox"><canvas id="c-nq"></canvas></div></section>' if nq else ""}
{quote_table("Futures a makro", q, "Data se nepodařilo stáhnout.")}
{'<section><h2>Největší akcie v pre-marketu</h2><div class="chartbox tall"><canvas id="c-pre"></canvas></div></section>' if pre else ""}
{quote_table("Akcie v pre-marketu (tabulka)", pre, "Pre-market data zatím nejsou k dispozici (o víkendu a ve svátek se neobchoduje).")}
{cal_section(cal)}
{WIDGETS}
{xsec}
<footer><p>Vygenerováno {now}. Data: Yahoo Finance a Forex Factory, mohou mít zpoždění. Nálada je odvozená jen ze změny futures, není to obchodní signál ani investiční doporučení.</p></footer>
</main>{charts_js}</body></html>"""
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(html, encoding="utf-8")
    print("Hotovo:", label)


if __name__ == "__main__":
    main()
