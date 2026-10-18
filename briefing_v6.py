#!/usr/bin/env python3
"""Ranní briefing (verze 6) pro obchodování Nasdaqu: pre-market, nálada trhu, kalendář z Forex Factory."""
import email.utils
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
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
    rows, live = [], False
    for s, name in cfg["stocks"].items():
        try:
            info = yf.Ticker(s).info
            pre = info.get("preMarketPrice")
            prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
            if pre and prev:
                rows.append({"sym": s, "name": name, "last": float(pre), "chg": (pre / prev - 1) * 100})
                live = True
            elif info.get("regularMarketPrice") and info.get("regularMarketChangePercent") is not None:
                rows.append({"sym": s, "name": name, "last": float(info["regularMarketPrice"]),
                             "chg": float(info["regularMarketChangePercent"])})
        except Exception as e:
            print("Bez dat:", s, e)
    pr = {t: i for i, t in enumerate(cfg.get("priority", []))}
    return sorted(rows, key=lambda r: (pr.get(r["sym"], 999), -r["chg"])), live


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
    """Vlastní jednoduchá sezónnost: jak se Nasdaq 100 choval v jednotlivých měsících."""
    try:
        s = yf.download("^NDX", period="15y", interval="1d", progress=False)["Close"].squeeze().dropna()
        m = s.groupby([s.index.year, s.index.month]).last().pct_change().dropna()
        now = datetime.now(PRAGUE)
        per = {i: [] for i in range(1, 13)}
        for (y, mo), v in m.items():
            if (y, mo) != (now.year, now.month):
                per[mo].append(v * 100)
        x = per[now.month]
        if len(x) < 5:
            return None, None
        text = (f"Sezónnost: v {MESICE[now.month - 1]} Nasdaq 100 za posledních {len(x)} let průměrně "
                f"{pc(sum(x) / len(x))} a rostl v {num(sum(1 for v in x if v > 0) / len(x) * 100, 0)}{NB}% let.")
        avgs = [round(sum(per[i]) / len(per[i]), 2) if per[i] else 0 for i in range(1, 13)]
        return text, {"m": avgs, "cur": now.month - 1}
    except Exception as e:
        print("Sezónnost se nepodařilo spočítat:", e)
        return None, None


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


def _get(url, headers, body=None):
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


NAV = [("index.html", "🏠", "Přehled", "Ranní souhrn trhu v kostce"),
       ("premarket.html", "🌅", "Premarket", "Největší akcie před otevřením burzy"),
       ("fundament.html", "📰", "Fundament", "Čísla z trhu, mapa akcií a zprávy"),
       ("kalendar.html", "📅", "Kalendář", "Důležité zprávy z USA na celý týden"),
       ("sentiment.html", "💬", "Sentiment", "Nálada ve zprávách za posledních 24 hodin"),
       ("plan.html", "🎯", "Plán dne", "Checklist, limity a kalkulačka rizika"),
       ("journal.html", "📓", "Deník obchodů", "Tvoje obchody, statistiky a poučení")]


def sidebar(active):
    out = ""
    for h, i, t, _ in NAV:
        cur = ' aria-current="page"' if h == active else ""
        out += f'<a href="{h}"{cur}><span>{i}</span>{t}</a>'
    return f'<aside class="side"><div class="brand">Obchodní cockpit</div><nav>{out}</nav></aside>'


def shell(active, body, data):
    title, subtitle = next((t, s) for h, i, t, s in NAV if h == active)
    now = datetime.now(PRAGUE).strftime("%-d. %-m. %Y v %H:%M")
    dj = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>{FONTS}<link rel="stylesheet" href="style.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script></head>
<body><div class="app">{sidebar(active)}<div class="content"><header class="pagehead"><h1>{title}</h1><p>{subtitle}</p></header>
<main>{body}<footer><p>Vygenerováno {now} (verze 6). Data: Yahoo Finance, Forex Factory a TradingView, mohou mít zpoždění. Nejde o obchodní signál ani investiční doporučení.</p></footer></main></div></div>
<script>const DATA={dj};</script><script>{CHARTS_JS}</script></body></html>"""


CHARTS_JS = """
const $ = id => document.getElementById(id);
Chart.defaults.color = "#8A94AD"; Chart.defaults.borderColor = "rgba(255,255,255,.07)"; Chart.defaults.font.family = '"IBM Plex Sans",system-ui,sans-serif';
const grad = x => { const g = x.chart.ctx.createLinearGradient(0, 0, 0, x.chart.height); g.addColorStop(0, "rgba(124,108,255,.35)"); g.addColorStop(1, "rgba(124,108,255,0)"); return g; };
const stagger = {delay: c => c.dataIndex * 45};
if (window.Chart && DATA.nq && $("c-nq")) {
  const full = DATA.nq;
  const nq = new Chart($("c-nq"), {type: "line", data: {labels: full.l.slice(-24), datasets: [{data: full.v.slice(-24), borderColor: "#7C6CFF", borderWidth: 2.5, tension: .3, fill: true,
    pointRadius: 0, pointHoverRadius: 7, pointHoverBackgroundColor: "#fff", backgroundColor: grad,
    segment: {borderColor: c => c.p1.parsed.y >= c.p0.parsed.y ? "#19D3A2" : "#FF5C7A"}}]},
    options: {maintainAspectRatio: false, interaction: {mode: "index", intersect: false}, plugins: {legend: {display: false}},
      scales: {x: {ticks: {maxTicksLimit: 6}, grid: {display: false}}, y: {ticks: {callback: v => v.toLocaleString("cs-CZ")}}}}});
  document.querySelectorAll("[data-r]").forEach(b => b.onclick = () => { const n = +b.dataset.r;
    nq.data.labels = n ? full.l.slice(-n) : full.l; nq.data.datasets[0].data = n ? full.v.slice(-n) : full.v; nq.update();
    document.querySelectorAll("[data-r]").forEach(x => x.classList.toggle("pill-off", x !== b)); });
}
if (window.Chart && DATA.sea && $("c-sea")) new Chart($("c-sea"), {type: "bar",
  data: {labels: ["Led","Úno","Bře","Dub","Kvě","Čvn","Čvc","Srp","Zář","Říj","Lis","Pro"], datasets: [{data: DATA.sea.m,
    backgroundColor: DATA.sea.m.map((v, i) => (v >= 0 ? "#19D3A2" : "#FF5C7A") + (i === DATA.sea.cur ? "" : "99")),
    borderColor: DATA.sea.m.map((v, i) => i === DATA.sea.cur ? "#FFFFFF" : "transparent"), borderWidth: 2, borderRadius: 8, hoverBackgroundColor: "#7C6CFF"}]},
  options: {maintainAspectRatio: false, animation: stagger, plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => "Průměr " + (c.parsed.y >= 0 ? "+" : "") + c.parsed.y.toFixed(2) + " %"}}},
    scales: {y: {ticks: {callback: v => v + " %"}}, x: {grid: {display: false}}}}});
if (window.Chart && DATA.pre && DATA.pre.length && $("c-pre")) new Chart($("c-pre"), {type: "bar",
  data: {labels: DATA.pre.map(r => r.s), datasets: [{data: DATA.pre.map(r => r.c), backgroundColor: DATA.pre.map(r => r.c >= 0 ? "#19D3A2" : "#FF5C7A"), borderRadius: 6, hoverBackgroundColor: "#7C6CFF"}]},
  options: {indexAxis: "y", maintainAspectRatio: false, animation: stagger, plugins: {legend: {display: false},
    tooltip: {callbacks: {title: i => DATA.pre[i[0].dataIndex].n, label: c => (c.parsed.x >= 0 ? "+" : "") + c.parsed.x.toFixed(2) + " %"}}},
    scales: {x: {ticks: {callback: v => v + " %"}}, y: {grid: {display: false}}}}});
if (window.Chart && DATA.pre && DATA.pre.length && $("c-br")) { const up = DATA.pre.filter(r => r.c >= 0).length;
  new Chart($("c-br"), {type: "doughnut", data: {labels: ["Rostou", "Klesají"], datasets: [{data: [up, DATA.pre.length - up], backgroundColor: ["#19D3A2", "#FF5C7A"], borderWidth: 0, hoverOffset: 14}]},
    options: {maintainAspectRatio: false, cutout: "68%", plugins: {legend: {position: "bottom"}}}}); }
if (window.Chart && DATA.sent && $("c-s")) new Chart($("c-s"), {type: "bar",
  data: {labels: DATA.sent.groups.map(g => g.name), datasets: [{data: DATA.sent.groups.map(g => g.score), borderRadius: 8, hoverBackgroundColor: "#7C6CFF",
    backgroundColor: DATA.sent.groups.map(g => g.score > 0 ? "#19D3A2" : g.score < 0 ? "#FF5C7A" : "#8A94AD")}]},
  options: {maintainAspectRatio: false, animation: stagger, plugins: {legend: {display: false}, tooltip: {callbacks: {
    label: c => "Skóre " + c.parsed.y, afterLabel: c => DATA.sent.groups[c.dataIndex].bull + " pozitivních, " + DATA.sent.groups[c.dataIndex].bear + " negativních z " + DATA.sent.groups[c.dataIndex].n}}},
    scales: {y: {min: -5, max: 5}, x: {grid: {display: false}, ticks: {maxRotation: 40}}}}});
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
if ($("cal-t") && DATA.cal) {
  let imp = "", day = ""; const dsel = $("cal-day");
  const days = [...new Set(DATA.cal.map(e => e.day))];
  dsel.innerHTML = '<option value="">Celý týden</option>' + days.map(d => `<option value="${d}">${DATA.cal.find(e => e.day === d).dn}</option>`).join("");
  const draw = () => { const rows = DATA.cal.filter(e => (!imp || e.impact === imp) && (!day || e.day === day));
    $("cal-t").innerHTML = rows.length ? "<div class='wrap'><table><thead><tr><th>Den</th><th>Čas</th><th>Událost</th><th>Dopad</th><th class='n'>Předpověď</th><th class='n'>Minule</th></tr></thead><tbody>" +
      rows.map(e => `<tr${e.ts < Date.now() ? ' style="opacity:.45"' : ""}><td>${e.dn}</td><td>${e.time}</td><th scope="row">${esc(e.title)}</th><td><span class="badge ${e.impact === "vysoký" ? "down" : "flat"}">${e.impact}</span></td><td class="n">${esc(e.forecast)}</td><td class="n">${esc(e.previous)}</td></tr>`).join("") + "</tbody></table></div>"
      : '<p class="empty">Žádné zprávy podle filtru.</p>'; };
  document.querySelectorAll("[data-i]").forEach(b => b.onclick = () => { imp = b.dataset.i; document.querySelectorAll("[data-i]").forEach(x => x.classList.toggle("pill-off", x !== b)); draw(); });
  dsel.onchange = () => { day = dsel.value; draw(); };
  const tick = () => { const n = DATA.cal.find(e => e.ts > Date.now() && e.impact === "vysoký") || DATA.cal.find(e => e.ts > Date.now());
    if (!n) { $("cd").textContent = "–"; $("cdt").textContent = "Tento týden už žádná další zpráva."; return; }
    let s = Math.floor((n.ts - Date.now()) / 1000); const d = Math.floor(s / 86400); s %= 86400;
    $("cd").textContent = (d ? d + " d " : "") + [Math.floor(s / 3600), Math.floor(s % 3600 / 60), s % 60].map(x => String(x).padStart(2, "0")).join(":");
    $("cdt").textContent = "Do zprávy: " + n.dn + " " + n.time + ", " + n.title; };
  tick(); setInterval(tick, 1000); draw();
}
"""


def tile(r):
    mine = " mine" if r["sym"] in CONFIG.get("priority", []) else ""
    a = min(0.18 + abs(r["chg"]) / 5 * 0.55, 0.75)
    rgb = "25,211,162" if r["chg"] >= 0 else "255,92,122"
    return (f'<div class="tile{mine}" style="background:rgba({rgb},{a:.2f})"><b>{escape(r["sym"])}</b>'
            f'<span>{escape(r["name"])}</span><em>{pc(r["chg"])}</em></div>')


def badge(b):
    b = str(b or "neutral").lower()
    cls = "up" if b == "bullish" else "down" if b == "bearish" else "flat"
    text = "Bullish" if b == "bullish" else "Bearish" if b == "bearish" else "Neutrální"
    return f'<span class="badge {cls}">{text}</span>'


def sgn(v):
    try:
        return f"{float(v):+.1f}".replace("-", "\u2212")
    except (TypeError, ValueError):
        return "0"


DNY = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
_FF = []


def ff_items():
    if not _FF:
        try:
            req = urllib.request.Request(FF_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                _FF.append(json.load(r))
        except Exception as e:
            print("Kalendář se nepodařilo stáhnout:", e)
            _FF.append(None)
    return _FF[0]


def _events(week):
    items = ff_items()
    if items is None:
        return None
    today = datetime.now(PRAGUE).date()
    out = []
    for e in items:
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        t = datetime.fromisoformat(e["date"]).astimezone(PRAGUE)
        if not week and t.date() != today:
            continue
        out.append({"time": t.strftime("%H:%M"), "title": e.get("title", ""), "impact": "vysoký" if e["impact"] == "High" else "střední",
                    "forecast": e.get("forecast") or "–", "previous": e.get("previous") or "–", "ts": int(t.timestamp() * 1000),
                    "day": t.strftime("%Y-%m-%d"), "dn": DNY[t.weekday()] + t.strftime(" %-d. %-m.")})
    return sorted(out, key=lambda x: x["ts"])


def calendar():
    return _events(False)


def week_events():
    return _events(True)


BULL = ["rally", "rallies", "surge", "surges", "soar", "soars", "jump", "jumps", "climb", "climbs", "gain", "gains", "rise", "rises", "rebound",
        "rebounds", "record high", "beat", "beats", "upgrade", "upgraded", "optimism", "optimistic", "rate cut", "rate cuts", "strong", "boost",
        "boosts", "bullish", "recovery", "breakthrough", "approval", "tops", "higher"]
BEAR = ["plunge", "plunges", "tumble", "tumbles", "slump", "slumps", "fall", "falls", "drop", "drops", "sink", "sinks", "selloff", "sell-off", "crash",
        "slide", "slides", "fear", "fears", "worry", "worries", "recession", "miss", "misses", "downgrade", "downgraded", "rate hike", "rate hikes",
        "inflation", "tariff", "tariffs", "war", "bearish", "warning", "warns", "concern", "concerns", "layoffs", "lawsuit", "probe", "default",
        "crisis", "slowdown", "weak", "lower"]


def head_score(t):
    low = t.lower()
    b = sum(1 for w in BULL if re.search(r"\b" + re.escape(w) + r"\b", low))
    r = sum(1 for w in BEAR if re.search(r"\b" + re.escape(w) + r"\b", low))
    return max(-1, min(1, b - r))


def feed_items(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):
        d = it.findtext("pubDate")
        try:
            dt = email.utils.parsedate_to_datetime(d) if d else None
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        out.append(((it.findtext("title") or "").strip(), (it.findtext("link") or "").strip(), dt))
    return out


def news_sentiment(cfg):
    """Zdarma: stáhne titulky z veřejných RSS kanálů a spočítá, kolik je pozitivních a negativních slov."""
    since = (datetime.now(PRAGUE) - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    srcs = dict(cfg["feeds"])
    for name, q in cfg["people"].items():
        srcs[name + " (zprávy o něm)"] = "https://news.google.com/rss/search?q=" + quote(q + " when:1d") + "&hl=en-US&gl=US&ceid=US:en"
    groups, bulls, bears, seen, allsc = [], [], [], set(), []
    for name, url in srcs.items():
        try:
            items = feed_items(url)
        except Exception as e:
            print("Zdroj zpráv nejde:", name, e)
            continue
        items = [(t, l, d) for t, l, d in items if t and (d is None or d >= since)][:40]
        if not items:
            continue
        sc = []
        for t, l, d in items:
            s = head_score(t)
            sc.append(s)
            if t not in seen:
                seen.add(t)
                allsc.append(s)
                row = {"t": t, "l": l if l.startswith("http") else "#", "src": name}
                if s > 0:
                    bulls.append(row)
                elif s < 0:
                    bears.append(row)
        groups.append({"name": name, "n": len(items), "bull": sum(1 for s in sc if s > 0), "bear": sum(1 for s in sc if s < 0),
                       "score": round(max(-5, min(5, sum(sc) / len(sc) * 10)), 1)})
    if not allsc:
        return {"status": "error"}
    ov = max(-5, min(5, sum(allsc) / len(allsc) * 10))
    return {"status": "ok", "n": len(allsc), "groups": groups, "bulls": bulls[:8], "bears": bears[:8],
            "overall": {"score": round(ov, 1), "bias": "bullish" if ov > 0.8 else "bearish" if ov < -0.8 else "neutral"}}


def bias_engine(q, pre, nq, sent, sea_data):
    comp, g = [], {r["sym"]: r["chg"] for r in q}

    def add(name, score, weight, text):
        comp.append({"n": name, "s": round(max(-2, min(2, score)), 1), "w": weight, "t": text})
    f = [g[k] for k in ("NQ=F", "ES=F") if k in g]
    if f:
        a = sum(f) / len(f)
        add("Futures", a / 0.4, 3, f"Futures na Nasdaq a S&P 500 jsou v průměru {pc(a)}.")
    if pre:
        n_up = sum(1 for r in pre if r["chg"] >= 0)
        add("Šířka akcií", (n_up / len(pre) - 0.5) * 4, 2, f"{n_up} z {len(pre)} největších akcií je v plusu.")
    if "^VIX" in g:
        add("Strach (VIX)", -g["^VIX"] / 5, 1.5, f"VIX se změnil o {pc(g['^VIX'])}. Růst strachu bývá pro akcie špatný.")
    if "DX-Y.NYB" in g:
        add("Dolar", -g["DX-Y.NYB"] / 0.3, 0.5, f"Dolarový index se změnil o {pc(g['DX-Y.NYB'])}. Silný dolar akciím spíš škodí.")
    if "^TNX" in g:
        add("Výnosy dluhopisů", -g["^TNX"] / 2, 1, f"Výnos 10letých dluhopisů se změnil o {pc(g['^TNX'])}. Růst výnosů tlačí akcie dolů.")
    if nq and len(nq["v"]) >= 12:
        v = nq["v"][-24:]
        d = (v[-1] / (sum(v) / len(v)) - 1) * 100
        add("Trend futures", d / 0.5, 1, f"Cena je o {pc(abs(d))} {'nad' if d >= 0 else 'pod'} průměrem posledního dne.")
    if sent and sent.get("status") == "ok":
        sc = sent["overall"]["score"]
        add("Zprávy", sc / 2.5, 1, f"Nálada v titulcích zpráv za 24 hodin: skóre {sgn(sc)} z 5.")
    if sea_data:
        a = sea_data["m"][sea_data["cur"]]
        add("Sezónnost", a / 1.5, 0.5, f"V tomto měsíci Nasdaq 100 historicky průměrně {pc(a)}.")
    if not comp:
        return "Bez dat", 0.0, comp
    tot = sum(c["s"] * c["w"] for c in comp) / sum(2 * c["w"] for c in comp) * 10
    label = "Bullish" if tot >= 5 else "Mírně bullish" if tot >= 2 else "Neutrální" if tot > -2 else "Mírně bearish" if tot > -5 else "Bearish"
    return label, tot, comp


def comp_html(comp):
    rows = ""
    for c in comp:
        cls = "up" if c["s"] >= 0 else "down"
        rows += (f'<div class="bar wide" title="{escape(c["t"])}"><span>{escape(c["n"])}</span><div class="track {cls}">'
                 f'<i style="width:{abs(c["s"]) / 2 * 100:.0f}%"></i></div><b class="{cls}">{sgn(c["s"])} <em>{escape(c["t"])}</em></b></div>')
    return f'<section><h2>Z čeho se skóre skládá (od −2 do +2)</h2>{rows}</section>' if rows else ""


def sentiment_body(s, cfg):
    links = " ".join(f'<a href="https://x.com/{escape(h)}">@{escape(h)}</a>' for h in cfg["x_accounts"])
    foot = (f'<section><h2>Účty na X</h2><p>{links}</p><p class="empty">Příspěvky z X se zdarma číst nedají, proto jsou tady jen odkazy. '
            'Nálada výše vychází z veřejných zpravodajských kanálů a z titulků o vybraných lidech. '
            'Je to jednoduché počítání pozitivních a negativních slov, ne umělá inteligence, takže ber ho jako hrubý ukazatel.</p></section>')
    if s.get("status") != "ok":
        return '<section><div class="note">Zprávy se dnes nepodařilo stáhnout. Zkus robota spustit znovu za chvíli.</div></section>' + foot
    o = s["overall"]
    sc = float(o["score"])
    word = {"bullish": "Bullish", "bearish": "Bearish"}.get(o["bias"], "Neutrální")
    cls = "up" if o["bias"] == "bullish" else "down" if o["bias"] == "bearish" else ""

    def lst(rows):
        return "".join(f'<li><a href="{escape(r["l"])}">{escape(r["t"])}</a> <span class="empty">({escape(r["src"])})</span></li>' for r in rows) or "<li>Nic.</li>"
    return (f'<section class="hero"><div><p class="lede {cls}">{word}</p><ul class="facts"><li>Skóre {sgn(sc)} z 5 (od −5 do +5).</li>'
            f'<li>Zpracováno {s["n"]} titulků od včerejších 15:00.</li></ul></div>{gauge(sc * 0.3)}</section>'
            '<section><h2>Nálada podle zdroje (najeď myší)</h2><div class="chartbox"><canvas id="c-s"></canvas></div></section>'
            f'<section><div class="xcards"><div><h2>Nejpozitivnější titulky</h2><ul class="facts">{lst(s["bulls"])}</ul></div>'
            f'<div><h2>Nejnegativnější titulky</h2><ul class="facts">{lst(s["bears"])}</ul></div></div></section>' + foot)


def main():
    cfg = CONFIG
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    sb = lambda f: sidebar(f)
    (docs / "journal.html").write_text(JOURNAL_HTML.replace("@@SIDEBAR@@", sb("journal.html")), encoding="utf-8")
    (docs / "plan.html").write_text(PLAN_HTML.replace("@@SIDEBAR@@", sb("plan.html")), encoding="utf-8")

    q, (pre, live), cal, week = quotes(cfg), premarket(cfg), calendar(), week_events()
    ptitle = "Největší akcie v pre-marketu" if live else "Největší akcie: změna za poslední obchodní den"
    sent = news_sentiment(cfg)
    nq = nq_series()
    sea, sea_data = seasonality()
    label, total, comp = bias_engine(q, pre, nq, sent, sea_data)
    vix = next((r for r in q if r["sym"] == "^VIX"), None)
    facts_l = [f"Skóre trhu: {sgn(total)} z 10 (od −10 do +10)."] if comp else []
    if vix:
        facts_l.append(f"VIX je na {num(vix['last'])} ({pc(vix['chg'])}), " +
                       ("strach na trhu roste." if vix["chg"] > 5 else "strach na trhu klesá." if vix["chg"] < -5 else "nálada je bez velkých změn."))
    up = 0
    if pre:
        up = sum(1 for r in pre if r["chg"] >= 0)
        facts_l.append(f"V pre-marketu je v plusu {up} z {len(pre)} největších akcií." if live
                       else f"Za poslední obchodní den skončilo v plusu {up} z {len(pre)} největších akcií.")
        facts_l.append(scenario(label, up / len(pre)))
    if sea:
        facts_l.append(sea)
    if cal:
        facts_l.append(f"Důležité zprávy z USA dnes: {len(cal)}.")
    data = {"nq": nq, "sea": sea_data, "cal": week, "sent": sent if sent.get("status") == "ok" else None,
            "pre": [{"s": r["sym"], "n": r["name"], "c": round(r["chg"], 2)} for r in pre]}

    low = label.lower()
    lede_cls = "up" if "bullish" in low else "down" if "bearish" in low else ""
    facts = "".join(f"<li>{escape(t)}</li>" for t in facts_l)
    hm = datetime.now(PRAGUE).strftime("%H:%M")
    nxt = next((e for e in (cal or []) if e["time"] >= hm), None)
    smood = {"bullish": "Bullish", "bearish": "Bearish", "neutral": "Neutrální"}.get(sent.get("overall", {}).get("bias"), "–")
    kp = ('<div class="kpis">'
          f'<div class="k"><b>{escape(nxt["time"]) if nxt else "–"}</b><span>{escape(nxt["title"]) if nxt else "Žádná další důležitá zpráva dnes"}</span></div>'
          f'<div class="k"><b>{f"{up} z {len(pre)}" if pre else "–"}</b><span>největších akcií v plusu</span></div>'
          f'<div class="k"><b>{smood}</b><span>nálada ve zprávách (viz Sentiment)</span></div></div>')
    dash = (f'<section class="hero"><div><p class="lede {lede_cls}">{escape(label)}</p><ul class="facts">{facts}</ul></div>{gauge(total * 0.15)}</section>{kp}'
            + comp_html(comp)
            + ('<section><h2>Nasdaq futures</h2><div class="pills"><button data-r="24">Poslední den</button>'
               '<button class="pill-off" data-r="0">5 dní</button></div><div class="chartbox"><canvas id="c-nq"></canvas></div></section>' if nq else ""))

    pm = ""
    if pre:
        pm += (f'<section><h2>{ptitle}</h2><div class="tiles">{"".join(tile(r) for r in pre)}</div></section>'
               '<section><div class="split"><div><h2>Kolik akcií roste</h2><div class="chartbox" style="height:240px"><canvas id="c-br"></canvas></div></div>'
               '<div><h2>Změna podle akcie (najeď myší)</h2><div class="chartbox tall"><canvas id="c-pre"></canvas></div></div></div></section>')
    else:
        pm += '<section><p class="empty">Data o akciích se nepodařilo stáhnout.</p></section>'
    if sea_data:
        pm += '<section><h2>Sezónnost Nasdaq 100: průměr podle měsíců</h2><div class="chartbox"><canvas id="c-sea"></canvas></div></section>'
    pm += quote_table(ptitle + " (tabulka)", pre, "Data nejsou k dispozici.")

    fd = quote_table("Futures a makro", q, "Data se nepodařilo stáhnout.") + cal_section(cal) + WIDGETS
    kal = ('<section class="hero"><div><p class="lede" id="cd">–</p><ul class="facts"><li id="cdt">Načítám…</li></ul></div></section>'
           '<section><div class="pills"><button data-i="">Všechny</button><button class="pill-off" data-i="vysoký">Vysoký dopad</button>'
           '<button class="pill-off" data-i="střední">Střední dopad</button></div><label style="max-width:240px">Den<select id="cal-day"></select></label>'
           '<div id="cal-t" style="margin-top:12px"></div></section>') if week else \
          '<section><div class="note">Kalendář se nepodařilo stáhnout. Otevři si <a href="https://www.forexfactory.com/calendar">Forex Factory</a>.</div></section>'

    pages = {"index.html": dash, "premarket.html": pm, "fundament.html": fd, "kalendar.html": kal, "sentiment.html": sentiment_body(sent, cfg)}
    for f, body in pages.items():
        (docs / f).write_text(shell(f, body, data), encoding="utf-8")
    print("Hotovo:", label, f"{total:+.1f}", "| zprávy:", sent.get("status"))


# ===== NASTAVENÍ: sem můžeš přidat akcie, zdroje zpráv nebo účty z X =====
CONFIG = {
    "futures": {
        "NQ=F": "Nasdaq 100 futures",
        "ES=F": "S&P 500 futures"
    },
    "macro": {
        "^VIX": "VIX (strach na trhu)",
        "DX-Y.NYB": "Dolarový index",
        "^TNX": "Výnos 10letých dluhopisů USA"
    },
    "stocks": {
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "NVDA": "Nvidia",
        "AMZN": "Amazon",
        "GOOG": "Alphabet",
        "PLTR": "Palantir",
        "BABA": "Alibaba",
        "ORCL": "Oracle",
        "META": "Meta",
        "TSLA": "Tesla",
        "AVGO": "Broadcom",
        "COST": "Costco",
        "NFLX": "Netflix",
        "AMD": "AMD",
        "ADBE": "Adobe",
        "PEP": "PepsiCo",
        "CSCO": "Cisco",
        "TMUS": "T-Mobile",
        "QCOM": "Qualcomm",
        "INTU": "Intuit",
        "AMGN": "Amgen",
        "TXN": "Texas Instruments",
        "ISRG": "Intuitive Surgical"
    },
    "priority": ["NVDA", "TSLA", "AAPL", "META", "PLTR", "AMZN", "GOOG", "BABA", "MSFT", "ORCL"],
    "x_accounts": [
        "elonmusk",
        "unusual_whales",
        "business",
        "CNBC",
        "realDonaldTrump"
    ],
    "feeds": {
        "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
        "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml"
    },
    "people": {
        "Elon Musk": "Elon Musk",
        "Donald Trump": "Donald Trump (tariffs OR Fed OR stocks OR economy)",
        "Unusual Whales": "Unusual Whales"
    }
}

# ===== Stránky (nech být) =====
STYLE_CSS = ':root{--bg:#070B14;--ink:#E8ECF6;--muted:#8A94AD;--rule:rgba(255,255,255,.09);--up:#19D3A2;--down:#FF5C7A;--acc:#7C6CFF;--glass:rgba(255,255,255,.045);--field:rgba(255,255,255,.06);color-scheme:dark}\n*{box-sizing:border-box}\nbody{margin:0;min-height:100vh;color:var(--ink);font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif;background:radial-gradient(1100px 560px at 12% -8%,rgba(124,108,255,.30),transparent 60%),radial-gradient(900px 520px at 95% 4%,rgba(25,211,162,.14),transparent 60%),var(--bg) fixed}\nmain{max-width:1000px;margin:0 auto;padding:28px 18px 72px}\nnav.top{display:flex;gap:8px;margin-bottom:26px}nav.top a{color:var(--muted);text-decoration:none;padding:7px 16px;border-radius:99px;border:1px solid transparent}\nnav.top a[aria-current]{background:var(--glass);color:var(--ink);border-color:var(--rule)}\nsection,.k{background:var(--glass);border:1px solid var(--rule);border-radius:18px;backdrop-filter:blur(10px)}\nsection{padding:20px 22px;margin-bottom:20px}[hidden]{display:none!important}\nh2{font-size:16px;font-weight:600;margin:0 0 14px}h3{font-size:14px;font-weight:600;margin:0 0 8px}\n.hero{display:flex;gap:24px;align-items:center;flex-wrap:wrap;border-color:rgba(124,108,255,.4);box-shadow:0 0 70px -24px rgba(124,108,255,.6)}.hero>div{flex:1 1 320px}\n.lede{font-size:clamp(34px,6vw,56px);font-weight:700;line-height:1.05;letter-spacing:-.02em;margin:0 0 14px}\n.lede.up{text-shadow:0 0 40px rgba(25,211,162,.4)}.lede.down{text-shadow:0 0 40px rgba(255,92,122,.4)}\n.facts{margin:0;padding-left:18px;color:var(--muted)}.facts li{margin:5px 0}.gauge{width:230px;max-width:100%}\n.chartbox{position:relative;height:280px}.chartbox.tall{height:480px}\n.xgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}\n.up{color:var(--up)}.down{color:var(--down)}.empty{color:var(--muted)}.n{text-align:right}.chg{font-weight:600}\n.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}\nth,td{padding:10px 8px;border-bottom:1px solid var(--rule);text-align:left;white-space:nowrap;font-weight:400}\nthead th{color:var(--muted);font-size:13px}tbody th{font-weight:500}tbody tr:last-child>*{border-bottom:0}\nfooter{color:var(--muted);font-size:13px;max-width:64ch}a{color:inherit}\n:focus-visible{outline:2px solid var(--acc);outline-offset:2px}\n.head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:20px}\n.tabs{display:flex;gap:4px;padding:4px;background:var(--glass);border:1px solid var(--rule);border-radius:12px}\n.tabs button{background:transparent;color:var(--muted);box-shadow:none}.tabs button[aria-selected=true]{background:var(--acc);color:#fff}\n.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:20px}\n.k{padding:16px 18px}.k b{display:block;font-size:30px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}.k span{color:var(--muted);font-size:13px}\nsection .kpis{margin-bottom:0}\n.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:5px}.cal .dow{color:var(--muted);font-size:12px;font-weight:400;text-align:center}\n.day{min-height:56px;padding:5px 7px;border:1px solid var(--rule);border-radius:10px;font-size:12px;font-variant-numeric:tabular-nums}\n.day span{display:block;color:var(--muted)}.day.up{background:rgba(25,211,162,.16)}.day.down{background:rgba(255,92,122,.16)}\n.calhead{display:flex;align-items:center;gap:12px;margin-bottom:12px}.calhead b{min-width:130px;text-align:center;font-weight:500}\n.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}\nlabel{display:block;font-size:13px;color:var(--muted)}\ninput,select,textarea,button{font:inherit;color:var(--ink)}\ninput,select,textarea{width:100%;margin-top:4px;padding:9px 11px;background:var(--field);border:1px solid var(--rule);border-radius:10px}\nselect option{background:#0E1424}\nbutton{padding:9px 16px;background:linear-gradient(135deg,#7C6CFF,#5B8CFF);color:#fff;border:0;border-radius:10px;cursor:pointer;font-weight:600}\nbutton.ghost{background:transparent;color:var(--ink);border:1px solid var(--rule)}button.sm{padding:4px 10px;font-size:13px}\n.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.chip{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border:1px solid var(--rule);border-radius:99px;font-size:13px;color:var(--ink);cursor:pointer}\n.chip input{width:auto;margin:0}.chip:has(input:checked){background:var(--acc);border-color:var(--acc);color:#fff}\n.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:28px;margin-top:24px}\n.bar{display:grid;grid-template-columns:70px 1fr auto;gap:10px;align-items:center;font-size:13px;margin:6px 0;font-variant-numeric:tabular-nums}\n.bar span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.track{height:8px;background:var(--glass);border-radius:4px}.track i{display:block;height:100%;border-radius:4px;background:currentColor}\n.bar em{color:var(--muted);font-style:normal;font-weight:400}\ndialog{width:min(660px,94vw);max-height:92vh;overflow:auto;background:#0E1424;color:var(--ink);border:1px solid var(--rule);border-radius:18px;padding:24px}\ndialog::backdrop{background:rgba(0,0,0,.65)}.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}\n#view img{max-width:100%;margin-top:10px;border-radius:10px}#view p{white-space:pre-wrap}\n@media (max-width:560px){.hide{display:none}}\n\nmain{padding-top:20px}\n.app{display:flex;min-height:100vh}\n.side{position:sticky;top:0;align-self:flex-start;height:100vh;width:236px;flex:none;padding:22px 14px;border-right:1px solid var(--rule);background:rgba(7,11,20,.6);backdrop-filter:blur(12px)}\n.brand{font-weight:700;font-size:16px;padding:0 12px 20px;background:linear-gradient(90deg,#fff,#9C8FFF);-webkit-background-clip:text;background-clip:text;color:transparent}\n.side nav{display:flex;flex-direction:column;gap:4px}\n.side a{display:flex;gap:10px;align-items:center;padding:11px 12px;border-radius:12px;color:var(--muted);text-decoration:none;border:1px solid transparent;transition:.15s}\n.side a:hover{background:var(--glass);color:var(--ink)}\n.side a[aria-current]{background:linear-gradient(135deg,rgba(124,108,255,.38),rgba(91,140,255,.18));color:#fff;border-color:rgba(124,108,255,.5)}\n.content{flex:1;min-width:0}\n.pagehead{max-width:1000px;margin:0 auto;padding:30px 18px 0}.pagehead h1{margin:0;font-size:clamp(28px,4vw,40px);font-weight:700;letter-spacing:-.02em}.pagehead p{margin:4px 0 0;color:var(--muted)}\n.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}\n.tile{padding:12px 14px;border-radius:14px;border:1px solid var(--rule);transition:transform .15s,box-shadow .15s}\n.tile:hover{transform:translateY(-4px) scale(1.04);box-shadow:0 12px 30px -10px rgba(0,0,0,.7)}\n.tile b{display:block;font-size:15px}.tile span{display:block;font-size:12px;color:rgba(255,255,255,.7);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tile em{display:block;font-style:normal;font-weight:700;font-size:20px;margin-top:6px}\n.pills{display:flex;gap:6px;margin-bottom:12px}.pills button{padding:6px 14px;font-size:13px}.pill-off{background:transparent!important;border:1px solid var(--rule)!important;color:var(--muted)!important}\n.badge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;font-weight:700}.badge.up{background:rgba(25,211,162,.18)}.badge.down{background:rgba(255,92,122,.18)}.badge.flat{background:var(--glass);color:var(--muted)}\n.xcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}.xc{padding:16px;border:1px solid var(--rule);border-radius:16px;background:var(--glass)}.xc p{margin:0 0 8px}\n.split{display:grid;grid-template-columns:260px 1fr;gap:24px;align-items:start}\n.note{padding:16px 18px;border-radius:14px;background:rgba(124,108,255,.12);border:1px solid rgba(124,108,255,.35)}\n@media (max-width:760px){.app{flex-direction:column}.side{position:static;height:auto;width:100%;padding:10px;border-right:0;border-bottom:1px solid var(--rule)}.brand{display:none}.side nav{flex-direction:row;overflow-x:auto}.side a{white-space:nowrap;padding:8px 12px}.split{grid-template-columns:1fr}}\n\n.bar{grid-template-columns:120px 1fr auto}.bar.wide{grid-template-columns:130px 140px 1fr}.bar.wide em{display:inline}\n.lede#cd{font-variant-numeric:tabular-nums}\n'

STYLE_CSS += ".tile.mine{border-color:rgba(124,108,255,.8);box-shadow:inset 0 0 0 1px rgba(124,108,255,.4)}"

JOURNAL_HTML = '<!doctype html>\n<html lang="cs"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>Deník obchodů</title>\n<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">\n<link rel="stylesheet" href="style.css"><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script></head>\n<body><div class="app">@@SIDEBAR@@<div class="content"><header class="pagehead"><h1>Deník obchodů</h1><p>Tvoje obchody, statistiky a poučení</p></header><main>\n<div class="head"><div class="tabs" role="tablist">\n<button role="tab" data-tab="over" aria-selected="true">Přehled</button><button role="tab" data-tab="trades" aria-selected="false">Obchody</button><button role="tab" data-tab="rep" aria-selected="false">Reporty</button></div>\n<button id="add" type="button">+ Přidat obchod</button></div>\n\n<div id="t-over"><div class="kpis" id="kpis"></div>\n<section class="p"><h2>Vývoj zisku (najeď myší na graf)</h2><div id="curve"></div></section>\n<section class="p"><div class="calhead"><button class="ghost sm" onclick="mv(-1)" aria-label="Předchozí měsíc">‹</button><b id="calt"></b><button class="ghost sm" onclick="mv(1)" aria-label="Další měsíc">›</button><span id="calsum"></span></div><div class="cal" id="cal"></div></section></div>\n\n<div id="t-trades" hidden><section class="p"><h2>Otevřené pozice</h2><div id="open"></div></section>\n<section class="p"><h2>Všechny obchody</h2><div id="list"></div>\n<div class="row"><button class="ghost" id="exp" type="button">Zálohovat do souboru</button><button class="ghost" id="imp" type="button">Obnovit ze zálohy</button><input id="impf" type="file" accept="application/json" hidden></div>\n<p class="empty">Obchody se ukládají jen v tomto prohlížeči na tomto zařízení. Zálohuj je pravidelně.</p></section></div>\n\n<div id="t-rep" hidden><section class="p"><h2>Filtry</h2><div class="grid" id="flt">\n<label>Od<input type="date" id="f-from"></label><label>Do<input type="date" id="f-to"></label>\n<label>Symbol<select id="f-sym"></select></label><label>Směr<select id="f-dir"><option value="">Oba</option><option value="long">Long</option><option value="short">Short</option></select></label>\n<label>Výsledek<select id="f-res"><option value="">Všechny</option><option value="w">Výhry</option><option value="l">Prohry</option></select></label><label>Strategie<select id="f-str"></select></label></div>\n<div class="chips" id="f-emo"></div><div class="row"><button class="ghost sm" id="f-reset" type="button">Reset</button></div></section>\n<section class="p"><h2>Souhrn filtrovaných obchodů</h2><div class="kpis" id="rsum"></div>\n<div class="two" id="rbars"></div></section></div>\n</main></div></div>\n\n<dialog id="dlg"><form id="f"><h2 id="dt">Nový obchod</h2><input type="hidden" name="id">\n<div class="grid">\n<label>Otevřeno<input name="opened" type="datetime-local" required></label><label>Zavřeno (po uzavření)<input name="closed" type="datetime-local"></label>\n<label>Instrument<input name="inst" value="NQ" required></label><label>Směr<select name="dir"><option value="long">Long</option><option value="short">Short</option></select></label>\n<label>Množství<input name="size" type="number" step="any" value="1"></label><label>Hodnota bodu ($)<input name="pv" type="number" step="any" placeholder="auto (NQ 20)"></label>\n<label>Vstup<input name="entry" type="number" step="any" required></label><label>Stop<input name="stop" type="number" step="any" required></label>\n<label>Cíl<input name="target" type="number" step="any" required></label><label>Výstup<input name="exit" type="number" step="any"></label>\n<label>Poplatky ($)<input name="fee" type="number" step="any"></label><label>Strategie<input name="strat" list="strs"></label></div><datalist id="strs"></datalist>\n<div class="grid" style="margin-top:12px"><label>Dodržel jsem plán?<select name="plan"><option value="">–</option><option value="ano">Ano</option><option value="ne">Ne</option></select></label><label>Hodnocení obchodu<select name="rate"><option value="">–</option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select></label></div><label style="margin-top:12px">Chyby</label><div class="chips" id="d-mis"></div>\n<label style="margin-top:12px">Emoce</label><div class="chips" id="d-emo"></div>\n<label style="margin-top:12px">Poznámky<textarea name="notes" rows="3"></textarea></label>\n<label style="margin-top:12px">Screenshoty (soubory nebo vložení přes Ctrl+V)<input name="shot" type="file" accept="image/*" multiple></label>\n<p class="empty" id="shotinfo"></p>\n<div class="row"><button type="submit">Uložit</button><button type="button" class="ghost" id="cancel">Zrušit</button></div></form></dialog>\n<dialog id="view"><div id="vbody"></div><div class="row"><button class="ghost" onclick="this.closest(\'dialog\').close()">Zavřít</button></div></dialog>\n\n<script>\nif (window.Chart) { Chart.defaults.color = "#8A94AD"; Chart.defaults.borderColor = "rgba(255,255,255,.07)"; Chart.defaults.font.family = \'"IBM Plex Sans",system-ui,sans-serif\'; }\nconst S = "trades", EMO = [["😌","Klid"],["💪","Sebevědomí"],["🏃","FOMO"],["🤑","Chamtivost"],["😨","Strach"],["😡","Revenge"],["⏱️","Netrpělivost"],["😕","Nejistota"],["🤯","Tilt"],["😴","Nuda"]];\nconst MIS = ["Brzký vstup","Pozdní vstup","Přesunutý stop","Předčasný výstup","Přeobchodování","Bez setupu","FOMO","Revenge obchod","Moc velká pozice"];\nconst PV = {NQ:20, MNQ:2, ES:50, MES:5, YM:5, MYM:0.5};\nlet eq = null, db, shots = [], calD = new Date(), E = Object.create(null);\nconst $ = id => document.getElementById(id), sum = a => a.reduce((x, y) => x + y, 0);\nconst esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;"}[c]));\nconst nf = (x, d = 0) => x.toLocaleString("cs-CZ", {maximumFractionDigits: d});\nconst sg = (x, d = 0) => (x >= 0 ? "+" : "\\u2212") + nf(Math.abs(x), d), usd = x => sg(x) + "\\u00a0$", cl = x => x >= 0 ? "up" : "down";\nconst localNow = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };\n\nconst open = () => new Promise((res, rej) => { const r = indexedDB.open("journal", 1);\n  r.onupgradeneeded = () => r.result.createObjectStore(S, {keyPath: "id", autoIncrement: true}); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });\nconst tx = (m, f) => new Promise((res, rej) => { const t = db.transaction(S, m), o = f(t.objectStore(S)); t.oncomplete = () => res(o && o.result); t.onerror = () => rej(t.error); });\n\nconst dir = t => t.dir === "short" ? -1 : 1, when = t => t.opened || t.date + "T00:00", day = t => when(t).slice(0, 10);\nconst imgs = t => t.shots || (t.shot ? [t.shot] : []);\nconst pnl = t => t.exit == null ? null : (t.exit - t.entry) * dir(t) * (t.size || 1) * (t.pv || PV[t.inst] || 1) - (t.fee || 0);\nconst risk = t => Math.abs(t.entry - t.stop);\nconst real = t => (t.exit != null && risk(t)) ? (t.exit - t.entry) * dir(t) / risk(t) : null;\nconst plan = t => risk(t) ? Math.abs(t.target - t.entry) / risk(t) : null;\n\nfunction st(list) {\n  const d = list.filter(t => pnl(t) !== null).sort((a, b) => when(a).localeCompare(when(b))), p = d.map(pnl);\n  const w = p.filter(x => x > 0), l = p.filter(x => x < 0), net = sum(p);\n  let c = 0, pk = 0, dd = 0, ws = 0, ls = 0, mw = 0, ml = 0;\n  p.forEach(x => { c += x; pk = Math.max(pk, c); dd = Math.min(dd, c - pk);\n    if (x > 0) { ws++; ls = 0; } else if (x < 0) { ls++; ws = 0; } else ws = ls = 0; mw = Math.max(mw, ws); ml = Math.max(ml, ls); });\n  const days = {}; d.forEach(t => days[day(t)] = (days[day(t)] || 0) + pnl(t));\n  const dv = Object.values(days), rs = d.map(real).filter(x => x !== null);\n  const hs = d.filter(t => t.closed && t.opened).map(t => (new Date(t.closed) - new Date(t.opened)) / 60000);\n  return {d, p, n: d.length, net, win: w.length, loss: l.length, be: p.length - w.length - l.length, days,\n    wr: d.length ? w.length / d.length * 100 : null, pf: l.length ? sum(w) / Math.abs(sum(l)) : null,\n    avgW: w.length ? sum(w) / w.length : 0, avgL: l.length ? sum(l) / l.length : 0, exp: d.length ? net / d.length : 0,\n    avgR: rs.length ? sum(rs) / rs.length : null, best: p.length ? Math.max(...p) : 0, worst: p.length ? Math.min(...p) : 0,\n    dd, mw, ml, dayWin: dv.length ? dv.filter(x => x > 0).length / dv.length * 100 : null, hold: hs.length ? sum(hs) / hs.length : null};\n}\nconst K = (v, l) => `<div class="k"><b>${v}</b><span>${l}</span></div>`;\nconst dash = "–";\n\nfunction over(trades) {\n  const s = st(trades);\n  $("kpis").innerHTML = K(`<span class="${cl(s.net)}">${usd(s.net)}</span>`, `Celkový zisk, ${s.n} obchodů`) +\n    K(s.wr == null ? dash : nf(s.wr, 1) + "\\u00a0%", `Úspěšnost (${s.win} výher, ${s.loss} proher, ${s.be} BE)`) +\n    K(s.pf == null ? dash : nf(s.pf, 2), `Profit factor (prům. výhra ${nf(s.avgW)} $ / ztráta ${nf(Math.abs(s.avgL))} $)`) +\n    K(s.dayWin == null ? dash : nf(s.dayWin, 1) + "\\u00a0%", "Ziskové dny") +\n    K(`<span class="down">${usd(s.dd)}</span>`, "Maximální drawdown");\n  if (eq) { eq.destroy(); eq = null; }\n  $("curve").innerHTML = s.n < 2 ? \'<p class="empty">Graf se zobrazí od dvou uzavřených obchodů.</p>\' : \'<div class="chartbox"><canvas id="c-eq"></canvas></div>\';\n  if (s.n >= 2 && window.Chart) { let c = 0; const cum = s.p.map(x => c += x);\n    eq = new Chart($("c-eq"), {type: "line", data: {labels: s.d.map(t => day(t)), datasets: [{data: cum, borderColor: "#7C6CFF", borderWidth: 2, tension: .3, fill: true, pointRadius: 0, pointHoverRadius: 5,\n      backgroundColor: x => { const g = x.chart.ctx.createLinearGradient(0, 0, 0, x.chart.height); g.addColorStop(0, "rgba(124,108,255,.35)"); g.addColorStop(1, "rgba(124,108,255,0)"); return g; }}]},\n      options: {maintainAspectRatio: false, interaction: {mode: "index", intersect: false}, plugins: {legend: {display: false}, tooltip: {callbacks: {label: x => "Celkem " + usd(x.parsed.y), afterLabel: x => "Tento obchod: " + usd(s.p[x.dataIndex])}}},\n      scales: {x: {ticks: {maxTicksLimit: 6}, grid: {display: false}}, y: {ticks: {callback: v => nf(v) + " $"}}}}}); }\n  const y = calD.getFullYear(), m = calD.getMonth(), pre = `${y}-${String(m + 1).padStart(2, "0")}`, off = (new Date(y, m, 1).getDay() + 6) % 7;\n  let h = ["Po","Út","St","Čt","Pá","So","Ne"].map(d => `<b class="dow">${d}</b>`).join("") + "<i></i>".repeat(off), tot = 0;\n  for (let d = 1; d <= new Date(y, m + 1, 0).getDate(); d++) { const v = s.days[`${pre}-${String(d).padStart(2, "0")}`]; if (v != null) tot += v;\n    h += `<div class="day ${v == null ? "" : cl(v)}"><span>${d}</span>${v == null ? "" : nf(v)}</div>`; }\n  $("cal").innerHTML = h; $("calt").textContent = calD.toLocaleString("cs-CZ", {month: "long", year: "numeric"});\n  $("calsum").innerHTML = `<span class="${cl(tot)}">${usd(tot)}</span>`;\n}\nconst mv = n => { calD = new Date(calD.getFullYear(), calD.getMonth() + n, 1); render(); };\n\nconst emoStr = t => (t.emo || []).map(e => (EMO.find(x => x[1] === e) || [""])[0]).join("");\nfunction table(rows, isOpen) {\n  if (!rows.length) return `<p class="empty">${isOpen ? "Žádná otevřená pozice." : "Zatím žádný obchod. Přidej první tlačítkem nahoře."}</p>`;\n  return `<div class="wrap"><table><thead><tr><th>Otevřeno</th><th>Symbol</th><th>Směr</th><th class="n">Mn.</th><th class="n">Vstup</th><th class="n">${isOpen ? "Stop" : "Výstup"}</th><th class="n">${isOpen ? "Cíl" : "Net P&amp;L"}</th><th class="n">${isOpen ? "R:R plán" : "R"}</th>${isOpen ? "" : \'<th class="hide">Emoce</th><th class="hide">Strategie</th>\'}<th></th></tr></thead><tbody>` +\n  rows.map(t => { const p = pnl(t), r = real(t);\n    return `<tr><td>${esc(when(t).replace("T", " "))}</td><td>${esc(t.inst)}</td><td>${t.dir === "short" ? "Short" : "Long"}</td><td class="n">${nf(t.size || 1, 2)}</td><td class="n">${nf(t.entry, 2)}</td>` +\n    (isOpen ? `<td class="n">${nf(t.stop, 2)}</td><td class="n">${nf(t.target, 2)}</td><td class="n">1:${nf(plan(t) || 0, 2)}</td>`\n      : `<td class="n">${nf(t.exit, 2)}</td><td class="n ${cl(p)}">${usd(p)}</td><td class="n">${r == null ? dash : sg(r, 2) + "R"}</td><td class="hide">${emoStr(t)}</td><td class="hide">${esc(t.strat || "")}</td>`) +\n    `<td class="n"><button class="ghost sm" data-a="view" data-id="${t.id}">${imgs(t).length ? "📷 " + imgs(t).length : "Detail"}</button> ` +\n    (isOpen ? `<button class="sm" data-a="close" data-id="${t.id}">Uzavřít</button> ` : "") +\n    `<button class="ghost sm" data-a="edit" data-id="${t.id}">Upravit</button> <button class="ghost sm" data-a="del" data-id="${t.id}">Smazat</button></td></tr>`; }).join("") + "</tbody></table></div>";\n}\n\nfunction bars(title, groups) {\n  const rows = groups.map(([k, ts]) => { const s = st(ts); return {k, net: s.net, n: s.n, wr: s.wr}; }).filter(r => r.n);\n  const mx = Math.max(1, ...rows.map(r => Math.abs(r.net)));\n  return `<div><h3>${title}</h3>` + (rows.length ? rows.map(r => `<div class="bar"><span>${esc(r.k)}</span><div class="track ${cl(r.net)}"><i style="width:${Math.abs(r.net) / mx * 100}%"></i></div><b class="${cl(r.net)}">${usd(r.net)} <em>${r.n}× · ${nf(r.wr)} %</em></b></div>`).join("") : \'<p class="empty">Žádná data.</p>\') + "</div>";\n}\nfunction reports() {\n  const g = id => $(id).value, em = [...document.querySelectorAll("#f-emo input:checked")].map(i => i.value);\n  const list = ALL.filter(t => pnl(t) !== null && (!g("f-from") || day(t) >= g("f-from")) && (!g("f-to") || day(t) <= g("f-to")) &&\n    (!g("f-sym") || t.inst === g("f-sym")) && (!g("f-dir") || t.dir === g("f-dir")) && (!g("f-str") || t.strat === g("f-str")) &&\n    (!g("f-res") || (g("f-res") === "w" ? pnl(t) > 0 : pnl(t) < 0)) && em.every(e => (t.emo || []).includes(e)));\n  const s = st(list);\n  $("rsum").innerHTML = K(`<span class="${cl(s.net)}">${usd(s.net)}</span>`, "Net P&L") + K(s.n, "Obchody") + K(s.wr == null ? dash : nf(s.wr, 1) + "\\u00a0%", "Úspěšnost") +\n    K(s.pf == null ? dash : nf(s.pf, 2), "Profit factor") + K(s.avgR == null ? dash : sg(s.avgR, 2) + "R", "Průměrné R") + K(usd(s.exp), "Expectancy na obchod") +\n    K(usd(s.best), "Nejlepší") + K(usd(s.worst), "Nejhorší") + K(s.mw, "Série výher") + K(s.ml, "Série proher") + K(nf(s.avgW) + " $", "Prům. výhra") + K(s.hold == null ? dash : nf(s.hold, 0) + " min", "Prům. délka");\n  const hr = t => new Date(when(t)).getHours(), wd = t => (new Date(when(t)).getDay() + 6) % 7;\n  $("rbars").innerHTML = bars("Podle hodiny otevření", [...Array(24).keys()].map(h => [String(h).padStart(2, "0") + ":00", list.filter(t => hr(t) === h)])) +\n    bars("Podle dne v týdnu", ["Po","Út","St","Čt","Pá","So","Ne"].map((n, i) => [n, list.filter(t => wd(t) === i)])) +\n    bars("Podle emoce", EMO.map(([i, n]) => [i + " " + n, list.filter(t => (t.emo || []).includes(n))])) +\n    bars("Podle strategie", [...new Set(list.map(t => t.strat).filter(Boolean))].map(n => [n, list.filter(t => t.strat === n)])) +\n    bars("Podle dodržení plánu", [["Dodržel plán", list.filter(t => t.plan === "ano")], ["Nedodržel plán", list.filter(t => t.plan === "ne")]]) +\n    bars("Podle chyby", MIS.map(n => [n, list.filter(t => (t.mis || []).includes(n))]));\n}\n\nlet ALL = [];\nasync function render() {\n  ALL = await tx("readonly", s => s.getAll());\n  const rows = [...ALL].sort((a, b) => when(b).localeCompare(when(a)));\n  over(ALL);\n  $("open").innerHTML = table(rows.filter(t => t.exit == null), true);\n  $("list").innerHTML = table(rows.filter(t => t.exit != null), false);\n  const keep = [$("f-sym").value, $("f-str").value], opt = (a, all) => `<option value="">${all}</option>` + a.map(x => `<option>${esc(x)}</option>`).join("");\n  const strs = [...new Set(ALL.map(t => t.strat).filter(Boolean))];\n  $("f-sym").innerHTML = opt([...new Set(ALL.map(t => t.inst))], "Všechny"); $("f-str").innerHTML = opt(strs, "Všechny");\n  $("f-sym").value = keep[0]; $("f-str").value = keep[1];\n  $("strs").innerHTML = strs.map(s => `<option value="${esc(s)}">`).join("");\n  reports();\n}\n\nconst chips = (box, name) => $(box).innerHTML = EMO.map(([i, n]) => `<label class="chip"><input type="checkbox" ${name ? `name="${name}"` : ""} value="${n}">${i} ${n}</label>`).join("");\nchips("d-emo", "emo"); $("d-mis").innerHTML = MIS.map(n => `<label class="chip"><input type="checkbox" name="mis" value="${n}">${n}</label>`).join(""); chips("f-emo");\ndocument.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {\n  document.querySelectorAll(".tabs button").forEach(x => x.setAttribute("aria-selected", x === b));\n  ["over", "trades", "rep"].forEach(k => $("t-" + k).hidden = k !== b.dataset.tab); });\n$("flt").addEventListener("input", reports); $("f-emo").addEventListener("input", reports);\n$("f-reset").onclick = () => { document.querySelectorAll("#flt input,#flt select").forEach(i => i.value = ""); document.querySelectorAll("#f-emo input").forEach(i => i.checked = false); reports(); };\n\nfunction dlg(t) {\n  const f = $("f"); f.reset(); shots = t ? [...imgs(t)] : [];\n  $("dt").textContent = t ? "Upravit obchod" : "Nový obchod"; f.elements.opened.value = localNow();\n  if (t) { for (const k of ["id","opened","closed","inst","dir","size","pv","entry","stop","target","exit","fee","strat","plan","rate","notes"]) f.elements[k].value = (k === "opened" ? when(t) : t[k]) ?? "";\n    f.querySelectorAll(\'[name=emo]\').forEach(c => c.checked = (t.emo || []).includes(c.value));\n    f.querySelectorAll(\'[name=mis]\').forEach(c => c.checked = (t.mis || []).includes(c.value)); }\n  $("shotinfo").textContent = shots.length ? shots.length + " screenshotů uloženo." : ""; $("dlg").showModal();\n}\n$("add").onclick = () => dlg(); $("cancel").onclick = () => $("dlg").close();\n$("f").elements.shot.onchange = e => { shots.push(...e.target.files); $("shotinfo").textContent = shots.length + " screenshotů připraveno."; e.target.value = ""; };\ndocument.addEventListener("paste", e => { const f = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));\n  if ($("dlg").open && f.length) { shots.push(...f); $("shotinfo").textContent = shots.length + " screenshotů připraveno."; } });\n$("f").onsubmit = async e => {\n  e.preventDefault(); const f = e.target.elements, n = k => f[k].value === "" ? null : parseFloat(f[k].value);\n  const t = {opened: f.opened.value, closed: f.closed.value || null, inst: f.inst.value.toUpperCase(), dir: f.dir.value, size: n("size") || 1, pv: n("pv"),\n    entry: n("entry"), stop: n("stop"), target: n("target"), exit: n("exit"), fee: n("fee"), strat: f.strat.value.trim(), notes: f.notes.value,\n    emo: [...e.target.querySelectorAll("[name=emo]:checked")].map(c => c.value), plan: f.plan.value, rate: n("rate"),\n    mis: [...e.target.querySelectorAll("[name=mis]:checked")].map(c => c.value), shots};\n  if (f.id.value) t.id = parseInt(f.id.value);\n  await tx("readwrite", s => s.put(t)); $("dlg").close(); render();\n};\n\ndocument.addEventListener("click", async e => {\n  const b = e.target.closest("[data-a]"); if (!b) return; const t = ALL.find(x => x.id == b.dataset.id), a = b.dataset.a;\n  if (a === "edit") dlg(t);\n  else if (a === "del") { if (confirm("Smazat tento obchod?")) { await tx("readwrite", s => s.delete(t.id)); render(); } }\n  else if (a === "close") { const v = parseFloat(String(prompt("Výstupní cena?") || "").replace(",", ".")); if (!isNaN(v)) { t.exit = v; t.closed = localNow(); await tx("readwrite", s => s.put(t)); render(); } }\n  else if (a === "view") { $("vbody").innerHTML = `<h2>${esc(t.inst)} ${t.dir}, ${esc(when(t).replace("T", " "))}</h2><p>${esc(t.notes || "Bez poznámky.")}</p><p>Plán: ${t.plan || "–"} | Hodnocení: ${t.rate || "–"} | Chyby: ${esc((t.mis || []).join(", ") || "žádné")}</p>`;\n    imgs(t).forEach(im => { const i = document.createElement("img"); i.src = URL.createObjectURL(im); i.alt = "Screenshot obchodu"; $("vbody").appendChild(i); }); $("view").showModal(); }\n});\n\nconst toUrl = b => new Promise(r => { const f = new FileReader(); f.onload = () => r(f.result); f.readAsDataURL(b); });\n$("exp").onclick = async () => {\n  const out = []; for (const t of ALL) out.push({...t, shot: undefined, shots: await Promise.all(imgs(t).map(toUrl))});\n  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], {type: "application/json"}));\n  a.download = "zaloha-denik-" + new Date().toISOString().slice(0, 10) + ".json"; a.click();\n};\n$("imp").onclick = () => $("impf").click();\n$("impf").onchange = async e => {\n  try { const ts = JSON.parse(await e.target.files[0].text());\n    for (const t of ts) { t.shots = await Promise.all((t.shots || []).map(async u => (await fetch(u)).blob())); delete t.id; await tx("readwrite", s => s.add(t)); }\n    render();\n  } catch { alert("Soubor se nepodařilo načíst. Je to záloha z tohoto deníku?"); }\n  e.target.value = "";\n};\nopen().then(d => { db = d; render(); });\n</script></body></html>\n'

PLAN_HTML = '<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Plán dne</title>\n<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><link rel="stylesheet" href="style.css"></head>\n<body><div class="app">@@SIDEBAR@@<div class="content"><header class="pagehead"><h1>Plán dne</h1><p>Checklist, limity a kalkulačka rizika</p></header><main>\n<section><h2>Dnešní limity (živě z tvého deníku)</h2><div class="kpis" id="live"></div><div id="warn"></div></section>\n<section><h2>Plán na dnešek</h2><div class="grid">\n<label>Můj bias<select id="p-bias"><option value="">–</option><option>Bullish</option><option>Bearish</option><option>Neutrální</option></select></label>\n<label>Max. počet obchodů<input id="p-maxTrades" type="number"></label><label>Max. denní ztráta ($)<input id="p-maxLoss" type="number"></label><label>Denní cíl ($)<input id="p-goal" type="number"></label></div>\n<label style="margin-top:12px">Klíčové úrovně<textarea id="p-levels" rows="2"></textarea></label>\n<label style="margin-top:12px">Setupy, které dnes obchoduji<textarea id="p-setups" rows="2"></textarea></label>\n<label style="margin-top:12px">Poznámky<textarea id="p-notes" rows="2"></textarea></label>\n<p class="empty">Plán se ukládá do tohoto prohlížeče, pro každý den zvlášť.</p></section>\n<section><h2>Checklist před obchodováním <span id="cprog" class="empty"></span></h2><div class="chips" id="checks"></div></section>\n<section><h2>Měsíční cíl</h2><div class="grid"><label>Cíl na měsíc ($)<input id="m-goal" type="number"></label></div><div id="mprog" style="margin-top:12px"></div></section>\n<section><h2>Kalkulačka velikosti pozice</h2><div class="grid">\n<label>Účet ($)<input id="c-acc" type="number" step="any" value="10000"></label><label>Riziko na obchod (%)<input id="c-risk" type="number" step="any" value="1"></label>\n<label>Vstup<input id="c-entry" type="number" step="any"></label><label>Stop<input id="c-stop" type="number" step="any"></label>\n<label>Hodnota bodu ($)<input id="c-pv" type="number" step="any" value="20"></label></div><p id="c-out" class="empty" style="margin-top:12px">Vyplň vstup a stop.</p></section>\n</main></div></div>\n<script>\nconst $ = id => document.getElementById(id), nf = (x, d = 0) => x.toLocaleString("cs-CZ", {maximumFractionDigits: d});\nconst sg = x => (x >= 0 ? "+" : "\\u2212") + nf(Math.abs(x)), cl = x => x >= 0 ? "up" : "down";\nconst today = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };\nconst ls = {get: k => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }};\nconst PV = {NQ: 20, MNQ: 2, ES: 50, MES: 5, YM: 5, MYM: 0.5};\nconst CHECK = ["Vyspal jsem se a jsem klidný", "Prošel jsem ranní briefing", "Znám důležité zprávy dne a jejich čas", "Mám nakreslené klíčové úrovně", "Vím, kolik riskuji na obchod", "Před vstupem mám připravený stop", "Dnes neobchoduji z vteřinové emoce"];\nconst key = "plan:" + today(), F = ["bias", "levels", "setups", "maxTrades", "maxLoss", "goal", "notes"];\nlet P = ls.get(key) || {checks: []};\nF.forEach(f => { $("p-" + f).value = P[f] ?? ""; $("p-" + f).addEventListener("input", () => { P[f] = $("p-" + f).value; ls.set(key, P); live(); }); });\n$("checks").innerHTML = CHECK.map((t, i) => `<label class="chip"><input type="checkbox" data-i="${i}" ${P.checks.includes(i) ? "checked" : ""}>${t}</label>`).join("");\nconst prog = () => { $("cprog").textContent = P.checks.length + " z " + CHECK.length; };\n$("checks").addEventListener("input", e => { const i = +e.target.dataset.i; P.checks = P.checks.filter(x => x !== i); if (e.target.checked) P.checks.push(i); ls.set(key, P); prog(); }); prog();\n$("m-goal").value = ls.get("mgoal") ?? ""; $("m-goal").addEventListener("input", () => { ls.set("mgoal", $("m-goal").value); live(); });\n\nfunction trades() { return new Promise(res => { const r = indexedDB.open("journal", 1);\n  r.onupgradeneeded = () => r.result.createObjectStore("trades", {keyPath: "id", autoIncrement: true});\n  r.onsuccess = () => { const d = r.result; if (!d.objectStoreNames.contains("trades")) return res([]);\n    const q = d.transaction("trades").objectStore("trades").getAll(); q.onsuccess = () => res(q.result); q.onerror = () => res([]); };\n  r.onerror = () => res([]); }); }\nasync function live() {\n  const ts = await trades(), d = today(), mo = d.slice(0, 7), w = t => t.opened || t.date + "T00:00";\n  const pnl = t => t.exit == null ? 0 : (t.exit - t.entry) * (t.dir === "short" ? -1 : 1) * (t.size || 1) * (t.pv || PV[t.inst] || 1) - (t.fee || 0);\n  const td = ts.filter(t => w(t).slice(0, 10) === d), p = td.reduce((s, t) => s + pnl(t), 0), m = ts.filter(t => w(t).slice(0, 7) === mo).reduce((s, t) => s + pnl(t), 0);\n  const ml = +$("p-maxLoss").value || 0, mt = +$("p-maxTrades").value || 0, g = +$("p-goal").value || 0, mg = +$("m-goal").value || 0;\n  const K = (v, l) => `<div class="k"><b>${v}</b><span>${l}</span></div>`;\n  $("live").innerHTML = K(`<span class="${cl(p)}">${sg(p)} $</span>`, "Dnešní výsledek") + K(td.length + (mt ? " z " + mt : ""), "Obchodů dnes") +\n    K(ml ? nf(Math.max(0, ml + Math.min(p, 0))) + " $" : "–", "Zbývá do limitu ztráty") + K(g ? nf(Math.max(0, g - p)) + " $" : "–", "Zbývá do denního cíle");\n  const w2 = []; if (ml && p <= -ml) w2.push("Limit ztráty je dosažen. Dnes už neobchoduj.");\n  if (mt && td.length >= mt) w2.push("Dosáhl jsi maximálního počtu obchodů."); if (g && p >= g) w2.push("Denní cíl je splněn. Zvaž konec dne.");\n  $("warn").innerHTML = w2.map(x => `<div class="note" style="margin-top:12px">${x}</div>`).join("");\n  $("mprog").innerHTML = mg ? `<div class="bar" style="grid-template-columns:90px 1fr auto"><span>Měsíc</span><div class="track ${cl(m)}"><i style="width:${Math.min(100, Math.max(0, m / mg * 100))}%"></i></div><b class="${cl(m)}">${sg(m)} $ z ${nf(mg)} $</b></div>` : \'<p class="empty">Zadej měsíční cíl a uvidíš postup.</p>\';\n}\nlive(); setInterval(live, 15000);\n\nfunction calc() {\n  const g = id => parseFloat($(id).value), a = g("c-acc"), r = g("c-risk"), e = g("c-entry"), s = g("c-stop"), pv = g("c-pv");\n  if ([a, r, e, s, pv].some(isNaN) || e === s) { $("c-out").textContent = "Vyplň vstup a stop."; return; }\n  const money = a * r / 100, size = money / (Math.abs(e - s) * pv), dr = e > s ? 1 : -1;\n  $("c-out").innerHTML = `Riskuješ <b>${nf(money)} $</b>. Velikost pozice: <b>${nf(size, 2)}</b> (zaokrouhli dolů na <b>${nf(Math.floor(size))}</b>). Cíl pro poměr 1:2 je <b>${nf(e + dr * Math.abs(e - s) * 2, 2)}</b>, pro 1:3 <b>${nf(e + dr * Math.abs(e - s) * 3, 2)}</b>.`;\n}\ndocument.querySelectorAll("[id^=c-]").forEach(i => i.addEventListener("input", calc));\n</script></body></html>\n'


if __name__ == "__main__":
    main()
