/**
 * Vercel Edge Function — PrizePicks CORS proxy
 * Runs on V8 at Vercel's CDN edge (different IP pool from Netlify).
 * Endpoint: /api/pp?path=/leagues
 *           /api/pp?path=/projections?league_id=2&per_page=25&page=1
 */

export const config = { runtime: "edge" };

const PP_BASE = "https://api.prizepicks.com";

const UA_POOL = [
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
  "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
];

function ppFetchHeaders(ua) {
  const h = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": ua,
    "Referer": "https://app.prizepicks.com/",
    "Origin": "https://app.prizepicks.com",
    "X-Device-ID": crypto.randomUUID(),
    "X-App-Version": "9.0.0",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
  };
  if (ua.includes("Chrome")) {
    h["sec-ch-ua"] = '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"';
    h["sec-ch-ua-mobile"] = ua.includes("iPhone") || ua.includes("Android") ? "?1" : "?0";
    h["sec-ch-ua-platform"] = ua.includes("Windows") ? '"Windows"' : ua.includes("Mac") ? '"macOS"' : '"Android"';
  }
  return h;
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization",
    "Access-Control-Max-Age":       "86400",
  };
}

function jsonResp(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...corsHeaders(), "Content-Type": "application/json" },
  });
}

export default async function handler(request) {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }

  const url  = new URL(request.url);
  const path = url.searchParams.get("path");

  if (!path) return jsonResp({ error: "Missing ?path= parameter" }, 400);

  const decoded = decodeURIComponent(path);
  const target  = PP_BASE + decoded;
  if (!target.startsWith(PP_BASE + "/")) {
    return jsonResp({ error: "Invalid target" }, 403);
  }

  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) {
      await new Promise(r => setTimeout(r, 300 * Math.pow(2, attempt)));
    }
    const ua = UA_POOL[(attempt + Math.floor(Math.random() * UA_POOL.length)) % UA_POOL.length];
    try {
      const resp = await fetch(target, {
        method: "GET",
        headers: ppFetchHeaders(ua),
      });

      if ((resp.status === 429 || resp.status === 403) && attempt < 2) {
        lastErr = new Error(`PP returned ${resp.status}`);
        continue;
      }

      const text = await resp.text();
      return new Response(text, {
        status: resp.status,
        headers: {
          ...corsHeaders(),
          "Content-Type":  resp.headers.get("Content-Type") || "application/json",
          "Cache-Control": "public, max-age=90",
          "X-PP-Status":   String(resp.status),
          "X-PP-Attempt":  String(attempt + 1),
        },
      });
    } catch (err) {
      lastErr = err;
    }
  }

  return jsonResp({ error: lastErr?.message || "Proxy failed after retries" }, 502);
}
