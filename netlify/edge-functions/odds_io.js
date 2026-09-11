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

const NFL_TEAM_TOKENS = [
  "cardinals", "arizona", "falcons", "atlanta", "ravens", "baltimore",
  "bills", "buffalo", "panthers", "carolina", "bears", "chicago",
  "bengals", "cincinnati", "browns", "cleveland", "cowboys", "dallas",
  "broncos", "denver", "detroit", "packers", "green bay",
  "texans", "houston texans", "colts", "indianapolis", "jaguars", "jacksonville",
  "chiefs", "kansas city", "raiders", "las vegas", "chargers",
  "rams", "dolphins", "miami", "vikings", "minnesota", "patriots", "new england",
  "saints", "new orleans", "giants", "jets", "eagles", "philadelphia",
  "steelers", "pittsburgh", "49ers", "niners", "san francisco",
  "seahawks", "seattle", "buccaneers", "bucs", "tampa", "titans", "tennessee",
  "commanders", "washington",
];
const NON_NFL_FOOTBALL_MARKERS = [
  "ncaaf", "ncaa", "college", "cfb",
  "cfl", "canadian football", "canada-cfl",
  "roughrider", "stampeder", "elks", "blue bomber", "tiger-cat", "tigercat",
  "argonaut", "alouette", "redblack", "red black", "bc lions", "b.c. lions",
  "b c lions", "british columbia", "saskatchewan", "winnipeg",
  "calgary stamp", "edmonton elk", "hamilton tiger", "ottawa red",
  "montreal alou", "toronto argonaut",
  "ufl", "xfl", "usfl", "aaf", "arena football",
  "roughneck", "battlehawk", "brahma", "showboat", "stallion",
];

function footballBlob(ev) {
  const lg = ev.league || {};
  return [lg.slug, lg.name, teamStr(ev.home), teamStr(ev.away)].join(" ").toLowerCase();
}

function looksLikeNonNflFootball(ev) {
  const blob = footballBlob(ev);
  if (NON_NFL_FOOTBALL_MARKERS.some((x) => blob.includes(x))) return true;
  if (blob.includes("lions") && (blob.includes("bc ") || blob.includes("b.c") || blob.includes("vancouver"))) return true;
  return false;
}

function looksLikeCollegeFootball(ev) {
  const blob = footballBlob(ev);
  return ["ncaaf", "ncaa", "college", "cfb"].some((x) => blob.includes(x));
}

function teamLooksNfl(name) {
  const n = String(name || "").toLowerCase();
  if (!n) return false;
  if (NON_NFL_FOOTBALL_MARKERS.some((x) => n.includes(x))) return false;
  if (n.includes("lions")) return n.includes("detroit") || n.trim() === "lions";
  return NFL_TEAM_TOKENS.some((tok) => n.includes(tok));
}

function isNflEvent(ev) {
  if (looksLikeNonNflFootball(ev)) return false;
  const lg = ev.league || {};
  const slug = String(lg.slug || "").toLowerCase();
  const name = String(lg.name || "").toLowerCase();
  if (slug.includes("cfl") || name.includes("cfl")) return false;
  if (slug.startsWith("nfl") || slug.includes("nfl") || ["usa-nfl", "us-nfl"].includes(slug)) return true;
  if (name.includes("national football") || name.trim() === "nfl" || (name.includes("nfl") && !name.includes("cfl"))) return true;
  const sp = ev.sport || {};
  const spSlug = String(sp.slug || "").toLowerCase();
  const spName = String(sp.name || "").toLowerCase();
  if (spSlug === "nfl" || spName === "nfl") return true;
  return teamLooksNfl(teamStr(ev.home)) && teamLooksNfl(teamStr(ev.away));
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

function nflStatHint(raw) {
  raw = String(raw || "").toLowerCase();
  if (!raw) return "";
  if (raw.includes("anytime") && (raw.includes("td") || raw.includes("touchdown"))) return "anytime_td";
  if ((raw.includes("pass") && raw.includes("rush") && (raw.includes("rec") || raw.includes("receiving")) && raw.includes("yard")) || raw.includes("pass+rush+rec"))
    return "pass_rush_rec_yds";
  if ((raw.includes("pass") && raw.includes("rush") && raw.includes("yard")) || raw.includes("pass+rush") || raw.includes("pass + rush"))
    return "pass_rush_yds";
  if (
    (raw.includes("rush") && (raw.includes("rec") || raw.includes("receiving")) && raw.includes("yard")) ||
    raw.includes("rush+rec") ||
    raw.includes("rec+rush")
  )
    return "rush_rec_yds";
  if (raw.includes("pass") && raw.includes("rush") && (raw.includes("td") || raw.includes("touchdown")) && !raw.includes("yard"))
    return "pass_rush_td";
  if (raw.includes("rush") && (raw.includes("rec") || raw.includes("receiving")) && (raw.includes("td") || raw.includes("touchdown")) && !raw.includes("yard"))
    return "rush_rec_td";
  if (raw.includes("passing yard") || raw.includes("pass yard") || raw.includes("pass yds")) return "pass_yds";
  if ((raw.includes("passing") || raw.includes("pass ")) && (raw.includes("td") || raw.includes("touchdown"))) return "pass_td";
  if (raw.includes("completion")) return "completions";
  if ((raw.includes("pass") || raw.includes("passing")) && raw.includes("attempt")) return "pass_att";
  if (raw.includes("defensive interception") || raw.includes("def int")) return "def_ints";
  if (raw.includes("interception") || raw === "int" || raw === "ints") return "ints";
  if (raw.includes("rushing yard") || raw.includes("rush yard") || raw.includes("rush yds")) return "rush_yds";
  if ((raw.includes("rush") && raw.includes("attempt")) || raw.includes("carries") || raw.includes("carry")) return "rush_att";
  if (raw.includes("rush") && (raw.includes("td") || raw.includes("touchdown"))) return "rush_td";
  if (raw.includes("receiving yard") || raw.includes("rec yard") || raw.includes("rec yds")) return "rec_yds";
  if (raw.includes("reception") || raw === "recs" || raw === "rec") return "receptions";
  if ((raw.includes("receiv") || raw.includes("rec ")) && (raw.includes("td") || raw.includes("touchdown"))) return "rec_td";
  if (raw.includes("target")) return "targets";
  if (raw.includes("first down") || raw.includes("1st down")) return "first_downs";
  if (raw.includes("fantasy")) return "fantasy";
  if (raw.includes("longest rec") || raw.includes("long rec")) return "long_rec";
  if (raw.includes("longest rush") || raw.includes("long rush")) return "long_rush";
  if (raw.includes("longest pass") || raw.includes("long pass") || raw.includes("longest completion")) return "long_pass";
  if (raw.includes("sack")) return "sacks";
  if (raw.includes("solo tackle")) return "solo_tackles";
  if (raw.includes("tackle") && (raw.includes("assist") || raw.includes("+") || raw.includes("combined"))) return "tackles_ast";
  if (raw.includes("tackle")) return "tackles";
  if (raw.includes("longest field") || raw.includes("long fg") || raw.includes("fg long")) return "fg_long";
  if (raw.includes("field goal") || raw === "fg" || raw === "fgs") return "fg_made";
  if (raw.includes("extra point") || raw === "pat" || raw === "xp") return "pat";
  if (raw.includes("kicking point") || raw.includes("kicker point")) return "kicking_pts";
  return "";
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
  const nfl = nflStatHint(blob);
  if (nfl) return nfl;
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

function statHintFromText(text) {
  const raw = String(text || "").trim().toLowerCase();
  if (!raw) return "";
  const nfl = nflStatHint(raw);
  if (nfl) return nfl;
  if (raw.includes("strikeout") || raw.includes("strike out")) return "strikeouts";
  if (raw.includes("total base")) return "tb";
  if (raw.includes("home run")) return "hr";
  if (raw.includes("rbi") || raw.includes("runs batted")) return "rbi";
  if (raw.includes("stolen") && raw.includes("base")) return "sb";
  if (raw.includes("base on balls") || raw.includes("walk")) return "bb";
  if (raw.includes("hits+runs") || raw.includes("hits + runs + rbis") || raw.includes("h+r+rbi")) return "hrr";
  if (raw.includes("runs scored") || raw === "runs") return "runs";
  if (raw.includes("hits") || raw === "hit") return "hits";
  return "";
}

function parsePlayerLabel(label) {
  const raw = String(label || "").trim();
  if (!raw) return { playerName: "", labelStat: "" };
  const m = raw.match(/^(.*?)\s*\(([^()]+)\)\s*$/);
  if (m) return { playerName: m[1].trim(), labelStat: m[2].trim() };
  return { playerName: raw, labelStat: "" };
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
        const { playerName, labelStat } = parsePlayerLabel(label);
        if (!playerName) continue;
        const hdp = odd.hdp;
        if (hdp === null || hdp === undefined) continue;
        const hf = parseFloat(hdp);
        if (Number.isNaN(hf)) continue;
        let mname = compositeMarketName(m, odd);
        const hint = statHintFromMarket(m) || statHintFromText(labelStat);
        if (labelStat && String(mname).trim().toLowerCase() === "player props") {
          mname = `Player Props · ${labelStat}`;
        }
        rows.push({
          eventId: eid,
          home: String(home),
          away: String(away),
          bookmaker: String(bk),
          market: String(mname),
          player: playerName,
          hdp: hf,
          over: odd.over,
          under: odd.under,
          statHint: hint,
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
  const dateFrom = url.searchParams.get("from");
  const dateTo = url.searchParams.get("to");
  const sport = String(url.searchParams.get("sport") || "mlb").toLowerCase();
  // API plan allows max 2 bookmakers (FanDuel, DraftKings).
  const bookmakers = url.searchParams.get("bookmakers") || "DraftKings,FanDuel";
  const dbg = url.searchParams.get("structure") || url.searchParams.get("debug");
  const wantStructure = ["1", "true", "yes"].includes(String(dbg || "").toLowerCase());
  const apiKey = Deno.env.get("ODDS_API_KEY") || Deno.env.get("ODDS_API_IO_KEY");

  if (!apiKey) return json({ ok: false, error: "missing_ODDS_API_KEY" });

  const isNfl = sport === "nfl" || sport === "football";
  const start = ((isNfl ? dateFrom || date : date) || "").slice(0, 10);
  const end = ((isNfl ? dateTo || dateFrom || date : date) || "").slice(0, 10);
  if (!start || start.length < 10) return json({ ok: false, error: isNfl ? "missing_from" : "missing_date" });

  const out = { ok: false, error: null, rows: [], meta: { apiCalls: 0, eventCount: 0, propRows: 0, sport: isNfl ? "nfl" : "mlb" } };

  try {
    const eventAttempts = isNfl
      ? [
          { sport: "nfl", ranged: true },
          { sport: "american-football", league: "nfl-regular-season", ranged: true },
          { sport: "american-football", ranged: true },
          { sport: "nfl", ranged: false },
          { sport: "american-football", league: "nfl-regular-season", ranged: false },
        ]
      : [{ sport: "baseball", ranged: false }];
    let events = [];
    for (const attempt of eventAttempts) {
      const params = { sport: attempt.sport, apiKey };
      if (attempt.league) params.league = attempt.league;
      if (isNfl && attempt.ranged) {
        params.from = `${start}T00:00:00Z`;
        params.to = `${end || start}T23:59:59Z`;
      }
      const evUrl = `${BASE}/events?${new URLSearchParams(params).toString()}`;
      let rawEv;
      try {
        const evRes = await fetch(evUrl, { headers: { Accept: "application/json" } });
        rawEv = await evRes.json();
      } catch (_) {
        continue;
      }
      out.meta.apiCalls += 1;
      if (rawEv && rawEv.error) {
        if (!isNfl) {
          out.error = String(rawEv.error);
          return json(out, 200);
        }
        continue;
      }
      const rawAll = eventsList(rawEv);
      const inRange = rawAll.filter((e) => {
        const dk = eventDateKey(e);
        if (!dk) return false;
        if (!isNfl) return dk === start;
        if (!attempt.ranged) return true;
        if (start && dk < start) return false;
        if (end && dk > end) return false;
        return true;
      });
      let filtered = inRange.filter(isNfl ? isNflEvent : isMlbEvent);
      if (filtered.length) {
        events = filtered;
        out.meta.eventsSport = attempt.sport;
        if (attempt.league) out.meta.eventsLeague = attempt.league;
        out.meta.eventsQuery = isNfl && attempt.ranged ? "ranged" : isNfl ? "next_14d" : "date";
        break;
      }
      if (!isNfl && inRange.length) {
        events = inRange;
        break;
      }
    }

    out.meta.eventCount = events.length;
    if (!events.length) {
      out.ok = true;
      out.meta.note = isNfl ? "no_nfl_events_for_range" : "no_mlb_events_for_date";
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
