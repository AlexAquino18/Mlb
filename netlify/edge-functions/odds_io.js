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

function appendPropRows(ev, rows) {
  const eid = ev.id;
  const home = teamStr(ev.home);
  const away = teamStr(ev.away);
  const bookmakers = ev.bookmakers || {};
  for (const bk of Object.keys(bookmakers)) {
    const markets = bookmakers[bk];
    if (!Array.isArray(markets)) continue;
    for (const m of markets) {
      const mname = m.name || "";
      const odds = m.odds || [];
      for (const odd of odds) {
        const label = odd.label;
        if (!label) continue;
        const hdp = odd.hdp;
        if (hdp === null || hdp === undefined) continue;
        const hf = parseFloat(hdp);
        if (Number.isNaN(hf)) continue;
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

    const rows = [];
    const ids = events.map((e) => e.id).filter((id) => id != null);
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
      out.meta.apiCalls += 1;
      for (const ev of multiList(multiRaw)) {
        appendPropRows(ev, rows);
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
    out.ok = true;
    return json(out, 200);
  } catch (e) {
    out.error = String(e && e.message ? e.message : e);
    return json(out, 200);
  }
};
