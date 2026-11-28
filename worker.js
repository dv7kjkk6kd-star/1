// Kompas klidu – přijímač obchodů z TradingView (Cloudflare Worker, zdarma)
// TradingView pošle při každém obchodu strategie zprávu sem a stránka Robot si ji odtud přečte.
// Potřebuje: KV úložiště napojené pod názvem OBCHODY a proměnnou TOKEN (tajné heslo, které je i v adrese webhooku).
// Pro AI asistenta: napojení Workers AI pod názvem AI (zdarma do denního limitu). Volitelně AI_LIMIT (počet otázek za den, výchozí 40).
// Verze 4: upozornění na Macu (Web Push) – /push/..., plus naplánované spouštění (Cron Trigger "0 * * * *" – posílá se jen v sobotu a v neděli v 10:00 pražského času).
// Verze 3: synchronizace celé aplikace mezi zařízeními (/sync/...).
// Verze 2: kontrola spojení (/status, /ping) a plány od mentora na všech zařízeních (/mentor/...), chráněné stejným heslem TOKEN.

const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS", "Access-Control-Allow-Headers": "Content-Type" };

function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...extra } });
}

const VERZE = 4;
const okTok = (env, url) => !!env.TOKEN && url.searchParams.get("t") === env.TOKEN;
function text(t, status = 200) { return new Response(t, { status, headers: { "Content-Type": "text/plain; charset=utf-8", ...CORS } }); }
async function logPut(env, key, data) { try { await env.OBCHODY.put(key, JSON.stringify(data), { expirationTtl: 60 * 24 * 3600 }); } catch (e) {} }

// ---------- upozornění (Web Push bez obsahu, text si service worker stáhne z /push/last) ----------
const b64u = buf => { let s = ""; new Uint8Array(buf).forEach(x => s += String.fromCharCode(x)); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); };
async function vapid(env) {
  let v = null; try { v = JSON.parse(await env.OBCHODY.get("push:vapid")); } catch (e) {}
  if (v && v.jwk && v.pub) return v;
  const k = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  v = { jwk: await crypto.subtle.exportKey("jwk", k.privateKey), pub: b64u(await crypto.subtle.exportKey("raw", k.publicKey)) };
  await env.OBCHODY.put("push:vapid", JSON.stringify(v)); return v;
}
async function pushOne(env, sub) {
  const v = await vapid(env), aud = new URL(sub.endpoint).origin, enc = o => b64u(new TextEncoder().encode(JSON.stringify(o)));
  const unsigned = enc({ typ: "JWT", alg: "ES256" }) + "." + enc({ aud, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: "mailto:kompas-klidu@users.noreply.github.com" });
  const key = await crypto.subtle.importKey("jwk", Object.assign({}, v.jwk, { key_ops: ["sign"] }), { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, new TextEncoder().encode(unsigned));
  return fetch(sub.endpoint, { method: "POST", headers: { TTL: "86400", Urgency: "high", Authorization: "vapid t=" + unsigned + "." + b64u(sig) + ", k=" + v.pub } });
}
async function pushAll(env, msg) {
  await env.OBCHODY.put("push:last", JSON.stringify(Object.assign({ at: Date.now() }, msg)));
  const r = await env.OBCHODY.list({ prefix: "push:sub:" }); let ok = 0, bad = 0;
  for (const k of r.keys) {
    try { const sub = JSON.parse(await env.OBCHODY.get(k.name)); const res = await pushOne(env, sub);
      if (res.status === 404 || res.status === 410) { await env.OBCHODY.delete(k.name); bad++; } else if (res.ok) ok++; else bad++;
    } catch (e) { bad++; }
  }
  return { ok, bad, n: r.keys.length };
}
// pražský čas
function prague(d) {
  const p = {}; new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Prague", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", weekday: "short", hour12: false })
    .formatToParts(d).forEach(x => p[x.type] = x.value);
  return { date: p.year + "-" + p.month + "-" + p.day, hour: +p.hour % 24, minute: +p.minute, wd: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(p.weekday) };
}
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const wdOf = iso => new Date(iso + "T12:00:00Z").getUTCDay();
const weekOf = iso => addDays(iso, -((wdOf(iso) + 6) % 7));
const czDay = iso => ["Ne", "Po", "Út", "St", "Čt", "Pá", "So"][wdOf(iso)] + " " + (+iso.slice(8)) + ". " + (+iso.slice(5, 7)) + ".";
function pushMsg(st, now) {
  if (!st || !st.first) return null;
  const have = new Set(st.have || []), revs = new Set(st.revs || []), miss = [];
  let start = addDays(now.date, -14); if (st.first > start) start = st.first;
  for (let d = start; d <= now.date; d = addDays(d, 1)) {
    const wd = wdOf(d); if (wd < 1 || wd > 5 || have.has(d)) continue;
    const fri = addDays(d, 5 - wd); if (now.date > fri || (now.date === fri && (now.hour > 16 || (now.hour === 16 && now.minute >= 20)))) miss.push(d);
  }
  if (miss.length) return { t: "📅 Kompas klidu: chybí zápis", b: "Chybí: " + miss.map(czDay).join(", ") + " – zapiš obchod, nebo den bez obchodu s důvodem.", u: "journal.html" };
  return null; // upozornění jen na chybějící zápis
}
async function cron(env) {
  const now = prague(new Date());
  const when = (now.wd === 6 || now.wd === 0) && now.hour === 10;
  if (!when) return;
  const tag = "push:sent:" + now.date + ":" + now.hour; if (await env.OBCHODY.get(tag)) return;
  let st = null; try { st = JSON.parse(await env.OBCHODY.get("push:state")); } catch (e) {}
  const msg = pushMsg(st, now); if (!msg) return;
  await env.OBCHODY.put(tag, "1", { expirationTtl: 3 * 86400 });
  await pushAll(env, msg);
}

export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(cron(env)); },
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });

    // 1) TradingView sem posílá obchody: POST /hook?t=TVUJ_TOKEN
    if (url.pathname === "/hook" && req.method === "POST") {
      if (!okTok(env, url)) { await logPut(env, "log:bad", { cas: new Date().toISOString() }); return json({ ok: false, chyba: "špatný token" }, 403); }
      const body = await req.text();
      let ev, citelne = true;
      try { ev = JSON.parse(body); } catch (e) { ev = { text: body.slice(0, 500) }; citelne = false; }
      await logPut(env, "log:raw", { cas: new Date().toISOString(), citelne, ukazka: body.slice(0, 300) });
      ev.prijato = new Date().toISOString();
      // každý obchod se uloží jako samostatná položka (data v metadatech), takže se nic nepřepíše, ani když přijdou dva naráz
      const meta = JSON.stringify(ev).length < 1000 ? ev : { cas: ev.cas, akce: ev.akce, cena: ev.cena, kontrakty: ev.kontrakty, pozice: ev.pozice, velikost: ev.velikost, id: ev.id, prijato: ev.prijato };
      await env.OBCHODY.put("e:" + ev.prijato + ":" + Math.random().toString(36).slice(2, 8), "1", { metadata: meta });
      return json({ ok: true });
    }

    // 2) Stránka Robot si čte obchody: GET /events
    if (url.pathname === "/events") {
      const all = [];
      let cursor;
      do {
        const r = await env.OBCHODY.list({ prefix: "e:", cursor, limit: 1000 });
        r.keys.forEach(k => { if (k.metadata) all.push(k.metadata); });
        cursor = r.list_complete ? null : r.cursor;
      } while (cursor && all.length < 20000);
      return json(all, 200, { "Cache-Control": "max-age=15" });
    }

    // 3) Svíčky pro graf (Yahoo Finance, 1 minuta, posledních 5 dní): GET /candles?sym=NQ=F
    if (url.pathname === "/candles") {
      const sym = (url.searchParams.get("sym") || "NQ=F").replace(/[^A-Za-z0-9=^.\-]/g, "");
      const y = "https://query1.finance.yahoo.com/v8/finance/chart/" + encodeURIComponent(sym) + "?interval=1m&range=5d&includePrePost=true";
      const r = await fetch(y, { headers: { "User-Agent": "Mozilla/5.0" }, cf: { cacheTtl: 60, cacheEverything: true } });
      if (!r.ok) return json({ chyba: "Yahoo neodpovídá", status: r.status }, 502);
      const d = await r.json();
      const res = d && d.chart && d.chart.result && d.chart.result[0];
      if (!res) return json({ chyba: "žádná data" }, 502);
      const q = res.indicators.quote[0], out = [];
      res.timestamp.forEach((t, i) => {
        if (q.open[i] != null && q.high[i] != null && q.low[i] != null && q.close[i] != null)
          out.push([t, +q.open[i].toFixed(2), +q.high[i].toFixed(2), +q.low[i].toFixed(2), +q.close[i].toFixed(2)]);
      });
      return json(out, 200, { "Cache-Control": "max-age=60" });
    }

    // 4) AI asistent (Cloudflare Workers AI, zdarma do denního limitu): POST /ai  {q, ctx, hist}
    if (url.pathname === "/ai" && req.method === "POST") {
      if (!env.AI) return json({ chyba: "Chybí napojení Workers AI. Ve Workeru dej Settings → Bindings → Add → Workers AI a jako název napiš AI." }, 500);
      const LIM = +(env.AI_LIMIT || 40);
      const day = new Date().toISOString().slice(0, 10), key = "ai:" + day;
      const used = +((await env.OBCHODY.get(key)) || 0);
      if (used >= LIM) return json({ chyba: "Dnešní limit " + LIM + " otázek je vyčerpaný (aby AI zůstala zdarma). Zítra to půjde znovu." }, 429);
      let b = {};
      try { b = await req.json(); } catch (e) {}
      const q = String(b.q || "").slice(0, 2000).trim();
      if (!q) return json({ chyba: "Prázdná otázka." }, 400);
      const ctxt = String(b.ctx || "").slice(0, 14000);
      const hist = Array.isArray(b.hist) ? b.hist.slice(-6).map(m => ({ role: m.role === "assistant" ? "assistant" : "user", content: String(m.content || "").slice(0, 1500) })) : [];
      const sys = "Jsi AI asistent na webu Kompas klidu, osobním obchodním kokpitu začínajícího obchodníka s futures MNQ (Micro Nasdaq). " +
        "Odpovídej vždy česky, jednoduše a krátce, jako trpělivý mentor. Znáš trading (price action, ICT pojmy jako FVG, CISD, order block, likvidita, risk management, psychologie obchodování). " +
        "Když se ptá na web, použij jen informace z části KONTEXT WEBU níže a nevymýšlej si čísla. Když něco v kontextu není, řekni to. " +
        "Nedávej jistá doporučení k obchodu, mluv o pravděpodobnostech a připomeň řízení rizika.\n\nKONTEXT WEBU:\n" + ctxt;
      const messages = [{ role: "system", content: sys }, ...hist, { role: "user", content: q }];
      const models = [env.AI_MODEL, "@cf/google/gemma-3-12b-it", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/meta/llama-3.1-8b-instruct"].filter(Boolean);
      let last = "";
      for (const m of models) {
        try {
          const r = await env.AI.run(m, { messages, max_tokens: 800 });
          const a = (r && (r.response || (r.choices && r.choices[0] && r.choices[0].message && r.choices[0].message.content))) || "";
          if (a) {
            await env.OBCHODY.put(key, String(used + 1), { expirationTtl: 172800 });
            return json({ a, model: m, zbyva: LIM - used - 1 });
          }
        } catch (e) { last = String(e && e.message || e); }
      }
      return json({ chyba: "AI teď neodpověděla. " + last }, 502);
    }

    // 5) Kontrola spojení: GET /status (bez hesla, nic tajného neukazuje)
    if (url.pathname === "/status") {
      let n = 0, last = null, cursor;
      do {
        const r = await env.OBCHODY.list({ prefix: "e:", cursor, limit: 1000 });
        n += r.keys.length; if (r.keys.length) last = r.keys[r.keys.length - 1].name.slice(2, 26);
        cursor = r.list_complete ? null : r.cursor;
      } while (cursor);
      const g = async k => { try { return JSON.parse(await env.OBCHODY.get(k)); } catch (e) { return null; } };
      const raw = await g("log:raw");
      return json({ v: VERZE, obchodu: n, posledni: last, spatneHeslo: (await g("log:bad") || {}).cas || null,
        posledniZprava: raw ? raw.cas : null, zpravaCitelna: raw ? raw.citelne : null, heslo: !!env.TOKEN, ai: !!env.AI }, 200, { "Cache-Control": "no-store" });
    }
    // 6) Kontrola hesla v prohlížeči: GET /ping?t=TVUJ_TOKEN
    if (url.pathname === "/ping") {
      if (!env.TOKEN) return text("⚠️ Ve Workeru chybí heslo TOKEN (Settings → Variables and Secrets).", 500);
      return okTok(env, url) ? text("✅ Heslo sedí. Worker běží (verze " + VERZE + ").") : text("❌ Špatné heslo. Zkontroluj, co máš za ?t= ve webhooku v TradingView.", 403);
    }

    // 7) Plány od mentora (synchronizace mezi zařízeními) – vždy s heslem ?t=TOKEN
    if (url.pathname.startsWith("/mentor/")) {
      if (!okTok(env, url)) return json({ ok: false, chyba: "špatné heslo" }, 403);
      const d = (url.searchParams.get("d") || "").replace(/[^0-9-]/g, "").slice(0, 10);
      const fid = (url.searchParams.get("f") || "").replace(/[^a-z0-9]/gi, "").slice(0, 24);
      const p = url.pathname;
      if (p === "/mentor/list" && req.method === "GET") {
        const out = []; let cursor;
        do {
          const r = await env.OBCHODY.list({ prefix: "m:", cursor, limit: 1000 });
          r.keys.forEach(k => out.push(Object.assign({ date: k.name.slice(2) }, k.metadata || {})));
          cursor = r.list_complete ? null : r.cursor;
        } while (cursor);
        return json(out, 200, { "Cache-Control": "no-store" });
      }
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return json({ ok: false, chyba: "chybí den" }, 400);
      if (p === "/mentor/plan" && req.method === "GET") {
        const v = await env.OBCHODY.get("m:" + d);
        return v ? new Response(v, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...CORS } }) : json({ ok: false }, 404);
      }
      if (p === "/mentor/plan" && req.method === "PUT") {
        let b; try { b = await req.json(); } catch (e) { return json({ ok: false, chyba: "špatná data" }, 400); }
        const saved = +b.saved || Date.now(), files = Array.isArray(b.files) ? b.files.slice(0, 40) : [];
        const plan = { date: d, dir: String(b.dir || ""), zones: Array.isArray(b.zones) ? b.zones.slice(0, 60) : [], note: String(b.note || "").slice(0, 200000), saved, files };
        await env.OBCHODY.put("m:" + d, JSON.stringify(plan), { metadata: { saved, n: files.length } });
        // smaž soubory, které už v plánu nejsou
        const keep = new Set(files.map(f => String(f.fid))); let cursor;
        do {
          const r = await env.OBCHODY.list({ prefix: "mf:" + d + ":", cursor, limit: 1000 });
          for (const k of r.keys) if (!keep.has(k.name.split(":")[2])) await env.OBCHODY.delete(k.name);
          cursor = r.list_complete ? null : r.cursor;
        } while (cursor);
        return json({ ok: true, saved });
      }
      if (p === "/mentor/plan" && req.method === "DELETE") {
        const s = +url.searchParams.get("s") || Date.now();
        const cur = await env.OBCHODY.getWithMetadata("m:" + d);
        if (cur.metadata && +cur.metadata.saved > s) return json({ ok: true, novejsi: true });
        await env.OBCHODY.put("m:" + d, "{}", { metadata: { saved: s, del: true } });
        let cursor;
        do {
          const r = await env.OBCHODY.list({ prefix: "mf:" + d + ":", cursor, limit: 1000 });
          for (const k of r.keys) await env.OBCHODY.delete(k.name);
          cursor = r.list_complete ? null : r.cursor;
        } while (cursor);
        return json({ ok: true });
      }
      if (p === "/mentor/file" && fid) {
        const key = "mf:" + d + ":" + fid;
        if (req.method === "PUT") {
          const buf = await req.arrayBuffer();
          if (buf.byteLength > 24 * 1048576) return json({ ok: false, chyba: "soubor je větší než 24 MB" }, 413);
          await env.OBCHODY.put(key, buf, { metadata: { type: (req.headers.get("Content-Type") || "application/octet-stream").slice(0, 80) } });
          return json({ ok: true });
        }
        if (req.method === "GET") {
          const r = await env.OBCHODY.getWithMetadata(key, { type: "arrayBuffer" });
          if (!r.value) return json({ ok: false }, 404);
          return new Response(r.value, { headers: { "Content-Type": (r.metadata && r.metadata.type) || "application/octet-stream", "Cache-Control": "private, max-age=86400", ...CORS } });
        }
      }
      return json({ ok: false, chyba: "neznámý požadavek" }, 400);
    }

    // 8) Synchronizace celé aplikace (obchody, dny bez obchodu, nastavení) – vždy s heslem ?t=TOKEN
    if (url.pathname.startsWith("/sync/")) {
      if (!okTok(env, url)) return json({ ok: false, chyba: "špatné heslo" }, 403);
      const p = url.pathname, k = (url.searchParams.get("k") || "").slice(0, 400), h = (url.searchParams.get("h") || "").replace(/[^a-f0-9]/g, "").slice(0, 64);
      if (p === "/sync/list" && req.method === "GET") {
        const out = []; let cursor;
        do {
          const r = await env.OBCHODY.list({ prefix: "s:", cursor, limit: 1000 });
          r.keys.forEach(x => out.push(Object.assign({ k: x.name.slice(2) }, x.metadata || {})));
          cursor = r.list_complete ? null : r.cursor;
        } while (cursor);
        return json(out, 200, { "Cache-Control": "no-store" });
      }
      if (p === "/sync/rec" && k) {
        if (req.method === "GET") { const v = await env.OBCHODY.get("s:" + k); return v ? new Response(v, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...CORS } }) : json({ ok: false }, 404); }
        if (req.method === "PUT") {
          let b; try { b = await req.json(); } catch (e) { return json({ ok: false, chyba: "špatná data" }, 400); }
          const u = Date.now();
          await env.OBCHODY.put("s:" + k, JSON.stringify(b.v === undefined ? null : b.v), { metadata: { h: String(b.h || "").slice(0, 40), u } });
          return json({ ok: true, u });
        }
        if (req.method === "DELETE") { const u = Date.now(); await env.OBCHODY.put("s:" + k, "null", { metadata: { d: 1, u } }); return json({ ok: true, u }); }
      }
      if (p === "/sync/blob" && h) {
        const key = "sb:" + h;
        if (req.method === "PUT") {
          const buf = await req.arrayBuffer();
          if (buf.byteLength > 24 * 1048576) return json({ ok: false, chyba: "soubor je větší než 24 MB" }, 413);
          await env.OBCHODY.put(key, buf, { metadata: { type: (req.headers.get("Content-Type") || "application/octet-stream").slice(0, 80) } });
          return json({ ok: true });
        }
        if (req.method === "GET") {
          const r = await env.OBCHODY.getWithMetadata(key, { type: "arrayBuffer" });
          if (!r.value) return json({ ok: false }, 404);
          return new Response(r.value, { headers: { "Content-Type": (r.metadata && r.metadata.type) || "application/octet-stream", "Cache-Control": "private, max-age=31536000", ...CORS } });
        }
      }
      if (p === "/sync/bkreq") {
        if (req.method === "PUT") { let b = {}; try { b = await req.json(); } catch (e) {} await env.OBCHODY.put("bkreq", JSON.stringify({ dev: String(b.dev || "").slice(0, 40), t: Date.now() })); return json({ ok: true }); }
        const v = await env.OBCHODY.get("bkreq"); return new Response(v || "null", { headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...CORS } });
      }
      return json({ ok: false, chyba: "neznámý požadavek" }, 400);
    }

    // 9) Upozornění
    if (url.pathname.startsWith("/push/")) {
      const p = url.pathname;
      if (p === "/push/key") return json({ pub: (await vapid(env)).pub }, 200, { "Cache-Control": "no-store" });
      if (p === "/push/last") { let m = null; try { m = JSON.parse(await env.OBCHODY.get("push:last")); } catch (e) {} return json(m || { t: "Kompas klidu", b: "Mrkni do Deníku.", u: "journal.html" }, 200, { "Cache-Control": "no-store" }); }
      if (!okTok(env, url)) return json({ ok: false, chyba: "špatné heslo" }, 403);
      if (p === "/push/sub" && (req.method === "POST" || req.method === "DELETE")) {
        let sub; try { sub = await req.json(); } catch (e) { return json({ ok: false }, 400); }
        if (!sub || !sub.endpoint || !/^https:\/\//.test(sub.endpoint)) return json({ ok: false, chyba: "chybí endpoint" }, 400);
        const id = "push:sub:" + b64u(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(sub.endpoint))).slice(0, 24);
        if (req.method === "DELETE") { await env.OBCHODY.delete(id); return json({ ok: true }); }
        await env.OBCHODY.put(id, JSON.stringify({ endpoint: sub.endpoint, keys: sub.keys || {} })); return json({ ok: true });
      }
      if (p === "/push/state" && req.method === "PUT") { const b = await req.text(); if (b.length > 20000) return json({ ok: false }, 413); await env.OBCHODY.put("push:state", b); return json({ ok: true }); }
      if (p === "/push/test" && req.method === "POST") return json(Object.assign({ ok: true }, await pushAll(env, { t: "🔔 Kompas klidu", b: "✅ Upozornění fungují. Takhle ti připomenu nezapsané dny.", u: "journal.html" })));
      if (p === "/push/check") { const now = prague(new Date()); let st = null; try { st = JSON.parse(await env.OBCHODY.get("push:state")); } catch (e) {} return json({ now, msg: pushMsg(st, now), subs: (await env.OBCHODY.list({ prefix: "push:sub:" })).keys.length }); }
      return json({ ok: false, chyba: "neznámý požadavek" }, 400);
    }

    // 10) Smazání všech obchodů (např. po testu): POST /reset?t=TVUJ_TOKEN
    if (url.pathname === "/reset" && req.method === "POST") {
      if (!okTok(env, url)) return json({ ok: false }, 403);
      let cursor;
      do {
        const r = await env.OBCHODY.list({ prefix: "e:", cursor, limit: 1000 });
        for (const k of r.keys) await env.OBCHODY.delete(k.name);
        cursor = r.list_complete ? null : r.cursor;
      } while (cursor);
      return json({ ok: true });
    }

    return json({ ok: true, v: VERZE, info: "Kompas klidu – přijímač obchodů z TradingView běží (verze " + VERZE + ")." });
  }
};
