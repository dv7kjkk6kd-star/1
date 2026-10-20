#!/usr/bin/env python3
"""Ranní briefing (verze 21) pro obchodování Nasdaqu: pre-market, nálada trhu, kalendář z Forex Factory."""
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
FF_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]


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
            '<stop offset="0" stop-color="#FF6B8A"/><stop offset=".5" stop-color="#C9D1E8"/><stop offset="1" stop-color="#2EE6A6"/></linearGradient></defs>'
            '<path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="url(#gg)" stroke-width="14" stroke-linecap="round"/>'
            f'<g transform="rotate({ang:.1f} 100 100)"><line x1="100" y1="100" x2="100" y2="34" stroke="#EEF1FA" stroke-width="3" stroke-linecap="round"/></g>'
            '<circle cx="100" cy="100" r="6" fill="#EEF1FA"/></svg>')


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
       ("zpravy.html", "🗞️", "Zprávy", "Všechny titulky a puls trhu"),
       ("premarket.html", "🌅", "Pre-market akcí", "Největší akcie před otevřením burzy"),
       ("fundament.html", "📰", "Fundament", "Čísla z trhu, hodnocení oblastí a zprávy"),
       ("predikce.html", "🔮", "Predikce zpráv", "Co se stane, když zpráva vyjde lépe nebo hůř"),
       ("kalendar.html", "📅", "Kalendář", "Důležité zprávy z USA na celý týden"),
       ("sentiment.html", "💬", "Sentiment", "Nálada ve zprávách za posledních 24 hodin"),
       ("bias.html", "🧭", "Bias", "Směr dne podle dat a RSI"),
       ("tyden.html", "🗓️", "Týdenní výhled", "Co čekat od celého týdne"),
       ("presnost.html", "🎯", "Přesnost biasu", "Jak často měl bias pravdu"),
       ("trpelivost.html", "⏳", "Trpělivost", "Trénink čekání: den bez obchodu je výhra"),
       ("mindset.html", "🧘", "Mindset", "Klid, meditace a trpělivost před obchodováním"),
       ("plan.html", "📝", "Plán dne", "Pravidla trpělivosti, limity a kalkulačka"),
       ("journal.html", "📓", "Deník obchodů", "Tvoje obchody, statistiky a rozbor"),
       ("roadmap.html", "🛣️", "Roadmap", "Tvoje cesta k disciplinovanému obchodování"),
       ("prirucka.html", "📘", "Příručka", "Slovníček a postup krok za krokem"),
       ("nastaveni.html", "⚙️", "Nastavení", "Účet, riziko a výchozí hodnoty")]


NAVG = [("Dnes", ["index.html"]),
        ("Zprávy a fundament", ["zpravy.html", "sentiment.html", "kalendar.html", "predikce.html", "fundament.html"]),
        ("Analýza", ["premarket.html", "bias.html", "tyden.html", "presnost.html"]),
        ("Příprava", ["trpelivost.html", "mindset.html", "plan.html"]), ("Obchodování", ["journal.html"]), ("Učení a nastavení", ["roadmap.html", "prirucka.html", "nastaveni.html"])]

SIDE_JS = """(function(){var el=document.getElementById("mk"),sub=document.getElementById("mks");if(!el)return;
var pad=function(n){return String(n).padStart(2,"0")},WD=["Mon","Tue","Wed","Thu","Fri"],ORD=["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
function ny(){var o={};new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",hour12:false,weekday:"short",hour:"2-digit",minute:"2-digit",second:"2-digit"}).formatToParts(new Date()).forEach(function(x){o[x.type]=x.value});return{wd:o.weekday,s:(+o.hour%24)*3600+(+o.minute)*60+(+o.second)}}
function tick(){var t=ny(),open=34200,close=57600,wk=WD.indexOf(t.wd)>=0,left,txt;
if(wk&&t.s>=open&&t.s<close){left=close-t.s;txt="Burza je otevřená, zavře za"}
else{var idx=ORD.indexOf(t.wd),d=(wk&&t.s<open)?0:1;while(WD.indexOf(ORD[(idx+d)%7])<0)d++;left=d*86400+open-t.s;txt="Burza otevře za"}
el.textContent=pad(Math.floor(left/3600))+":"+pad(Math.floor(left%3600/60))+":"+pad(left%60);sub.textContent=txt}
tick();setInterval(tick,1000)})();"""


def sidebar(active):
    meta = {h: (i, t) for h, i, t, _ in NAV}
    out = ""
    for g, hs in NAVG:
        out += f'<p class="grp">{g}</p>'
        for h in hs:
            i, t = meta[h]
            cur = ' aria-current="page"' if h == active else ""
            out += f'<a href="{h}"{cur}><span>{i}</span>{t}</a>'
    clock = '<div class="clock"><span id="mks">Burza</span><b id="mk">–</b><span>podle času v New Yorku, svátky nezohledňuje</span></div>'
    return f'<aside class="side"><div class="brand">Trader Dojo<small>trénink trpělivosti – ovládnutí sebe sama</small><small class="mot">vše si mě ve správný moment najde</small></div>{clock}<nav>{out}</nav></aside><script>{SIDE_JS}{SCROLL_JS}</script>'


HINTS = {
    "Z čeho se skóre skládá (od −2 do +2)": "Každý řádek je jeden ukazatel. Zelená svědčí pro růst, červená pro pokles. Najeď myší pro vysvětlení.",
    "Nasdaq futures": "Čára je zelená, když cena roste, a červená, když klesá. Najeď myší a uvidíš cenu i změnu od začátku grafu.",
    "Tvůj ranní postup": "Projdi kroky odshora dolů. Zabere to asi 20 minut a budeš připravený.",
    "Kolik akcií roste": "Zelená je část akcií v plusu, červená v minusu.",
    "Změna podle akcie (najeď myší)": "Klikni na sloupec a otevře se graf akcie na TradingView.",
    "Sezónnost Nasdaq 100: průměr podle měsíců": "Průměrná změna Nasdaq 100 v každém měsíci za posledních 14 let. Zvýrazněný je letošní měsíc.",
    "Nejbližší důležité zprávy z USA (červené a oranžové)": "Červená znamená velký dopad na trh, oranžová střední. Před takovou zprávou bývá lepší počkat.",
    "Změna za poslední den (najeď myší)": "U VIXu, dolaru a výnosů je růst pro akcie špatný, a proto je zbarvený obráceně.",
    "Nálada podle zdroje (najeď myší)": "Každý sloupec je jeden zdroj zpráv. Nad nulou převažují pozitivní titulky, pod nulou negativní.",
    "Dnešní limity (živě z tvého deníku)": "Čísla se berou z tvého deníku obchodů a obnovují se sama.",
    "Checklist před obchodováním": "Zaškrtni, co máš hotové. Pokud něco chybí, nejdřív to dodělej.",
    "Výsledky firem (earnings) u tvých akcií v příštích 3 týdnech": "Kolem výsledků firem bývají velké pohyby, proto je dobré o nich vědět předem.",
}


def add_hints(body):
    for title, text in HINTS.items():
        for tag in (f"<h2>{title}</h2>",):
            if tag in body:
                body = body.replace(tag, tag + f'<p class="hint">{text}</p>', 1)
    return body


def shell(active, body, data, extra_js=""):
    title, subtitle = next((t, s) for h, i, t, s in NAV if h == active)
    now = datetime.now(PRAGUE).strftime("%-d. %-m. %Y v %H:%M")
    dj = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>{FONTS}<link rel="stylesheet" href="style.css">
<script src="tips.js" defer></script><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script></head>
<body class="bg-{BG.get(active, 'chart')}"><div class="app">{sidebar(active)}<div class="content"><header class="pagehead"><h1>{title}</h1><p>{subtitle}</p></header>
<main>{add_hints(body)}<footer><p>Vygenerováno {now} (verze 21). Data: Yahoo Finance, Forex Factory a TradingView, mohou mít zpoždění.</p></footer></main></div></div>
<script>const DATA={dj};</script><script>{CHARTS_JS}{extra_js}</script></body></html>"""


CHARTS_JS = """
const $ = id => document.getElementById(id);
Chart.defaults.color = "#C9D1E8"; Chart.defaults.borderColor = "rgba(255,255,255,.07)"; Chart.defaults.font.family = '"IBM Plex Sans",system-ui,sans-serif';
const grad = x => { const g = x.chart.ctx.createLinearGradient(0, 0, 0, x.chart.height); g.addColorStop(0, "rgba(139,124,255,.35)"); g.addColorStop(1, "rgba(139,124,255,0)"); return g; };
const stagger = {delay: c => c.dataIndex * 45};
Chart.register({id: "cross", afterDraw(c) { if (c.config.type === "line" && c.tooltip && c.tooltip._active && c.tooltip._active.length) { const x = c.tooltip._active[0].element.x, a = c.chartArea, g = c.ctx; g.save(); g.beginPath(); g.moveTo(x, a.top); g.lineTo(x, a.bottom); g.lineWidth = 1; g.strokeStyle = "rgba(255,255,255,.28)"; g.setLineDash([4, 4]); g.stroke(); g.restore(); } }});
if (window.Chart && DATA.nq && $("c-nq")) {
  const full = DATA.nq;
  const nq = new Chart($("c-nq"), {type: "line", data: {labels: full.l.slice(-24), datasets: [{data: full.v.slice(-24), borderColor: "#8B7CFF", borderWidth: 2.5, tension: .3, fill: true,
    pointRadius: 0, pointHoverRadius: 7, pointHoverBackgroundColor: "#fff", backgroundColor: grad,
    segment: {borderColor: c => c.p1.parsed.y >= c.p0.parsed.y ? "#2EE6A6" : "#FF6B8A"}}]},
    options: {maintainAspectRatio: false, interaction: {mode: "index", intersect: false}, plugins: {legend: {display: false}, tooltip: {callbacks: {afterLabel: c => { const d = c.chart.data.datasets[0].data, ch = (c.parsed.y / d[0] - 1) * 100; return "Od začátku grafu: " + (ch >= 0 ? "+" : "") + ch.toFixed(2) + " %"; }}}},
      scales: {x: {ticks: {maxTicksLimit: 6}, grid: {display: false}}, y: {ticks: {callback: v => v.toLocaleString("cs-CZ")}}}}});
  document.querySelectorAll("[data-r]").forEach(b => b.onclick = () => { const n = +b.dataset.r;
    nq.data.labels = n ? full.l.slice(-n) : full.l; nq.data.datasets[0].data = n ? full.v.slice(-n) : full.v; nq.update();
    document.querySelectorAll("[data-r]").forEach(x => x.classList.toggle("pill-off", x !== b)); });
}
if (window.Chart && DATA.sea && $("c-sea")) new Chart($("c-sea"), {type: "bar",
  data: {labels: ["Led","Úno","Bře","Dub","Kvě","Čvn","Čvc","Srp","Zář","Říj","Lis","Pro"], datasets: [{data: DATA.sea.m,
    backgroundColor: DATA.sea.m.map((v, i) => (v >= 0 ? "#2EE6A6" : "#FF6B8A") + (i === DATA.sea.cur ? "" : "99")),
    borderColor: DATA.sea.m.map((v, i) => i === DATA.sea.cur ? "#FFFFFF" : "transparent"), borderWidth: 2, borderRadius: 8, hoverBackgroundColor: "#8B7CFF"}]},
  options: {maintainAspectRatio: false, animation: stagger, plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => "Průměr " + (c.parsed.y >= 0 ? "+" : "") + c.parsed.y.toFixed(2) + " %"}}},
    scales: {y: {ticks: {callback: v => v + " %"}}, x: {grid: {display: false}}}}});
if (window.Chart && DATA.pre && DATA.pre.length && $("c-pre")) new Chart($("c-pre"), {type: "bar",
  data: {labels: DATA.pre.map(r => r.s), datasets: [{data: DATA.pre.map(r => r.c), backgroundColor: DATA.pre.map(r => r.c >= 0 ? "#2EE6A6" : "#FF6B8A"), borderRadius: 6, hoverBackgroundColor: "#8B7CFF"}]},
  options: {onClick: (e, els) => { if (els.length) window.open("https://www.tradingview.com/chart/?symbol=" + DATA.pre[els[0].index].s, "_blank"); }, onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : "default"; }, indexAxis: "y", maintainAspectRatio: false, animation: stagger, plugins: {legend: {display: false},
    tooltip: {callbacks: {title: i => DATA.pre[i[0].dataIndex].n, label: c => (c.parsed.x >= 0 ? "+" : "") + c.parsed.x.toFixed(2) + " %"}}},
    scales: {x: {ticks: {callback: v => v + " %"}}, y: {grid: {display: false}}}}});
if (window.Chart && DATA.pre && DATA.pre.length && $("c-br")) { const up = DATA.pre.filter(r => r.c >= 0).length;
  new Chart($("c-br"), {type: "doughnut", data: {labels: ["Rostou", "Klesají"], datasets: [{data: [up, DATA.pre.length - up], backgroundColor: ["#2EE6A6", "#FF6B8A"], borderWidth: 0, hoverOffset: 14}]},
    options: {maintainAspectRatio: false, cutout: "68%", plugins: {legend: {position: "bottom"}}}}); }
if (window.Chart && DATA.sent && $("c-s")) new Chart($("c-s"), {type: "bar",
  data: {labels: DATA.sent.groups.map(g => g.name), datasets: [{data: DATA.sent.groups.map(g => g.score), borderRadius: 8, hoverBackgroundColor: "#8B7CFF",
    backgroundColor: DATA.sent.groups.map(g => g.score > 0 ? "#2EE6A6" : g.score < 0 ? "#FF6B8A" : "#C9D1E8")}]},
  options: {maintainAspectRatio: false, animation: stagger, plugins: {legend: {display: false}, tooltip: {callbacks: {
    label: c => "Skóre " + c.parsed.y, afterLabel: c => DATA.sent.groups[c.dataIndex].bull + " pozitivních, " + DATA.sent.groups[c.dataIndex].bear + " negativních z " + DATA.sent.groups[c.dataIndex].n}}},
    scales: {y: {min: -5, max: 5}, x: {grid: {display: false}, ticks: {maxRotation: 40}}}}});
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
if (window.Chart && DATA.mac && DATA.mac.length && $("c-mac")) new Chart($("c-mac"), {type: "bar",
  data: {labels: DATA.mac.map(r => r.n), datasets: [{data: DATA.mac.map(r => r.c), borderRadius: 8, hoverBackgroundColor: "#8B7CFF",
    backgroundColor: DATA.mac.map(r => (r.inv ? r.c <= 0 : r.c >= 0) ? "#2EE6A6" : "#FF6B8A")}]},
  options: {indexAxis: "y", maintainAspectRatio: false, animation: stagger, plugins: {legend: {display: false},
    tooltip: {callbacks: {label: c => (c.parsed.x >= 0 ? "+" : "") + c.parsed.x.toFixed(2) + " %" + (DATA.mac[c.dataIndex].inv ? " (růst je pro akcie špatný)" : "")}}},
    scales: {x: {ticks: {callback: v => v + " %"}}, y: {grid: {display: false}}}}});
if ($("cal-t") && DATA.cal) {
  let imp = "", day = ""; const dsel = $("cal-day");
  const days = [...new Set(DATA.cal.map(e => e.day))];
  dsel.innerHTML = '<option value="">Celý týden</option>' + days.map(d => `<option value="${d}">${DATA.cal.find(e => e.day === d).dn}</option>`).join("");
  const draw = () => { const rows = DATA.cal.filter(e => (!imp || e.impact === imp) && (!day || e.day === day));
    $("cal-t").innerHTML = rows.length ? "<div class='wrap'><table><thead><tr><th>Den</th><th>Čas</th><th>Událost</th><th>Dopad</th><th class='n'>Předpověď</th><th class='n'>Minule</th></tr></thead><tbody>" +
      rows.map(e => `<tr${e.ts < Date.now() ? ' style="opacity:.45"' : ""}><td>${e.dn}</td><td>${e.time}</td><th scope="row">${esc(e.title)}</th><td><span class="badge ${e.impact === "vysoký" ? "down" : "warn"}">${e.impact}</span></td><td class="n">${esc(e.forecast)}</td><td class="n">${esc(e.previous)}</td></tr>`).join("") + "</tbody></table></div>"
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
    rgb = "46,230,166" if r["chg"] >= 0 else "255,107,138"
    return (f'<a class="tile{mine}" target="_blank" rel="noopener" href="https://www.tradingview.com/chart/?symbol={escape(r["sym"])}" style="background:rgba({rgb},{a:.2f})"><b>{escape(r["sym"])}</b>'
            f'<span>{escape(r["name"])}</span><em>{pc(r["chg"])}</em></a>')


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
        items, ok = [], False
        for url in FF_URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    items += json.load(r)
                ok = True
            except Exception as e:
                print("Kalendář se nepodařilo stáhnout:", url, e)
        _FF.append(items if ok else None)
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
    items = list(root.iter("item")) or [e for e in root.iter() if e.tag.endswith("}entry")]
    for it in items:
        kid = lambda k: next((x for x in it if x.tag.split("}")[-1] == k), None)
        d = None
        for k in ("pubDate", "updated", "published", "date"):
            if kid(k) is not None and kid(k).text:
                d = kid(k).text.strip()
                break
        dt = None
        if d:
            try:
                dt = email.utils.parsedate_to_datetime(d)
            except Exception:
                try:
                    dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
                except Exception:
                    dt = None
        if dt is not None and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ln = kid("link")
        link = ((ln.text or ln.get("href") or "") if ln is not None else "").strip()
        t = kid("title")
        out.append((((t.text if t is not None else "") or "").strip(), link, dt))
    return out


def news_sentiment(cfg):
    """Zdarma: stáhne titulky z veřejných RSS kanálů a spočítá, kolik je pozitivních a negativních slov."""
    since = (datetime.now(PRAGUE) - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    srcs = dict(cfg["feeds"])
    for name, q in {**cfg.get("topics", {}), **cfg["people"]}.items():
        srcs[name if name.startswith("Téma") else name + " (zprávy o něm)"] = "https://news.google.com/rss/search?q=" + quote(q + " when:1d") + "&hl=en-US&gl=US&ceid=US:en"
    groups, bulls, bears, seen, allsc, allitems = [], [], [], set(), [], []
    for name, url in srcs.items():
        try:
            items = feed_items(url)
        except Exception as e:
            print("Zdroj zpráv nejde:", name, e)
            continue
        items = [(t, l, d) for t, l, d in items if t and (d is None or d >= since)][:30]
        if not items:
            continue
        sc = []
        for t, l, d in items:
            s = head_score(t)
            sc.append(s)
            if t not in seen:
                seen.add(t)
                allsc.append(s)
                allitems.append({"t": t, "l": l if l.startswith("http") else "#", "src": name, "s": s})
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
    return {"status": "ok", "n": len(allsc), "groups": groups, "bulls": bulls[:8], "bears": bears[:8], "all": allitems[:400],
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
            f'<li>Zpracováno {s["n"]} titulků od včerejších 15:00.</li></ul></div>{gauge_box(sc * 0.3, sc, 5, "skóre zpráv")}</section>'
            '<section><h2>Nálada podle zdroje (najeď myší)</h2><div class="chartbox"><canvas id="c-s"></canvas></div></section>'
            f'<section><div class="xcards"><div><h2>Nejpozitivnější titulky</h2><ul class="facts">{lst(s["bulls"])}</ul></div>'
            f'<div><h2>Nejnegativnější titulky</h2><ul class="facts">{lst(s["bears"])}</ul></div></div></section>' + foot)


KEYS = ["FOMC", "CPI", "Non-Farm", "NFP", "PCE", "GDP", "Interest Rate", "Funds Rate", "Powell", "Retail Sales", "ISM", "Unemployment Rate", "JOLTS", "PPI"]


def dny(n):
    return "den" if n == 1 else "dny" if 2 <= n <= 4 else "dní"


def fmt_in(ms):
    s = max(0, int((ms - datetime.now(PRAGUE).timestamp() * 1000) / 1000))
    d, h = s // 86400, s % 86400 // 3600
    return f"{d} d {h} h" if d else f"{h} h {s % 3600 // 60} min"


def event_row(e):
    cls = "down" if e["impact"] == "vysoký" else "warn"
    return (f'<tr><td>{e["dn"]}</td><td>{e["time"]}</td><th scope="row">{escape(e["title"])}</th>'
            f'<td><span class="badge {cls}">{e["impact"]}</span></td><td class="n">{escape(e["forecast"])}</td><td class="n">{escape(e["previous"])}</td></tr>')


def events_table(rows):
    return ('<div class="wrap"><table><thead><tr><th>Den</th><th>Čas</th><th>Událost</th><th>Dopad</th><th class="n">Předpověď</th><th class="n">Minule</th></tr></thead>'
            f'<tbody>{"".join(event_row(e) for e in rows)}</tbody></table></div>')


def day_risk(week):
    today = datetime.now(PRAGUE).strftime("%Y-%m-%d")
    td = [e for e in (week or []) if e["day"] == today]
    hi = [e for e in td if e["impact"] == "vysoký"]
    if hi:
        return "Vysoké riziko", "down", td, hi
    if td:
        return "Zvýšená pozornost", "warn", td, hi
    return "Klidný den", "up", td, hi


def _d(x):
    return x.date() if hasattr(x, "date") and callable(x.date) else x


def earnings(cfg):
    out, today = [], datetime.now(PRAGUE).date()
    for s in cfg.get("priority", []):
        try:
            c = yf.Ticker(s).calendar
            ed = c.get("Earnings Date") if isinstance(c, dict) else list(c.loc["Earnings Date"])
            ed = ed if isinstance(ed, (list, tuple)) else [ed]
            ds = sorted(d for d in map(_d, ed) if d >= today)
            if ds and (ds[0] - today).days <= 21:
                d = ds[0]
                out.append({"sym": s, "name": cfg["stocks"].get(s, s), "days": (d - today).days, "dn": DNY[d.weekday()] + f" {d.day}. {d.month}."})
        except Exception as e:
            print("Výsledky firmy nejdou:", s, e)
    return sorted(out, key=lambda e: e["days"])


def etile(e):
    n = e["days"]
    when = "dnes" if n == 0 else f"za {n} {dny(n)}"
    return (f'<div class="tile ev{" soon" if n <= 7 else ""}"><b>{escape(e["sym"])}</b><span>{escape(e["name"])}</span>'
            f'<em>{e["dn"]}</em><span>{when}</span></div>')


def hl(rows):
    return "".join(f'<li><a href="{escape(r["l"])}">{escape(r["t"])}</a> <span class="empty">({escape(r["src"])})</span></li>' for r in rows) or "<li>Nic.</li>"


def fundament_body(q, week, sent, cfg):
    now_ms = datetime.now(PRAGUE).timestamp() * 1000
    word, cls, td, hi = day_risk(week)
    up_ev = [e for e in (week or []) if e["ts"] > now_ms]
    g = {r["sym"]: r for r in q}
    facts = []
    if week is None:
        facts.append("Kalendář se nepodařilo stáhnout. Otevři Forex Factory.")
    else:
        facts.append(f"Dnes: {len(td)} důležitých zpráv z USA, z toho {len(hi)} s vysokým dopadem." if td
                     else "Dnes nejsou žádné důležité zprávy z USA (červené ani oranžové).")
        if up_ev:
            n = up_ev[0]
            facts.append(f"Nejbližší: {n['dn']} {n['time']}, {n['title']} (za {fmt_in(n['ts'])}).")
        fed = next((e for e in up_ev if re.search(r"FOMC|Funds Rate|Powell", e["title"])), None)
        if fed:
            facts.append(f"Fed: {fed['title']} je {fed['dn']} {fed['time']} (za {fmt_in(fed['ts'])}).")
    if "^TNX" in g and "DX-Y.NYB" in g:
        facts.append(f"Výnos 10letých dluhopisů je {num(g['^TNX']['last'])} % ({pc(g['^TNX']['chg'])}), dolarový index {pc(g['DX-Y.NYB']['chg'])}.")
    if sent.get("status") == "ok":
        facts.append(f"Nálada ve zprávách za 24 hodin: skóre {sgn(sent['overall']['score'])} z 5.")
    earn = earnings(cfg)
    wk = [e for e in earn if e["days"] <= 7]
    nxt = up_ev[0] if up_ev else None
    kp = ('<div class="kpis">'
          f'<div class="k"><b class="{cls}">{len(td)}</b><span>důležitých zpráv dnes ({len(hi)} s vysokým dopadem)</span></div>'
          f'<div class="k"><b>{fmt_in(nxt["ts"]) if nxt else "–"}</b><span>{escape(nxt["title"]) if nxt else "do další zprávy"}</span></div>'
          f'<div class="k"><b>{len(wk)}</b><span>tvých akcií má výsledky do 7 dní</span></div></div>')
    body = (f'<section class="hero"><div><p class="lede {cls}">{word}</p><ul class="facts">{"".join(f"<li>{escape(f)}</li>" for f in facts)}</ul></div></section>' + kp
            + cat_html(categories(q, week, sent)))
    if up_ev:
        body += ('<section><h2>Nejbližší důležité zprávy z USA (červené a oranžové)</h2>' + events_table(up_ev[:10]) +
                 '<p class="empty">Čas je pražský. Celý týden najdeš v záložce Kalendář, skutečné hodnoty po vydání zprávy na '
                 '<a href="https://www.forexfactory.com/calendar">Forex Factory</a>.</p></section>')
    body += ('<section><h2>Výsledky firem (earnings) u tvých akcií v příštích 3 týdnech</h2>' +
             (f'<div class="tiles">{"".join(etile(e) for e in earn)}</div><p class="empty">Oranžově jsou výsledky do 7 dní. Kolem výsledků bývají velké pohyby, proto je dobré o nich vědět předem.</p>'
              if earn else '<p class="empty">Žádné výsledky v příštích 3 týdnech, nebo se data nepodařilo stáhnout.</p>') + '</section>')
    if q:
        body += '<section><h2>Změna za poslední den (najeď myší)</h2><div class="chartbox" style="height:240px"><canvas id="c-mac"></canvas></div></section>'
    body += quote_table("Futures a makro (tabulka)", q, "Data se nepodařilo stáhnout.")
    if sent.get("status") == "ok":
        body += (f'<section><div class="xcards"><div><h2>Pozitivní titulky</h2><ul class="facts">{hl(sent["bulls"][:5])}</ul></div>'
                 f'<div><h2>Negativní titulky</h2><ul class="facts">{hl(sent["bears"][:5])}</ul></div></div></section>')
    return body + vysvetlivky() + WIDGETS


# ===== NOVÉ V VERZI 9: historie, RSI, scénáře zpráv, týdenní výhled, nové stránky =====
def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    r = 100 - 100 / (1 + up / dn)
    return float(r.dropna().iloc[-1])


def intraday_rsi():
    """RSI (síla pohybu) na grafech M5, M15 a H1 pro Nasdaq futures. Zdarma z Yahoo."""
    try:
        d = yf.download("NQ=F", period="5d", interval="5m", progress=False)["Close"].squeeze().dropna()
        out = {"M5": rsi(d), "M15": rsi(d.resample("15min").last().dropna()), "H1": rsi(d.resample("60min").last().dropna())}
        return {k: round(v, 1) for k, v in out.items()}
    except Exception as e:
        print("RSI se nepodařilo spočítat:", e)
        return None


def rsi_word(v):
    return ("Bullish", "up") if v >= 55 else ("Bearish", "down") if v <= 45 else ("Neutrální", "")


def hist_path():
    return ROOT / "docs" / "history.json"


def update_history(label, total):
    """Uloží dnešní bias a doplní skutečný pohyb Nasdaqu za minulé dny (pro stránku Přesnost)."""
    try:
        h = json.loads(hist_path().read_text(encoding="utf-8"))
    except Exception:
        h = []
    today = datetime.now(PRAGUE).strftime("%Y-%m-%d")
    h = [x for x in h if x.get("date") != today]
    h.append({"date": today, "label": label, "score": round(total, 1)})
    try:
        c = yf.download("^NDX", period="6mo", interval="1d", progress=False)["Close"].squeeze().dropna()
        ch = c.pct_change() * 100
        real = {i.strftime("%Y-%m-%d"): round(float(v), 2) for i, v in ch.dropna().items()}
        for x in h:
            if "real" not in x and x["date"] < today and x["date"] in real:
                x["real"] = real[x["date"]]
    except Exception as e:
        print("Skutečný pohyb Nasdaqu nejde stáhnout:", e)
    h = sorted(h, key=lambda x: x["date"])[-400:]
    hist_path().write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")
    return h


def hit(x):
    """1 = bias měl pravdu, 0 = neměl, None = ještě nevíme."""
    if "real" not in x:
        return None
    b = x["label"].lower()
    r = x["real"]
    if "bullish" in b:
        return 1 if r > 0.1 else 0
    if "bearish" in b:
        return 1 if r < -0.1 else 0
    return 1 if abs(r) < 0.5 else 0


def conf(comp, total):
    if not comp:
        return 0
    sg_ = 1 if total >= 0 else -1
    w = sum(c["w"] for c in comp)
    return round(sum(c["w"] for c in comp if c["s"] * sg_ > 0.05) / w * 100)


def numval(s):
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(s or ""))
    return float(m.group().replace(",", ".")) if m else None


RULES = [
    (r"CPI|PPI|PCE|Inflation|Price", "Inflace", "Čísla o cenách. Fed podle nich rozhoduje o sazbách.",
     ("Výš než předpověď", "Inflace je horší. Fed zůstane přísný, výnosy rostou.", "down"), ("Podle předpovědi", "Trh to čekal, reakce bývá malá.", ""),
     ("Níž než předpověď", "Inflace slábne. Naděje na nižší sazby, akcie často rostou.", "up"), 1),
    (r"Non-Farm|NFP|Employment|Unemployment|JOLTS|Jobless|Payroll|ADP", "Trh práce", "Kolik lidí pracuje. Silná práce může zvednout sazby.",
     ("Výš než předpověď", "Silná ekonomika, ale vyšší výnosy. Nasdaq často tlačí dolů.", "down"), ("Podle předpovědi", "Bez překvapení, klidnější reakce.", ""),
     ("Níž než předpověď", "Slabší práce. Naděje na nižší sazby, ale roste strach z recese.", "warn"), 1),
    (r"FOMC|Funds Rate|Powell|Fed|Rate Decision", "Fed a sazby", "Rozhodnutí o úrokových sazbách. Nejsilnější zpráva týdne.",
     ("Přísnější (hawkish)", "Sazby vyšší nebo delší. Nasdaq obvykle klesá.", "down"), ("Podle očekávání", "Pohyb přijde až při tiskové konferenci.", ""),
     ("Mírnější (dovish)", "Naděje na nižší sazby. Nasdaq obvykle roste.", "up"), 1),
    (r"GDP|Retail|ISM|PMI|Sales|Confidence|Sentiment|Housing|Durable", "Růst ekonomiky", "Jak se daří ekonomice a spotřebě.",
     ("Výš než předpověď", "Silná ekonomika. Pro akcie dobré, pokud nezvedne sazby.", "up"), ("Podle předpovědi", "Bez překvapení.", ""),
     ("Níž než předpověď", "Slabší ekonomika. Trh se může bát zpomalení.", "down"), 1),
]


DIRW = {"up": "bullish, hledej long", "down": "bearish, hledej short", "warn": "nejasné, počkej na reakci", "": "bez směru, nespěchej"}


def vysvetlivky():
    return ('<section><details open><summary><b>Jak číst zprávy: vysvětlení jednoduše</b></summary><ul class="facts" style="margin-top:12px">'
            '<li><b>Předpověď</b> je číslo, které čekají analytici. <b>Skutečnost</b> je číslo, které opravdu vyjde. Trh se hýbe podle překvapení, tedy podle rozdílu mezi nimi.</li>'
            '<li><b>Bullish</b> = čekám růst, hledám long. <b>Bearish</b> = čekám pokles, hledám short.</li>'
            '<li><b>Příklad inflace (CPI):</b> předpověď 3,0 %, vyjde 3,2 %. Ceny rostou rychleji, Fed zůstane přísný, takže je to <b>bearish</b>. Vyjde 2,8 % → <b>bullish</b>.</li>'
            '<li><b>Pozor:</b> vyšší číslo není vždy bullish. U inflace je vyšší horší. U růstu ekonomiky (GDP, ISM) je vyšší lepší. U trhu práce silné číslo zvedá výnosy, takže je to často bearish.</li>'
            '<li><b>Hawkish</b> (jestřábí) = Fed mluví přísně, sazby nechá vysoko nebo zvedne → <b>bearish</b> pro Nasdaq.</li>'
            '<li><b>Dovish</b> (holubičí) = Fed mluví mírně, sazby sníží nebo to naznačí → <b>bullish</b> pro Nasdaq.</li>'
            '<li><b>Moje pravidlo:</b> po velké zprávě počkám aspoň 5 minut. Long nebo short beru jen tehdy, když sedí i můj setup.</li></ul></details></section>')


def rule_for(title):
    for r in RULES:
        if re.search(r[0], title, re.I):
            return r
    return None


def predikce_body(week):
    vy = vysvetlivky()
    if not week:
        return '<section><div class="note">Kalendář se nepodařilo stáhnout. Otevři <a href="https://www.forexfactory.com/calendar">Forex Factory</a>.</div></section>'
    now_ms = datetime.now(PRAGUE).timestamp() * 1000
    ev = [e for e in week if e["ts"] > now_ms][:12]
    out = vy + ('<section><h2>Jak to funguje</h2><p class="hint">U každé důležité zprávy vidíš, co se obvykle stane, když vyjde lépe, stejně nebo hůř než předpověď. '
           'Jsou to pravidla z praxe, ne jistota. Procenta pravděpodobnosti zdarma nejdou spočítat poctivě, proto je tu nevymýšlím.</p></section>')
    cards = ""
    for e in ev:
        r = rule_for(e["title"])
        if not r:
            continue
        f, p = numval(e["forecast"]), numval(e["previous"])
        hint = ""
        if f is not None and p is not None:
            hint = ("Předpověď je <b>vyšší</b> než minule." if f > p else "Předpověď je <b>nižší</b> než minule." if f < p else "Předpověď je stejná jako minule.")
        sc = "".join(f'<div class="scen {c[2]}"><b>{escape(c[0])} · {DIRW.get(c[2], "")}</b><span>{escape(c[1])}</span></div>' for c in (r[3], r[4], r[5]))
        ex = ""
        if r[1] == "Fed a sazby":
            ex = "Příklad: Fed řekne, že sazby zůstanou vysoko (hawkish) → bearish, hledej short. Řekne, že sazby sníží (dovish) → bullish, hledej long."
        elif f is not None:
            u = (re.search(r"[%KMB]+$", str(e["forecast"]).strip()) or [""])[0]
            step = max(round(abs(f) * 0.07, 1), 0.1)
            fm = lambda v: str(round(v, 1)).replace(".", ",") + u
            ex = (f"Příklad: předpověď {escape(e['forecast'])}, vyjde {fm(f + step)} → {DIRW.get(r[3][2], '')}. "
                  f"Vyjde {fm(f - step)} → {DIRW.get(r[5][2], '')}.")
        cards += (f'<section><div class="evh"><span class="badge {"down" if e["impact"] == "vysoký" else "warn"}">{e["impact"]}</span> <b>{e["dn"]} {e["time"]}</b> · {escape(e["title"])} '
                  f'<em class="empty">({r[1]}, za {fmt_in(e["ts"])})</em></div><p class="empty">{r[2]} Předpověď: {escape(e["forecast"] or "–")}, minule: {escape(e["previous"] or "–")}. {hint}</p>'
                  f'<p class="exm">{ex}</p>'
                  f'<div class="scens">{sc}</div></section>')
    return out + (cards or '<section><p class="empty">Tento týden nejsou žádné zprávy, ke kterým umím scénáře.</p></section>')


def categories(q, week, sent):
    g = {r["sym"]: r["chg"] for r in q}
    now_ms = datetime.now(PRAGUE).timestamp() * 1000
    ups = [e for e in (week or []) if e["ts"] > now_ms]
    cats = []

    def cat(name, bull, bear, note):
        s = max(-2, min(2, len(bull) - len(bear)))
        cats.append({"n": name, "s": s, "bull": bull, "bear": bear, "note": note})
    for key, name, pattern, inv in (("inf", "Inflace", r"CPI|PPI|PCE|Inflation", True), ("job", "Trh práce", r"Non-Farm|NFP|Employment|Unemployment|JOLTS|Jobless|ADP", True),
                                    ("gr", "Růst ekonomiky", r"GDP|Retail|ISM|PMI|Sales", False)):
        bull, bear = [], []
        for e in ups:
            if not re.search(pattern, e["title"], re.I):
                continue
            f, p = numval(e["forecast"]), numval(e["previous"])
            if f is None or p is None or f == p:
                continue
            up = f > p
            text = f"{e['title']}: předpověď {e['forecast']} proti minulým {e['previous']}"
            (bear if (up and inv) or (not up and not inv) else bull).append(text)
        note = {"inf": "Vyšší inflace tlačí akcie dolů.", "job": "Silná práce zvedá výnosy, což Nasdaqu škodí.", "gr": "Silnější růst obvykle akciím pomáhá."}[key]
        cat(name, bull, bear, note)
    bull, bear = [], []
    if "^TNX" in g:
        (bear if g["^TNX"] > 0.5 else bull if g["^TNX"] < -0.5 else []).append(f"Výnos 10letých dluhopisů se změnil o {pc(g['^TNX'])}")
    if "DX-Y.NYB" in g:
        (bear if g["DX-Y.NYB"] > 0.3 else bull if g["DX-Y.NYB"] < -0.3 else []).append(f"Dolarový index se změnil o {pc(g['DX-Y.NYB'])}")
    fed = [e for e in ups if re.search(r"FOMC|Funds Rate|Powell", e["title"])]
    if fed:
        bear.append(f"Blíží se Fed: {fed[0]['title']} ({fed[0]['dn']} {fed[0]['time']}), nejistota roste")
    cat("Fed, sazby a dluhopisy", bull, bear, "Vyšší výnosy a silný dolar bývají pro Nasdaq špatné.")
    bull, bear = [], []
    if "^VIX" in g:
        (bear if g["^VIX"] > 5 else bull if g["^VIX"] < -5 else []).append(f"VIX (strach) se změnil o {pc(g['^VIX'])}")
    if sent.get("status") == "ok":
        sc = sent["overall"]["score"]
        (bull if sc > 0.8 else bear if sc < -0.8 else []).append(f"Titulky zpráv mají skóre {sgn(sc)} z 5")
    cat("Nálada a zprávy", bull, bear, "Strach a negativní titulky obvykle předcházejí poklesům.")
    return cats


def cat_html(cats):
    rows = ""
    for c in cats:
        cls = "up" if c["s"] > 0 else "down" if c["s"] < 0 else ""
        word = {2: "Silně bullish", 1: "Bullish", 0: "Neutrální", -1: "Bearish", -2: "Silně bearish"}[c["s"]]
        li = lambda a, k: "".join(f'<li class="{k}">{escape(x)}</li>' for x in a)
        rows += (f'<div class="cat"><div class="cath"><b>{escape(c["n"])}</b><span class="badge {cls or "warn"}">{word}</span></div>'
                 f'<div class="bar wide"><span></span><div class="track {cls or "up"}"><i style="width:{abs(c["s"]) / 2 * 100:.0f}%"></i></div><b></b></div>'
                 f'<div class="det"><ul class="facts">{li(c["bull"], "up")}{li(c["bear"], "down")}</ul><p class="empty">{escape(c["note"])}</p></div></div>')
    return ('<section><h2>Hodnocení podle oblastí</h2><p class="hint">Každá oblast má zelené (bullish) a červené (bearish) faktory. Přepni si krátký nebo podrobný pohled.</p>'
            '<div class="pills" id="vtabs"><button data-v="s">Stručně</button><button class="pill-off" data-v="d">Detailně</button></div>'
            f'<div id="cats" class="brief">{rows}</div></section>')


FUND_JS = """
document.querySelectorAll("#vtabs button").forEach(b => b.onclick = () => { $("cats").className = b.dataset.v === "d" ? "full" : "brief";
  document.querySelectorAll("#vtabs button").forEach(x => x.classList.toggle("pill-off", x !== b)); });
"""


def zpravy_body(q, sent):
    g = {r["sym"]: r for r in q}
    cards = ""
    for sym, name, inv in (("^VIX", "VIX (strach)", True), ("DX-Y.NYB", "Dolarový index", True), ("^TNX", "Výnos 10letých dluhopisů", True), ("NQ=F", "Nasdaq futures", False)):
        if sym in g:
            r = g[sym]
            good = (r["chg"] <= 0) if inv else (r["chg"] >= 0)
            cards += f'<div class="k"><b class="{"up" if good else "down"}">{num(r["last"])}</b><span>{name}: {pc(r["chg"])}</span></div>'
    hero = ""
    if sent.get("status") == "ok":
        o = sent["overall"]
        sc = float(o["score"])
        word = {"bullish": "Bullish", "bearish": "Bearish"}.get(o["bias"], "Neutrální")
        cls = "up" if o["bias"] == "bullish" else "down" if o["bias"] == "bearish" else ""
        its = sent.get("all") or []
        pos, neg = sum(1 for x in its if x["s"] > 0), sum(1 for x in its if x["s"] < 0)
        hero = (f'<section class="hero"><div><p class="empty" style="margin:0 0 8px">Nálada zpráv za posledních 24 hodin</p><p class="lede {cls}">{word}</p>'
                f'<ul class="facts"><li>Zpracováno {sent["n"]} titulků z {len(sent["groups"])} zdrojů.</li><li>Pozitivních {pos}, negativních {neg}.</li>'
                f'<li>Skóre {sgn(sc)} z 5 (od −5 do +5). Plus znamená převahu pozitivních zpráv.</li></ul></div>{gauge_box(sc * 0.3, sc, 5, "skóre zpráv")}</section>')
    body = f'<section><h2>Puls trhu</h2><div class="kpis">{cards}</div><p class="hint">Zeleně je to, co je pro akcie dobré. U strachu, dolaru a výnosů je dobré pokles.</p></section>' if cards else ""
    if sent.get("status") != "ok":
        return body + '<section><div class="note">Zprávy se dnes nepodařilo stáhnout.</div></section>'
    return hero + body + ('<section><h2>Všechny zprávy za posledních 24 hodin</h2><div class="pills"><button data-f="">Všechny</button><button class="pill-off" data-f="1">Pozitivní</button>'
                   '<button class="pill-off" data-f="-1">Negativní</button></div><label style="max-width:300px;margin-top:10px">Zdroj<select id="nsrc"></select></label>'
                   '<ul class="facts" id="nlist" style="margin-top:12px"></ul></section>')


NEWS_JS = """
if ($("nlist")) { let f = "", src = ""; const srcs = [...new Set(DATA.news.map(n => n.src))];
  $("nsrc").innerHTML = '<option value="">Všechny zdroje</option>' + srcs.map(s => `<option>${esc(s)}</option>`).join("");
  const draw = () => { const rows = DATA.news.filter(n => (!f || Math.sign(n.s) === +f) && (!src || n.src === src));
    $("nlist").innerHTML = rows.length ? rows.map(n => `<li><span class="badge ${n.s > 0 ? "up" : n.s < 0 ? "down" : "warn"}">${n.s > 0 ? "pozitivní" : n.s < 0 ? "negativní" : "neutrální"}</span> <a href="${esc(n.l)}">${esc(n.t)}</a> <span class="empty">(${esc(n.src)})</span></li>`).join("") : "<li>Nic.</li>"; };
  document.querySelectorAll("[data-f]").forEach(b => b.onclick = () => { f = b.dataset.f; document.querySelectorAll("[data-f]").forEach(x => x.classList.toggle("pill-off", x !== b)); draw(); });
  $("nsrc").onchange = () => { src = $("nsrc").value; draw(); }; draw(); }
"""


def bias_body(label, total, comp, rs, q, week):
    cls = "up" if "bullish" in label.lower() else "down" if "bearish" in label.lower() else ""
    c = conf(comp, total)
    oe = (f'<section class="hero"><div><p class="empty" style="margin:0 0 8px">Open Bias Engine, výpočet z dat před otevřením</p><p class="lede {cls}">{escape(label)}</p>'
          f'<ul class="facts"><li>Skóre {sgn(total)} z 10.</li><li>Shoda ukazatelů: {c} %. Čím víc ukazatelů míří stejným směrem, tím je bias spolehlivější.</li></ul></div>{gauge_box(total * 0.15, total, 10, "skóre biasu")}</section>')
    if rs:
        rows = "".join(f'<div class="k"><b class="{rsi_word(v)[1]}">{v}</b><span>RSI {k}: {rsi_word(v)[0]}</span></div>' for k, v in rs.items())
        avg = sum(rs.values()) / len(rs)
        rb = (f'<section><h2>Denní bias podle RSI</h2><p class="hint">RSI měří sílu pohybu od 0 do 100. Nad 55 převažují kupující, pod 45 prodávající.</p><div class="kpis">{rows}</div>'
              f'<p class="lede {rsi_word(avg)[1]}" style="font-size:22px">Celkově: {rsi_word(avg)[0]}</p></section>')
    else:
        rb = '<section><div class="note">RSI se dnes nepodařilo spočítat (Yahoo neposlal data po minutách).</div></section>'
    nxt = ('<section class="hero"><div><p class="empty" style="margin:0 0 8px">Nejbližší velká zpráva z USA</p><p class="lede" id="cd">–</p><ul class="facts"><li id="cdt">Načítám…</li></ul></div></section>')
    return oe + rb + nxt + comp_html(comp)


BIAS_JS = """
if ($("cd") && DATA.cal && !$("cal-t")) { const tick = () => { const n = DATA.cal.find(e => e.ts > Date.now() && e.impact === "vysoký") || DATA.cal.find(e => e.ts > Date.now());
  if (!n) { $("cd").textContent = "–"; $("cdt").textContent = "Tento týden už žádná další zpráva."; return; }
  let s = Math.floor((n.ts - Date.now()) / 1000); const d = Math.floor(s / 86400); s %= 86400;
  $("cd").textContent = (d ? d + " d " : "") + [Math.floor(s / 3600), Math.floor(s % 3600 / 60), s % 60].map(x => String(x).padStart(2, "0")).join(":");
  $("cdt").textContent = "Do zprávy: " + n.dn + " " + n.time + ", " + n.title; }; tick(); setInterval(tick, 1000); }
"""


def weekly():
    try:
        c = yf.download("^NDX", period="3mo", interval="1d", progress=False)["Close"].squeeze().dropna()
        mv = c.pct_change().abs().dropna().tail(20).mean() * 100
        last = float(c.iloc[-1])
        wk = float(c.iloc[-1] / c.iloc[-6] - 1) * 100
        exp = float(mv) * 5 ** 0.5
        return {"last": last, "daily": float(mv), "wk": wk, "exp": exp, "lo": last * (1 - exp / 100), "hi": last * (1 + exp / 100)}
    except Exception as e:
        print("Týdenní data nejdou:", e)
        return None


def tyden_body(w, week, label):
    body = ""
    if w:
        tone_ = "up" if w["wk"] > 1 else "down" if w["wk"] < -1 else ""
        body += (f'<section class="hero"><div><p class="empty" style="margin:0 0 8px">Týdenní výhled pro Nasdaq 100</p><p class="lede {tone_}">{"Bullish týden" if w["wk"] > 1 else "Bearish týden" if w["wk"] < -1 else "Vyrovnaný týden"}</p>'
                 f'<ul class="facts"><li>Za posledních 5 obchodních dní se index změnil o {pc(w["wk"])}.</li><li>Průměrný denní pohyb je {num(w["daily"])} %.</li>'
                 f'<li>Dnešní denní bias: {escape(label)}.</li></ul></div></section>'
                 f'<div class="kpis"><div class="k"><b>{num(w["lo"], 0)}</b><span>běžný týdenní pohyb dolů</span></div><div class="k"><b>{num(w["last"], 0)}</b><span>poslední zavírací cena</span></div>'
                 f'<div class="k"><b>{num(w["hi"], 0)}</b><span>běžný týdenní pohyb nahoru</span></div></div>'
                 '<p class="hint">Rozmezí je jen odhad podle běžných pohybů za poslední měsíc (denní pohyb krát odmocnina z pěti dní). Není to předpověď.</p>')
    else:
        body += '<section><div class="note">Týdenní data se nepodařilo stáhnout.</div></section>'
    if week:
        body += ('<section><h2>Kolik důležitých zpráv je který den</h2><div class="chartbox" style="height:240px"><canvas id="c-wk"></canvas></div></section>'
                 '<section><h2>Zprávy s vysokým dopadem tento týden</h2>' + events_table([e for e in week if e["impact"] == "vysoký"][:15]) + '</section>')
    return body


WK_JS = """
if (window.Chart && DATA.wk && $("c-wk")) new Chart($("c-wk"), {type: "bar", data: {labels: DATA.wk.d, datasets: [{label: "Vysoký dopad", data: DATA.wk.h, backgroundColor: "#FF6B8A", borderRadius: 6}, {label: "Střední dopad", data: DATA.wk.m, backgroundColor: "#FFB454", borderRadius: 6}]},
  options: {maintainAspectRatio: false, plugins: {legend: {position: "bottom"}}, scales: {x: {stacked: true, grid: {display: false}}, y: {stacked: true, ticks: {precision: 0}}}}});
"""


def presnost_body(h):
    done = [x for x in h if hit(x) is not None]
    if not done:
        return ('<section class="hero"><div><p class="lede">Zatím málo dat</p><ul class="facts"><li>Robot si každý den uloží svůj bias. Druhý den dopíše, jak se Nasdaq opravdu pohnul.</li>'
                '<li>První výsledky uvidíš po dvou dnech provozu. Do té doby je tu prázdno, to je v pořádku.</li></ul></div></section>')
    ok = sum(hit(x) for x in done)
    last = done[-20:]
    ok20 = sum(hit(x) for x in last)
    rows = "".join(f'<tr><td>{x["date"]}</td><td>{escape(x["label"])}</td><td class="n">{sgn(x["score"])}</td><td class="n {("up" if x["real"] >= 0 else "down")}">{pc(x["real"])}</td>'
                   f'<td><span class="badge {"up" if hit(x) else "down"}">{"trefil" if hit(x) else "netrefil"}</span></td></tr>' for x in reversed(done[-30:]))
    return (f'<section class="hero"><div><p class="empty" style="margin:0 0 8px">Jak často měl denní bias pravdu</p><p class="lede {"up" if ok / len(done) >= .55 else "warn"}">{ok / len(done) * 100:.0f} %</p>'
            f'<ul class="facts"><li>Celkem {len(done)} dní, trefil {ok}×.</li><li>Posledních {len(last)} dní: {ok20 / len(last) * 100:.0f} %.</li>'
            '<li>Neutrální bias se počítá jako trefa, když byl pohyb menší než 0,5 %.</li></ul></div></section>'
            '<section><h2>Bias a skutečný pohyb (najeď myší)</h2><div class="chartbox"><canvas id="c-acc"></canvas></div></section>'
            f'<section><h2>Posledních 30 dní</h2><div class="wrap"><table><thead><tr><th>Den</th><th>Bias</th><th class="n">Skóre</th><th class="n">Skutečný pohyb</th><th>Výsledek</th></tr></thead><tbody>{rows}</tbody></table></div>'
            '<p class="empty">Skutečný pohyb je změna indexu Nasdaq 100 za celý den. Nejde o výsledek tvých obchodů.</p></section>')


ACC_JS = """
if (window.Chart && DATA.hist && $("c-acc")) { const h = DATA.hist.filter(x => x.real != null);
  new Chart($("c-acc"), {type: "bar", data: {labels: h.map(x => x.date), datasets: [{type: "line", label: "Skóre biasu", data: h.map(x => x.score), borderColor: "#8B7CFF", yAxisID: "y2", tension: .3, pointRadius: 3},
    {label: "Skutečný pohyb (%)", data: h.map(x => x.real), borderRadius: 6, backgroundColor: h.map(x => x.real >= 0 ? "#2EE6A6" : "#FF6B8A")}]},
    options: {maintainAspectRatio: false, interaction: {mode: "index", intersect: false}, plugins: {legend: {position: "bottom"}}, scales: {y: {ticks: {callback: v => v + " %"}}, y2: {position: "right", min: -10, max: 10, grid: {display: false}}, x: {grid: {display: false}, ticks: {maxTicksLimit: 8}}}}}); }
"""


def roadmap_body():
    st_ = [("Etapa 1: Základy", ["Umím vysvětlit, co je long, short, stop loss a take profit", "Znám časy: burza otevírá v 15:30 pražského času", "Každé ráno projdu Přehled, Fundament a Kalendář", "Vím, co je VIX, dolar a výnosy a jak hýbou Nasdaqem"]),
           ("Etapa 2: Deník a disciplína", ["Zapisuji každý obchod do deníku i se screenshotem", "Každý obchod má stop loss před vstupem", "Dodržel jsem plán aspoň 20 obchodů po sobě", "Znám své tři nejčastější chyby"]),
           ("Etapa 3: Mikro kontrakty", ["Riskuji nejvýš 1 % účtu na obchod", "Používám kalkulačku v Plánu dne před každým obchodem", "Mám 30 dní deníku s kladným očekáváním (expectancy)", "Vyhnu se obchodování před velkými zprávami"]),
           ("Etapa 4: Trpělivost a konzistence", ["Dokážu celý den neobchodovat, když nevidím svůj setup", "Splnil jsem 10 dní za sebou „Dnes jsem se ovládl“", "Mám jasně popsanou jednu strategii s pravidly", "Měsíc skončil v plusu při dodržení pravidel"]),
           ("Etapa 5: Růst", ["Zvyšuji počet kontraktů jen po třech dobrých měsících", "Dělám měsíční rozbor deníku (záložka Analýza)", "Mám záložní plán na špatné období (pauza, menší velikost)"])]
    out = '<section><p class="hint">Zaškrtávej, co už umíš. Ukládá se v tomto prohlížeči. Postupuj po etapách, nic nepřeskakuj.</p><p id="rprog" class="lede" style="font-size:24px"></p></section>'
    n = 0
    for t, items in st_:
        out += f'<section><h2>{t}</h2><div class="chips">' + "".join(f'<label class="chip"><input type="checkbox" data-r="{n + i}">{escape(x)}</label>' for i, x in enumerate(items)) + '</div></section>'
        n += len(items)
    return out


ROAD_JS = """
if ($("rprog")) { let R = []; try { R = JSON.parse(localStorage.getItem("roadmap")) || []; } catch (e) {}
  const all = document.querySelectorAll("[data-r]"), draw = () => { $("rprog").textContent = "Splněno " + R.length + " z " + all.length; };
  all.forEach(c => { c.checked = R.includes(+c.dataset.r); c.onchange = () => { R = R.filter(x => x !== +c.dataset.r); if (c.checked) R.push(+c.dataset.r); try { localStorage.setItem("roadmap", JSON.stringify(R)); } catch (e) {} draw(); }; }); draw(); }
"""


def prirucka_body():
    G = [("Long", "Nakupuješ a chceš, aby cena vzrostla."), ("Short", "Prodáváš a chceš, aby cena klesla."), ("Stop loss", "Cena, kde obchod uzavřeš se ztrátou, aby ses nezničil. Zadáváš ho vždy před vstupem."),
         ("Take profit (TP)", "Cena, kde obchod uzavřeš se ziskem. TP1 je 1:2, TP2 je 1:2,5."), ("R:R (poměr zisku k riziku)", "Kolikrát víc můžeš vydělat, než kolik riskuješ. 1:2 znamená riskuješ 1, chceš vydělat 2."),
         ("Mikro kontrakt (MNQ)", "Malá verze Nasdaq futures. Jeden bod je 2 $. Hodí se pro začátek."), ("Pre-market", "Obchodování před oficiálním otevřením burzy. Ukazuje náladu před startem."),
         ("Bias", "Tvůj názor na směr dne: bullish (růst), bearish (pokles), neutrální."), ("Bullish", "Čekáš, že cena poroste. Hledáš long (nákup)."), ("Bearish", "Čekáš, že cena klesne. Hledáš short (prodej)."), ("Hawkish", "Fed mluví přísně: sazby nechá vysoko nebo je zvedne. Pro Nasdaq je to bearish."), ("Dovish", "Fed mluví mírně: sazby sníží nebo naznačí snížení. Pro Nasdaq je to bullish."), ("Překvapení", "Rozdíl mezi předpovědí a skutečným číslem. Trh se hýbe hlavně podle něj."),
         ("VIX", "Index strachu. Když roste, investoři se bojí a akcie často klesají."), ("Dolarový index (DXY)", "Síla dolaru. Silný dolar bývá pro akcie spíš špatný."),
         ("Výnos 10letých dluhopisů", "Úrok z amerického dluhu. Vyšší výnos tlačí na technologické akcie."), ("Fed a FOMC", "Americká centrální banka a její výbor, který rozhoduje o úrokových sazbách."),
         ("CPI", "Inflace, tedy jak rostou ceny. Velmi sledované číslo."), ("NFP (Non-Farm Payrolls)", "Kolik nových pracovních míst vzniklo. Vychází první pátek v měsíci."),
         ("RSI", "Číslo 0 až 100 o síle pohybu. Nad 70 bývá přehřátý trh, pod 30 vyprodaný."), ("Profit factor", "Součet zisků dělený součtem ztrát. Nad 1,5 je dobré."),
         ("Expectancy", "Kolik průměrně vyděláš nebo prodělaš na jednom obchodu."), ("Drawdown", "Největší propad od vrcholu tvého účtu."), ("FOMO", "Strach, že ti něco uteče. Vede k pozdním a špatným vstupům.")]
    d = "".join(f'<div class="gl"><b>{escape(a)}</b><span>{escape(b)}</span></div>' for a, b in G)
    return ('<section><h2>Jak používat cockpit každé ráno</h2><ol class="steps"><li>Mindset: pusť meditaci a chvíli dýchej.</li><li>Přehled: přečti si bias a skóre.</li><li>Fundament a Kalendář: zjisti, kdy jsou velké zprávy.</li>'
            '<li>Premarket: podívej se na své akcie.</li><li>Plán dne: vyplň limity a checklist, spočítej kontrakty.</li><li>Po obchodování zapiš obchod do Deníku.</li></ol></section>'
            f'<section><h2>Slovníček</h2><div class="glos">{d}</div></section>'
            '<section><h2>Rozbor v Deníku</h2><p>Hodnocení v záložce Analýza počítají pravidla přímo v prohlížeči. Hlubší rozbor získáš tlačítkem „Zkopírovat data pro Claude“ a vložením do chatu.</p></section>')


def nastaveni_body():
    return ('<section><h2>Moje výchozí hodnoty</h2><p class="hint">Používá je Plán dne (kalkulačka) a Deník. Ukládá se v tomto prohlížeči.</p><div class="grid">'
            '<label>Velikost účtu ($)<input id="s-acc" type="number" value="50000"></label><label>Riziko na obchod (%)<input id="s-risk" type="number" step="any" value="1"></label>'
            '<label>Výchozí vzdálenost stopu (body)<input id="s-stop" type="number" step="any" value="35"></label></div><div class="row" style="margin-top:12px"><button id="s-save">Uložit</button><span id="s-msg" class="empty"></span></div></section>'
            '<section><h2>Jak se mění akcie a zdroje</h2><p>Seznam akcií, zdrojů zpráv a účtů na X je v souboru <b>briefing_v9.py</b> nahoře v části CONFIG. Uprav ho na GitHubu tužkou a ulož (Commit changes).</p></section>')


SET_JS = """
if ($("s-save")) { let S = {}; try { S = JSON.parse(localStorage.getItem("settings")) || {}; } catch (e) {}
  $("s-acc").value = S.acc || 50000; $("s-risk").value = S.risk || 1; $("s-stop").value = S.stop || 35;
  $("s-save").onclick = () => { S = {acc: +$("s-acc").value, risk: +$("s-risk").value, stop: +$("s-stop").value};
    try { localStorage.setItem("settings", JSON.stringify(S)); localStorage.setItem("defStop35", JSON.stringify(String(S.stop))); $("s-msg").textContent = "Uloženo."; } catch (e) { $("s-msg").textContent = "Uložení se nepovedlo."; } }; }
"""


PAT_LINES = ["Stačí si počkat na ten můj obchod.", "Trh nezajímá, co chci. Proti ničemu nebojuji, jen čekám.", "Žádné emoce, pouze trpělivost.",
             "Moje práce není být každý den aktivní a vynucovat nové obchody.", "Moje práce je čekat na příležitosti, kdy vím, že jsem silný.",
             "Den, kdy sedím u grafu, nic neobchoduju a umím se ovládnout, je moje výhra.", "Trpělivé čekání ukáže, jestli na to mám.", "Snažím se být nejlepší verzí sebe sama.",
             "Vím, že na to mám. Když vše dodržím, stane se to.", "Když tomu dám všechno na maximum, přijde to.", "Nemám FOMO. Další příležitost přijde vždycky.",
             "Nemám revenge. Po ztrátě vstanu od grafu.", "Z malých timeframeů nevstupuji. Počkám si na větší.", "Nejdřív větší timeframe pro směr, potom menší pro vstup.",
             "Neobchoduji, abych něco dokázal, ale proto, že vidím svůj setup.", "Jeden dobrý obchod je víc než deset nucených.", "Čekání je také pozice.",
             "Nejlepší obchod dne je často ten, který jsem neudělal.", "Dodržet plán je úspěch, i když obchod skončí ztrátou.", "Trh tu bude i zítra."]


def trpelivost_body():
    return ('<section class="hero"><div><p class="empty" style="margin:0 0 8px">Věta dne</p><p class="lede" id="pday" style="font-size:clamp(24px,3.6vw,38px)"></p>'
            '<div class="xptop"><b id="lvl"></b><span class="empty" id="xptxt"></span></div><div class="xpbar"><i id="xpfill"></i></div></div>'
            '<div class="ringbox"><div class="ring" id="qring"><b id="qpct">0 %</b></div><span class="empty">denní úkoly</span></div></section>'
            '<div class="pills" id="ptabs"><button data-t="dnes">Dnes</button><button class="pill-off" data-t="nutkani">Nutkání obchodovat</button>'
            '<button class="pill-off" data-t="trenink">Zkouška klidné ruky</button><button class="pill-off" data-t="kviz">Kvíz</button><button class="pill-off" data-t="odznaky">Odznaky</button></div>'
            # ---- DNES
            '<div id="tab-dnes">'
            '<section><h2>Denní úkoly</h2><p class="hint">Za každý splněný úkol dostaneš body. Některé se odškrtnou samy, jak plníš trénink.</p><div class="chips" id="pquests"></div></section>'
            '<section><h2>Jak dopadl dnešek?</h2><p class="hint">Zapiš to pravdivě. Vyber jedno. Počítá se do série, kalendáře a bodů.</p>'
            '<div class="pills" id="pst"><button data-s="wait">Seděl jsem u grafu, neobchodoval a ovládl se (+30 bodů)</button><button class="pill-off" data-s="setup">Obchodoval jsem jen svůj setup (+20 bodů)</button>'
            '<button class="pill-off" data-s="broke">Porušil jsem pravidla</button></div><div class="kpis" id="pk"></div></section>'
            '<section><h2>Posledních 28 dní</h2><div class="cal" id="pcal"></div><p class="empty">Zelená = ovládl jsem se. Červená = porušil jsem pravidla. Prázdné = nezapsáno.</p></section>'
            '<section><h2>Než vstoupím: 6 otázek</h2><p class="hint">Odškrtni všechny otázky, na které odpovídáš ano. Obchod bereš jen když jsou odškrtnuté všechny.</p><div class="chips" id="pq"></div><p id="pqres" class="lede" style="font-size:22px;margin-top:12px"></p></section>'
            '<section><h2>Moje pravidla trpělivosti</h2><ol class="steps" id="plist"></ol></section>'
            '<section><h2>Nenutím obchody</h2><ul class="facts"><li>Moje práce není být každý den aktivní a stále vynucovat nové obchody.</li>'
            '<li>Počkám si na to, co mám a kde jsem silný. Trh nezajímá, co chci, takže proti ničemu nebojuji, jen čekám.</li>'
            '<li>Žádné emoce, pouze trpělivost. Vím, že na to mám a že když vše dodržím, stane se to. Když tomu dám všechno na maximum, přijde to.</li></ul></section>'
            '<section><h2>Proti FOMO a revenge: vždy si počkat na větší timeframe</h2><ul class="facts"><li>Směr určuji na větším grafu (denní, 4 hodiny, hodina). Vstup hledám až na menším (15 minut, 5 minut).</li>'
            '<li>Z grafu 1 minuta nebo 5 minut sám směr neurčuji. Malý graf je šum.</li><li>Po ztrátě vstanu od grafu aspoň na 15 minut. Žádný vstup „na odpuštění“.</li>'
            '<li>Když mi něco ujelo, nehoním to. Počkám na návrat k úrovni nebo na další setup.</li><li>Když cítím nutkání „něco udělat“, zmáčknu tlačítko v záložce Nutkání obchodovat.</li></ul></section>'
            '<section><h2>Večerní zápis</h2><label>Co jsem dnes udělal pro svou trpělivost?<textarea id="pnote" rows="3"></textarea></label><p class="empty">Ukládá se v tomto prohlížeči.</p></section></div>'
            # ---- NUTKANI
            '<div id="tab-nutkani" hidden><section><h2>Cítím nutkání obchodovat</h2><p class="hint">Zmáčkni tlačítko dřív, než klikneš na jakýkoli obchod. Zabere to jednu minutu. Za každé odolané nutkání dostaneš 5 bodů.</p>'
            '<div class="kpis" id="ik"></div><div class="center2"><button class="bigbtn" id="ibtn">Cítím nutkání obchodovat</button></div>'
            '<div id="iflow" hidden><div class="orb2" id="iorb"></div><p class="lede" id="itime" style="text-align:center;font-size:44px"></p><p class="empty" id="iph" style="text-align:center"></p>'
            '<h3 style="margin-top:14px">Mezitím odpověz sám sobě:</h3><div class="chips" id="iq"></div>'
            '<div class="center2" style="margin-top:16px"><button id="ino" disabled>Obchod nedělám, odolal jsem</button><button class="ghost" id="iyes" disabled>Mám platný setup a jdu podle plánu</button></div>'
            '<p class="empty" id="ihint" style="text-align:center">Tlačítka se odemknou po konci minuty.</p></div></section></div>'
            # ---- TRENINK
            '<div id="tab-trenink" hidden><section><h2>Zkouška klidné ruky</h2><p class="hint">Drž tlačítko stisknuté, dokud se kruh nezaplní. Když pustíš dřív, začínáš znovu. Je to trénink čekání, bez emocí a bez klikání.</p>'
            '<div class="pills" id="htarget"><button data-h="15">15 sekund</button><button class="pill-off" data-h="30">30 sekund</button><button class="pill-off" data-h="60">60 sekund</button></div>'
            '<div class="center2"><div class="holdwrap"><div class="ring big" id="hring"></div><button class="holdbtn" id="hold">Drž</button></div></div>'
            '<p class="lede" id="htxt" style="text-align:center;font-size:26px;margin-top:14px">Zmáčkni a drž</p><p class="empty" id="hbest" style="text-align:center"></p></section></div>'
            # ---- KVIZ
            '<div id="tab-kviz" hidden><section><h2>Co uděláš? Kvíz trpělivosti</h2><p class="hint">Šest situací, které zažívá každý trader. Vyber, co uděláš. Za nejlepší výsledek dostaneš 10 bodů za správnou odpověď.</p>'
            '<div id="qz"></div></section></div>'
            # ---- ODZNAKY
            '<div id="tab-odznaky" hidden><section><h2>Odznaky</h2><p class="hint">Odemykají se samy, jak trénuješ. Zešedlé ještě nemáš.</p><div class="badges" id="pbadges"></div></section></div>')


PAT_JS = r"""
if ($("pday")) {
  const L = DATA.pat, g = k => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } }, st = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const tk = (off = 0) => { const d = new Date(Date.now() - off * 86400000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
  $("pday").textContent = L[Math.floor(Date.now() / 86400000) % L.length];
  $("plist").innerHTML = L.map(x => `<li>${x}</li>`).join("");
  const today = tk();
  function burst(msg) { for (let i = 0; i < 26; i++) { const s = document.createElement("span"); s.className = "conf"; s.textContent = ["✨", "🎯", "💪", "🧘", "⭐"][i % 5]; s.style.left = (10 + Math.random() * 80) + "vw"; s.style.animationDelay = (Math.random() * .4) + "s"; document.body.appendChild(s); setTimeout(() => s.remove(), 2200); }
    if (msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 2600); } }
  // ---- tabs
  document.querySelectorAll("#ptabs button").forEach(b => b.onclick = () => { document.querySelectorAll("#ptabs button").forEach(x => x.classList.toggle("pill-off", x !== b));
    ["dnes", "nutkani", "trenink", "kviz", "odznaky"].forEach(k => $("tab-" + k).hidden = k !== b.dataset.t); if (b.dataset.t === "odznaky") badges(); });
  // ---- points, level, streak
  function streak() { let n = 0; for (let i = 0; i < 400; i++) { const v = g("pstat:" + tk(i)); if (v && v !== "broke") n++; else if (i > 0 || v === "broke") break; } return n; }
  function xp() { let x = 0; for (let i = 0; i < 400; i++) { const d = tk(i), v = g("pstat:" + d); if (v === "wait") x += 30; else if (v === "setup") x += 20;
      const im = (g("pimp") || {})[d]; if (im) x += im.r * 5; const q = g("pquest:" + d); if (q) x += q.length * 5; }
    return x + (g("pquizbest") || 0) * 10 + (g("pholdbest") || 0); }
  const LV = [[0, "Nováček"], [100, "Pozorovatel"], [300, "Lovec setupů"], [700, "Sniper"], [1500, "Mistr trpělivosti"]];
  function level() { const x = xp(); let i = 0; LV.forEach((l, k) => { if (x >= l[0]) i = k; }); const nx = LV[i + 1];
    $("lvl").textContent = "Úroveň " + (i + 1) + ": " + LV[i][1]; $("xptxt").textContent = x + " bodů" + (nx ? ", do další úrovně chybí " + (nx[0] - x) : ", nejvyšší úroveň");
    $("xpfill").style.width = (nx ? (x - LV[i][0]) / (nx[0] - LV[i][0]) * 100 : 100) + "%"; }
  // ---- daily quests
  const QS = ["Přečetl jsem větu dne a pravidla", "Prošel jsem 6 otázek před vstupem (všechny ano)", "Dal jsem zkoušku klidné ruky (15 s)", "Zapsal jsem večerní poznámku", "Dnes nevstupuji z grafu 1 nebo 5 minut"];
  const qk = "pquest:" + today; let QD = g(qk) || [];
  function quest(i, on = true) { QD = QD.filter(x => x !== i); if (on) QD.push(i); st(qk, QD); drawQ(); level(); }
  function drawQ() { $("pquests").innerHTML = QS.map((t, i) => `<label class="chip"><input type="checkbox" data-qi="${i}" ${QD.includes(i) ? "checked" : ""}>${t}</label>`).join("");
    document.querySelectorAll("[data-qi]").forEach(c => c.onchange = () => quest(+c.dataset.qi, c.checked));
    const p = Math.round(QD.length / QS.length * 100); $("qpct").textContent = p + " %"; $("qring").style.background = `conic-gradient(#2EE6A6 ${p}%, rgba(255,255,255,.12) 0)`; if (p === 100 && !g("pqfull:" + today)) { st("pqfull:" + today, 1); burst("Všechny denní úkoly splněny!"); } }
  // ---- day status + calendar
  function drawDay() { const cur = g("pstat:" + today); document.querySelectorAll("#pst button").forEach(b => b.classList.toggle("pill-off", b.dataset.s !== cur));
    let h = "", ok = 0, bad = 0; for (let i = 27; i >= 0; i--) { const k = tk(i), v = g("pstat:" + k);
      h += `<div class="day ${v === "broke" ? "down" : v ? "up" : ""}"><span>${+k.slice(8)}</span>${v === "wait" ? "čekání" : v === "setup" ? "setup" : v === "broke" ? "chyba" : ""}</div>`; if (v === "broke") bad++; else if (v) ok++; }
    $("pcal").innerHTML = h; $("pk").innerHTML = `<div class="k"><b class="up">${streak()}</b><span>dní za sebou, kdy jsem se ovládl</span></div><div class="k"><b>${ok}</b><span>zvládnutých dní za 28 dní</span></div><div class="k"><b class="${bad ? "down" : ""}">${bad}</b><span>porušených dní</span></div>`; }
  document.querySelectorAll("#pst button").forEach(b => b.onclick = () => { st("pstat:" + today, b.dataset.s); st("patience:" + today, b.dataset.s !== "broke"); drawDay(); level();
    if (b.dataset.s === "wait") burst("Den bez obchodu je výhra!"); else if (b.dataset.s === "setup") burst("Obchodoval jsi jen svůj setup."); });
  // ---- questions
  const Q = ["Vidím svůj setup, ne jen pohyb ceny?", "Sedí směr na větším timeframe?", "Mám stop a velikost spočítanou předem?", "Není za 15 minut velká zpráva?", "Nejsem naštvaný, nervózní ani znuděný?", "Obchodoval bych to i po třech ztrátách v řadě?"];
  $("pq").innerHTML = Q.map((q, i) => `<label class="chip"><input type="checkbox" data-q="${i}">${q}</label>`).join("");
  const pk = "pq:" + today; let Qs = g(pk) || [];
  function qres() { const n = Qs.length; $("pqres").className = "lede " + (n === 6 ? "up" : n >= 4 ? "warn" : "down"); $("pqres").textContent = n === 6 ? "Zelená: můžeš obchodovat podle plánu." : n === 0 ? "" : "Zatím " + n + " z 6. Chybí-li něco, počkej."; }
  document.querySelectorAll("[data-q]").forEach(c => { c.checked = Qs.includes(+c.dataset.q); c.onchange = () => { Qs = Qs.filter(x => x !== +c.dataset.q); if (c.checked) Qs.push(+c.dataset.q); st(pk, Qs); qres(); if (Qs.length === 6 && !QD.includes(1)) quest(1); }; }); qres();
  const nk = "pnote:" + today; $("pnote").value = g(nk) || ""; $("pnote").oninput = () => { st(nk, $("pnote").value); if ($("pnote").value.trim().length > 10 && !QD.includes(3)) quest(3); };
  // ---- impulses
  const IM = () => (g("pimp") || {}), imAdd = (k) => { const m = IM(); m[today] = m[today] || {r: 0, t: 0}; m[today][k]++; st("pimp", m); drawIK(); level(); };
  function drawIK() { const m = IM(), t = m[today] || {r: 0, t: 0}; let r = 0; Object.values(m).forEach(v => r += v.r);
    $("ik").innerHTML = `<div class="k"><b class="up">${t.r}</b><span>odolaných nutkání dnes</span></div><div class="k"><b>${r}</b><span>odolaných celkem</span></div><div class="k"><b>${t.t}</b><span>obchodů podle plánu po rozmyslu dnes</span></div>`; }
  let itm = null;
  $("iq").innerHTML = ["Je to nutkání ze strachu, že mi něco uteče?", "Chci si vzít ztrátu zpátky?", "Nudím se?", "Vidím svůj setup na větším timeframe?"].map(q => `<label class="chip"><input type="checkbox">${q}</label>`).join("");
  $("ibtn").onclick = () => { $("iflow").hidden = false; $("ibtn").hidden = true; $("ino").disabled = $("iyes").disabled = true; $("ihint").hidden = false; let s = 60, ph = 0;
    const PH = ["Nádech nosem (4)", "Pomalý výdech (6)"]; clearInterval(itm);
    const tick = () => { $("itime").textContent = "0:" + String(s).padStart(2, "0"); const c = (60 - s) % 10; $("iph").textContent = c < 4 ? PH[0] : PH[1]; $("iorb").classList.toggle("big", c < 4);
      if (s <= 0) { clearInterval(itm); $("ino").disabled = $("iyes").disabled = false; $("ihint").hidden = true; $("itime").textContent = "Hotovo"; $("iph").textContent = "Teď se rozhodni s klidnou hlavou."; } s--; };
    tick(); itm = setInterval(tick, 1000); };
  const endImp = () => { $("iflow").hidden = true; $("ibtn").hidden = false; document.querySelectorAll("#iq input").forEach(c => c.checked = false); };
  $("ino").onclick = () => { imAdd("r"); endImp(); burst("Odolal jsi. +5 bodů."); };
  $("iyes").onclick = () => { imAdd("t"); endImp(); };
  // ---- hold test
  let HT = 15, hraf = null, ht0 = 0;
  document.querySelectorAll("#htarget button").forEach(b => b.onclick = () => { HT = +b.dataset.h; document.querySelectorAll("#htarget button").forEach(x => x.classList.toggle("pill-off", x !== b)); $("htxt").textContent = "Zmáčkni a drž " + HT + " s"; });
  const hb = () => { const b = g("pholdbest") || 0; $("hbest"). textContent = b ? "Nejdelší zvládnutá zkouška: " + b + " s" : "Zatím žádná zvládnutá zkouška."; }; hb();
  const hstop = (done) => { cancelAnimationFrame(hraf); hraf = null; const s = (performance.now() - ht0) / 1000;
    if (!ht0) return; ht0 = 0; $("hold").classList.remove("on");
    if (done) { $("htxt").textContent = "Zvládl jsi to! " + HT + " sekund klidu."; if (HT > (g("pholdbest") || 0)) { st("pholdbest", HT); } hb(); burst("Klidná ruka. +" + HT + " bodů"); if (HT >= 15 && !QD.includes(2)) quest(2); level(); }
    else { $("htxt").textContent = "Pustil jsi po " + s.toFixed(1) + " s. Zkus to znovu. Bez emocí."; $("hring").style.background = ""; } };
  const hloop = () => { const s = (performance.now() - ht0) / 1000, p = Math.min(1, s / HT); $("hring").style.background = `conic-gradient(#8B7CFF ${p * 100}%, rgba(255,255,255,.12) 0)`; $("htxt").textContent = Math.max(0, HT - s).toFixed(1) + " s";
    if (p >= 1) hstop(true); else hraf = requestAnimationFrame(hloop); };
  $("hold").addEventListener("pointerdown", e => { e.preventDefault(); ht0 = performance.now(); $("hold").classList.add("on"); hloop(); });
  ["pointerup", "pointerleave", "pointercancel"].forEach(ev => $("hold").addEventListener(ev, () => { if (ht0) hstop(false); }));
  $("hold").addEventListener("contextmenu", e => e.preventDefault());
  // ---- quiz
  const QZ = [
    {q: "Trh ti ujel o 80 bodů bez tebe. Co uděláš?", o: ["Naskočím hned, ať něco neztratím", "Nechám to být a počkám na návrat k úrovni nebo na další setup", "Vsadím dvojnásobek, ať to dohoním"], a: 1, e: "Honit ujetý trh je klasické FOMO. Další příležitost přijde vždycky."},
    {q: "Právě jsi prodělal a cítíš nutkání to hned vrátit.", o: ["Hned otevřu další obchod", "Zdvojnásobím počet kontraktů", "Vstanu od grafu aspoň na 15 minut"], a: 2, e: "Revenge obchod z vzteku končí obvykle větší ztrátou. Pauza je nejlepší ochrana."},
    {q: "Na minutovém grafu vidíš silný signál, ale denní graf jde opačně.", o: ["Vezmu to, signál je silný", "Počkám, až se shodne větší timeframe", "Zkusím long i short najednou"], a: 1, e: "Směr dává větší timeframe. Malý graf je plný šumu."},
    {q: "Je 15:45, nic se neděje a ty se nudíš.", o: ["Najdu aspoň nějaký obchod", "Přepnu na jiný instrument, třeba něco najdu", "Nic neobchoduju. Čekání je moje práce"], a: 2, e: "Nuda není setup. Den bez obchodu, kdy se ovládneš, je výhra."},
    {q: "Za 10 minut vyjde CPI.", o: ["Vstoupím před zprávou, ať stihnu pohyb", "Počkám na zprávu a na reakci trhu", "Zvětším pozici, bude to velký pohyb"], a: 1, e: "Před velkou zprávou je trh nepředvídatelný. Počkej aspoň 5 minut na reakci."},
    {q: "Dnes jsi nic neobchodoval a ovládl ses. Jak to hodnotíš?", o: ["Zbytečný den, zítra to dohoním", "Zbytečný den, mohl jsem aspoň zkusit něco malého", "Výhra, dodržel jsem plán"], a: 2, e: "Disciplína je to, co odlišuje tradery. Výsledek přijde s trpělivostí."}];
  let qi = 0, qsc = 0;
  function qshow() { const b = $("qz"); if (qi >= QZ.length) { const best = Math.max(qsc, g("pquizbest") || 0); st("pquizbest", best); b.innerHTML = `<p class="lede ${qsc >= 5 ? "up" : "warn"}" style="font-size:34px">${qsc} z ${QZ.length}</p><p class="empty">Nejlepší výsledek: ${best} z ${QZ.length}.</p><button id="qre">Zkusit znovu</button>`; $("qre").onclick = () => { qi = 0; qsc = 0; qshow(); }; level(); if (qsc === QZ.length) burst("Perfektní kvíz!"); return; }
    const q = QZ[qi]; b.innerHTML = `<p class="empty">Otázka ${qi + 1} z ${QZ.length}</p><h3 style="font-size:20px">${q.q}</h3><div class="qopts">${q.o.map((t, i) => `<button class="qopt ghost" data-i="${i}">${t}</button>`).join("")}</div><p id="qfb"></p>`;
    b.querySelectorAll(".qopt").forEach(x => x.onclick = () => { const i = +x.dataset.i; b.querySelectorAll(".qopt").forEach(y => { y.disabled = true; if (+y.dataset.i === q.a) y.classList.add("good"); else if (y === x) y.classList.add("bad"); });
      if (i === q.a) qsc++; $("qfb").innerHTML = `<b class="${i === q.a ? "up" : "down"}">${i === q.a ? "Správně." : "Ne tak docela."}</b> ${q.e} <br><button id="qnx" style="margin-top:12px">Další</button>`; $("qnx").onclick = () => { qi++; qshow(); }; }); }
  qshow();
  // ---- badges
  const BD = [["🌱", "První zápis", "Zapsal jsi první den", () => Object.keys(localStorage).some(k => k.startsWith("pstat:"))], ["🪑", "Den bez obchodu", "Seděl jsi u grafu a ovládl se", () => Object.keys(localStorage).some(k => k.startsWith("pstat:") && g(k) === "wait")],
    ["🔥", "3 dny v řadě", "Série 3 dny", () => streak() >= 3], ["⚡", "7 dní v řadě", "Série týden", () => streak() >= 7], ["🏔️", "14 dní v řadě", "Série dva týdny", () => streak() >= 14], ["👑", "30 dní v řadě", "Série měsíc", () => streak() >= 30],
    ["🛑", "10 odolaných nutkání", "Zastavil ses 10krát", () => Object.values(IM()).reduce((a, v) => a + v.r, 0) >= 10], ["🧠", "50 odolaných nutkání", "Zastavil ses 50krát", () => Object.values(IM()).reduce((a, v) => a + v.r, 0) >= 50],
    ["🧘", "Klidná ruka 30 s", "Zkouška 30 sekund", () => (g("pholdbest") || 0) >= 30], ["🗿", "Klidná ruka 60 s", "Zkouška 60 sekund", () => (g("pholdbest") || 0) >= 60],
    ["🎓", "Kvíz bez chyby", "6 z 6", () => (g("pquizbest") || 0) >= 6], ["✅", "Všechny denní úkoly", "Dnes splněno 100 %", () => !!g("pqfull:" + today)], ["🎯", "Sniper", "Dosáhl jsi úrovně Sniper", () => xp() >= 700]];
  function badges() { $("pbadges").innerHTML = BD.map(b => { const on = b[3](); return `<div class="bd ${on ? "on" : ""}"><span>${b[0]}</span><b>${b[1]}</b><em>${b[2]}</em></div>`; }).join(""); }
  drawQ(); drawDay(); drawIK(); level(); badges();
}
"""


def save_prices():
    """Uloží cenu Nasdaq futures v 15:00 pražského času pro každý den (Deník s ní předvyplní vstup)."""
    out = {"p": {}, "last": None}
    try:
        c = yf.download("NQ=F", period="30d", interval="5m", progress=False)["Close"].squeeze().dropna()
        c.index = c.index.tz_convert("Europe/Prague")
        dd, hh = c.index.strftime("%Y-%m-%d"), c.index.strftime("%H:%M")
        for day_ in sorted(set(dd)):
            part = c[(dd == day_) & (hh >= "15:00") & (hh <= "15:10")]
            if len(part):
                out["p"][day_] = round(float(part.iloc[0]), 2)
        out["last"] = {"d": dd[-1], "t": hh[-1], "v": round(float(c.iloc[-1]), 2)}
    except Exception as e:
        print("Ceny v 15:00 se nepodařilo stáhnout:", e)
    (ROOT / "docs" / "prices.json").write_text(json.dumps(out), encoding="utf-8")


TIPS = {
    "Bullish": "Čekáš, že cena poroste. Hledáš long (nákup).",
    "Bearish": "Čekáš, že cena klesne. Hledáš short (prodej).",
    "Hawkish": "Fed mluví přísně: sazby nechá vysoko nebo je zvedne. Pro Nasdaq je to bearish.",
    "Dovish": "Fed mluví mírně: sazby sníží nebo to naznačí. Pro Nasdaq je to bullish.",
    "Neutrální": "Bez jasného směru. Raději počkej na reakci trhu.",
    "VIX": "Index strachu. Když roste, investoři se bojí a akcie často klesají.",
    "Pre-market": "Obchodování před oficiálním otevřením burzy (v 15:30 pražského času). Ukazuje náladu před startem.",
    "Premarket": "Obchodování před oficiálním otevřením burzy (v 15:30 pražského času). Ukazuje náladu před startem.",
    "Futures": "Kontrakty na budoucí cenu indexu. Obchodují se skoro celý den, a proto ukazují náladu před otevřením burzy.",
    "Bias": "Tvůj nebo robotův názor na směr dne: bullish (růst), bearish (pokles) nebo neutrální.",
    "RSI": "Číslo 0 až 100 o síle pohybu. Nad 55 převažují kupující, pod 45 prodávající. Nad 70 bývá přehřátý trh.",
    "FOMC": "Výbor americké centrální banky (Fed), který rozhoduje o úrokových sazbách. Nejsilnější zpráva týdne.",
    "Fed": "Americká centrální banka. Její rozhodnutí o sazbách hýbe celým trhem.",
    "CPI": "Inflace, tedy jak rychle rostou ceny. Vyšší než čekání bývá bearish.",
    "PPI": "Ceny u výrobců. Ukazuje, jestli se bude inflace zrychlovat.",
    "PCE": "Další měřítko inflace, které Fed sleduje nejvíc.",
    "NFP": "Non-Farm Payrolls: kolik nových pracovních míst vzniklo v USA. Vychází první pátek v měsíci.",
    "Non-Farm": "Kolik nových pracovních míst vzniklo v USA. Vychází první pátek v měsíci.",
    "GDP": "Růst ekonomiky USA. Silnější číslo bývá bullish.",
    "ISM": "Průzkum nálady ve firmách. Nad 50 znamená růst, pod 50 zpomalení.",
    "Sentiment": "Nálada ve zprávách. Robot počítá pozitivní a negativní slova v titulcích. Je to hrubý ukazatel.",
    "Stop loss": "Cena, kde obchod uzavřeš se ztrátou, aby ses nezničil. Zadáváš ho vždy před vstupem.",
    "Take profit": "Cena, kde obchod uzavřeš se ziskem.",
    "TP1": "První cíl zisku. Poměr zisku k riziku 1:2.",
    "TP2": "Druhý cíl zisku. Poměr zisku k riziku 1:2,5.",
    "R:R": "Poměr zisku k riziku. 1:2 znamená, že riskuješ 1 a chceš vydělat 2.",
    "Long": "Nákup. Vyděláš, když cena roste.",
    "Short": "Prodej. Vyděláš, když cena klesá.",
    "MNQ": "Mikro kontrakt na Nasdaq 100. Jeden bod je 2 $.",
    "Profit factor": "Součet zisků dělený součtem ztrát. Nad 1,5 je dobré, pod 1 prodělávaš.",
    "Drawdown": "Největší propad účtu od vrcholu.",
    "Expectancy": "Kolik průměrně vyděláš nebo prodělaš na jednom obchodu.",
    "FOMO": "Strach, že ti něco uteče. Vede k pozdním a špatným vstupům.",
    "Revenge": "Obchod z vzteku po ztrátě. Skoro vždy končí špatně.",
    "Tilt": "Stav, kdy emoce přebírají vládu a obchoduješ bez pravidel.",
    "Timeframe": "Časový rámec grafu. Větší (denní, hodina) ukazuje směr, menší (5 minut) vstup.",
    "Setup": "Tvůj opakovatelný obchodní vzor s pravidly pro vstup, stop a cíl.",
    "Earnings": "Výsledky firem. Kolem nich bývají velké pohyby ceny akcie.",
    "DXY": "Dolarový index, síla dolaru. Silný dolar bývá pro akcie spíš špatný.",
    "Skóre": "Číslo od −10 do +10. Plus znamená převahu růstu, mínus převahu poklesu.",
    "Open Bias Engine": "Výpočet směru dne z dat před otevřením: futures, šířka akcií, VIX, dolar, výnosy, trend, zprávy a sezónnost.",
    "Hit rate": "Jak často měl bias pravdu.",
}

TIPS_JS = "const TIPS = " + json.dumps(TIPS, ensure_ascii=False) + r""";
(function () {
  const keys = Object.keys(TIPS).sort((a, b) => b.length - a.length), lower = {};
  keys.forEach(k => lower[k.toLowerCase()] = TIPS[k]);
  const src = "(?<![\\p{L}\\d])(" + keys.map(s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")(?![\\p{L}\\d])";
  const reT = new RegExp(src, "iu"), reG = new RegExp(src, "giu"), SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "CANVAS", "BUTTON", "TITLE", "IFRAME", "H1"]);
  let busy = false, tm = null;
  function run(root) {
    busy = true;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: n => { const p = n.parentElement;
      return (!p || SKIP.has(p.tagName) || p.closest(".tip,.side,dialog textarea")) ? NodeFilter.FILTER_REJECT : reT.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT; }});
    const nodes = []; while (w.nextNode()) nodes.push(w.currentNode);
    const secs = new Map(), seenIn = s => { if (!secs.has(s)) { const set = new Set(); s.querySelectorAll(".tip").forEach(t => set.add(t.firstChild.textContent.toLowerCase())); secs.set(s, set); } return secs.get(s); };
    nodes.forEach(n => { const t = n.nodeValue, frag = document.createDocumentFragment(), seen = seenIn(n.parentElement.closest("section") || document.body); let last = 0, m; reG.lastIndex = 0;
      while ((m = reG.exec(t))) { const k = m[0].toLowerCase(); if (seen.has(k) || !lower[k]) continue; seen.add(k); frag.append(t.slice(last, m.index));
        const s = document.createElement("span"); s.className = "tip"; s.tabIndex = 0; s.dataset.tip = lower[k]; s.textContent = m[0];
        const q = document.createElement("sup"); q.textContent = "*"; s.append(q); frag.append(s); last = m.index + m[0].length; }
      if (last) { frag.append(t.slice(last)); n.replaceWith(frag); } });
    busy = false;
  }
  const box = document.createElement("div"); box.id = "tipbox"; try { box.setAttribute("popover", "manual"); } catch (e) {}
  const hide = () => { try { if (box.matches(":popover-open")) box.hidePopover(); } catch (e) {} box.style.display = "none"; };
  function show(t) { box.textContent = t.dataset.tip; box.style.display = "block"; try { box.showPopover(); } catch (e) {}
    const r = t.getBoundingClientRect(), bw = box.offsetWidth, bh = box.offsetHeight; let x = Math.min(Math.max(8, r.left), window.innerWidth - bw - 8), y = r.bottom + 8;
    if (y + bh > window.innerHeight - 8) y = Math.max(8, r.top - bh - 8); box.style.left = x + "px"; box.style.top = y + "px"; }
  function start() { document.body.appendChild(box); const main = document.querySelector("main") || document.body; run(main);
    new MutationObserver(() => { if (busy) return; clearTimeout(tm); tm = setTimeout(() => run(main), 150); }).observe(main, {childList: true, subtree: true});
    document.addEventListener("mouseover", e => { const t = e.target.closest && e.target.closest(".tip"); if (t) show(t); });
    document.addEventListener("mouseout", e => { const t = e.target.closest && e.target.closest(".tip"); if (t) hide(); });
    document.addEventListener("focusin", e => { const t = e.target.closest && e.target.closest(".tip"); if (t) show(t); });
    document.addEventListener("focusout", hide); window.addEventListener("scroll", hide, true); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
"""


BG = {"zpravy.html": "news", "sentiment.html": "news", "kalendar.html": "news", "predikce.html": "news", "fundament.html": "news", "premarket.html": "analysis", "bias.html": "analysis", "tyden.html": "analysis", "presnost.html": "analysis", "trpelivost.html": "yinyang", "mindset.html": "buddha", "plan.html": "target",
      "journal.html": "diary", "roadmap.html": "mountain", "prirucka.html": "book", "nastaveni.html": "sliders"}

SCROLL_JS = """(function(){var s=document.querySelector(".side");if(!s)return;
try{var y=sessionStorage.getItem("sideY");if(y!==null)s.scrollTop=+y;}catch(e){}
var a=s.querySelector("a[aria-current]");
if(a){var r=a.getBoundingClientRect(),sr=s.getBoundingClientRect();if(r.bottom>sr.bottom-12||r.top<sr.top+12){s.scrollTop+=r.top-sr.top-s.clientHeight/2;}}
s.addEventListener("wheel",function(e){var up=e.deltaY<0;if((up&&s.scrollTop<=0)||(!up&&s.scrollTop+s.clientHeight>=s.scrollHeight-1)){e.preventDefault();}},{passive:false});
var save=function(){try{sessionStorage.setItem("sideY",s.scrollTop)}catch(e){}};
s.addEventListener("scroll",save);s.querySelectorAll("a").forEach(function(l){l.addEventListener("click",save)});})();"""


def gauge_box(avg, val, mx, cap):
    cls = "up" if val > 0.05 else "down" if val < -0.05 else ""
    word = "Bullish" if val > mx * 0.05 else "Bearish" if val < -mx * 0.05 else "Neutral"
    return f'<div class="gbox">{gauge(avg)}<div class="gnum {cls}">{sgn(val)}<span> z {mx}</span></div><div class="gword {cls}">{word}</div><div class="gcap">{cap}</div></div>'


def gb(val, mx, cap):
    val = max(-mx, min(mx, val))
    return gauge_box(val / mx * 1.5, val, mx, cap)


def inject_gauge(body, box, title, text):
    if body.lstrip().startswith('<section class="hero">'):
        return body.replace("</div></section>", "</div>" + box + "</section>", 1)
    return (f'<section class="hero"><div><p class="empty" style="margin:0 0 8px">{title}</p><ul class="facts"><li>{text}</li></ul></div>{box}</section>' + body)


def acc_box(h):
    done = [x for x in h if hit(x) is not None]
    if not done:
        return None
    acc = sum(hit(x) for x in done) / len(done) * 100
    cls = "up" if acc >= 55 else "down" if acc < 45 else ""
    word = "Dobrá přesnost" if acc >= 55 else "Slabá přesnost" if acc < 45 else "Průměrná přesnost"
    avg = max(-1.0, min(1.0, (acc - 50) / 25)) * 1.5
    return f'<div class="gbox">{gauge(avg)}<div class="gnum {cls}">{acc:.0f}<span> %</span></div><div class="gword {cls}">{word}</div><div class="gcap">trefených dní</div></div>'


def main():
    cfg = CONFIG
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    sb = lambda f: sidebar(f)
    (docs / "journal.html").write_text(JOURNAL_HTML.replace("@@SIDEBAR@@", sb("journal.html")), encoding="utf-8")
    (docs / "plan.html").write_text(PLAN_HTML.replace("@@SIDEBAR@@", sb("plan.html")), encoding="utf-8")
    (docs / "mindset.html").write_text(MINDSET_HTML.replace("@@SIDEBAR@@", sb("mindset.html")), encoding="utf-8")

    q, (pre, live), cal, week = quotes(cfg), premarket(cfg), calendar(), week_events()
    ptitle = "Největší akcie v pre-marketu" if live else "Největší akcie: změna za poslední obchodní den"
    sent = news_sentiment(cfg)
    nq = nq_series()
    sea, sea_data = seasonality()
    label, total, comp = bias_engine(q, pre, nq, sent, sea_data)
    vix = next((r for r in q if r["sym"] == "^VIX"), None)
    facts_l = [f"Skóre trhu je {sgn(total)} z 10. Plus znamená převahu růstu, mínus převahu poklesu."] if comp else []
    if vix:
        facts_l.append(f"Strach na trhu (VIX) je {num(vix['last'])} ({pc(vix['chg'])}) a " +
                       ("roste, což bývá špatné pro akcie." if vix["chg"] > 5 else "klesá, což akciím pomáhá." if vix["chg"] < -5 else "téměř se nemění."))
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
    data = {"mac": [{"n": r["name"], "c": round(r["chg"], 2), "inv": r["sym"] in ("^VIX", "DX-Y.NYB", "^TNX")} for r in q], "nq": nq, "sea": sea_data, "cal": week, "sent": sent if sent.get("status") == "ok" else None,
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
    steps = ('<section><h2>Tvůj ranní postup</h2><div class="steps-row">' + "".join(
        f'<a class="step" href="{h}"><span class="num">{i}</span><b>{t}</b><span>{d}</span></a>' for i, (h, t, d) in enumerate(
            [("fundament.html", "Zprávy a riziko dne", "Víš, co přijde a kdy?"), ("premarket.html", "Tvoje akcie", "Jak se hýbou před otevřením?"),
             ("mindset.html", "Klid", "3 minuty dýchání"), ("plan.html", "Plán dne", "Limity a checklist")], 1)) + '</div></section>')
    dash = (f'<section class="hero"><div><p class="lede {lede_cls}">{escape(label)}</p><ul class="facts">{facts}</ul></div>{gauge_box(total * 0.15, total, 10, "skóre trhu")}</section>{kp}'
            + steps + comp_html(comp)
            + ('<section><h2>Nasdaq futures</h2><div class="pills"><button data-r="24">Poslední den</button><button class="pill-off" data-r="72">3 dny</button>'
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

    fd = fundament_body(q, week, sent, cfg)
    kal = ('<section class="hero"><div><p class="lede" id="cd">–</p><ul class="facts"><li id="cdt">Načítám…</li></ul></div></section>'
           '<section><div class="pills"><button data-i="">Všechny</button><button class="pill-off" data-i="vysoký">Vysoký dopad</button>'
           '<button class="pill-off" data-i="střední">Střední dopad</button></div><label style="max-width:240px">Den<select id="cal-day"></select></label>'
           '<div id="cal-t" style="margin-top:12px"></div></section>') if week else \
          '<section><div class="note">Kalendář se nepodařilo stáhnout. Otevři si <a href="https://www.forexfactory.com/calendar">Forex Factory</a>.</div></section>'

    (docs / "tips.js").write_text(TIPS_JS, encoding="utf-8")
    save_prices()
    hist = update_history(label, total)
    rs = intraday_rsi()
    wkd = weekly()
    days = {}
    for e in (week or []):
        d = days.setdefault(e["day"], {"dn": e["dn"], "h": 0, "m": 0})
        d["h" if e["impact"] == "vysoký" else "m"] += 1
    ks = sorted(days)
    data.update({"news": (sent.get("all") or []) if sent.get("status") == "ok" else [], "hist": hist, "pat": PAT_LINES,
                 "wk": {"d": [days[k]["dn"] for k in ks], "h": [days[k]["h"] for k in ks], "m": [days[k]["m"] for k in ks]}})
    cats20 = categories(q, week, sent)
    tilt = sum(c["s"] for c in cats20[:3])
    fscore = round(sum(c["s"] for c in cats20) / max(1, len(cats20)) * 5, 1)
    if week:
        bx = gb(tilt, 6, "naklonění zpráv")
        kal = inject_gauge(kal, bx, "Zprávy z USA", "Jak by zprávy tento týden mohly zatlačit na trh.")
        pred = inject_gauge(predikce_body(week), bx, "Predikce zpráv", "Celkové naklonění ze všech zpráv, které se blíží.")
    else:
        pred = predikce_body(week)
    fd = inject_gauge(fd, gb(fscore, 10, "skóre fundamentu"), "Fundament", "Souhrn hodnocení oblastí.")
    if pre:
        pavg = sum(r["chg"] for r in pre) / len(pre)
        pm = inject_gauge(pm, gb(round(pavg * 3, 1), 10, "skóre akcií"), "Akcie před otevřením", f"Průměrná změna sledovaných akcií je {pc(pavg)}.")
    tyd = tyden_body(wkd, week, label)
    if wkd:
        tyd = inject_gauge(tyd, gb(round(wkd["wk"] * 2, 1), 10, "skóre týdne"), "Týdenní výhled", "Směr podle posledních pěti dní.")
    prs = presnost_body(hist)
    ab = acc_box(hist)
    if ab:
        prs = inject_gauge(prs, ab, "Přesnost biasu", "Jak často měl bias pravdu.")
    pages = {"index.html": (dash, ""), "premarket.html": (pm, ""), "fundament.html": (fd, FUND_JS), "kalendar.html": (vysvetlivky() + kal, ""), "sentiment.html": (sentiment_body(sent, cfg), ""),
             "zpravy.html": (zpravy_body(q, sent), NEWS_JS), "predikce.html": (pred, ""), "bias.html": (bias_body(label, total, comp, rs, q, week), BIAS_JS),
             "tyden.html": (tyd, WK_JS), "presnost.html": (prs, ACC_JS), "roadmap.html": (roadmap_body(), ROAD_JS),
             "prirucka.html": (prirucka_body(), ""), "nastaveni.html": (nastaveni_body(), SET_JS), "trpelivost.html": (trpelivost_body(), PAT_JS)}
    for f, (body, js) in pages.items():
        (docs / f).write_text(shell(f, body, data, js), encoding="utf-8")
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
        "CNBC Economy": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "CNBC Investing": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
        "CNBC Finance": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "MarketWatch Live": "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/",
        "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
        "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
        "Bloomberg Markets": "https://feeds.bloomberg.com/markets/news.rss",
        "Wall Street Journal": "https://feeds.a.dowjones.com/rss/RSSMarketsMain.xml",
        "Nasdaq.com": "https://www.nasdaq.com/feed/rssoutbound?category=Markets",
        "FXStreet": "https://www.fxstreet.com/rss/news",
        "Investing.com": "https://www.investing.com/rss/news_25.rss",
        "Seeking Alpha": "https://seekingalpha.com/market_currents.xml",
        "Benzinga": "https://www.benzinga.com/feed",
        "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "The Guardian Business": "https://www.theguardian.com/business/rss"
    },
    "topics": {
        "Téma: Nasdaq": "Nasdaq 100",
        "Téma: Fed": "Federal Reserve OR Powell",
        "Téma: inflace": "inflation OR CPI",
        "Téma: práce": "jobs report OR payrolls",
        "Téma: cla": "tariffs OR trade war",
        "Téma: AI akcie": "Nvidia OR semiconductors"
    },
    "people": {
        "Elon Musk": "Elon Musk",
        "Donald Trump": "Donald Trump (tariffs OR Fed OR stocks OR economy)",
        "Unusual Whales": "Unusual Whales"
    }
}

# ===== Stránky (nech být) =====
STYLE_CSS = ':root{--bg:#080C1A;--ink:#EEF1FA;--muted:#C9D1E8;--rule:rgba(255,255,255,.09);--up:#2EE6A6;--down:#FF6B8A;--acc:#8B7CFF;--glass:rgba(255,255,255,.045);--field:rgba(255,255,255,.06);color-scheme:dark}\n*{box-sizing:border-box}\nbody{margin:0;min-height:100vh;color:var(--ink);font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif;background:radial-gradient(1100px 560px at 12% -8%,rgba(139,124,255,.30),transparent 60%),radial-gradient(900px 520px at 95% 4%,rgba(46,230,166,.14),transparent 60%),var(--bg) fixed}\nmain{max-width:1000px;margin:0 auto;padding:28px 18px 72px}\nnav.top{display:flex;gap:8px;margin-bottom:26px}nav.top a{color:var(--muted);text-decoration:none;padding:7px 16px;border-radius:99px;border:1px solid transparent}\nnav.top a[aria-current]{background:var(--glass);color:var(--ink);border-color:var(--rule)}\nsection,.k{background:var(--glass);border:1px solid var(--rule);border-radius:18px;backdrop-filter:blur(10px)}\nsection{padding:20px 22px;margin-bottom:20px}[hidden]{display:none!important}\nh2{font-size:16px;font-weight:600;margin:0 0 14px}h3{font-size:14px;font-weight:600;margin:0 0 8px}\n.hero{display:flex;gap:24px;align-items:center;flex-wrap:wrap;border-color:rgba(139,124,255,.4);box-shadow:0 0 70px -24px rgba(139,124,255,.6)}.hero>div{flex:1 1 320px}\n.lede{font-size:clamp(34px,6vw,56px);font-weight:700;line-height:1.05;letter-spacing:-.02em;margin:0 0 14px}\n.lede.up{text-shadow:0 0 40px rgba(46,230,166,.4)}.lede.down{text-shadow:0 0 40px rgba(255,107,138,.4)}\n.facts{margin:0;padding-left:18px;color:var(--muted)}.facts li{margin:5px 0}.gauge{width:230px;max-width:100%}\n.chartbox{position:relative;height:280px}.chartbox.tall{height:480px}\n.xgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}\n.up{color:var(--up)}.down{color:var(--down)}.empty{color:var(--muted)}.n{text-align:right}.chg{font-weight:600}\n.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}\nth,td{padding:10px 8px;border-bottom:1px solid var(--rule);text-align:left;white-space:nowrap;font-weight:400}\nthead th{color:var(--muted);font-size:13px}tbody th{font-weight:500}tbody tr:last-child>*{border-bottom:0}\nfooter{color:var(--muted);font-size:13px;max-width:64ch}a{color:inherit}\n:focus-visible{outline:2px solid var(--acc);outline-offset:2px}\n.head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:20px}\n.tabs{display:flex;gap:4px;padding:4px;background:var(--glass);border:1px solid var(--rule);border-radius:12px}\n.tabs button{background:transparent;color:var(--muted);box-shadow:none}.tabs button[aria-selected=true]{background:var(--acc);color:#fff}\n.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:20px}\n.k{padding:16px 18px}.k b{display:block;font-size:30px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}.k span{color:var(--muted);font-size:13px}\nsection .kpis{margin-bottom:0}\n.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:5px}.cal .dow{color:var(--muted);font-size:12px;font-weight:400;text-align:center}\n.day{min-height:56px;padding:5px 7px;border:1px solid var(--rule);border-radius:10px;font-size:12px;font-variant-numeric:tabular-nums}\n.day span{display:block;color:var(--muted)}.day.up{background:rgba(46,230,166,.16)}.day.down{background:rgba(255,107,138,.16)}\n.calhead{display:flex;align-items:center;gap:12px;margin-bottom:12px}.calhead b{min-width:130px;text-align:center;font-weight:500}\n.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}\nlabel{display:block;font-size:13px;color:var(--muted)}\ninput,select,textarea,button{font:inherit;color:var(--ink)}\ninput,select,textarea{width:100%;margin-top:4px;padding:9px 11px;background:var(--field);border:1px solid var(--rule);border-radius:10px}\nselect option{background:#0C1124}\nbutton{padding:9px 16px;background:linear-gradient(135deg,#8B7CFF,#38BDF8);color:#fff;border:0;border-radius:10px;cursor:pointer;font-weight:600}\nbutton.ghost{background:transparent;color:var(--ink);border:1px solid var(--rule)}button.sm{padding:4px 10px;font-size:13px}\n.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.chip{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border:1px solid var(--rule);border-radius:99px;font-size:13px;color:var(--ink);cursor:pointer}\n.chip input{width:auto;margin:0}.chip:has(input:checked){background:var(--acc);border-color:var(--acc);color:#fff}\n.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:28px;margin-top:24px}\n.bar{display:grid;grid-template-columns:70px 1fr auto;gap:10px;align-items:center;font-size:13px;margin:6px 0;font-variant-numeric:tabular-nums}\n.bar span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.track{height:8px;background:var(--glass);border-radius:4px}.track i{display:block;height:100%;border-radius:4px;background:currentColor}\n.bar em{color:var(--muted);font-style:normal;font-weight:400}\ndialog{width:min(660px,94vw);max-height:92vh;overflow:auto;background:#0C1124;color:var(--ink);border:1px solid var(--rule);border-radius:18px;padding:24px}\ndialog::backdrop{background:rgba(0,0,0,.65)}.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}\n#view img{max-width:100%;margin-top:10px;border-radius:10px}#view p{white-space:pre-wrap}\n@media (max-width:560px){.hide{display:none}}\n\nmain{padding-top:20px}\n.app{display:flex;min-height:100vh}\n.side{position:sticky;top:0;align-self:flex-start;height:100vh;width:236px;flex:none;padding:22px 14px;border-right:1px solid var(--rule);background:rgba(7,11,20,.6);backdrop-filter:blur(12px)}\n.brand{font-weight:700;font-size:16px;padding:0 12px 20px;background:linear-gradient(90deg,#fff,#B3A9FF);-webkit-background-clip:text;background-clip:text;color:transparent}\n.side nav{display:flex;flex-direction:column;gap:4px}\n.side a{display:flex;gap:10px;align-items:center;padding:11px 12px;border-radius:12px;color:var(--muted);text-decoration:none;border:1px solid transparent;transition:.15s}\n.side a:hover{background:var(--glass);color:var(--ink)}\n.side a[aria-current]{background:linear-gradient(135deg,rgba(139,124,255,.38),rgba(56,189,248,.18));color:#fff;border-color:rgba(139,124,255,.5)}\n.content{flex:1;min-width:0}\n.pagehead{max-width:1000px;margin:0 auto;padding:30px 18px 0}.pagehead h1{margin:0;font-size:clamp(28px,4vw,40px);font-weight:700;letter-spacing:-.02em}.pagehead p{margin:4px 0 0;color:var(--muted)}\n.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}\n.tile{padding:12px 14px;border-radius:14px;border:1px solid var(--rule);transition:transform .15s,box-shadow .15s}\n.tile:hover{transform:translateY(-4px) scale(1.04);box-shadow:0 12px 30px -10px rgba(0,0,0,.7)}\n.tile b{display:block;font-size:15px}.tile span{display:block;font-size:12px;color:rgba(255,255,255,.7);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tile em{display:block;font-style:normal;font-weight:700;font-size:20px;margin-top:6px}\n.pills{display:flex;gap:6px;margin-bottom:12px}.pills button{padding:6px 14px;font-size:13px}.pill-off{background:transparent!important;border:1px solid var(--rule)!important;color:var(--muted)!important}\n.badge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;font-weight:700}.badge.up{background:rgba(46,230,166,.18)}.badge.down{background:rgba(255,107,138,.18)}.badge.flat{background:var(--glass);color:var(--muted)}\n.xcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}.xc{padding:16px;border:1px solid var(--rule);border-radius:16px;background:var(--glass)}.xc p{margin:0 0 8px}\n.split{display:grid;grid-template-columns:260px 1fr;gap:24px;align-items:start}\n.note{padding:16px 18px;border-radius:14px;background:rgba(139,124,255,.12);border:1px solid rgba(139,124,255,.35)}\n@media (max-width:760px){.app{flex-direction:column}.side{position:static;height:auto;width:100%;padding:10px;border-right:0;border-bottom:1px solid var(--rule)}.brand{display:none}.side nav{flex-direction:row;overflow-x:auto}.side a{white-space:nowrap;padding:8px 12px}.split{grid-template-columns:1fr}}\n\n.bar{grid-template-columns:120px 1fr auto}.bar.wide{grid-template-columns:130px 140px 1fr}.bar.wide em{display:inline}\n.lede#cd{font-variant-numeric:tabular-nums}\n\n/* v8 */\n:root{--warn:#FFB454;--acc2:#38BDF8}\nbody{background:radial-gradient(1000px 520px at 10% -12%,rgba(139,124,255,.30),transparent 62%),radial-gradient(800px 480px at 96% 2%,rgba(56,189,248,.16),transparent 60%),radial-gradient(700px 500px at 50% 112%,rgba(244,114,182,.10),transparent 60%),var(--bg) fixed}\nsection,.k,.step{background:linear-gradient(160deg,rgba(255,255,255,.07),rgba(255,255,255,.025));border:1px solid rgba(255,255,255,.10);border-radius:20px;box-shadow:0 24px 50px -34px rgba(0,0,0,.9)}\nsection{padding:24px 26px;margin-bottom:22px}\nh2{font-size:17px;font-weight:600;margin:0 0 6px}.hint{color:var(--muted);margin:0 0 16px;font-size:14px;max-width:70ch}\nsection>h2:only-child{margin-bottom:14px}\n.hero{background:linear-gradient(135deg,rgba(139,124,255,.24),rgba(56,189,248,.08));border-color:rgba(139,124,255,.5);box-shadow:0 0 80px -28px rgba(139,124,255,.7)}\n.side{display:flex;flex-direction:column}.side nav{flex:1}\n.grp{margin:16px 0 4px;padding:0 12px;font-size:12px;color:var(--muted);opacity:.75}\n.clock{margin-top:14px;padding:14px;border-radius:16px;background:var(--glass);border:1px solid var(--rule);font-size:13px;color:var(--muted)}\n.clock b{display:block;margin:2px 0;font-size:24px;font-weight:700;color:var(--ink);font-variant-numeric:tabular-nums}\n.steps-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}\n.step{display:block;padding:16px 18px;text-decoration:none;color:var(--ink);transition:transform .15s,border-color .15s}\n.step:hover{transform:translateY(-3px);border-color:rgba(139,124,255,.7)}\n.step .num{display:inline-flex;width:26px;height:26px;border-radius:50%;align-items:center;justify-content:center;font-size:13px;font-weight:700;background:linear-gradient(135deg,#8B7CFF,#38BDF8);margin-bottom:8px}\n.step b{display:block;font-size:16px}.step span:last-child{color:var(--muted);font-size:13px}\na.tile{display:block;text-decoration:none;color:var(--ink);cursor:pointer}\n.k b{font-size:32px}\n.pagehead h1{background:linear-gradient(90deg,#fff,#C9C2FF);-webkit-background-clip:text;background-clip:text;color:transparent}\n.note{border-radius:16px}\n@media (max-width:760px){.clock{display:none}.grp{display:none}section{padding:18px}}\n'

STYLE_CSS += ".tile.mine{border-color:rgba(139,124,255,.8);box-shadow:inset 0 0 0 1px rgba(139,124,255,.4)}"

JOURNAL_HTML = '<!doctype html>\n<html lang="cs"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>Deník obchodů</title>\n<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">\n<link rel="stylesheet" href="style.css"><script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script></head>\n<body class="bg-diary"><div class="app">@@SIDEBAR@@<div class="content"><header class="pagehead"><h1>Deník obchodů</h1><p>Tvoje obchody, statistiky a poučení</p></header><main>\n<div class="head"><div class="tabs" role="tablist">\n<button role="tab" data-tab="over" aria-selected="true">Přehled</button><button role="tab" data-tab="trades" aria-selected="false">Obchody</button><button role="tab" data-tab="rep" aria-selected="false">Reporty</button><button role="tab" data-tab="ai" aria-selected="false">Analýza</button><button role="tab" data-tab="disc" aria-selected="false">Disciplína</button></div>\n<button id="add" type="button">+ Přidat obchod</button></div>\n\n<div id="t-over"><div id="bk"></div><div class="gdyn" id="gdyn"></div><div class="kpis" id="kpis"></div>\n<section class="p"><h2>Vývoj zisku (najeď myší na graf)</h2><div id="curve"></div></section>\n<section class="p"><h2>Výsledek po dnech</h2><div class="chartbox" style="height:220px"><canvas id="c-day"></canvas></div></section>\n<section class="p"><div class="calhead"><button class="ghost sm" onclick="mv(-1)" aria-label="Předchozí měsíc">‹</button><b id="calt"></b><button class="ghost sm" onclick="mv(1)" aria-label="Další měsíc">›</button><span id="calsum"></span></div><div class="cal" id="cal"></div></section></div>\n\n<div id="t-trades" hidden><section class="p"><h2>Otevřené pozice</h2><div id="open"></div></section>\n<section class="p"><h2>Všechny obchody</h2><div id="list"></div>\n<div class="row"><button class="ghost" id="exp" type="button">Zálohovat do souboru</button><button class="ghost" id="imp" type="button">Obnovit ze zálohy</button><input id="impf" type="file" accept="application/json" hidden></div>\n<p class="empty">Obchody se ukládají jen v tomto prohlížeči na tomto zařízení. Zálohuj je pravidelně.</p></section></div>\n\n<div id="t-rep" hidden><section class="p"><h2>Filtry</h2><div class="grid" id="flt">\n<label>Od<input type="date" id="f-from"></label><label>Do<input type="date" id="f-to"></label>\n<label>Symbol<select id="f-sym"></select></label><label>Směr<select id="f-dir"><option value="">Oba</option><option value="long">Long</option><option value="short">Short</option></select></label>\n<label>Výsledek<select id="f-res"><option value="">Všechny</option><option value="w">Výhry</option><option value="l">Prohry</option></select></label><label>Strategie<select id="f-str"></select></label></div>\n<div class="chips" id="f-emo"></div><div class="row"><button class="ghost sm" id="f-reset" type="button">Reset</button></div></section>\n<section class="p"><h2>Souhrn filtrovaných obchodů</h2><div class="kpis" id="rsum"></div>\n<div class="two" id="rbars"></div></section></div>\n<div id="t-ai" hidden>\n<section class="p"><h2>Hodnocení tvého obchodování</h2><p class="hint">Hodnotí pravidla v prohlížeči zdarma, bez umělé inteligence. Pro hlubší rozbor zmáčkni tlačítko dole a vlož data do chatu s Claudem.</p><div id="coach"></div></section>\n<section class="p"><div class="two"><div><h2>Skóre tradera</h2><div class="chartbox" style="height:300px"><canvas id="c-radar"></canvas></div></div>\n<div><h2>S trendem a proti trendu</h2><div id="trendbars"></div><h2 style="margin-top:18px">S fundamentem a proti němu</h2><p class="hint">Porovnává tvůj směr s ranním biasem, který robot uložil ten den.</p><div id="alignbars"></div></div></div></section>\n<section class="p"><h2>Roční přehled</h2><div class="chartbox" style="height:240px"><canvas id="c-year"></canvas></div><div id="yeartab" style="margin-top:12px"></div></section>\n<section class="p"><h2>Účty</h2><div id="accbars"></div></section>\n<section class="p"><h2>Import a data pro Claude</h2><div class="row"><button class="ghost" id="csvb" type="button">Importovat obchody z CSV</button><input id="csvf" type="file" accept=".csv,text/csv" hidden><button id="cp" type="button">Zkopírovat data pro Claude</button></div>\n<p class="empty">CSV musí mít hlavičku se sloupci: datum, symbol, smer, kontrakty, vstup, vystup, stop (stop je nepovinný). Zkopírovaná data vlož do chatu na claude.ai a napiš: „Rozeber mi můj deník.“</p><p id="cpmsg" class="empty"></p></section></div>\n<div id="t-disc" hidden><section class="p"><h2>Tiltmeter: křivka účtu a dodržování plánu</h2><div id="d-tilt"></div></section><section class="p"><h2>Cena obchodů mimo plán</h2><div id="d-leak"></div></section><section class="p"><h2>Emoce a chyby v penězích</h2><div id="d-emo"></div></section><section class="p"><h2>Zmeškané obchody (čekání je taky výsledek)</h2><div id="d-miss"></div></section><section class="p"><h2>Rozbor týdne</h2><div id="d-rev"></div></section><section class="p"><h2>Playbook: moje setupy</h2><div id="d-pb"></div></section><section class="p"><h2>Štítky obchodů</h2><div id="d-tags"></div></section></div>\n</main></div></div>\n\n<dialog id="dlg"><form id="f"><h2 id="dt">Nový obchod</h2><input type="hidden" name="id">\n<div class="grid">\n<label>Otevřeno<input name="opened" type="datetime-local" required></label><label>Zavřeno (po uzavření)<input name="closed" type="datetime-local"></label>\n<label>Instrument<input name="inst" value="MNQ" required></label><label>Směr<select name="dir"><option value="long">Long</option><option value="short">Short</option></select></label>\n<label>Počet mikro kontraktů<input name="size" type="number" step="1" min="1" value="1"></label><label>Vzdálenost stopu (body)<input id="sd" type="number" step="any" value="35"></label><input name="pv" type="hidden">\n<label>Vstup<input name="entry" type="number" step="any" required></label><label>Stop loss<input name="stop" type="number" step="any" required></label>\n<label>Take profit 1 (1:2)<input name="target" type="number" step="any" required></label><label>Take profit 2 (1:2,5)<input name="target2" type="number" step="any"></label><label>Výstup<input name="exit" type="number" step="any"></label>\n<label>Poplatky ($)<input name="fee" type="number" step="any"></label><label>Strategie<input name="strat" list="strs"></label><label>Trend dne<select name="trend"><option value="">–</option><option value="with">Obchoduji s trendem</option><option value="against">Obchoduji proti trendu</option></select></label><label>Účet<input name="acc" value="Hlavní" list="accs"></label><label>Štítky (čárkou)<input name="tags" placeholder="např. NFP, otevření"></label></div><p class="empty" id="pbnote" style="margin:6px 0 0"></p><datalist id="strs"></datalist><datalist id="accs"></datalist>\n<p class="empty" id="entrynote" style="margin:8px 0 0"></p><div class="note" id="rr" style="margin-top:12px">Zadej vstupní cenu a stop loss i oba take profity se doplní samy.</div>\n<div class="grid" style="margin-top:12px"><label>Dodržel jsem plán?<select name="plan"><option value="">–</option><option value="ano">Ano</option><option value="ne">Ne</option></select></label><label>Hodnocení obchodu<select name="rate"><option value="">–</option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select></label></div><label style="margin-top:12px">Chyby</label><div class="chips" id="d-mis"></div>\n<label style="margin-top:12px">Emoce</label><div class="chips" id="d-emo"></div>\n<label style="margin-top:12px">Poznámky<textarea name="notes" rows="3"></textarea></label>\n<label style="margin-top:12px">Screenshoty (soubory nebo vložení přes Ctrl+V)<input name="shot" type="file" accept="image/*" multiple></label>\n<p class="empty" id="shotinfo"></p>\n<div class="row"><button type="submit">Uložit</button><button type="button" class="ghost" id="cancel">Zrušit</button></div></form></dialog>\n<dialog id="view"><div id="vbody"></div><div class="row"><button class="ghost" onclick="this.closest(\'dialog\').close()">Zavřít</button></div></dialog>\n\n<script>\nif (window.Chart) { Chart.defaults.color = "#C9D1E8"; Chart.defaults.borderColor = "rgba(255,255,255,.07)"; Chart.defaults.font.family = \'"IBM Plex Sans",system-ui,sans-serif\'; }\nif (window.Chart) Chart.register({id: "cross", afterDraw(c) { if (c.config.type === "line" && c.tooltip && c.tooltip._active && c.tooltip._active.length) { const x = c.tooltip._active[0].element.x, a = c.chartArea, g = c.ctx; g.save(); g.beginPath(); g.moveTo(x, a.top); g.lineTo(x, a.bottom); g.lineWidth = 1; g.strokeStyle = "rgba(255,255,255,.28)"; g.setLineDash([4, 4]); g.stroke(); g.restore(); } }});\nconst S = "trades", EMO = [["😌","Klid"],["💪","Sebevědomí"],["🏃","FOMO"],["🤑","Chamtivost"],["😨","Strach"],["😡","Revenge"],["⏱️","Netrpělivost"],["😕","Nejistota"],["🤯","Tilt"],["😴","Nuda"]];\nconst MIS = ["Brzký vstup","Pozdní vstup","Přesunutý stop","Předčasný výstup","Přeobchodování","Bez setupu","FOMO","Revenge obchod","Moc velká pozice"];\nconst lsGet = k => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } };\nconst PV = {NQ:20, MNQ:2, ES:50, MES:5, YM:5, MYM:0.5};\nlet touched = {}, dist = null, dayc = null, eq = null, db, shots = [], calD = new Date(), E = Object.create(null);\nconst $ = id => document.getElementById(id), sum = a => a.reduce((x, y) => x + y, 0);\nconst esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;"}[c]));\nconst nf = (x, d = 0) => x.toLocaleString("cs-CZ", {maximumFractionDigits: d});\nconst sg = (x, d = 0) => (x >= 0 ? "+" : "\\u2212") + nf(Math.abs(x), d), usd = x => sg(x) + "\\u00a0$", cl = x => x >= 0 ? "up" : "down";\nconst localNow = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };\n\nconst open = () => new Promise((res, rej) => { const r = indexedDB.open("journal", 1);\n  r.onupgradeneeded = () => r.result.createObjectStore(S, {keyPath: "id", autoIncrement: true}); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });\nconst tx = (m, f) => new Promise((res, rej) => { const t = db.transaction(S, m), o = f(t.objectStore(S)); t.oncomplete = () => res(o && o.result); t.onerror = () => rej(t.error); });\n\nconst dir = t => t.dir === "short" ? -1 : 1, when = t => t.opened || t.date + "T00:00", day = t => when(t).slice(0, 10);\nconst imgs = t => t.shots || (t.shot ? [t.shot] : []);\nconst pnl = t => t.exit == null ? null : (t.exit - t.entry) * dir(t) * (t.size || 1) * (t.pv || PV[t.inst] || 1) - (t.fee || 0);\nconst risk = t => Math.abs(t.entry - t.stop);\nconst real = t => (t.exit != null && risk(t)) ? (t.exit - t.entry) * dir(t) / risk(t) : null;\nconst plan = t => risk(t) ? Math.abs(t.target - t.entry) / risk(t) : null;\n\nfunction st(list) {\n  const d = list.filter(t => pnl(t) !== null).sort((a, b) => when(a).localeCompare(when(b))), p = d.map(pnl);\n  const w = p.filter(x => x > 0), l = p.filter(x => x < 0), net = sum(p);\n  let c = 0, pk = 0, dd = 0, ws = 0, ls = 0, mw = 0, ml = 0;\n  p.forEach(x => { c += x; pk = Math.max(pk, c); dd = Math.min(dd, c - pk);\n    if (x > 0) { ws++; ls = 0; } else if (x < 0) { ls++; ws = 0; } else ws = ls = 0; mw = Math.max(mw, ws); ml = Math.max(ml, ls); });\n  const days = {}; d.forEach(t => days[day(t)] = (days[day(t)] || 0) + pnl(t));\n  const dv = Object.values(days), rs = d.map(real).filter(x => x !== null);\n  const hs = d.filter(t => t.closed && t.opened).map(t => (new Date(t.closed) - new Date(t.opened)) / 60000);\n  return {d, p, n: d.length, net, win: w.length, loss: l.length, be: p.length - w.length - l.length, days,\n    wr: d.length ? w.length / d.length * 100 : null, pf: l.length ? sum(w) / Math.abs(sum(l)) : null,\n    avgW: w.length ? sum(w) / w.length : 0, avgL: l.length ? sum(l) / l.length : 0, exp: d.length ? net / d.length : 0,\n    avgR: rs.length ? sum(rs) / rs.length : null, best: p.length ? Math.max(...p) : 0, worst: p.length ? Math.min(...p) : 0,\n    dd, mw, ml, dayWin: dv.length ? dv.filter(x => x > 0).length / dv.length * 100 : null, hold: hs.length ? sum(hs) / hs.length : null};\n}\nconst K = (v, l) => `<div class="k"><b>${v}</b><span>${l}</span></div>`;\nconst dash = "–";\n\nfunction over(trades) {\nfunction dg(el,v,big,sub,word,cls,cap){var a=Math.max(-1,Math.min(1,v))*80;el.innerHTML=`<div class="gbox"><svg class="gauge" viewBox="0 0 200 110"><defs><linearGradient id="gd"><stop offset="0" stop-color="#FF6B8A"/><stop offset=".5" stop-color="#C9D1E8"/><stop offset="1" stop-color="#2EE6A6"/></linearGradient></defs><path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="url(#gd)" stroke-width="14" stroke-linecap="round"/><g transform="rotate(${a} 100 100)"><line x1="100" y1="100" x2="100" y2="34" stroke="#EEF1FA" stroke-width="3" stroke-linecap="round"/></g><circle cx="100" cy="100" r="6" fill="#EEF1FA"/></svg><div class="gnum ${cls}">${big}<span> ${sub}</span></div><div class="gword ${cls}">${word}</div><div class="gcap">${cap}</div></div>`;}  const s = st(trades);\n  dg($("gdyn"), Math.max(-1, Math.min(1, s.net / 500)), usd(s.net), "", s.n === 0 ? "Zatím bez obchodů" : s.net > 0 ? "Bullish výsledek" : s.net < 0 ? "Bearish výsledek" : "Neutral", s.net > 0 ? "up" : s.net < 0 ? "down" : "", s.n + " obchodů celkem");\n  $("kpis").innerHTML = K(`<span class="${cl(s.net)}">${usd(s.net)}</span>`, `Celkový zisk, ${s.n} obchodů`) +\n    K(s.wr == null ? dash : nf(s.wr, 1) + "\\u00a0%", `Úspěšnost (${s.win} výher, ${s.loss} proher, ${s.be} BE)`) +\n    K(s.pf == null ? dash : nf(s.pf, 2), `Profit factor (prům. výhra ${nf(s.avgW)} $ / ztráta ${nf(Math.abs(s.avgL))} $)`) +\n    K(s.dayWin == null ? dash : nf(s.dayWin, 1) + "\\u00a0%", "Ziskové dny") +\n    K(`<span class="down">${usd(s.dd)}</span>`, "Maximální drawdown");\n  if (eq) { eq.destroy(); eq = null; }\n  $("curve").innerHTML = s.n < 2 ? \'<p class="empty">Graf se zobrazí od dvou uzavřených obchodů.</p>\' : \'<div class="chartbox"><canvas id="c-eq"></canvas></div>\';\n  if (s.n >= 2 && window.Chart) { let c = 0; const cum = s.p.map(x => c += x);\n    eq = new Chart($("c-eq"), {type: "line", data: {labels: s.d.map(t => day(t)), datasets: [{data: cum, borderColor: "#8B7CFF", borderWidth: 2, tension: .3, fill: true, pointRadius: 0, pointHoverRadius: 5,\n      backgroundColor: x => { const g = x.chart.ctx.createLinearGradient(0, 0, 0, x.chart.height); g.addColorStop(0, "rgba(139,124,255,.35)"); g.addColorStop(1, "rgba(139,124,255,0)"); return g; }}]},\n      options: {maintainAspectRatio: false, interaction: {mode: "index", intersect: false}, plugins: {legend: {display: false}, tooltip: {callbacks: {label: x => "Celkem " + usd(x.parsed.y), afterLabel: x => "Tento obchod: " + usd(s.p[x.dataIndex])}}},\n      scales: {x: {ticks: {maxTicksLimit: 6}, grid: {display: false}}, y: {ticks: {callback: v => nf(v) + " $"}}}}}); }\n  dayChart(s);\n  const y = calD.getFullYear(), m = calD.getMonth(), pre = `${y}-${String(m + 1).padStart(2, "0")}`, off = (new Date(y, m, 1).getDay() + 6) % 7;\n  let h = ["Po","Út","St","Čt","Pá","So","Ne"].map(d => `<b class="dow">${d}</b>`).join("") + "<i></i>".repeat(off), tot = 0;\n  for (let d = 1; d <= new Date(y, m + 1, 0).getDate(); d++) { const v = s.days[`${pre}-${String(d).padStart(2, "0")}`]; if (v != null) tot += v;\n    h += `<div class="day ${v == null ? "" : cl(v)}"><span>${d}</span>${v == null ? "" : nf(v)}</div>`; }\n  $("cal").innerHTML = h; $("calt").textContent = calD.toLocaleString("cs-CZ", {month: "long", year: "numeric"});\n  $("calsum").innerHTML = `<span class="${cl(tot)}">${usd(tot)}</span>`;\n}\nfunction dayChart(s) {\n  if (dayc) { dayc.destroy(); dayc = null; }\n  const k = Object.keys(s.days).sort();\n  if (!window.Chart || !$("c-day") || !k.length) return;\n  dayc = new Chart($("c-day"), {type: "bar", data: {labels: k, datasets: [{data: k.map(d => s.days[d]), borderRadius: 6, hoverBackgroundColor: "#8B7CFF", backgroundColor: k.map(d => s.days[d] >= 0 ? "#2EE6A6" : "#FF6B8A")}]},\n    options: {maintainAspectRatio: false, plugins: {legend: {display: false}, tooltip: {callbacks: {label: x => usd(x.parsed.y)}}}, scales: {x: {ticks: {maxTicksLimit: 8}, grid: {display: false}}, y: {ticks: {callback: v => nf(v) + " $"}}}}});\n}\nconst mv = n => { calD = new Date(calD.getFullYear(), calD.getMonth() + n, 1); render(); };\n\nconst emoStr = t => (t.emo || []).map(e => (EMO.find(x => x[1] === e) || [""])[0]).join("");\nfunction table(rows, isOpen) {\n  if (!rows.length) return `<p class="empty">${isOpen ? "Žádná otevřená pozice." : "Zatím žádný obchod. Přidej první tlačítkem nahoře."}</p>`;\n  return `<div class="wrap"><table><thead><tr><th>Otevřeno</th><th>Symbol</th><th>Směr</th><th class="n">Mikro</th><th class="n">Vstup</th><th class="n">${isOpen ? "Stop" : "Výstup"}</th><th class="n">${isOpen ? "TP1" : "Net P&amp;L"}</th><th class="n">${isOpen ? "TP2" : "R"}</th>${isOpen ? "" : \'<th class="hide">Emoce</th><th class="hide">Strategie</th>\'}<th></th></tr></thead><tbody>` +\n  rows.map(t => { const p = pnl(t), r = real(t);\n    return `<tr><td>${esc(when(t).replace("T", " "))}</td><td>${esc(t.inst)}</td><td>${t.dir === "short" ? "Short" : "Long"}</td><td class="n">${nf(t.size || 1, 0)}</td><td class="n">${nf(t.entry, 2)}</td>` +\n    (isOpen ? `<td class="n">${nf(t.stop, 2)}</td><td class="n">${nf(t.target, 2)} <span class="empty">(1:${nf(plan(t) || 0, 1)})</span></td><td class="n">${t.target2 != null ? nf(t.target2, 2) + \' <span class="empty">(1:\' + nf(Math.abs(t.target2 - t.entry) / (risk(t) || 1), 1) + ")</span>" : dash}</td>`\n      : `<td class="n">${nf(t.exit, 2)}</td><td class="n ${cl(p)}">${usd(p)}</td><td class="n">${r == null ? dash : sg(r, 2) + "R"}</td><td class="hide">${emoStr(t)}</td><td class="hide">${esc(t.strat || "")}</td>`) +\n    `<td class="n"><button class="ghost sm" data-a="view" data-id="${t.id}">${imgs(t).length ? "📷 " + imgs(t).length : "Detail"}</button> ` +\n    (isOpen ? `<button class="sm" data-a="close" data-id="${t.id}">Uzavřít</button> ` : "") +\n    `<button class="ghost sm" data-a="edit" data-id="${t.id}">Upravit</button> <button class="ghost sm" data-a="del" data-id="${t.id}">Smazat</button></td></tr>`; }).join("") + "</tbody></table></div>";\n}\n\nfunction bars(title, groups) {\n  const rows = groups.map(([k, ts]) => { const s = st(ts); return {k, net: s.net, n: s.n, wr: s.wr}; }).filter(r => r.n);\n  const mx = Math.max(1, ...rows.map(r => Math.abs(r.net)));\n  return `<div><h3>${title}</h3>` + (rows.length ? rows.map(r => `<div class="bar"><span>${esc(r.k)}</span><div class="track ${cl(r.net)}"><i style="width:${Math.abs(r.net) / mx * 100}%"></i></div><b class="${cl(r.net)}">${usd(r.net)} <em>${r.n}× · ${nf(r.wr)} %</em></b></div>`).join("") : \'<p class="empty">Žádná data.</p>\') + "</div>";\n}\nfunction reports() {\n  const g = id => $(id).value, em = [...document.querySelectorAll("#f-emo input:checked")].map(i => i.value);\n  const list = ALL.filter(t => pnl(t) !== null && (!g("f-from") || day(t) >= g("f-from")) && (!g("f-to") || day(t) <= g("f-to")) &&\n    (!g("f-sym") || t.inst === g("f-sym")) && (!g("f-dir") || t.dir === g("f-dir")) && (!g("f-str") || t.strat === g("f-str")) &&\n    (!g("f-res") || (g("f-res") === "w" ? pnl(t) > 0 : pnl(t) < 0)) && em.every(e => (t.emo || []).includes(e)));\n  const s = st(list);\n  $("rsum").innerHTML = K(`<span class="${cl(s.net)}">${usd(s.net)}</span>`, "Net P&L") + K(s.n, "Obchody") + K(s.wr == null ? dash : nf(s.wr, 1) + "\\u00a0%", "Úspěšnost") +\n    K(s.pf == null ? dash : nf(s.pf, 2), "Profit factor") + K(s.avgR == null ? dash : sg(s.avgR, 2) + "R", "Průměrné R") + K(usd(s.exp), "Expectancy na obchod") +\n    K(usd(s.best), "Nejlepší") + K(usd(s.worst), "Nejhorší") + K(s.mw, "Série výher") + K(s.ml, "Série proher") + K(nf(s.avgW) + " $", "Prům. výhra") + K(s.hold == null ? dash : nf(s.hold, 0) + " min", "Prům. délka");\n  const hr = t => new Date(when(t)).getHours(), wd = t => (new Date(when(t)).getDay() + 6) % 7;\n  $("rbars").innerHTML = bars("Podle hodiny otevření", [...Array(24).keys()].map(h => [String(h).padStart(2, "0") + ":00", list.filter(t => hr(t) === h)])) +\n    bars("Podle dne v týdnu", ["Po","Út","St","Čt","Pá","So","Ne"].map((n, i) => [n, list.filter(t => wd(t) === i)])) +\n    bars("Podle emoce", EMO.map(([i, n]) => [i + " " + n, list.filter(t => (t.emo || []).includes(n))])) +\n    bars("Podle strategie", [...new Set(list.map(t => t.strat).filter(Boolean))].map(n => [n, list.filter(t => t.strat === n)])) +\n    bars("Podle dodržení plánu", [["Dodržel plán", list.filter(t => t.plan === "ano")], ["Nedodržel plán", list.filter(t => t.plan === "ne")]]) +\n    bars("Podle chyby", MIS.map(n => [n, list.filter(t => (t.mis || []).includes(n))]));\n}\n\nlet ALL = [];\nasync function render() {\n  ALL = await tx("readonly", s => s.getAll());\n  const rows = [...ALL].sort((a, b) => when(b).localeCompare(when(a)));\n  over(ALL);\n  $("open").innerHTML = table(rows.filter(t => t.exit == null), true);\n  $("list").innerHTML = table(rows.filter(t => t.exit != null), false);\n  const keep = [$("f-sym").value, $("f-str").value], opt = (a, all) => `<option value="">${all}</option>` + a.map(x => `<option>${esc(x)}</option>`).join("");\n  const strs = [...new Set(ALL.map(t => t.strat).filter(Boolean))];\n  $("f-sym").innerHTML = opt([...new Set(ALL.map(t => t.inst))], "Všechny"); $("f-str").innerHTML = opt(strs, "Všechny");\n  $("f-sym").value = keep[0]; $("f-str").value = keep[1];\n  $("accs").innerHTML = [...new Set(ALL.map(t => t.acc).filter(Boolean))].map(s => `<option value="${esc(s)}">`).join("");\n  $("strs").innerHTML = strs.map(s => `<option value="${esc(s)}">`).join("");\n  reports();\n}\n\nconst chips = (box, name) => $(box).innerHTML = EMO.map(([i, n]) => `<label class="chip"><input type="checkbox" ${name ? `name="${name}"` : ""} value="${n}">${i} ${n}</label>`).join("");\nchips("d-emo", "emo"); $("d-mis").innerHTML = MIS.map(n => `<label class="chip"><input type="checkbox" name="mis" value="${n}">${n}</label>`).join(""); chips("f-emo");\ndocument.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {\n  document.querySelectorAll(".tabs button").forEach(x => x.setAttribute("aria-selected", x === b));\n  ["over", "trades", "rep", "ai", "disc"].forEach(k => $("t-" + k).hidden = k !== b.dataset.tab); if (b.dataset.tab === "ai") analysis(); });\n$("flt").addEventListener("input", reports); $("f-emo").addEventListener("input", reports);\n$("f-reset").onclick = () => { document.querySelectorAll("#flt input,#flt select").forEach(i => i.value = ""); document.querySelectorAll("#f-emo input").forEach(i => i.checked = false); reports(); };\n\nfunction dlg(t) {\n  const f = $("f"); f.reset(); shots = t ? [...imgs(t)] : []; touched = t ? {target: 1, target2: 1} : {}; dist = t ? (Math.abs(t.entry - t.stop) || null) : null; $("sd").value = lsGet("defStop35") || 35;\n  $("dt").textContent = t ? "Upravit obchod" : "Nový obchod"; f.elements.opened.value = localNow(); f.elements.acc.value = lsGet("lastAcc") || "Hlavní";\n  if (t) { for (const k of ["id","opened","closed","inst","dir","size","pv","entry","stop","target","target2","exit","fee","strat","plan","rate","notes","trend","acc","tags"]) f.elements[k].value = (k === "opened" ? when(t) : t[k]) ?? "";\n    f.querySelectorAll(\'[name=emo]\').forEach(c => c.checked = (t.emo || []).includes(c.value));\n    f.querySelectorAll(\'[name=mis]\').forEach(c => c.checked = (t.mis || []).includes(c.value)); }\n  rr(); $("shotinfo").textContent = shots.length ? shots.length + " screenshotů uloženo." : ""; $("dlg").showModal(); entryAuto = false; $("entrynote").textContent = ""; if (!t) fillEntry();\n}\n$("add").onclick = () => dlg(); $("cancel").onclick = () => $("dlg").close();\n$("f").elements.shot.onchange = e => { shots.push(...e.target.files); $("shotinfo").textContent = shots.length + " screenshotů připraveno."; e.target.value = ""; };\ndocument.addEventListener("paste", e => { const f = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));\n  if ($("dlg").open && f.length) { shots.push(...f); $("shotinfo").textContent = shots.length + " screenshotů připraveno."; } });\n$("f").onsubmit = async e => {\n  e.preventDefault(); const f = e.target.elements, n = k => f[k].value === "" ? null : parseFloat(f[k].value);\n  const t = {opened: f.opened.value, closed: f.closed.value || null, inst: f.inst.value.toUpperCase(), dir: f.dir.value, size: n("size") || 1, pv: n("pv"),\n    entry: n("entry"), stop: n("stop"), target: n("target"), target2: n("target2"), exit: n("exit"), fee: n("fee"), strat: f.strat.value.trim(), tags: f.tags.value.trim(), trend: f.trend.value, acc: f.acc.value.trim() || "Hlavní", notes: f.notes.value,\n    emo: [...e.target.querySelectorAll("[name=emo]:checked")].map(c => c.value), plan: f.plan.value, rate: n("rate"),\n    mis: [...e.target.querySelectorAll("[name=mis]:checked")].map(c => c.value), shots};\n  if (f.id.value) t.id = parseInt(f.id.value);\n  try { localStorage.setItem("lastAcc", JSON.stringify(t.acc)); } catch (x) {} await tx("readwrite", s => s.put(t)); $("dlg").close(); render();\n};\n\ndocument.addEventListener("click", async e => {\n  const b = e.target.closest("[data-a]"); if (!b) return; const t = ALL.find(x => x.id == b.dataset.id), a = b.dataset.a;\n  if (a === "edit") dlg(t);\n  else if (a === "del") { if (confirm("Smazat tento obchod?")) { await tx("readwrite", s => s.delete(t.id)); render(); } }\n  else if (a === "close") { const v = parseFloat(String(prompt("Výstupní cena?") || "").replace(",", ".")); if (!isNaN(v)) { t.exit = v; t.closed = localNow(); await tx("readwrite", s => s.put(t)); render(); } }\n  else if (a === "view") { $("vbody").innerHTML = `<h2>${esc(t.inst)} ${t.dir}, ${esc(when(t).replace("T", " "))}</h2><p>${esc(t.notes || "Bez poznámky.")}</p><p>Plán: ${t.plan || "–"} | Hodnocení: ${t.rate || "–"} | Chyby: ${esc((t.mis || []).join(", ") || "žádné")}</p>`;\n    imgs(t).forEach(im => { const i = document.createElement("img"); i.src = URL.createObjectURL(im); i.alt = "Screenshot obchodu"; $("vbody").appendChild(i); }); $("view").showModal(); }\n});\n\nconst toUrl = b => new Promise(r => { const f = new FileReader(); f.onload = () => r(f.result); f.readAsDataURL(b); });\n$("exp").onclick = async () => {\n  const out = []; for (const t of ALL) out.push({...t, shot: undefined, shots: await Promise.all(imgs(t).map(toUrl))});\n  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], {type: "application/json"}));\n  a.download = "zaloha-denik-" + new Date().toISOString().slice(0, 10) + ".json"; a.click();\n};\n$("imp").onclick = () => $("impf").click();\n$("impf").onchange = async e => {\n  try { const ts = JSON.parse(await e.target.files[0].text());\n    for (const t of ts) { t.shots = await Promise.all((t.shots || []).map(async u => (await fetch(u)).blob())); delete t.id; await tx("readwrite", s => s.add(t)); }\n    render();\n  } catch { alert("Soubor se nepodařilo načíst. Je to záloha z tohoto deníku?"); }\n  e.target.value = "";\n};\nconst rnd = x => Math.round(x * 4) / 4;\nfunction rr() {\n  const f = $("f").elements, e = parseFloat(f.entry.value), s = parseFloat(f.stop.value), n = parseFloat(f.size.value) || 1, pv = PV[f.inst.value.toUpperCase()] || 1;\n  if (isNaN(e) || isNaN(s) || e === s) { $("rr").textContent = "Zadej vstupní cenu a stop loss a oba take profity se doplní samy."; return; }\n  const d = Math.abs(e - s), t1 = parseFloat(f.target.value), t2 = parseFloat(f.target2.value);\n  $("rr").innerHTML = `Riskuješ <b>${nf(d, 2)} bodů</b> = <b>${nf(d * pv * n)} $</b> (${nf(n, 0)} mikro). ` +\n    (isNaN(t1) ? "" : `TP1 (1:${nf(Math.abs(t1 - e) / d, 1)}): <b class="up">+${nf(Math.abs(t1 - e) * pv * n)} $</b>. `) +\n    (isNaN(t2) ? "" : `TP2 (1:${nf(Math.abs(t2 - e) / d, 1)}): <b class="up">+${nf(Math.abs(t2 - e) * pv * n)} $</b>.`);\n}\nfunction auto(src) {\n  const f = $("f").elements, e = parseFloat(f.entry.value), sg = f.dir.value === "short" ? -1 : 1;\n  if (src === "stop") touched.stop = 1;\n  if (isNaN(e)) return rr();\n  if (src === "sd") { dist = null; touched.stop = 0; }\n  const s0 = parseFloat(f.stop.value);\n  if ((src === "stop" || (touched.stop && dist == null)) && !isNaN(s0) && s0 !== e) dist = Math.abs(e - s0);\n  const d = dist || parseFloat($("sd").value) || 35;\n  if (src !== "stop") f.stop.value = rnd(e - sg * d);\n  if (!touched.target) f.target.value = rnd(e + sg * 2 * d);\n  if (!touched.target2) f.target2.value = rnd(e + sg * 2.5 * d);\n  rr();\n}\n{ const fe = $("f").elements;\n  fe.entry.addEventListener("input", () => auto("entry")); fe.dir.addEventListener("change", () => auto("dir")); fe.stop.addEventListener("input", () => auto("stop"));\n  fe.target.addEventListener("input", () => { touched.target = 1; rr(); }); fe.target2.addEventListener("input", () => { touched.target2 = 1; rr(); });\n  fe.size.addEventListener("input", rr); fe.inst.addEventListener("input", rr);\n  $("sd").addEventListener("input", () => { try { localStorage.setItem("defStop35", JSON.stringify($("sd").value)); } catch (e) {} auto("sd"); }); }\nlet HIST = [], radar = null, yearc = null;\nfetch("history.json", {cache: "no-store"}).then(r => r.ok ? r.json() : []).then(h => { HIST = Array.isArray(h) ? h : []; }).catch(() => {});\nfunction biasOf(t) { const h = HIST.find(x => x.date === day(t)); if (!h) return null; const b = (h.label || "").toLowerCase(); return b.includes("bullish") ? 1 : b.includes("bearish") ? -1 : 0; }\nfunction grp(list, fn) { const w = [], a = []; list.forEach(t => { const r = fn(t); if (r === 1) w.push(t); else if (r === -1) a.push(t); }); return [w, a]; }\nfunction score(s, list) {\n  const pf = s.pf == null ? 0 : Math.min(100, s.pf / 2 * 100), wr = s.wr == null ? 0 : Math.min(100, s.wr / 60 * 100);\n  const planned = list.filter(t => t.plan); const pl = planned.length ? planned.filter(t => t.plan === "ano").length / planned.length * 100 : 50;\n  const rs = list.map(real).filter(x => x !== null); const rr2 = rs.length ? Math.min(100, Math.max(0, sum(rs) / rs.length * 100 + 40)) : 0;\n  const dd = s.net > 0 ? Math.min(100, Math.max(0, 100 - Math.abs(s.dd) / Math.max(1, s.net) * 40)) : 20;\n  const emoBad = list.filter(t => (t.emo || []).some(e => ["FOMO", "Revenge", "Tilt", "Chamtivost", "Netrpělivost"].includes(e))).length;\n  const calm = list.length ? 100 - emoBad / list.length * 100 : 50;\n  return [["Profit factor", pf], ["Úspěšnost", wr], ["Dodržení plánu", pl], ["Poměr zisku k riziku", rr2], ["Kontrola ztrát", dd], ["Klid a emoce", calm]].map(x => [x[0], Math.round(x[1])]);\n}\nfunction grade(sc) { const m = sum(sc.map(x => x[1])) / sc.length; return m >= 80 ? ["A", m] : m >= 65 ? ["B", m] : m >= 50 ? ["C", m] : m >= 35 ? ["D", m] : ["E", m]; }\nfunction analysis() {\n  const list = ALL.filter(t => pnl(t) !== null), s = st(list);\n  if (s.n < 3) { $("coach").innerHTML = \'<p class="empty">Přidej aspoň 3 uzavřené obchody a objeví se hodnocení.</p>\'; return; }\n  const sc = score(s, list), [g, m] = grade(sc), good = [], bad = [];\n  if (s.pf != null && s.pf >= 1.5) good.push("Profit factor " + nf(s.pf, 2) + ": výhry jsou větší než ztráty."); else if (s.pf != null && s.pf < 1) bad.push("Profit factor " + nf(s.pf, 2) + " je pod 1, ztráty převažují nad zisky.");\n  if (s.wr != null && s.wr >= 50) good.push("Úspěšnost " + nf(s.wr, 0) + " %."); else if (s.wr != null) bad.push("Úspěšnost je jen " + nf(s.wr, 0) + " %. Zpřísni výběr setupů.");\n  const np = list.filter(t => t.plan === "ne"), yp = list.filter(t => t.plan === "ano");\n  if (np.length) bad.push("Plán jsi nedodržel u " + np.length + " obchodů, jejich výsledek " + usd(sum(np.map(pnl))) + ".");\n  if (yp.length) good.push("Podle plánu jsi obchodoval " + yp.length + "× s výsledkem " + usd(sum(yp.map(pnl))) + ".");\n  if (s.avgR != null && s.avgR > 0.3) good.push("Průměrné R je " + sg(s.avgR, 2) + ".");\n  if (s.ml >= 3) bad.push("Série " + s.ml + " proher za sebou. Po dvou ztrátách si udělej pauzu.");\n  const bt = {}; list.forEach(t => (t.emo || []).forEach(e => bt[e] = (bt[e] || 0) + pnl(t)));\n  const worst = Object.entries(bt).sort((a, b) => a[1] - b[1])[0]; if (worst && worst[1] < 0) bad.push("Nejdražší emoce: " + worst[0] + " (" + usd(worst[1]) + ").");\n  const ms = {}; list.forEach(t => (t.mis || []).forEach(e => ms[e] = (ms[e] || 0) + 1)); const mm = Object.entries(ms).sort((a, b) => b[1] - a[1])[0]; if (mm) bad.push("Nejčastější chyba: " + mm[0] + " (" + mm[1] + "×).");\n  if (s.n < 20) bad.push("Zatím jen " + s.n + " obchodů. Známka je orientační, spolehlivá bude od 20 obchodů.");\n  if (!good.length) good.push("Zatím málo dat na silné stránky. Pokračuj v zápisech."); if (!bad.length) bad.push("Žádná výrazná slabina. Drž proces.");\n  const li = a => a.map(x => `<li>${esc(x)}</li>`).join("");\n  $("coach").innerHTML = `<div class="two"><div class="k"><b style="font-size:46px">${g}</b><span>Známka (${nf(m, 0)} ze 100)</span></div><div><h3>Silné stránky</h3><ul class="facts">${li(good)}</ul><h3 style="margin-top:12px">Slabé stránky</h3><ul class="facts">${li(bad)}</ul></div></div>`;\n  if (radar) radar.destroy(); if (window.Chart) radar = new Chart($("c-radar"), {type: "radar", data: {labels: sc.map(x => x[0]), datasets: [{data: sc.map(x => x[1]), borderColor: "#8B7CFF", backgroundColor: "rgba(139,124,255,.25)", pointBackgroundColor: "#38BDF8"}]},\n    options: {maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {r: {min: 0, max: 100, ticks: {display: false}, grid: {color: "rgba(255,255,255,.1)"}, angleLines: {color: "rgba(255,255,255,.1)"}}}}});\n  const [tw, ta] = grp(list, t => t.trend === "with" ? 1 : t.trend === "against" ? -1 : 0);\n  $("trendbars").innerHTML = bars("Trend", [["S trendem", tw], ["Proti trendu", ta]]);\n  const [fw, fa] = grp(list, t => { const b = biasOf(t); if (b === null || b === 0) return 0; return b === dir(t) ? 1 : -1; });\n  $("alignbars").innerHTML = bars("Fundament", [["Se směrem biasu", fw], ["Proti biasu", fa]]) + (HIST.length ? "" : \'<p class="empty">Historie biasu se začne plnit po prvním běhu robota.</p>\');\n  const ym = {}; list.forEach(t => { const k = day(t).slice(0, 7); ym[k] = (ym[k] || 0) + pnl(t); }); const yk = Object.keys(ym).sort();\n  if (yearc) yearc.destroy(); if (window.Chart && yk.length) yearc = new Chart($("c-year"), {type: "bar", data: {labels: yk, datasets: [{data: yk.map(k => ym[k]), borderRadius: 6, backgroundColor: yk.map(k => ym[k] >= 0 ? "#2EE6A6" : "#FF6B8A")}]},\n    options: {maintainAspectRatio: false, plugins: {legend: {display: false}, tooltip: {callbacks: {label: x => usd(x.parsed.y)}}}, scales: {x: {grid: {display: false}}, y: {ticks: {callback: v => nf(v) + " $"}}}}});\n  $("yeartab").innerHTML = yk.length ? `<div class="wrap"><table><thead><tr><th>Měsíc</th><th class="n">Obchodů</th><th class="n">Výsledek</th></tr></thead><tbody>${yk.map(k => `<tr><td>${k}</td><td class="n">${list.filter(t => day(t).slice(0, 7) === k).length}</td><td class="n ${cl(ym[k])}">${usd(ym[k])}</td></tr>`).join("")}</tbody></table></div>` : "";\n  $("accbars").innerHTML = bars("Podle účtu", [...new Set(list.map(t => t.acc || "Hlavní"))].map(a => [a, list.filter(t => (t.acc || "Hlavní") === a)]));\n}\n$("csvb").onclick = () => $("csvf").click();\n$("csvf").onchange = async e => {\n  try { const lines = (await e.target.files[0].text()).trim().split(/\\r?\\n/), sep = lines[0].includes(";") ? ";" : ",", h = lines[0].toLowerCase().split(sep).map(x => x.trim()); let n = 0;\n    const ix = k => h.findIndex(x => x.startsWith(k));\n    for (const ln of lines.slice(1)) { const c = ln.split(sep), v = k => c[ix(k)], num = k => { const x = parseFloat(String(v(k) ?? "").replace(",", ".")); return isNaN(x) ? null : x; };\n      if (!v("dat") || num("vst") == null) continue; const d = String(v("dat")).trim().slice(0, 10), dr = /^short|^s$/i.test((v("sm") || v("dir") || "").trim()) ? "short" : "long";\n      const inst = (v("sym") || "MNQ").toUpperCase(), e0 = num("vst"), st0 = num("stop"), d0 = st0 != null ? Math.abs(e0 - st0) : 35, sg2 = dr === "short" ? -1 : 1;\n      await tx("readwrite", s => s.add({opened: d + "T09:30", closed: d + "T10:00", inst, dir: dr, size: num("kontr") || 1, entry: e0, stop: st0 ?? rnd(e0 - sg2 * d0), target: rnd(e0 + sg2 * 2 * d0), target2: rnd(e0 + sg2 * 2.5 * d0), exit: num("vyst"), fee: num("poplat"), acc: "Import", emo: [], mis: [], shots: []})); n++; }\n    alert("Načteno obchodů: " + n); render();\n  } catch (x) { alert("CSV se nepodařilo načíst."); } e.target.value = "";\n};\n$("cp").onclick = async () => {\n  const list = ALL.filter(t => pnl(t) !== null), s = st(list);\n  const rows = list.map(t => [day(t), t.inst, t.dir, t.size, t.entry, t.stop, t.exit, Math.round(pnl(t)), t.trend || "", t.plan || "", (t.emo || []).join("/"), (t.mis || []).join("/"), t.strat || "", t.acc || ""].join(";")).join("\\n");\n  const txt = `Rozeber mi můj obchodní deník (mikro kontrakty MNQ). Dej mi známku, silné a slabé stránky a 3 konkrétní kroky.\\nSouhrn: obchodů ${s.n}, výsledek ${Math.round(s.net)} $, úspěšnost ${s.wr == null ? "-" : Math.round(s.wr)} %, profit factor ${s.pf == null ? "-" : s.pf.toFixed(2)}.\\nSloupce: datum;symbol;směr;kontrakty;vstup;stop;výstup;výsledek $;trend;plán;emoce;chyby;strategie;účet\\n${rows}`;\n  try { await navigator.clipboard.writeText(txt); $("cpmsg").textContent = "Zkopírováno. Vlož do chatu na claude.ai."; } catch (x) { prompt("Zkopíruj text:", txt); }\n};\nlet PR = {}, entryAuto = false;\nfetch("prices.json", {cache: "no-store"}).then(r => r.ok ? r.json() : {}).then(p => { PR = p || {}; if ($("dlg").open && !$("f").elements.id.value) fillEntry(); }).catch(() => {});\nfunction fillEntry() {\n  const f = $("f").elements, d = (f.opened.value || "").slice(0, 10);\n  if (f.id.value || !/^(MNQ|NQ)/i.test(f.inst.value)) return;\n  let v = PR.p && PR.p[d], txt = "";\n  if (v != null) txt = "Vstupní cena je předvyplněná cenou Nasdaq futures dne " + d + " v 15:00 pražského času. Můžeš ji přepsat.";\n  else if (PR.last && PR.last.d === d) { v = PR.last.v; txt = "Cena v 15:00 ještě není k dispozici. Předvyplněna je poslední známá cena (" + PR.last.t + "). Můžeš ji přepsat."; }\n  if (v == null) { $("entrynote").textContent = "Cena v 15:00 pro tento den není uložená. Zadej vstup ručně."; return; }\n  f.entry.value = rnd(v); entryAuto = true; $("entrynote").textContent = txt; auto("entry");\n}\n$("f").elements.entry.addEventListener("input", () => { entryAuto = false; $("entrynote").textContent = ""; });\n$("f").elements.opened.addEventListener("change", () => { if (entryAuto || !$("f").elements.entry.value) fillEntry(); });\n$("f").elements.inst.addEventListener("change", () => { if (!$("f").elements.entry.value) fillEntry(); });\n/* ===== Disciplína, playbook, zmeškané obchody, rozbor týdne, záloha ===== */\nconst LS = {get: (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }};\nconst todayS = () => localNow().slice(0, 10);\nconst weekKey = (d0) => { const d = new Date(d0 + "T12:00"); const wd = (d.getDay() + 6) % 7; d.setDate(d.getDate() - wd); return d.toISOString().slice(0, 10); };\nconst REASONS = ["Nesplnil jsem podmínky setupu", "Čekal jsem na větší timeframe", "Byl jsem na denním limitu", "Nebyl čas nebo klid", "Bál jsem se (chyba, setup byl platný)", "Jiný důvod"];\nconst tbl = (head, rows) => `<div class="wrap"><table><thead><tr>${head.map((h, i) => `<th${i ? " class=\\"n\\"" : ""}>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;\nconst rowH = a => "<tr>" + a.map((c, i) => `<td${i ? " class=\\"n\\"" : ""}>${c}</td>`).join("") + "</tr>";\nconst cs = x => `<span class="${cl(x)}">${usd(x)}</span>`;\n\nfunction tilt(closed) {\n  if (!closed.length) return "<p class=\\"empty\\">Zatím žádné uzavřené obchody. Tiltmeter se začne kreslit po prvním obchodu.</p>";\n  const n = closed.length, W = Math.max(n, 12) * 14, H = 110; let c = 0; const eqv = closed.map(t => c += pnl(t));\n  const mn = Math.min(0, ...eqv), mx = Math.max(0, ...eqv), rg = (mx - mn) || 1, y = v => 8 + (1 - (v - mn) / rg) * 64;\n  const pts = eqv.map((v, i) => `${i * 14 + 7},${y(v).toFixed(1)}`).join(" ");\n  const cells = closed.map((t, i) => `<rect x="${i * 14 + 1}" y="88" width="12" height="16" rx="3" fill="${t.plan === "ano" ? "#2EE6A6" : t.plan === "ne" ? "#FF6B8A" : "#5B6785"}"><title>${day(t)}: ${usd(pnl(t))}${t.plan ? ", plán " + t.plan : ""}</title></rect>`).join("");\n  let cur = 0, curT = null; for (let i = n - 1; i >= 0; i--) { const p = closed[i].plan; if (p !== "ano" && p !== "ne") break; if (curT === null) curT = p; if (p !== curT) break; cur++; }\n  let best = 0, run = 0; closed.forEach(t => { if (t.plan === "ano") { run++; best = Math.max(best, run); } else run = 0; });\n  const msg = curT === "ano" ? `Aktuální série: ${cur}× po sobě jsi dodržel plán.` : curT === "ne" ? `Pozor: poslední ${cur}× jsi plán nedodržel. Zastav se a dej si pauzu.` : "U posledního obchodu není vyplněno, jestli jsi dodržel plán.";\n  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:130px"><line x1="0" x2="${W}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" stroke="rgba(255,255,255,.25)" stroke-dasharray="4 4"/><polyline points="${pts}" fill="none" stroke="#8B7CFF" stroke-width="2.5" vector-effect="non-scaling-stroke"/>${cells}</svg>` +\n    `<p class="hint">Fialová čára je křivka účtu. Zelené čtverečky dole jsou obchody podle plánu, červené mimo plán, šedé bez odpovědi. ${msg} Nejdelší série podle plánu: ${best}.</p>`;\n}\n\nfunction missedHtml() {\n  const L = LS.get("jr:missed", []), ok = L.filter(x => x.ok).length;\n  return `<div class="grid"><label>Datum<input type="date" id="ms-d" value="${todayS()}"></label><label>Setup<input id="ms-s" list="pbl" placeholder="např. Break and retest"></label>` +\n    `<label>Proč jsem ho nevzal<select id="ms-r">${REASONS.map(r => `<option>${r}</option>`).join("")}</select></label></div>` +\n    `<label class="chip" style="margin-top:8px"><input type="checkbox" id="ms-ok" checked>Správně jsem počkal (byl jsem v souladu s plánem)</label><div class="row"><button type="button" id="ms-add">Zapsat zmeškaný obchod</button></div>` +\n    (L.length ? `<p class="hint">Celkem ${L.length} zmeškaných obchodů, z toho ${ok}× jsi správně počkal.</p>` + tbl(["Datum", "Setup", "Důvod", "Správně", ""], [...L].reverse().map(x => rowH([x.d, esc(x.s), esc(x.r), x.ok ? "<span class=\\"up\\">ano</span>" : "<span class=\\"down\\">ne</span>", `<button class="ghost sm" data-ms="${x.id}">Smazat</button>`]))) : "<p class=\\"empty\\">Zatím nic. Když uvidíš dobrý setup a nevezmeš ho, zapiš proč. Čekání je taky výsledek.</p>");\n}\n\nfunction reviewHtml(closed) {\n  const wk = weekKey(todayS()), R = LS.get("jr:rev", {}), cur = R[wk] || {}, tr = closed.filter(t => weekKey(day(t)) === wk), net = sum(tr.map(pnl));\n  const pl = tr.filter(t => t.plan === "ano").length, pn = tr.filter(t => t.plan === "ne").length, ms = LS.get("jr:missed", []).filter(x => weekKey(x.d) === wk && x.ok).length;\n  const stats = `<div class="kpis">${K(tr.length, "obchodů tento týden")}${K(cs(net), "výsledek týdne")}${K(pl + pn ? nf(pl / (pl + pn) * 100) + "&nbsp;%" : dash, "dodržení plánu")}${K(ms, "správně počkáno")}</div>`;\n  const q = [["a", "Co se mi tento týden dařilo?"], ["b", "Kde jsem porušil pravidla a proč?"], ["c", "Co zlepším příští týden? (jedna věc)"]];\n  const old = Object.keys(R).filter(k => k !== wk).sort().reverse().slice(0, 8);\n  return stats + q.map(([k, l]) => `<label style="margin-top:10px">${l}<textarea rows="2" data-rv="${k}">${esc(cur[k] || "")}</textarea></label>`).join("") +\n    `<label style="margin-top:10px;max-width:260px">Známka týdne<select data-rv="g"><option value="">–</option>${[1, 2, 3, 4, 5].map(i => `<option${String(cur.g) === String(i) ? " selected" : ""}>${i}</option>`).join("")}</select></label><p class="empty">Týden od ${wk}. Ukládá se samo. Nejlepší čas je neděle večer.</p>` +\n    (old.length ? `<details style="margin-top:10px"><summary>Dřívější rozbory (${old.length})</summary>${old.map(k => `<p class="hint"><b>Týden od ${k}</b>${R[k].g ? " (známka " + R[k].g + ")" : ""}<br>${esc(R[k].a || "")}<br>${esc(R[k].b || "")}<br><i>${esc(R[k].c || "")}</i></p>`).join("")}</details>` : "");\n}\n\nfunction playbookHtml(closed) {\n  const P = LS.get("jr:pb", []);\n  const rows = P.map(p => { const l = closed.filter(t => (t.strat || "").toLowerCase() === p.name.toLowerCase()), s = st(l); return rowH([`<b>${esc(p.name)}</b><br><span class="empty">${esc(p.desc || "")}</span><br><span class="empty">Vstup jen když: ${esc(p.rules || "–")}</span>`, s.n, s.wr == null ? dash : nf(s.wr) + "&nbsp;%", cs(s.net), `<button class="ghost sm" data-pbd="${esc(p.name)}">Smazat</button>`]); });\n  return `<div class="grid"><label>Název setupu<input id="pb-n" placeholder="např. Break and retest"></label><label>Popis<input id="pb-d" placeholder="co to je"></label><label>Podmínky vstupu<input id="pb-r" placeholder="např. větší TF souhlasí, retest, potvrzení"></label></div><div class="row"><button type="button" id="pb-add">Přidat do playbooku</button></div>` +\n    (P.length ? tbl(["Setup", "Obchodů", "Úspěšnost", "Výsledek", ""], rows) : "<p class=\\"empty\\">Playbook je seznam tvých pár ověřených setupů. Doporučuju mít jen 1 až 3. Ve formuláři obchodu pak vybereš setup a statistiky se spočítají zvlášť.</p>") + `<datalist id="pbl">${P.map(p => `<option value="${esc(p.name)}">`).join("")}</datalist>`;\n}\n\nfunction disc() {\n  const closed = ALL.filter(t => pnl(t) !== null).sort((a, b) => when(a).localeCompare(when(b))), s = st(closed);\n  const broken = closed.filter(t => t.plan === "ne"), leak = sum(broken.map(pnl)), ans = closed.filter(t => t.plan === "ano" || t.plan === "ne").length;\n  $("d-tilt").innerHTML = tilt(closed);\n  $("d-leak").innerHTML = closed.length ? `<div class="kpis">${K(cs(leak), "cena obchodů mimo plán (" + broken.length + "×)")}${K(cs(s.net - leak), "výsledek bez nich")}${K(ans ? nf((ans - broken.length) / ans * 100) + "&nbsp;%" : dash, "dodržení plánu")}</div><p class="hint">${leak < 0 ? "Kdybys těchto obchodů nevzal, měl bys na účtu o " + nf(Math.abs(leak)) + " $ víc. To je cena netrpělivosti." : broken.length ? "Obchody mimo plán dopadly dobře, ale jsou riziko. Dlouhodobě vždy platí plán." : "Zatím žádný obchod mimo plán. Skvělé."}</p>` : "<p class=\\"empty\\">Zatím žádné obchody.</p>";\n  const cost = (names, key) => names.map(n => { const l = closed.filter(t => (t[key] || []).includes(n)); return [n, l.length, sum(l.map(pnl))]; }).filter(x => x[1]).sort((a, b) => a[2] - b[2]);\n  const emo = cost(EMO.map(e => e[1]), "emo").map(x => [(EMO.find(e => e[1] === x[0]) || [""])[0] + " " + x[0], x[1], x[2]]), mis = cost(MIS, "mis");\n  const ct = r => r.length ? tbl(["", "Obchodů", "Součet", "Průměr"], r.map(x => rowH([x[0], x[1], cs(x[2]), cs(x[2] / x[1])]))) : "<p class=\\"empty\\">Zatím nic. Při zápisu obchodu odškrtni emoce a chyby.</p>";\n  $("d-emo").innerHTML = "<h3>Kolik tě stojí emoce</h3>" + ct(emo) + "<h3 style=\\"margin-top:16px\\">Nejdražší chyby</h3>" + ct(mis);\n  const tg = {}; closed.forEach(t => (t.tags || "").split(",").map(x => x.trim()).filter(Boolean).forEach(x => (tg[x.toLowerCase()] = tg[x.toLowerCase()] || []).push(t)));\n  const tk = Object.keys(tg);\n  $("d-tags").innerHTML = tk.length ? tbl(["Štítek", "Obchodů", "Úspěšnost", "Výsledek"], tk.map(k => { const q = st(tg[k]); return rowH([esc(k), q.n, q.wr == null ? dash : nf(q.wr) + "&nbsp;%", cs(q.net)]); })) : "<p class=\\"empty\\">Ve formuláři obchodu je políčko Štítky. Napiš třeba: NFP, otevření, retest. Tady se ukáže, který štítek ti vydělává.</p>";\n  $("d-miss").innerHTML = missedHtml(); $("d-rev").innerHTML = reviewHtml(closed); $("d-pb").innerHTML = playbookHtml(closed);\n  const strs = [...new Set([...ALL.map(t => t.strat), ...LS.get("jr:pb", []).map(p => p.name)].filter(Boolean))]; $("strs").innerHTML = strs.map(x => `<option value="${esc(x)}">`).join("");\n  bkInfo();\n}\nfunction bkInfo() {\n  const last = LS.get("jr:lastbk", null), days = last ? Math.floor((Date.now() - last) / 864e5) : null, need = ALL.length && (days === null || days >= 7);\n  $("bk").innerHTML = ALL.length ? `<div class="note" style="margin-bottom:12px${need ? ";border-color:#FF6B8A" : ""}">${last ? "Poslední záloha: před " + days + " dny." : "Deník ještě nikdy nebyl zálohovaný."} ${need ? "Doporučuju zálohovat teď. Deník je jen v tomto prohlížeči." : "Vše v pořádku."} <button class="ghost sm" id="bk-now" type="button">Zálohovat teď</button></div>` : "";\n  if ($("bk-now")) $("bk-now").onclick = () => $("exp").click();\n}\ndocument.addEventListener("click", e => {\n  const t = e.target, g = a => t.getAttribute && t.getAttribute(a);\n  if (t.id === "ms-add") { const s = $("ms-s").value.trim(); if (!s) { $("ms-s").focus(); return; } const L = LS.get("jr:missed", []); L.push({id: Date.now(), d: $("ms-d").value || todayS(), s, r: $("ms-r").value, ok: $("ms-ok").checked}); LS.set("jr:missed", L); disc(); }\n  else if (g("data-ms")) { LS.set("jr:missed", LS.get("jr:missed", []).filter(x => String(x.id) !== g("data-ms"))); disc(); }\n  else if (t.id === "pb-add") { const n = $("pb-n").value.trim(); if (!n) { $("pb-n").focus(); return; } const P = LS.get("jr:pb", []).filter(p => p.name.toLowerCase() !== n.toLowerCase()); P.push({name: n, desc: $("pb-d").value.trim(), rules: $("pb-r").value.trim()}); LS.set("jr:pb", P); disc(); }\n  else if (g("data-pbd")) { if (confirm("Smazat setup z playbooku? Obchody zůstanou.")) { LS.set("jr:pb", LS.get("jr:pb", []).filter(p => p.name !== g("data-pbd"))); disc(); } }\n});\ndocument.addEventListener("input", e => { const k = e.target.getAttribute && e.target.getAttribute("data-rv"); if (!k) return; const wk = weekKey(todayS()), R = LS.get("jr:rev", {}); R[wk] = R[wk] || {}; R[wk][k] = e.target.value; LS.set("jr:rev", R); });\n$("f").elements.strat.addEventListener("input", e => { const p = LS.get("jr:pb", []).find(x => x.name.toLowerCase() === e.target.value.trim().toLowerCase()); $("pbnote").textContent = p ? "Podmínky tohoto setupu: " + (p.rules || "nevyplněno") + ". Splňuješ je všechny?" : ""; });\n$("exp").onclick = async () => {\n  const out = []; for (const t of ALL) out.push({...t, shot: undefined, shots: await Promise.all(imgs(t).map(toUrl))});\n  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify({v: 2, trades: out, extra: {missed: LS.get("jr:missed", []), pb: LS.get("jr:pb", []), rev: LS.get("jr:rev", {})}})], {type: "application/json"}));\n  a.download = "zaloha-denik-" + todayS() + ".json"; a.click(); LS.set("jr:lastbk", Date.now()); bkInfo();\n};\n$("impf").onchange = async e => {\n  try { const raw = JSON.parse(await e.target.files[0].text()), ts = Array.isArray(raw) ? raw : raw.trades;\n    for (const t of ts) { t.shots = await Promise.all((t.shots || []).map(async u => (await fetch(u)).blob())); delete t.id; await tx("readwrite", s => s.add(t)); }\n    if (raw.extra) { const m = LS.get("jr:missed", []), ids = new Set(m.map(x => x.id)); (raw.extra.missed || []).forEach(x => { if (!ids.has(x.id)) m.push(x); }); LS.set("jr:missed", m);\n      const pb = LS.get("jr:pb", []), nm = new Set(pb.map(p => p.name.toLowerCase())); (raw.extra.pb || []).forEach(p => { if (!nm.has(p.name.toLowerCase())) pb.push(p); }); LS.set("jr:pb", pb);\n      LS.set("jr:rev", Object.assign({}, raw.extra.rev || {}, LS.get("jr:rev", {}))); }\n    render();\n  } catch (x) { alert("Soubor se nepodařilo načíst. Je to záloha z tohoto deníku?"); }\n  e.target.value = "";\n};\nconst _render = render; render = async function () { await _render(); disc(); };\n\nopen().then(d => { db = d; render(); });\n</script><script src="tips.js"></script></body></html>'

STYLE_CSS += """
.lede.warn{color:#FFB454;text-shadow:0 0 40px rgba(255,180,84,.4)}
.badge.warn{background:rgba(255,180,84,.2);color:#FFB454}
.tile.ev{background:rgba(255,255,255,.05)}.tile.ev.soon{background:rgba(255,180,84,.18);border-color:rgba(255,180,84,.5)}
.kpis .k b.warn{color:#FFB454}
"""

STYLE_CSS += """
/* v9 */
.video{position:relative;padding-top:56.25%;border-radius:14px;overflow:hidden;border:1px solid var(--rule)}.video iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
.scens{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:10px}
.scen{border:1px solid var(--rule);border-radius:14px;padding:12px 14px;background:var(--glass)}.scen b{display:block;margin-bottom:4px}.scen span{font-size:13px;color:var(--muted)}
.scen.up{border-color:rgba(46,230,166,.5);background:rgba(46,230,166,.08)}.scen.down{border-color:rgba(255,107,138,.5);background:rgba(255,107,138,.08)}.scen.warn{border-color:rgba(255,180,84,.5);background:rgba(255,180,84,.08)}
.evh{margin-bottom:6px}.cat{border-top:1px solid var(--rule);padding:12px 0}.cat:first-child{border-top:0}.cath{display:flex;justify-content:space-between;align-items:center;gap:10px}
#cats.brief .det{display:none}#cats.full .det{display:block}.det ul li.up::marker{color:#2EE6A6}.det ul li.down::marker{color:#FF6B8A}
.glos{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}.gl{border:1px solid var(--rule);border-radius:12px;padding:10px 14px;background:var(--glass)}.gl b{display:block}.gl span{font-size:13px;color:var(--muted)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.side a span{font-size:12px;width:16px;text-align:center;opacity:.8}.side a{gap:9px;padding:9px 12px;font-size:14px}
.brand small{display:block;font-size:11px;font-weight:500;letter-spacing:.04em;margin-top:2px;-webkit-text-fill-color:var(--muted);color:var(--muted)}
.exm{margin:6px 0 0;font-size:14px}details summary{cursor:pointer}
"""

STYLE_CSS += """
/* v12 */
.side{width:268px;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}.gword{font-size:20px;font-weight:700;letter-spacing:.02em}.gword.up{color:var(--up)}.gword.down{color:var(--down)}.gdyn{margin:10px 0 4px;display:flex;justify-content:center}.clock{margin:0 0 12px;padding:10px 14px}.clock b{font-size:26px}.brand{padding-bottom:14px}
.tip{border-bottom:1px dotted #C9D1E8;cursor:help;position:relative}.tip sup{color:#38BDF8;font-size:.7em;margin-left:1px;font-weight:700}
.tip:hover::after,.tip:focus::after{content:attr(data-tip);position:absolute;left:0;top:calc(100% + 6px);z-index:60;width:max-content;max-width:260px;white-space:normal;text-align:left;background:#0C1124;border:1px solid rgba(139,124,255,.7);color:#EEF1FA;padding:10px 12px;border-radius:10px;font-size:14px;font-weight:400;font-style:normal;line-height:1.4;box-shadow:0 14px 34px -8px rgba(0,0,0,.9)}
.tip.r:hover::after,.tip.r:focus::after{left:auto;right:0}
.lede .tip sup,.k b .tip sup{font-size:.32em}
.xptop{display:flex;justify-content:space-between;gap:10px;margin-top:16px;align-items:baseline;flex-wrap:wrap}.xpbar{height:12px;border-radius:99px;background:rgba(255,255,255,.1);overflow:hidden;margin-top:6px}
.xpbar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#8B7CFF,#38BDF8,#2EE6A6);border-radius:99px;transition:width .6s}
.ringbox{display:flex;flex-direction:column;align-items:center;gap:6px;flex:0 0 auto!important}.ring{width:120px;height:120px;border-radius:50%;background:rgba(255,255,255,.12);display:flex;align-items:center;justify-content:center;position:relative}
.ring::before{content:"";position:absolute;inset:12px;border-radius:50%;background:#0C1124}.ring b{position:relative;font-size:26px}.ring.big{width:230px;height:230px}.ring.big::before{inset:16px}
.center2{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin:14px 0}.bigbtn{padding:18px 30px;font-size:19px;border-radius:99px;box-shadow:0 0 50px -10px rgba(139,124,255,.9)}
.orb2{width:120px;height:120px;border-radius:50%;margin:20px auto;background:radial-gradient(circle at 35% 30%,#B3A9FF,#38BDF8 60%,#2B3A8F);box-shadow:0 0 50px rgba(139,124,255,.5);transform:scale(.7);transition:transform 4s ease-in-out}.orb2.big{transform:scale(1.15)}
.holdwrap{position:relative;width:230px;height:230px}.holdwrap .ring{position:absolute;inset:0}.holdbtn{position:absolute;inset:34px;border-radius:50%;font-size:24px;touch-action:none;user-select:none;-webkit-user-select:none;background:linear-gradient(135deg,#8B7CFF,#38BDF8);transition:transform .15s}.holdbtn.on{transform:scale(.94)}
.qopts{display:grid;gap:10px;margin:14px 0}.qopt{text-align:left;padding:14px 16px;font-weight:500;line-height:1.35}.qopt.good{border-color:#2EE6A6!important;background:rgba(46,230,166,.16)!important}.qopt.bad{border-color:#FF6B8A!important;background:rgba(255,107,138,.16)!important}
.badges{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}.bd{border:1px solid var(--rule);border-radius:16px;padding:14px;text-align:center;filter:grayscale(1);opacity:.4}.bd.on{filter:none;opacity:1;border-color:rgba(139,124,255,.7);background:rgba(139,124,255,.12)}
.bd span{display:block;font-size:34px}.bd b{display:block;margin-top:4px}.bd em{display:block;font-style:normal;font-size:13px;color:var(--muted)}
.conf{position:fixed;top:-30px;font-size:26px;z-index:90;animation:fall 2s ease-in forwards;pointer-events:none}@keyframes fall{to{transform:translateY(110vh) rotate(360deg);opacity:.2}}
.toast{position:fixed;left:50%;bottom:40px;transform:translateX(-50%);background:#0C1124;border:1px solid #8B7CFF;border-radius:14px;padding:14px 22px;font-weight:600;z-index:95;box-shadow:0 20px 50px -10px rgba(0,0,0,.9)}
.lede.warn{color:#FFB454}
"""
STYLE_CSS = re.sub(r"font-size:(\d+(?:\.\d+)?)px", lambda m: "font-size:%spx" % (round(float(m.group(1)) * 1.14, 1) if float(m.group(1)) <= 30 else m.group(1)), STYLE_CSS)
STYLE_CSS += """
/* v14 čitelnost */
:root{--muted:#C9D1E8;--rule:rgba(255,255,255,.18);--ink:#FFFFFF}
body{font-size:17px;background:radial-gradient(1000px 520px at 10% -12%,rgba(139,124,255,.38),transparent 62%),radial-gradient(800px 480px at 96% 2%,rgba(56,189,248,.24),transparent 60%),radial-gradient(700px 500px at 50% 112%,rgba(244,114,182,.18),transparent 60%),#121A33 fixed}
.side{background:rgba(22,30,58,.94);border-right:1px solid rgba(255,255,255,.14)}
.side a{color:#DCE2F4;font-size:16.5px}.side a:hover{background:rgba(255,255,255,.1);color:#fff}
.grp{color:#9FD8FF;opacity:1;font-size:13.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.brand{font-size:21px}.brand small{font-size:13px;color:#C9D1E8;-webkit-text-fill-color:#C9D1E8}
.clock{background:rgba(255,255,255,.09);border-color:rgba(255,255,255,.2);color:#DCE2F4;font-size:14.5px}.clock b{color:#fff}
section,.k,.step{background:linear-gradient(160deg,rgba(255,255,255,.12),rgba(255,255,255,.06));border:1px solid rgba(255,255,255,.2)}
h2{font-size:20px;color:#fff}h2::before{content:"";display:inline-block;width:5px;height:18px;border-radius:3px;margin-right:10px;vertical-align:-2px;background:linear-gradient(#8B7CFF,#38BDF8)}
h3{font-size:16.5px;color:#7DD3FC}
.hint{color:#D5DCEF;font-size:16px}.facts{color:#E3E8F6}.facts li{margin:7px 0}.empty{color:#C9D1E8}
.pagehead p{color:#D5DCEF;font-size:17px}label{color:#DCE2F4;font-size:15px}.k span{color:#D5DCEF;font-size:15px}
th,td{font-size:16px}thead th{color:#C9D1E8}.bar span{color:#E3E8F6}.bar em{color:#C9D1E8}.tile span{color:rgba(255,255,255,.88)}footer{color:#C9D1E8}
.pill-off{color:#DCE2F4!important;border-color:rgba(255,255,255,.28)!important}
input,select,textarea{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.25)}button.ghost{border-color:rgba(255,255,255,.3)}
.chip{border-color:rgba(255,255,255,.3);font-size:15px}.tip{border-bottom-color:#7DD3FC}.step span:last-child{color:#D5DCEF;font-size:15px}
.badge{font-size:13.5px}.day{border-color:rgba(255,255,255,.2)}.day span{color:#D5DCEF}
"""

STYLE_CSS += """
/* v15 pozadí */
body::before{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;background-repeat:no-repeat}
body.bg-chart::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 1600 900%27 preserveAspectRatio=%27xMidYMax slice%27%3E%3Cdefs%3E%3ClinearGradient id=%27g%27 x1=%270%27 x2=%271%27%3E%3Cstop offset=%270%27 stop-color=%27%238B7CFF%27/%3E%3Cstop offset=%271%27 stop-color=%27%232EE6A6%27/%3E%3C/linearGradient%3E%3ClinearGradient id=%27a%27 x1=%270%27 y1=%270%27 x2=%270%27 y2=%271%27%3E%3Cstop offset=%270%27 stop-color=%27%238B7CFF%27 stop-opacity=%27.55%27/%3E%3Cstop offset=%271%27 stop-color=%27%238B7CFF%27 stop-opacity=%270%27/%3E%3C/linearGradient%3E%3C/defs%3E%3Cline x1=%270%27 y1=%27100%27 x2=%271600%27 y2=%27100%27 stroke=%27%23fff%27 stroke-opacity=%27.25%27 stroke-width=%271%27/%3E%3Cline x1=%270%27 y1=%27230%27 x2=%271600%27 y2=%27230%27 stroke=%27%23fff%27 stroke-opacity=%27.25%27 stroke-width=%271%27/%3E%3Cline x1=%270%27 y1=%27360%27 x2=%271600%27 y2=%27360%27 stroke=%27%23fff%27 stroke-opacity=%27.25%27 stroke-width=%271%27/%3E%3Cline x1=%270%27 y1=%27490%27 x2=%271600%27 y2=%27490%27 stroke=%27%23fff%27 stroke-opacity=%27.25%27 stroke-width=%271%27/%3E%3Cline x1=%270%27 y1=%27620%27 x2=%271600%27 y2=%27620%27 stroke=%27%23fff%27 stroke-opacity=%27.25%27 stroke-width=%271%27/%3E%3Cline x1=%270%27 y1=%27750%27 x2=%271600%27 y2=%27750%27 stroke=%27%23fff%27 stroke-opacity=%27.25%27 stroke-width=%271%27/%3E%3Cline x1=%270%27 y1=%27880%27 x2=%271600%27 y2=%27880%27 stroke=%27%23fff%27 stroke-opacity=%27.25%27 stroke-width=%271%27/%3E%3Cpath d=%27M0 549 L40 546 L80 574 L120 563 L160 557 L200 556 L240 523 L280 516 L320 519 L360 536 L400 495 L440 471 L480 430 L520 448 L560 456 L600 411 L640 443 L680 475 L720 480 L760 482 L800 446 L840 398 L880 393 L920 349 L960 316 L1000 287 L1040 240 L1080 230 L1120 217 L1160 238 L1200 232 L1240 237 L1280 229 L1320 235 L1360 224 L1400 200 L1440 234 L1480 267 L1520 288 L1560 298 L1600 275 L1600 900 L0 900 Z%27 fill=%27url%28%23a%29%27/%3E%3Cline x1=%2720%27 y1=%27522%27 x2=%2720%27 y2=%27568%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%2710%27 y=%27532%27 width=%2720%27 height=%277%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%2760%27 y1=%27520%27 x2=%2760%27 y2=%27605%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%2750%27 y=%27540%27 width=%2720%27 height=%2730%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%27100%27 y1=%27529%27 x2=%27100%27 y2=%27632%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%2790%27 y=%27544%27 width=%2720%27 height=%2754%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27140%27 y1=%27541%27 x2=%27140%27 y2=%27603%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%27130%27 y=%27562%27 width=%2720%27 height=%2734%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%27180%27 y1=%27548%27 x2=%27180%27 y2=%27584%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%27170%27 y=%27564%27 width=%2720%27 height=%2712%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%27220%27 y1=%27514%27 x2=%27220%27 y2=%27596%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%27210%27 y=%27546%27 width=%2720%27 height=%2741%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%27260%27 y1=%27489%27 x2=%27260%27 y2=%27541%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27250%27 y=%27499%27 width=%2720%27 height=%2712%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27300%27 y1=%27474%27 x2=%27300%27 y2=%27534%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%27290%27 y=%27497%27 width=%2720%27 height=%2726%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%27340%27 y1=%27469%27 x2=%27340%27 y2=%27545%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27330%27 y=%27497%27 width=%2720%27 height=%2739%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27380%27 y1=%27502%27 x2=%27380%27 y2=%27570%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27370%27 y=%27519%27 width=%2720%27 height=%2715%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27420%27 y1=%27447%27 x2=%27420%27 y2=%27528%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27410%27 y=%27483%27 width=%2720%27 height=%2733%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27460%27 y1=%27436%27 x2=%27460%27 y2=%27504%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%27450%27 y=%27465%27 width=%2720%27 height=%2731%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%27500%27 y1=%27396%27 x2=%27500%27 y2=%27492%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27490%27 y=%27412%27 width=%2720%27 height=%2750%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27540%27 y1=%27425%27 x2=%27540%27 y2=%27449%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27530%27 y=%27436%27 width=%2720%27 height=%275%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27580%27 y1=%27414%27 x2=%27580%27 y2=%27481%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27570%27 y=%27441%27 width=%2720%27 height=%2723%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27620%27 y1=%27385%27 x2=%27620%27 y2=%27465%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%27610%27 y=%27408%27 width=%2720%27 height=%2733%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%27660%27 y1=%27411%27 x2=%27660%27 y2=%27503%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27650%27 y=%27424%27 width=%2720%27 height=%2744%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27700%27 y1=%27445%27 x2=%27700%27 y2=%27525%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27690%27 y=%27460%27 width=%2720%27 height=%2737%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27740%27 y1=%27423%27 x2=%27740%27 y2=%27542%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27730%27 y=%27462%27 width=%2720%27 height=%2748%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27780%27 y1=%27466%27 x2=%27780%27 y2=%27497%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27770%27 y=%27477%27 width=%2720%27 height=%2714%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27820%27 y1=%27400%27 x2=%27820%27 y2=%27490%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27810%27 y=%27430%27 width=%2720%27 height=%2746%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27860%27 y1=%27387%27 x2=%27860%27 y2=%27431%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27850%27 y=%27404%27 width=%2720%27 height=%2717%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27900%27 y1=%27352%27 x2=%27900%27 y2=%27432%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27890%27 y=%27367%27 width=%2720%27 height=%2742%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27940%27 y1=%27339%27 x2=%27940%27 y2=%27408%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27930%27 y=%27356%27 width=%2720%27 height=%2717%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%27980%27 y1=%27270%27 x2=%27980%27 y2=%27320%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%27970%27 y=%27287%27 width=%2720%27 height=%2714%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%271020%27 y1=%27241%27 x2=%271020%27 y2=%27294%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271010%27 y=%27261%27 width=%2720%27 height=%2710%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271060%27 y1=%27182%27 x2=%271060%27 y2=%27271%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271050%27 y=%27218%27 width=%2720%27 height=%2717%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271100%27 y1=%27213%27 x2=%271100%27 y2=%27254%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271090%27 y=%27239%27 width=%2720%27 height=%275%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271140%27 y1=%27151%27 x2=%271140%27 y2=%27220%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%271130%27 y=%27189%27 width=%2720%27 height=%274%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%271180%27 y1=%27181%27 x2=%271180%27 y2=%27290%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%271170%27 y=%27210%27 width=%2720%27 height=%2759%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%271220%27 y1=%27182%27 x2=%271220%27 y2=%27257%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%271210%27 y=%27222%27 width=%2720%27 height=%2728%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%271260%27 y1=%27203%27 x2=%271260%27 y2=%27282%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271250%27 y=%27239%27 width=%2720%27 height=%2714%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271300%27 y1=%27204%27 x2=%271300%27 y2=%27266%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271290%27 y=%27241%27 width=%2720%27 height=%278%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271340%27 y1=%27210%27 x2=%271340%27 y2=%27280%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271330%27 y=%27246%27 width=%2720%27 height=%2716%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271380%27 y1=%27215%27 x2=%271380%27 y2=%27274%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271370%27 y=%27241%27 width=%2720%27 height=%277%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271420%27 y1=%27165%27 x2=%271420%27 y2=%27216%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271410%27 y=%27193%27 width=%2720%27 height=%2715%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271460%27 y1=%27206%27 x2=%271460%27 y2=%27295%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271450%27 y=%27242%27 width=%2720%27 height=%2724%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271500%27 y1=%27234%27 x2=%271500%27 y2=%27304%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271490%27 y=%27261%27 width=%2720%27 height=%2724%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cline x1=%271540%27 y1=%27231%27 x2=%271540%27 y2=%27317%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%271530%27 y=%27263%27 width=%2720%27 height=%2748%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%271580%27 y1=%27281%27 x2=%271580%27 y2=%27334%27 stroke=%27%232EE6A6%27 stroke-width=%273%27/%3E%3Crect x=%271570%27 y=%27297%27 width=%2720%27 height=%2710%27 rx=%273%27 fill=%27%232EE6A6%27/%3E%3Cline x1=%271620%27 y1=%27237%27 x2=%271620%27 y2=%27298%27 stroke=%27%23FF6B8A%27 stroke-width=%273%27/%3E%3Crect x=%271610%27 y=%27275%27 width=%2720%27 height=%2710%27 rx=%273%27 fill=%27%23FF6B8A%27/%3E%3Cpath d=%27M0 549 L40 546 L80 574 L120 563 L160 557 L200 556 L240 523 L280 516 L320 519 L360 536 L400 495 L440 471 L480 430 L520 448 L560 456 L600 411 L640 443 L680 475 L720 480 L760 482 L800 446 L840 398 L880 393 L920 349 L960 316 L1000 287 L1040 240 L1080 230 L1120 217 L1160 238 L1200 232 L1240 237 L1280 229 L1320 235 L1360 224 L1400 200 L1440 234 L1480 267 L1520 288 L1560 298 L1600 275%27 fill=%27none%27 stroke=%27url%28%23g%29%27 stroke-width=%277%27 stroke-linejoin=%27round%27/%3E%3C/svg%3E');background-size:cover;background-position:center bottom;opacity:.17}
body.bg-yinyang::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 500 500%27%3E%3Ccircle cx=%27250%27 cy=%27250%27 r=%27240%27 fill=%27none%27 stroke=%27%23fff%27 stroke-width=%278%27/%3E%3Cpath d=%27M250 10 A240 240 0 0 1 250 490 A120 120 0 0 1 250 250 A120 120 0 0 0 250 10 Z%27 fill=%27%23fff%27/%3E%3Ccircle cx=%27250%27 cy=%27130%27 r=%2732%27 fill=%27%23fff%27/%3E%3Ccircle cx=%27250%27 cy=%27370%27 r=%2732%27 fill=%27%23121A33%27/%3E%3C/svg%3E');background-size:min(78vh,720px);background-position:right 6% center;opacity:.13}
body.bg-buddha::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 500 520%27%3E%3Cg fill=%27none%27 stroke=%27%23B3A9FF%27 stroke-width=%273%27 stroke-dasharray=%276 10%27%3E%3Ccircle cx=%27250%27 cy=%27160%27 r=%27120%27/%3E%3Ccircle cx=%27250%27 cy=%27160%27 r=%27170%27/%3E%3Ccircle cx=%27250%27 cy=%27160%27 r=%27220%27/%3E%3C/g%3E%3Cg fill=%27%23fff%27%3E%3Ccircle cx=%27250%27 cy=%2772%27 r=%2714%27/%3E%3Ccircle cx=%27250%27 cy=%27125%27 r=%2746%27/%3E%3Cpath d=%27M195 190 Q250 160 305 190 L330 300 Q250 322 170 300 Z%27/%3E%3Cpath d=%27M180 295 Q250 350 320 295 L345 335 Q250 395 155 335 Z%27/%3E%3Cellipse cx=%27250%27 cy=%27370%27 rx=%27165%27 ry=%2746%27/%3E%3C/g%3E%3Cg fill=%27%2338BDF8%27%3E%3Cellipse cx=%27150%27 cy=%27440%27 rx=%2770%27 ry=%2720%27/%3E%3Cellipse cx=%27350%27 cy=%27440%27 rx=%2770%27 ry=%2720%27/%3E%3Cellipse cx=%27250%27 cy=%27450%27 rx=%2790%27 ry=%2724%27/%3E%3C/g%3E%3C/svg%3E');background-size:min(86vh,760px);background-position:right 5% center;opacity:.15}
body.bg-target::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 500 500%27%3E%3Cg fill=%27none%27 stroke=%27%23fff%27 stroke-width=%278%27%3E%3Ccircle cx=%27250%27 cy=%27250%27 r=%27230%27/%3E%3Ccircle cx=%27250%27 cy=%27250%27 r=%27160%27/%3E%3Ccircle cx=%27250%27 cy=%27250%27 r=%2790%27/%3E%3C/g%3E%3Ccircle cx=%27250%27 cy=%27250%27 r=%2728%27 fill=%27%23FF6B8A%27/%3E%3Cg stroke=%27%2338BDF8%27 stroke-width=%278%27%3E%3Cline x1=%27250%27 y1=%270%27 x2=%27250%27 y2=%27120%27/%3E%3Cline x1=%27250%27 y1=%27380%27 x2=%27250%27 y2=%27500%27/%3E%3Cline x1=%270%27 y1=%27250%27 x2=%27120%27 y2=%27250%27/%3E%3Cline x1=%27380%27 y1=%27250%27 x2=%27500%27 y2=%27250%27/%3E%3C/g%3E%3C/svg%3E');background-size:min(76vh,680px);background-position:right 6% center;opacity:.12}
body.bg-mountain::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 1600 700%27 preserveAspectRatio=%27xMidYMax slice%27%3E%3Cpath d=%27M0 700 L260 360 L420 500 L700 170 L960 460 L1180 260 L1600 640 L1600 700 Z%27 fill=%27%238B7CFF%27 fill-opacity=%27.7%27/%3E%3Cpath d=%27M0 700 L200 520 L420 640 L640 450 L900 640 L1200 500 L1600 700 Z%27 fill=%27%2338BDF8%27 fill-opacity=%27.5%27/%3E%3Cpath d=%27M700 170 L700 90 L770 115 L700 140%27 fill=%27%232EE6A6%27 stroke=%27%232EE6A6%27 stroke-width=%276%27/%3E%3C/svg%3E');background-size:cover;background-position:center bottom;opacity:.16}
@media (max-width:760px){body::before{opacity:.09!important}}
.brand small.mot{font-style:italic;color:#9FD8FF;-webkit-text-fill-color:#9FD8FF;margin-top:6px;line-height:1.35}.brand small{line-height:1.35}
"""
STYLE_CSS += """
/* v16 pozadí podle skupin */
body.bg-analysis::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 1600 900%27 preserveAspectRatio=%27xMidYMid slice%27%3E%3Crect x=%2780%27 y=%27520%27 width=%2746%27 height=%27180%27 rx=%278%27 fill=%27%2338BDF8%27/%3E%3Crect x=%27150%27 y=%27440%27 width=%2746%27 height=%27260%27 rx=%278%27 fill=%27%238B7CFF%27/%3E%3Crect x=%27220%27 y=%27550%27 width=%2746%27 height=%27150%27 rx=%278%27 fill=%27%2338BDF8%27/%3E%3Crect x=%27290%27 y=%27360%27 width=%2746%27 height=%27340%27 rx=%278%27 fill=%27%238B7CFF%27/%3E%3Crect x=%27360%27 y=%27280%27 width=%2746%27 height=%27420%27 rx=%278%27 fill=%27%2338BDF8%27/%3E%3Crect x=%27430%27 y=%27400%27 width=%2746%27 height=%27300%27 rx=%278%27 fill=%27%238B7CFF%27/%3E%3Crect x=%27500%27 y=%27220%27 width=%2746%27 height=%27480%27 rx=%278%27 fill=%27%2338BDF8%27/%3E%3Crect x=%27570%27 y=%27320%27 width=%2746%27 height=%27380%27 rx=%278%27 fill=%27%238B7CFF%27/%3E%3Ccircle cx=%271200%27 cy=%27380%27 r=%2780%27 fill=%27none%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Ccircle cx=%271200%27 cy=%27380%27 r=%27160%27 fill=%27none%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Ccircle cx=%271200%27 cy=%27380%27 r=%27240%27 fill=%27none%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Cline x1=%271200%27 y1=%27380%27 x2=%271440%27 y2=%27380%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Cline x1=%271200%27 y1=%27380%27 x2=%271320%27 y2=%27588%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Cline x1=%271200%27 y1=%27380%27 x2=%271080%27 y2=%27588%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Cline x1=%271200%27 y1=%27380%27 x2=%27960%27 y2=%27380%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Cline x1=%271200%27 y1=%27380%27 x2=%271080%27 y2=%27172%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Cline x1=%271200%27 y1=%27380%27 x2=%271320%27 y2=%27172%27 stroke=%27%23fff%27 stroke-opacity=%27.5%27 stroke-width=%273%27/%3E%3Cpolygon points=%271400,380 1275,510 1090,571 1080,380 1105,215 1285,233%27 fill=%27%232EE6A6%27 fill-opacity=%27.45%27 stroke=%27%232EE6A6%27 stroke-width=%275%27/%3E%3Ccircle cx=%27880%27 cy=%27560%27 r=%2790%27 fill=%27none%27 stroke=%27%23fff%27 stroke-width=%2714%27/%3E%3Cline x1=%27945%27 y1=%27625%27 x2=%271010%27 y2=%27690%27 stroke=%27%23fff%27 stroke-width=%2718%27 stroke-linecap=%27round%27/%3E%3C/svg%3E');background-size:cover;background-position:center;opacity:0.16}
body.bg-equity::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 1600 900%27 preserveAspectRatio=%27xMidYMax slice%27%3E%3Cpath d=%27M0 766 L100 742 L200 735 L300 704 L400 672 L500 695 L600 724 L700 670 L800 674 L900 681 L1000 611 L1100 594 L1200 541 L1300 523 L1400 489 L1500 504 L1600 471%27 fill=%27none%27 stroke=%27%23fff%27 stroke-width=%278%27 stroke-linejoin=%27round%27 stroke-dasharray=%271 0%27/%3E%3Cpath d=%27M100 708 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M200 701 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M300 670 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M400 638 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M500 729 l16 -28 h-32 z%27 fill=%27%23FF6B8A%27/%3E%3Cpath d=%27M600 758 l16 -28 h-32 z%27 fill=%27%23FF6B8A%27/%3E%3Cpath d=%27M700 636 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M800 708 l16 -28 h-32 z%27 fill=%27%23FF6B8A%27/%3E%3Cpath d=%27M900 715 l16 -28 h-32 z%27 fill=%27%23FF6B8A%27/%3E%3Cpath d=%27M1000 577 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M1100 560 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M1200 507 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M1300 489 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M1400 455 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Cpath d=%27M1500 538 l16 -28 h-32 z%27 fill=%27%23FF6B8A%27/%3E%3Cpath d=%27M1600 437 l16 28 h-32 z%27 fill=%27%232EE6A6%27/%3E%3Ccircle cx=%271300%27 cy=%27150%27 r=%2728%27 fill=%27none%27 stroke=%27%23FFB454%27 stroke-width=%276%27/%3E%3Ctext x=%271300%27 y=%27162%27 font-size=%2730%27 text-anchor=%27middle%27 fill=%27%23FFB454%27%3E%24%3C/text%3E%3Ccircle cx=%271370%27 cy=%27190%27 r=%2728%27 fill=%27none%27 stroke=%27%23FFB454%27 stroke-width=%276%27/%3E%3Ctext x=%271370%27 y=%27202%27 font-size=%2730%27 text-anchor=%27middle%27 fill=%27%23FFB454%27%3E%24%3C/text%3E%3Ccircle cx=%271440%27 cy=%27150%27 r=%2728%27 fill=%27none%27 stroke=%27%23FFB454%27 stroke-width=%276%27/%3E%3Ctext x=%271440%27 y=%27162%27 font-size=%2730%27 text-anchor=%27middle%27 fill=%27%23FFB454%27%3E%24%3C/text%3E%3Ccircle cx=%271510%27 cy=%27190%27 r=%2728%27 fill=%27none%27 stroke=%27%23FFB454%27 stroke-width=%276%27/%3E%3Ctext x=%271510%27 y=%27202%27 font-size=%2730%27 text-anchor=%27middle%27 fill=%27%23FFB454%27%3E%24%3C/text%3E%3C/svg%3E');background-size:cover;background-position:center bottom;opacity:0.17}
body.bg-book::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 1500 800%27 preserveAspectRatio=%27xMidYMid slice%27%3E%3Cpath d=%27M750 150 Q450 90 100 150 L100 700 Q450 640 750 700 Z%27 fill=%27%238B7CFF%27 fill-opacity=%27.35%27 stroke=%27%23fff%27 stroke-width=%278%27/%3E%3Cpath d=%27M750 150 Q1050 90 1400 150 L1400 700 Q1050 640 750 700 Z%27 fill=%27%2338BDF8%27 fill-opacity=%27.30%27 stroke=%27%23fff%27 stroke-width=%278%27/%3E%3Cline x1=%27150%27 y1=%27230%27 x2=%27640%27 y2=%27230%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27860%27 y1=%27230%27 x2=%271350%27 y2=%27230%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27150%27 y1=%27290%27 x2=%27640%27 y2=%27290%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27860%27 y1=%27290%27 x2=%271350%27 y2=%27290%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27150%27 y1=%27350%27 x2=%27640%27 y2=%27350%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27860%27 y1=%27350%27 x2=%271350%27 y2=%27350%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27150%27 y1=%27410%27 x2=%27640%27 y2=%27410%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27860%27 y1=%27410%27 x2=%271350%27 y2=%27410%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27150%27 y1=%27470%27 x2=%27640%27 y2=%27470%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27860%27 y1=%27470%27 x2=%271350%27 y2=%27470%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27150%27 y1=%27530%27 x2=%27640%27 y2=%27530%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27860%27 y1=%27530%27 x2=%271350%27 y2=%27530%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27150%27 y1=%27590%27 x2=%27640%27 y2=%27590%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27860%27 y1=%27590%27 x2=%271350%27 y2=%27590%27 stroke=%27%23fff%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3C/svg%3E');background-size:cover;background-position:center;opacity:0.14}
body.bg-sliders::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 1600 800%27 preserveAspectRatio=%27xMidYMid slice%27%3E%3Cline x1=%27200%27 y1=%27220%27 x2=%271400%27 y2=%27220%27 stroke=%27%23fff%27 stroke-width=%2710%27 stroke-linecap=%27round%27 stroke-opacity=%27.7%27/%3E%3Ccircle cx=%27560%27 cy=%27220%27 r=%2738%27 fill=%27%238B7CFF%27 stroke=%27%23fff%27 stroke-width=%278%27/%3E%3Cline x1=%27200%27 y1=%27400%27 x2=%271400%27 y2=%27400%27 stroke=%27%23fff%27 stroke-width=%2710%27 stroke-linecap=%27round%27 stroke-opacity=%27.7%27/%3E%3Ccircle cx=%271040%27 cy=%27400%27 r=%2738%27 fill=%27%238B7CFF%27 stroke=%27%23fff%27 stroke-width=%278%27/%3E%3Cline x1=%27200%27 y1=%27580%27 x2=%271400%27 y2=%27580%27 stroke=%27%23fff%27 stroke-width=%2710%27 stroke-linecap=%27round%27 stroke-opacity=%27.7%27/%3E%3Ccircle cx=%27800%27 cy=%27580%27 r=%2738%27 fill=%27%238B7CFF%27 stroke=%27%23fff%27 stroke-width=%278%27/%3E%3C/svg%3E');background-size:cover;background-position:center;opacity:0.14}
body.bg-yinyang::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 500 500%27%3E%3Ccircle cx=%27250%27 cy=%27250%27 r=%27240%27 fill=%27none%27 stroke=%27%23fff%27 stroke-width=%278%27/%3E%3Cpath d=%27M250 10 A240 240 0 0 1 250 490 A120 120 0 0 1 250 250 A120 120 0 0 0 250 10 Z%27 fill=%27%23fff%27/%3E%3Ccircle cx=%27250%27 cy=%27130%27 r=%2732%27 fill=%27%23fff%27/%3E%3Ccircle cx=%27250%27 cy=%27370%27 r=%2732%27 fill=%27%23121A33%27/%3E%3C/svg%3E');background-size:max(135vh,100vw);background-position:center;opacity:.11}
"""
STYLE_CSS += """
/* v17 velké obrázky přes celou šířku */
body.bg-yinyang::before{background-size:max(150vh,115vw);background-position:center;opacity:.13}
body.bg-buddha::before{background-size:max(130vh,100vw);background-position:center 30%;opacity:.15}
body.bg-target::before{background-size:max(130vh,100vw);background-position:center;opacity:.14}
body.bg-equity::before{background-size:cover;background-position:center bottom;opacity:.34}
body.bg-chart::before{opacity:.24}body.bg-analysis::before{opacity:.24}body.bg-mountain::before{opacity:.22}body.bg-book::before{opacity:.2}body.bg-sliders::before{opacity:.2}
"""
STYLE_CSS += """
/* v18 */
body.bg-diary::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 1500 900%27 preserveAspectRatio=%27xMidYMid meet%27%3E%3Crect x=%27230%27 y=%27100%27 width=%27900%27 height=%27720%27 rx=%2734%27 fill=%27%238B7CFF%27 fill-opacity=%27.30%27 stroke=%27%23fff%27 stroke-width=%279%27/%3E%3Crect x=%27250%27 y=%27150%27 width=%2760%27 height=%2722%27 rx=%2711%27 fill=%27%23fff%27/%3E%3Crect x=%27250%27 y=%27230%27 width=%2760%27 height=%2722%27 rx=%2711%27 fill=%27%23fff%27/%3E%3Crect x=%27250%27 y=%27310%27 width=%2760%27 height=%2722%27 rx=%2711%27 fill=%27%23fff%27/%3E%3Crect x=%27250%27 y=%27390%27 width=%2760%27 height=%2722%27 rx=%2711%27 fill=%27%23fff%27/%3E%3Crect x=%27250%27 y=%27470%27 width=%2760%27 height=%2722%27 rx=%2711%27 fill=%27%23fff%27/%3E%3Crect x=%27250%27 y=%27550%27 width=%2760%27 height=%2722%27 rx=%2711%27 fill=%27%23fff%27/%3E%3Crect x=%27250%27 y=%27630%27 width=%2760%27 height=%2722%27 rx=%2711%27 fill=%27%23fff%27/%3E%3Crect x=%27250%27 y=%27710%27 width=%2760%27 height=%2722%27 rx=%2711%27 fill=%27%23fff%27/%3E%3Cline x1=%27380%27 y1=%27230%27 x2=%271050%27 y2=%27230%27 stroke=%27%23fff%27 stroke-opacity=%27.8%27 stroke-width=%275%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27380%27 y1=%27300%27 x2=%271050%27 y2=%27300%27 stroke=%27%23fff%27 stroke-opacity=%27.8%27 stroke-width=%275%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27380%27 y1=%27370%27 x2=%271050%27 y2=%27370%27 stroke=%27%23fff%27 stroke-opacity=%27.8%27 stroke-width=%275%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27380%27 y1=%27440%27 x2=%271050%27 y2=%27440%27 stroke=%27%23fff%27 stroke-opacity=%27.8%27 stroke-width=%275%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27380%27 y1=%27510%27 x2=%271050%27 y2=%27510%27 stroke=%27%23fff%27 stroke-opacity=%27.8%27 stroke-width=%275%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27380%27 y1=%27580%27 x2=%271050%27 y2=%27580%27 stroke=%27%23fff%27 stroke-opacity=%27.8%27 stroke-width=%275%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27380%27 y1=%27650%27 x2=%271050%27 y2=%27650%27 stroke=%27%23fff%27 stroke-opacity=%27.8%27 stroke-width=%275%27 stroke-linecap=%27round%27/%3E%3Cpath d=%27M330 235 l16 18 l32 -38%27 fill=%27none%27 stroke=%27%232EE6A6%27 stroke-width=%279%27 stroke-linecap=%27round%27 stroke-linejoin=%27round%27/%3E%3Cpath d=%27M330 375 l16 18 l32 -38%27 fill=%27none%27 stroke=%27%232EE6A6%27 stroke-width=%279%27 stroke-linecap=%27round%27 stroke-linejoin=%27round%27/%3E%3Cpath d=%27M330 515 l16 18 l32 -38%27 fill=%27none%27 stroke=%27%232EE6A6%27 stroke-width=%279%27 stroke-linecap=%27round%27 stroke-linejoin=%27round%27/%3E%3Cg transform=%27rotate%2835 1230 420%29%27%3E%3Crect x=%271190%27 y=%27160%27 width=%2756%27 height=%27440%27 rx=%2714%27 fill=%27%2338BDF8%27/%3E%3Cpolygon points=%271190,600 1246,600 1218,680%27 fill=%27%23FFB454%27/%3E%3Crect x=%271190%27 y=%27160%27 width=%2756%27 height=%2760%27 rx=%2714%27 fill=%27%23fff%27/%3E%3C/g%3E%3Cg fill=%27none%27 stroke=%27%23FFB454%27 stroke-width=%278%27%3E%3Ccircle cx=%271240%27 cy=%27760%27 r=%2752%27/%3E%3Ccircle cx=%271340%27 cy=%27700%27 r=%2752%27/%3E%3C/g%3E%3Ctext x=%271240%27 y=%27782%27 font-size=%2764%27 text-anchor=%27middle%27 fill=%27%23FFB454%27%3E%24%3C/text%3E%3Ctext x=%271340%27 y=%27722%27 font-size=%2764%27 text-anchor=%27middle%27 fill=%27%23FFB454%27%3E%24%3C/text%3E%3C/svg%3E');background-size:contain;background-position:center;opacity:.28}
body::before{left:268px}
body.bg-yinyang::before{background-size:max(118vh,calc((100vw - 268px) * .92));background-position:center}
body.bg-buddha::before{background-size:max(112vh,calc((100vw - 268px) * .85));background-position:center 35%}
body.bg-target::before{background-size:max(112vh,calc((100vw - 268px) * .85));background-position:center}
@media (max-width:760px){body::before{left:0}}
.hero>.gbox{flex:0 0 auto}.gbox{display:flex;flex-direction:column;align-items:center;gap:2px;min-width:230px}
.gnum{font-size:40px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}.gnum span{font-size:17px;font-weight:500;color:#C9D1E8}.gcap{color:#C9D1E8;font-size:15px}
.tip:hover::after,.tip:focus::after{content:none!important}.tip sup{font-size:.7em;margin-left:1px;color:#7DD3FC}.lede .tip sup,.k b .tip sup{font-size:.34em}
#tipbox{position:fixed;display:none;margin:0;inset:auto;z-index:2147483000;max-width:min(330px,calc(100vw - 16px));background:#0C1124;border:1px solid rgba(139,124,255,.85);color:#fff;padding:11px 14px;border-radius:12px;font-size:15px;font-weight:400;line-height:1.45;box-shadow:0 18px 44px -8px rgba(0,0,0,.95);pointer-events:none;overflow:visible}
"""
STYLE_CSS += """
/* v19 */
body.bg-news::before{background-image:url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 1500 900%27 preserveAspectRatio=%27xMidYMid meet%27%3E%3Crect x=%27200%27 y=%27110%27 width=%27860%27 height=%27700%27 rx=%2730%27 fill=%27%2338BDF8%27 fill-opacity=%27.22%27 stroke=%27%23fff%27 stroke-width=%279%27/%3E%3Crect x=%27250%27 y=%27160%27 width=%27760%27 height=%2770%27 rx=%2714%27 fill=%27%23fff%27 fill-opacity=%27.9%27/%3E%3Crect x=%27250%27 y=%27260%27 width=%27330%27 height=%27140%27 rx=%2714%27 fill=%27%238B7CFF%27 fill-opacity=%27.8%27/%3E%3Cline x1=%27620%27 y1=%27290%27 x2=%271000%27 y2=%27290%27 stroke=%27%23fff%27 stroke-width=%2710%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27620%27 y1=%27340%27 x2=%27900%27 y2=%27340%27 stroke=%27%23fff%27 stroke-width=%2710%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27250%27 y1=%27430%27 x2=%27600%27 y2=%27430%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27660%27 y1=%27430%27 x2=%271010%27 y2=%27430%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27250%27 y1=%27486%27 x2=%27600%27 y2=%27486%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27660%27 y1=%27486%27 x2=%271010%27 y2=%27486%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27250%27 y1=%27542%27 x2=%27600%27 y2=%27542%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27660%27 y1=%27542%27 x2=%271010%27 y2=%27542%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27250%27 y1=%27598%27 x2=%27600%27 y2=%27598%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27660%27 y1=%27598%27 x2=%271010%27 y2=%27598%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27250%27 y1=%27654%27 x2=%27600%27 y2=%27654%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27660%27 y1=%27654%27 x2=%271010%27 y2=%27654%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27250%27 y1=%27710%27 x2=%27600%27 y2=%27710%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cline x1=%27660%27 y1=%27710%27 x2=%271010%27 y2=%27710%27 stroke=%27%23fff%27 stroke-opacity=%27.85%27 stroke-width=%276%27 stroke-linecap=%27round%27/%3E%3Cpath d=%27M1130 180 h260 a30 30 0 0 1 30 30 v110 a30 30 0 0 1 -30 30 h-170 l-60 50 v-50 h-30 a30 30 0 0 1 -30 -30 v-110 a30 30 0 0 1 30 -30 z%27 fill=%27%232EE6A6%27 fill-opacity=%27.55%27 stroke=%27%23fff%27 stroke-width=%277%27/%3E%3Cpath d=%27M1180 600 h200 a30 30 0 0 1 30 30 v90 a30 30 0 0 1 -30 30 h-140 l-50 44 v-44 h-10 a30 30 0 0 1 -30 -30 v-90 a30 30 0 0 1 30 -30 z%27 fill=%27%23FF6B8A%27 fill-opacity=%27.55%27 stroke=%27%23fff%27 stroke-width=%277%27/%3E%3Cpolygon points=%271290,235 1330,295 1250,295%27 fill=%27%23fff%27/%3E%3Cpolygon points=%271280,690 1320,640 1240,640%27 fill=%27%23fff%27 transform=%27rotate%28180 1280 665%29%27/%3E%3C/svg%3E');background-size:contain;background-position:center;opacity:.26}
"""
MINDSET_HTML = '<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Mindset</title>\n<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><link rel="stylesheet" href="style.css">\n<style>\n.orb{width:190px;height:190px;border-radius:50%;margin:26px auto 12px;background:radial-gradient(circle at 35% 30%,#B3A9FF,#38BDF8 60%,#2B3A8F);box-shadow:0 0 70px rgba(139,124,255,.55);transform:scale(.6);transition:transform var(--d,4s) linear}\n.orb.big{transform:scale(1.12)}.timer{font-size:46px;font-weight:700;text-align:center;font-variant-numeric:tabular-nums}.mid{text-align:center}\n.center{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:12px}.rule{font-size:clamp(20px,3vw,28px);font-weight:600;line-height:1.3;margin:0}\n.video{position:relative;padding-top:56.25%;border-radius:14px;overflow:hidden;border:1px solid var(--rule)}.video iframe{position:absolute;inset:0;width:100%;height:100%;border:0}.steps li{margin:8px 0}.links a{display:block;padding:10px 0;border-bottom:1px solid var(--rule)}input[type=range]{padding:0}\n</style></head>\n<body class="bg-buddha"><div class="app">@@SIDEBAR@@<div class="content"><header class="pagehead"><h1>Mindset</h1><p>Klid, soustředění a rutina před obchodováním</p></header><main>\n<section class="hero"><div><p class="empty" style="margin:0 0 8px">Můj úkol dne</p><p class="rule">Moje práce je čekat na příležitosti, kdy vím, že jsem silný.</p><p class="empty" style="margin:10px 0 0">Netlačím obchod každý den za každou cenu. Když nic neobchoduju a ovládnu se, je to moje výhra.</p><p style="margin:10px 0 0"><a href="trpelivost.html">Otevřít trénink trpělivosti</a></p></div></section>\n<section><h2>Vedená meditace (20 minut), hlavní část přípravy</h2><p class="hint">Zmáčkni přehrávání, zavři oči a poslouchej. Je to vedená meditace ve slovenštině.</p>\n<div class="video"><iframe src="https://www.youtube.com/embed/jd7adPU3JAw" title="Vedená meditácia - ZMENA REALITY" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen loading="lazy"></iframe></div>\n<p class="mid empty"><a href="https://www.youtube.com/watch?v=jd7adPU3JAw" target="_blank" rel="noopener">Otevřít video na YouTube</a>, kdyby se zde nepřehrálo.</p></section>\n<section><h2>Jak se dnes cítím?</h2><div class="grid">\n<label>Klid: <b id="mv-calm"></b> z 5<input type="range" min="1" max="5" id="m-calm"></label>\n<label>Soustředění: <b id="mv-focus"></b> z 5<input type="range" min="1" max="5" id="m-focus"></label>\n<label>Energie: <b id="mv-energy"></b> z 5<input type="range" min="1" max="5" id="m-energy"></label></div>\n<div class="gdyn" id="gdyn"></div><div class="note" id="mood" style="margin-top:14px"></div></section>\n<section><h2>Dýchání</h2><p class="hint">Vyber způsob dýchání. Kruh roste při nádechu a zmenšuje se při výdechu. Dýchej nosem a pomalu.</p>\n<label style="max-width:360px;margin:0 auto">Způsob<select id="pat"><option value="box">Do čtverce: 4 nádech, 4 zadržet, 4 výdech, 4 zadržet</option><option value="calm">Uklidňující: 4 nádech, 6 výdech</option><option value="478">Spánek a klid: 4 nádech, 7 zadržet, 8 výdech</option></select></label>\n<div class="orb" id="orb"></div><div class="timer" id="otxt">Připraven</div><p class="mid empty" id="ocyc"></p>\n<div class="center"><button onclick="startBreath(1)">1 minuta</button><button onclick="startBreath(3)">3 minuty</button><button onclick="startBreath(5)">5 minut</button><button class="ghost" onclick="stopBreath()">Zastavit</button></div></section>\n<section><h2>Pravidla trpělivosti</h2><ol class="steps" id="pat-rules"></ol></section>\n<section><h2>Rutina před otevřením burzy (asi 20 minut)</h2><ol class="steps">\n<li><b>Voda a pohyb (3 min):</b> napij se a projdi se nebo se protáhni.</li><li><b>Meditace a dýchání (20 min):</b> pusť si video výše, případně jen dýchání.</li>\n<li><b>Ranní briefing (5 min):</b> přečti si záložku Přehled a Fundament. Poznamenej si zprávy a jejich čas.</li><li><b>Plán dne (5 min):</b> v záložce Plán dne vyplň bias, úrovně a limity.</li>\n<li><b>Zavři rušení (1 min):</b> vypni sociální sítě a oznámení až do prvního obchodu.</li><li><b>Řekni si pravidlo:</b> „Dnes dodržím plán, i kdyby nebyl žádný obchod."</li></ol></section>\n<section><h2>Meditace zdarma</h2><div class="links">\n<a href="https://insighttimer.com/" target="_blank" rel="noopener">Insight Timer: velká zdarma knihovna vedených meditací</a>\n<a href="https://www.smilingmind.com.au/" target="_blank" rel="noopener">Smiling Mind: krátké meditace zdarma</a>\n<a href="https://www.youtube.com/results?search_query=meditace+5+minut+%C4%8Desky" target="_blank" rel="noopener">YouTube: meditace na 5 minut česky</a>\n<a href="https://www.youtube.com/results?search_query=meditation+for+traders+calm+focus" target="_blank" rel="noopener">YouTube: meditace pro tradery, klid a soustředění</a></div></section>\n<section><h2>Večerní reflexe</h2>\n<label>Co se mi dnes povedlo?<textarea id="r-a" rows="2"></textarea></label>\n<label style="margin-top:12px">Co příště udělám jinak?<textarea id="r-b" rows="2"></textarea></label>\n<label style="margin-top:12px">Jak jsem zvládl emoce?<textarea id="r-c" rows="2"></textarea></label>\n<p class="empty">Ukládá se do tohoto prohlížeče, pro každý den zvlášť.</p></section>\n<section><h2>Pravidla klidného tradera</h2><ol class="steps" id="rules"></ol></section>\n</main></div></div>\n<script>\nconst $ = id => document.getElementById(id);\nconst today = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };\nconst ls = {get: k => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }};\nconst RULES = ["Moje práce není předvídat trh, ale dodržet plán.", "Žádný obchod není tak důležitý, aby stál za porušení pravidel.", "Po ztrátě dýchám. Po výhře dýchám. Velikost pozice se nemění podle emoce.",\n  "Trh tu bude i zítra. Šance nemusím honit.", "Čekání je také pozice.", "Nejlepší obchod dne je často ten, který jsem neudělal.", "Stop je cena za klid, ne porážka.",\n  "Neobchoduji, abych si něco dokázal, ani abych se odrazil ze ztráty.", "Jeden dobrý setup stačí.", "Nejdřív pozoruji emoci, potom jednám. Ne naopak.",\n  "Úspěch měřím dodržením procesu, ne výsledkem jednoho dne.", "Když váhám, nevstupuji."];\n$("rules").innerHTML = RULES.map(r => `<li>${r}</li>`).join("");\nconst PAT_RULES = ["Stačí si počkat na ten můj obchod.", "Trh nezajímá, co chci. Proti ničemu nebojuji, jen čekám.", "Žádné emoce, pouze trpělivost.", "Moje práce není být každý den aktivní a vynucovat nové obchody.", "Moje práce je čekat na příležitosti, kdy vím, že jsem silný.", "Den, kdy sedím u grafu, nic neobchoduju a umím se ovládnout, je moje výhra.", "Trpělivé čekání ukáže, jestli na to mám.", "Snažím se být nejlepší verzí sebe sama.", "Vím, že na to mám. Když vše dodržím, stane se to.", "Když tomu dám všechno na maximum, přijde to.", "Nemám FOMO. Další příležitost přijde vždycky.", "Nemám revenge. Po ztrátě vstanu od grafu.", "Z malých timeframeů nevstupuji. Počkám si na větší.", "Nejdřív větší timeframe pro směr, potom menší pro vstup.", "Neobchoduji, abych něco dokázal, ale proto, že vidím svůj setup.", "Jeden dobrý obchod je víc než deset nucených.", "Čekání je také pozice.", "Nejlepší obchod dne je často ten, který jsem neudělal.", "Dodržet plán je úspěch, i když obchod skončí ztrátou.", "Trh tu bude i zítra."];\n$("pat-rules").innerHTML = PAT_RULES.map(r => `<li>${r}</li>`).join("");\nconst mk = "mood:" + today(); let M = ls.get(mk) || {calm: 3, focus: 3, energy: 3};\nfunction dg(el,v,big,sub,word,cls,cap){var a=Math.max(-1,Math.min(1,v))*80;el.innerHTML=`<div class="gbox"><svg class="gauge" viewBox="0 0 200 110"><defs><linearGradient id="gd"><stop offset="0" stop-color="#FF6B8A"/><stop offset=".5" stop-color="#C9D1E8"/><stop offset="1" stop-color="#2EE6A6"/></linearGradient></defs><path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="url(#gd)" stroke-width="14" stroke-linecap="round"/><g transform="rotate(${a} 100 100)"><line x1="100" y1="100" x2="100" y2="34" stroke="#EEF1FA" stroke-width="3" stroke-linecap="round"/></g><circle cx="100" cy="100" r="6" fill="#EEF1FA"/></svg><div class="gnum ${cls}">${big}<span> ${sub}</span></div><div class="gword ${cls}">${word}</div><div class="gcap">${cap}</div></div>`;}function mood() { ["calm", "focus", "energy"].forEach(k => $("mv-" + k).textContent = M[k]); var av = (M.calm + M.focus + M.energy) / 3; dg($("gdyn"), (av - 3) / 2, av.toFixed(1), "z 5", av >= 3.7 ? "Bullish nálada" : av <= 2.3 ? "Bearish nálada" : "Neutral nálada", av >= 3.7 ? "up" : av <= 2.3 ? "down" : "", "tvoje dnešní nálada");\n  $("mood").textContent = (M.calm <= 2 || M.focus <= 2) ? "Dnes nejsi v optimálním stavu. Nejdřív 3 minuty dýchání. Obchoduj jen se zmenšenou velikostí, nebo vůbec."\n    : (M.calm >= 4 && M.focus >= 4 && M.energy >= 3) ? "Skvělé. Drž se plánu a neobchoduj víc, než musíš." : "Dobrý základ. Dej si ještě krátké dýchání a drž se plánu."; }\n["calm", "focus", "energy"].forEach(k => { $("m-" + k).value = M[k]; $("m-" + k).oninput = () => { M[k] = +$("m-" + k).value; ls.set(mk, M); mood(); }; }); mood();\nconst rk = "refl:" + today(), R = ls.get(rk) || {}; ["a", "b", "c"].forEach(k => { $("r-" + k).value = R[k] || ""; $("r-" + k).oninput = () => { R[k] = $("r-" + k).value; ls.set(rk, R); }; });\nfunction chime() { try { const a = new (window.AudioContext || window.webkitAudioContext)(); [0, 0.9, 1.8].forEach((d, i) => { const o = a.createOscillator(), g = a.createGain(); o.frequency.value = [528, 396, 528][i];\n  o.connect(g); g.connect(a.destination); const t = a.currentTime + d; g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(.25, t + .05); g.gain.exponentialRampToValueAtTime(.001, t + 1.6); o.start(t); o.stop(t + 1.7); }); } catch (e) {} }\nlet br = null;\nconst PAT = {box: [["Nádech", 4, 1], ["Zadrž", 4, 1], ["Výdech", 4, 0], ["Zadrž", 4, 0]], calm: [["Nádech", 4, 1], ["Výdech", 6, 0]], "478": [["Nádech", 4, 1], ["Zadrž", 7, 1], ["Výdech", 8, 0]]};\nfunction startBreath(mins) { stopBreath(); const P = PAT[$("pat").value], total = P.reduce((a, p) => a + p[1], 0), orb = $("orb"); let t = 0, ph = 0, left = P[0][1], cyc = 0;\n  const setPhase = () => { const p = P[ph]; orb.style.setProperty("--d", (p[0] === "Zadrž" ? 0.01 : p[1]) + "s"); if (p[0] === "Nádech") orb.classList.add("big"); if (p[0] === "Výdech") orb.classList.remove("big"); };\n  setPhase();\n  const tick = () => { if (t >= mins * 60) { stopBreath(true); return; }\n    $("otxt").textContent = P[ph][0] + " " + left; $("ocyc").textContent = "Dokončených cyklů: " + cyc; t++; left--;\n    if (left <= 0) { ph = (ph + 1) % P.length; if (ph === 0) cyc++; left = P[ph][1]; setPhase(); } };\n  tick(); br = setInterval(tick, 1000); }\nfunction stopBreath(done) { clearInterval(br); br = null; $("orb").classList.remove("big"); $("otxt").textContent = done ? "Hotovo. Jak se cítíš?" : "Připraven"; if (done) chime(); }\n</script><script src="tips.js"></script></body></html>\n'

PLAN_HTML = '<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Plán dne</title>\n<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><link rel="stylesheet" href="style.css"></head>\n<body class="bg-target"><div class="app">@@SIDEBAR@@<div class="content"><header class="pagehead"><h1>Plán dne</h1><p>Checklist, limity a kalkulačka rizika</p></header><main>\n<section class="hero"><div><p class="empty" style="margin:0 0 8px">Pravidla trpělivosti</p><p class="rule" id="pday" style="font-size:clamp(20px,3vw,26px);font-weight:600;margin:0"></p>\n<ul class="facts" id="plist" style="margin-top:12px"></ul>\n<div style="margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button id="pbtn">Dnes jsem se ovládl</button><span class="empty" id="pstreak"></span><a href="trpelivost.html">Trénink trpělivosti</a></div></div></section>\n<section><h2>Dnešní limity (živě z tvého deníku)</h2><p class="hint">Čísla se berou z deníku obchodů a obnovují se sama. Nastav limity v plánu níže.</p><div class="kpis" id="live"></div><div id="warn"></div></section>\n<section><h2>Plán na dnešek</h2><div class="grid">\n<label>Můj bias<select id="p-bias"><option value="">–</option><option>Bullish</option><option>Bearish</option><option>Neutrální</option></select></label>\n<label>Max. počet obchodů<input id="p-maxTrades" type="number"></label><label>Max. denní ztráta ($)<input id="p-maxLoss" type="number"></label><label>Denní cíl ($)<input id="p-goal" type="number"></label></div>\n<label style="margin-top:12px">Klíčové úrovně<textarea id="p-levels" rows="2"></textarea></label>\n<label style="margin-top:12px">Setupy, které dnes obchoduji<textarea id="p-setups" rows="2"></textarea></label>\n<label style="margin-top:12px">Poznámky<textarea id="p-notes" rows="2"></textarea></label>\n<p class="empty">Plán se ukládá do tohoto prohlížeče, pro každý den zvlášť.</p></section>\n<section><h2>Checklist před obchodováním <span id="cprog" class="empty"></span></h2><div class="gdyn" id="gdyn"></div><p class="hint">Zaškrtni, co máš hotové. Když něco chybí, nejdřív to dodělej.</p><div class="chips" id="checks"></div></section>\n<section><h2>Měsíční cíl</h2><div class="grid"><label>Cíl na měsíc ($)<input id="m-goal" type="number"></label></div><div id="mprog" style="margin-top:12px"></div></section>\n<section><h2>Pravidla rizika</h2><ol class="steps"><li>Na jeden obchod riskuji nejvýš 1 % účtu (u účtu 50 000 $ je to 500 $).</li><li>Stop zadám před vstupem a nikdy ho neposouvám dál od vstupu.</li>\n<li>Po dvou ztrátách za sebou dělám pauzu a vstávám od grafu.</li><li>Před důležitou zprávou z USA nevstupuji, počkám na reakci trhu.</li><li>Jestli nevidím svůj setup, neobchoduji. Čekání je také pozice.</li></ol></section>\n<section><h2>Kalkulačka: kolik mikro kontraktů</h2><div class="grid">\n<label>Účet ($)<input id="c-acc" type="number" step="any" value="50000"></label><label>Riziko na obchod (%)<input id="c-risk" type="number" step="any" value="1"></label>\n<label>Vstup<input id="c-entry" type="number" step="any"></label><label>Stop<input id="c-stop" type="number" step="any"></label>\n<label>Hodnota bodu na 1 mikro ($)<input id="c-pv" type="number" step="any" value="2"></label></div><p id="c-out" class="empty" style="margin-top:12px">Vyplň vstup a stop.</p></section>\n</main></div></div>\n<script>\nconst $ = id => document.getElementById(id), nf = (x, d = 0) => x.toLocaleString("cs-CZ", {maximumFractionDigits: d});\nconst sg = x => (x >= 0 ? "+" : "\\u2212") + nf(Math.abs(x)), cl = x => x >= 0 ? "up" : "down";\nconst today = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };\nconst ls = {get: k => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }};\nconst PV = {NQ: 20, MNQ: 2, ES: 50, MES: 5, YM: 5, MYM: 0.5};\nconst CHECK = ["Vyspal jsem se a jsem klidný", "Prošel jsem ranní briefing", "Znám důležité zprávy dne a jejich čas", "Mám nakreslené klíčové úrovně", "Vím, kolik riskuji na obchod", "Před vstupem mám připravený stop", "Dnes neobchoduji z vteřinové emoce"];\nconst key = "plan:" + today(), F = ["bias", "levels", "setups", "maxTrades", "maxLoss", "goal", "notes"];\nlet P = ls.get(key) || {checks: []};\nF.forEach(f => { $("p-" + f).value = P[f] ?? ""; $("p-" + f).addEventListener("input", () => { P[f] = $("p-" + f).value; ls.set(key, P); live(); }); });\n$("checks").innerHTML = CHECK.map((t, i) => `<label class="chip"><input type="checkbox" data-i="${i}" ${P.checks.includes(i) ? "checked" : ""}>${t}</label>`).join("");\nfunction dg(el,v,big,sub,word,cls,cap){var a=Math.max(-1,Math.min(1,v))*80;el.innerHTML=`<div class="gbox"><svg class="gauge" viewBox="0 0 200 110"><defs><linearGradient id="gd"><stop offset="0" stop-color="#FF6B8A"/><stop offset=".5" stop-color="#C9D1E8"/><stop offset="1" stop-color="#2EE6A6"/></linearGradient></defs><path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="url(#gd)" stroke-width="14" stroke-linecap="round"/><g transform="rotate(${a} 100 100)"><line x1="100" y1="100" x2="100" y2="34" stroke="#EEF1FA" stroke-width="3" stroke-linecap="round"/></g><circle cx="100" cy="100" r="6" fill="#EEF1FA"/></svg><div class="gnum ${cls}">${big}<span> ${sub}</span></div><div class="gword ${cls}">${word}</div><div class="gcap">${cap}</div></div>`;}const prog = () => { $("cprog").textContent = P.checks.length + " z " + CHECK.length; var f = P.checks.length / CHECK.length; dg($("gdyn"), f * 2 - 1, P.checks.length, "z " + CHECK.length, f >= 1 ? "Připraven" : f >= 0.5 ? "Skoro připraven" : "Nepřipraven", f >= 1 ? "up" : f < 0.5 ? "down" : "", "checklist před obchodem"); };\n$("checks").addEventListener("input", e => { const i = +e.target.dataset.i; P.checks = P.checks.filter(x => x !== i); if (e.target.checked) P.checks.push(i); ls.set(key, P); prog(); }); prog();\n$("m-goal").value = ls.get("mgoal") ?? ""; $("m-goal").addEventListener("input", () => { ls.set("mgoal", $("m-goal").value); live(); });\n\nfunction trades() { return new Promise(res => { const r = indexedDB.open("journal", 1);\n  r.onupgradeneeded = () => r.result.createObjectStore("trades", {keyPath: "id", autoIncrement: true});\n  r.onsuccess = () => { const d = r.result; if (!d.objectStoreNames.contains("trades")) return res([]);\n    const q = d.transaction("trades").objectStore("trades").getAll(); q.onsuccess = () => res(q.result); q.onerror = () => res([]); };\n  r.onerror = () => res([]); }); }\nasync function live() {\n  const ts = await trades(), d = today(), mo = d.slice(0, 7), w = t => t.opened || t.date + "T00:00";\n  const pnl = t => t.exit == null ? 0 : (t.exit - t.entry) * (t.dir === "short" ? -1 : 1) * (t.size || 1) * (t.pv || PV[t.inst] || 1) - (t.fee || 0);\n  const td = ts.filter(t => w(t).slice(0, 10) === d), p = td.reduce((s, t) => s + pnl(t), 0), m = ts.filter(t => w(t).slice(0, 7) === mo).reduce((s, t) => s + pnl(t), 0);\n  const ml = +$("p-maxLoss").value || 0, mt = +$("p-maxTrades").value || 0, g = +$("p-goal").value || 0, mg = +$("m-goal").value || 0;\n  const K = (v, l) => `<div class="k"><b>${v}</b><span>${l}</span></div>`;\n  $("live").innerHTML = K(`<span class="${cl(p)}">${sg(p)} $</span>`, "Dnešní výsledek") + K(td.length + (mt ? " z " + mt : ""), "Obchodů dnes") +\n    K(ml ? nf(Math.max(0, ml + Math.min(p, 0))) + " $" : "–", "Zbývá do limitu ztráty") + K(g ? nf(Math.max(0, g - p)) + " $" : "–", "Zbývá do denního cíle");\n  const w2 = []; if (ml && p <= -ml) w2.push("Limit ztráty je dosažen. Dnes už neobchoduj.");\n  if (mt && td.length >= mt) w2.push("Dosáhl jsi maximálního počtu obchodů."); if (g && p >= g) w2.push("Denní cíl je splněn. Zvaž konec dne.");\n  $("warn").innerHTML = w2.map(x => `<div class="note" style="margin-top:12px">${x}</div>`).join("");\n  $("mprog").innerHTML = mg ? `<div class="bar" style="grid-template-columns:90px 1fr auto"><span>Měsíc</span><div class="track ${cl(m)}"><i style="width:${Math.min(100, Math.max(0, m / mg * 100))}%"></i></div><b class="${cl(m)}">${sg(m)} $ z ${nf(mg)} $</b></div>` : \'<p class="empty">Zadej měsíční cíl a uvidíš postup.</p>\';\n}\nlive(); setInterval(live, 15000);\n\nfunction calc() {\n  const g = id => parseFloat($(id).value), a = g("c-acc"), r = g("c-risk"), e = g("c-entry"), s = g("c-stop"), pv = g("c-pv");\n  if ([a, r, e, s, pv].some(isNaN) || e === s) { $("c-out").textContent = "Vyplň vstup a stop."; return; }\n  const money = a * r / 100, size = money / (Math.abs(e - s) * pv), dr = e > s ? 1 : -1;\n  $("c-out").innerHTML = `Riskuješ <b>${nf(money)} $</b>. Počet mikro kontraktů: <b>${nf(Math.floor(size))}</b> (přesně ${nf(size, 2)}). Cíl pro poměr 1:2 je <b>${nf(e + dr * Math.abs(e - s) * 2, 2)}</b>, pro 1:3 <b>${nf(e + dr * Math.abs(e - s) * 3, 2)}</b>.`;\n}\ndocument.querySelectorAll("[id^=c-]").forEach(i => i.addEventListener("input", calc));\n\nconst PL = ["Stačí si počkat na ten můj obchod.", "Trh nezajímá, co chci. Proti ničemu nebojuji, jen čekám.", "Žádné emoce, pouze trpělivost.", "Moje práce není být každý den aktivní a vynucovat nové obchody.", "Moje práce je čekat na příležitosti, kdy vím, že jsem silný.", "Den, kdy sedím u grafu, nic neobchoduju a umím se ovládnout, je moje výhra.", "Trpělivé čekání ukáže, jestli na to mám.", "Snažím se být nejlepší verzí sebe sama.", "Vím, že na to mám. Když vše dodržím, stane se to.", "Když tomu dám všechno na maximum, přijde to.", "Nemám FOMO. Další příležitost přijde vždycky.", "Nemám revenge. Po ztrátě vstanu od grafu.", "Z malých timeframeů nevstupuji. Počkám si na větší.", "Nejdřív větší timeframe pro směr, potom menší pro vstup.", "Neobchoduji, abych něco dokázal, ale proto, že vidím svůj setup.", "Jeden dobrý obchod je víc než deset nucených.", "Čekání je také pozice.", "Nejlepší obchod dne je často ten, který jsem neudělal.", "Dodržet plán je úspěch, i když obchod skončí ztrátou.", "Trh tu bude i zítra."];\n$("pday").textContent = PL[Math.floor(Date.now() / 86400000) % PL.length];\n$("plist").innerHTML = PL.slice(0, 8).map(x => `<li>${x}</li>`).join("");\nconst SET = ls.get("settings") || {}; if (SET.acc) $("c-acc").value = SET.acc; if (SET.risk) $("c-risk").value = SET.risk;\nconst pk = "patience:" + today();\nfunction pstreak() { let n = 0; const d = new Date(); for (let i = 0; i < 365; i++) { const x = new Date(d - i * 86400000); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); const k = "patience:" + x.toISOString().slice(0, 10);\n  if (ls.get(k)) n++; else if (i > 0) break; } $("pstreak").textContent = ls.get(pk) ? "Dnes splněno. Dní za sebou: " + n : "Zmáčkni na konci dne, když jsi dodržel plán a netlačil obchod."; $("pbtn").textContent = ls.get(pk) ? "Zrušit zápis" : "Dnes jsem se ovládl"; }\n$("pbtn").onclick = () => { ls.set(pk, !ls.get(pk)); pstreak(); }; pstreak();\n</script><script src="tips.js"></script></body></html>\n'


if __name__ == "__main__":
    main()
