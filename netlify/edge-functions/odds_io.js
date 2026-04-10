// Odds-API.io proxy — same JSON contract as /api/odds_io (Vercel Python).
// GET /.netlify/functions/odds_io?date=YYYY-MM-DD
// Env: ODDS_API_KEY or ODDS_API_IO_KEY

const BASE = "https://api.odds-api.io/v3";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Accept",
    "Access-Control-Max-Age": "86400",
    "Cache-Control": "public, max-age=900",
    "Content-Type": "application/json; charset=utf-8",
  };
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: corsHeaders() });
}

function eventsList(raw) {
  if (Array.isArray(raw)) return raw;
  if (raw && typeof raw === "object") {
    for (const k of ["data", "events", "results", "items"]) {
      if (Array.isArray(raw[k])) return raw[k];
    }
  }
  return [];
}

function multiList(raw) {
  if (Array.isArray(raw)) return raw;
  if (raw && typeof raw === "object") {
    for (const k of ["data", "results", "events"]) {
      if (Array.isArray(raw[k])) return raw[k];
    }
  }
  return [];
}

function isMlbEvent(ev) {
  const lg = ev.league || {};
  const slug = String(lg.slug || "").toLowerCase();
  const name = String(lg.name || "").toLowerCase();
  if (slug === "mlb") return true;
  if (name.includes("major league") && name.includes("baseball")) return true;
  if (["usa-mlb", "us-mlb"].includes(slug)) return true;
  const sp = (ev.sport && ev.sport.slug) || "";
  if (!slug && sp === "baseball") return true;
  return false;
}

function eventDateKey(ev) {
  const ds = ev.date || ev.startTime || "";
  return typeof ds === "string" && ds.length >= 10 ? ds.slice(0, 10) : "";
}

function teamStr(v) {
  if (v == null) return "";
  if (typeof v === "object") {
    return String(v.name || v.title || v.shortName || v.label || "").trim();
  }
  return String(v).trim();
}

const _ISO_NOISE = /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|^\d{4}-\d{2}-\d{2}$/;
const _UUID_NOISE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isNoiseString(s) {
  const t = String(s || "").trim();
  if (!t || t.length > 400) return true;
  if (_UUID_NOISE.test(t)) return true;
  if (_ISO_NOISE.test(t)) return true;
  return false;
}

function shouldSkipHarvestKey(k) {
  if (!k) return false;
  const kl = k.toLowerCase();
  if (["odds", "bookmakers", "id", "eventid", "event_id"].includes(kl)) return true;
  if (k.endsWith("At") || k.endsWith("_at")) return true;
  if (["timestamp", "lastupdated", "lastmodified", "syncedat", "starttime", "datetime", "date", "time", "ts"].includes(kl))
    return true;
  return false;
}

function allStringValues(obj, skip, depth = 0) {
  if (!obj || typeof obj !== "object" || depth > 4) return "";
  const parts = [];
  for (const k of Object.keys(obj)) {
    if (skip.has(k) || shouldSkipHarvestKey(k)) continue;
    const v = obj[k];
    if (typeof v === "string" && v.trim()) {
      if (!isNoiseString(v)) parts.push(v.trim());
    } else if (v && typeof v === "object" && !Array.isArray(v))
      parts.push(allStringValues(v, skip, depth + 1));
  }
  return parts.join(" ");
}

function compositeMarketName(m, odd) {
  const chunks = [];
  const name = (m.name || "").trim();
  if (name && !isNoiseString(name)) chunks.push(name);
  for (const k of [
    "title", "label", "type", "category", "group", "description", "handicapName",
    "key", "slug", "statistic", "statType", "propType", "betType", "subType",
  ]) {
    if (m[k] && String(m[k]).trim()) {
      const sv = String(m[k]).trim();
      if (!isNoiseString(sv)) chunks.push(sv);
    }
  }
  const lbl = String(odd.label || "").trim();
  for (const k of ["stat", "market", "type", "selectionName", "description", "name"]) {
    const v = odd[k];
    if (v && String(v).trim() && String(v).trim() !== lbl) {
      const sv = String(v).trim();
      if (!isNoiseString(sv)) chunks.push(sv);
    }
  }
  chunks.push(allStringValues(m, new Set(["odds", "bookmakers"]), 0));
  chunks.push(
    allStringValues(odd, new Set(["label", "hdp", "over", "under", "home", "away", "draw"]), 0),
  );
  const seen = new Set();
  const out = [];
  for (const c of chunks) {
    if (!c || isNoiseString(c)) continue;
    const cl = c.toLowerCase().trim();
    if (!cl || seen.has(cl)) continue;
    seen.add(cl);
    out.push(c.trim());
  }
  return out.length ? out.join(" · ") : "Player Props";
}

function statHintFromMarket(m) {
  let blob = "";
  try {
    const o = { ...m };
    delete o.odds;
    blob = JSON.stringify(o).toLowerCase();
  } catch (_) {
    return "";
  }
  if (!blob) return "";
  if (blob.includes("strikeout") || blob.includes("strike out")) return "strikeouts";
  if (blob.includes("pitcher") && (blob.includes(" k ") || blob.includes("k's") || blob.includes(" ks ")))
    return "strikeouts";
  if (blob.includes("total base")) return "tb";
  if (blob.includes("home run")) return "hr";
  if (blob.includes("rbi") || blob.includes("runs batted")) return "rbi";
  if (blob.includes("stolen") && blob.includes("base")) return "sb";
  if (blob.includes("base on balls") || (blob.includes("walk") && !blob.includes("pitcher"))) return "bb";
  if (blob.includes("hits") && !blob.includes("pitcher") && !blob.includes("allowed") && !blob.includes("against"))
    return "hits";
  if (blob.includes("runs") && (blob.includes("scored") || blob.includes("batter"))) return "runs";
  if (blob.includes("hits+runs") || blob.includes("h+r+rbi")) return "hrr";
  return "";
}

function firstPlayerPropMarket(ev) {
  const bks = ev.bookmakers || {};
  for (const bkName of Object.keys(bks)) {
    const markets = bks[bkName];
    if (!Array.isArray(markets)) continue;
    for (let mi = 0; mi < markets.length; mi++) {
      const m = markets[mi];
      if (!m || typeof m !== "object") continue;
      for (const odd of m.odds || []) {
        if (odd && odd.label) return { bk: bkName, mi: String(mi), m };
      }
    }
  }
  return null;
}

function debugTrimEvent(ev) {
  const out = {
    id: ev.id,
    home: ev.home,
    away: ev.away,
    date: ev.date,
    top_level_keys: Object.keys(ev).sort().slice(0, 80),
    bookmakers: {},
  };
  const fpp = firstPlayerPropMarket(ev);
  if (fpp) {
    const o0 = (fpp.m.odds || [])[0] || {};
    const keys = typeof o0 === "object" && o0 ? Object.keys(o0).sort() : [];
    const sample = {};
    for (const k of keys.slice(0, 20)) sample[k] = o0[k];
    out.player_prop_market_example = {
      bookmaker: fpp.bk,
      market_index: fpp.mi,
      name: fpp.m.name,
      market_keys: Object.keys(fpp.m).sort(),
      stat_hint_guess: statHintFromMarket(fpp.m),
      first_odd_keys: keys,
      first_odd_sample: sample,
    };
  }
  const bks = ev.bookmakers || {};
  let bi = 0;
  for (const bkName of Object.keys(bks)) {
    if (bi++ >= 2) break;
    const markets = bks[bkName];
    if (!Array.isArray(markets)) {
      out.bookmakers[bkName] = markets;
      continue;
    }
    const tlist = [];
    for (let mi = 0; mi < Math.min(3, markets.length); mi++) {
      const m = markets[mi];
      if (!m || typeof m !== "object") {
        tlist.push(m);
        continue;
      }
      const mo = { name: m.name, market_keys: Object.keys(m).sort() };
      const odds = m.odds || [];
      mo.odds_count = Array.isArray(odds) ? odds.length : null;
      mo.odds_sample = [];
      if (Array.isArray(odds)) {
        for (let oi = 0; oi < Math.min(4, odds.length); oi++) {
          const odd = odds[oi];
          if (odd && typeof odd === "object") {
            const o = {};
            for (const k of Object.keys(odd).sort()) o[k] = odd[k];
            mo.odds_sample.push(o);
          } else mo.odds_sample.push(odd);
        }
      }
      tlist.push(mo);
    }
    out.bookmakers[bkName] = tlist;
  }
  return out;
}

function appendPropRows(ev, rows, eventTeams) {
  const eid = ev.id;
  let home = teamStr(ev.home);
  let away = teamStr(ev.away);
  if (eventTeams && eid != null) {
    const pair = eventTeams.get(String(eid));
    if (pair) {
      if (!home) home = pair[0];
      if (!away) away = pair[1];
    }
  }
  const bookmakers = ev.bookmakers || {};
  for (const bk of Object.keys(bookmakers)) {
    const markets = bookmakers[bk];
    if (!Array.isArray(markets)) continue;
    for (const m of markets) {
      const odds = m.odds || [];
      for (const odd of odds) {
        const label = odd.label;
        if (!label) continue;
        const hdp = odd.hdp;
        if (hdp === null || hdp === undefined) continue;
        const hf = parseFloat(hdp);
        if (Number.isNaN(hf)) continue;
        const mname = compositeMarketName(m, odd);
        rows.push({
          eventId: eid,
          home: String(home),
          away: String(away),
          bookmaker: String(bk),
          market: String(mname),
          player: String(label).trim(),
          hdp: hf,
          over: odd.over,
          under: odd.under,
          statHint: statHintFromMarket(m),
        });
      }
    }
  }
}

export default async (request) => {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }

  const url = new URL(request.url);
  const date = url.searchParams.get("date");
  const bookmakers = url.searchParams.get("bookmakers") || "DraftKings,FanDuel";
  const dbg = url.searchParams.get("structure") || url.searchParams.get("debug");
  const wantStructure = ["1", "true", "yes"].includes(String(dbg || "").toLowerCase());
  const apiKey = Deno.env.get("ODDS_API_KEY") || Deno.env.get("ODDS_API_IO_KEY");

  if (!apiKey) return json({ ok: false, error: "missing_ODDS_API_KEY" });
  if (!date || date.length < 10) return json({ ok: false, error: "missing_date" });

  const dateKey = date.slice(0, 10);
  const out = { ok: false, error: null, rows: [], meta: { apiCalls: 0, eventCount: 0, propRows: 0 } };

  try {
    const evUrl = `${BASE}/events?${new URLSearchParams({ sport: "baseball", apiKey }).toString()}`;
    const evRes = await fetch(evUrl, { headers: { Accept: "application/json" } });
    const rawEv = await evRes.json();
    out.meta.apiCalls = 1;

    if (rawEv && rawEv.error) {
      out.error = String(rawEv.error);
      return json(out, 200);
    }

    const rawAll = eventsList(rawEv);
    const onDate = rawAll.filter((e) => eventDateKey(e) === dateKey);
    let events = onDate.filter(isMlbEvent);
    if (!events.length && onDate.length) events = onDate;

    out.meta.eventCount = events.length;
    if (!events.length) {
      out.ok = true;
      out.meta.note = "no_mlb_events_for_date";
      return json(out, 200);
    }

    const eventTeams = new Map();
    for (const e of events) {
      if (e.id == null) continue;
      eventTeams.set(String(e.id), [teamStr(e.home), teamStr(e.away)]);
    }

    const rows = [];
    const ids = events.map((e) => e.id).filter((id) => id != null);
    let firstMultiRaw = null;
    for (let i = 0; i < ids.length; i += 10) {
      const chunk = ids.slice(i, i + 10);
      const multiUrl =
        `${BASE}/odds/multi?` +
        new URLSearchParams({
          apiKey,
          eventIds: chunk.join(","),
          bookmakers,
        }).toString();
      const mRes = await fetch(multiUrl, { headers: { Accept: "application/json" } });
      const multiRaw = await mRes.json();
      if (wantStructure && firstMultiRaw == null) firstMultiRaw = multiRaw;
      out.meta.apiCalls += 1;
      for (const ev of multiList(multiRaw)) {
        appendPropRows(ev, rows, eventTeams);
      }
    }

    out.rows = rows;
    out.meta.propRows = rows.length;
    const seenM = new Set();
    const sampleMarkets = [];
    for (const row of rows) {
      const m = row.market || "";
      if (m && !seenM.has(m)) {
        seenM.add(m);
        sampleMarkets.push(m.slice(0, 160));
        if (sampleMarkets.length >= 24) break;
      }
    }
    out.meta.sampleMarkets = sampleMarkets;
    const seenT = new Set();
    const sampleEventTeams = [];
    for (const row of rows) {
      const key = `${row.home}|${row.away}`;
      if (row.home && !seenT.has(key)) {
        seenT.add(key);
        sampleEventTeams.push({ home: row.home, away: row.away });
        if (sampleEventTeams.length >= 6) break;
      }
    }
    out.meta.sampleEventTeams = sampleEventTeams;
    if (wantStructure && firstMultiRaw != null) {
      const lst = multiList(firstMultiRaw);
      if (lst.length && lst[0]) {
        let sj = JSON.stringify(debugTrimEvent(lst[0]), null, 2);
        if (sj.length > 36000) sj = sj.slice(0, 36000) + "\n… (truncated)";
        out.meta.oddsStructureSample = sj;
      }
    }
    out.ok = true;
    return json(out, 200);
  } catch (e) {
    out.error = String(e && e.message ? e.message : e);
    return json(out, 200);
  }
};
