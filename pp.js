// netlify/edge-functions/pp.js
// PrizePicks API proxy — deployed as a Netlify EDGE function (not a serverless function).
// Edge functions run on Deno/V8 at the CDN edge, have full fetch + crypto, no cold starts.
//
// Deploy setup:
//   1. Move this file to: netlify/edge-functions/pp.js
//   2. In netlify.toml add:
//        [[edge_functions]]
//          path = "/.netlify/functions/pp"
//          function = "pp"
//   3. Remove the [functions] section if pp.js was the only function.
//
// Usage from the browser (unchanged):
//   /.netlify/functions/pp?path=/leagues
//   /.netlify/functions/pp?path=/projections?league_id=2&per_page=25&page=1

export default async (request, context) => {
  // ── CORS preflight ──────────────────────────────────────────────────────────
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }

  const url  = new URL(request.url);
  const path = url.searchParams.get("path");

  if (!path) {
    return json({ error: "Missing ?path= parameter" }, 400);
  }

  // Only proxy to PrizePicks — safety check
  const decoded = decodeURIComponent(path);
  const target  = "https://api.prizepicks.com" + decoded;
  if (!target.startsWith("https://api.prizepicks.com/")) {
    return json({ error: "Invalid target" }, 403);
  }

  // Rotate through several realistic User-Agent strings to reduce block rate
  const UA_POOL = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Safari/537.36",
  ];
  const ua = UA_POOL[Math.floor(Math.random() * UA_POOL.length)];

  const ppHeaders = {
    "Accept":            "application/json, text/plain, */*",
    "Accept-Language":   "en-US,en;q=0.9",
    "Accept-Encoding":   "gzip, deflate, br",
    "User-Agent":        ua,
    "Referer":           "https://app.prizepicks.com/",
    "Origin":            "https://app.prizepicks.com",
    "X-Device-ID":       crypto.randomUUID(),
    "X-App-Version":     "9.0.0",
    "Cache-Control":     "no-cache",
    "Pragma":            "no-cache",
    "Sec-Fetch-Dest":    "empty",
    "Sec-Fetch-Mode":    "cors",
    "Sec-Fetch-Site":    "same-site",
  };

  // Retry up to 3 times with exponential backoff
  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) {
      await new Promise(r => setTimeout(r, 300 * Math.pow(2, attempt)));
      // Rotate UA on retry
      ppHeaders["User-Agent"] = UA_POOL[(attempt) % UA_POOL.length];
      ppHeaders["X-Device-ID"] = crypto.randomUUID();
    }
    try {
      const resp = await fetch(target, {
        method:  "GET",
        headers: ppHeaders,
      });

      // 429 = rate limited, 403 = blocked — retry with different UA
      if ((resp.status === 429 || resp.status === 403) && attempt < 2) {
        lastErr = new Error(`PP returned ${resp.status}`);
        continue;
      }

      const text = await resp.text();

      // Return the proxied response with CORS headers
      return new Response(text, {
        status: resp.status,
        headers: {
          ...corsHeaders(),
          "Content-Type":  resp.headers.get("Content-Type") || "application/json",
          "X-PP-Status":   String(resp.status),
          "X-PP-Attempt":  String(attempt + 1),
          "Cache-Control": "public, max-age=90",
        },
      });
    } catch (err) {
      lastErr = err;
    }
  }

  return json({ error: lastErr?.message || "Proxy failed after retries" }, 502);
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...corsHeaders(), "Content-Type": "application/json" },
  });
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization",
    "Access-Control-Max-Age":       "86400",
  };
}

// Edge function config — Deno runtime, runs at CDN edge
export const config = { path: "/.netlify/functions/pp" };
