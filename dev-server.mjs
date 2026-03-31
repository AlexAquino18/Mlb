#!/usr/bin/env node
/**
 * Local static server + PrizePicks proxy (same path as Netlify: /.netlify/functions/pp).
 * Run: npm start  → open http://localhost:8080  (do not use file://)
 */
import http from "http";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PP_BASE = "https://api.prizepicks.com";
const PORT = Number(process.env.PORT) || 8080;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".ico": "image/x-icon",
  ".png": "image/png",
  ".svg": "image/svg+xml",
};

function cors(res, extra = {}) {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Accept, Content-Type",
    ...extra,
  };
}

const UA_POOL = [
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
  "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
];

function randomUA(offset = 0) {
  return UA_POOL[(Math.floor(Math.random() * UA_POOL.length) + offset) % UA_POOL.length];
}

function randomHex(len) {
  return Array.from({ length: len }, () => Math.floor(Math.random() * 16).toString(16)).join("");
}

function randomUUID() {
  return `${randomHex(8)}-${randomHex(4)}-4${randomHex(3)}-${(8 + Math.floor(Math.random() * 4)).toString(16)}${randomHex(3)}-${randomHex(12)}`;
}

async function proxyPrizePicks(pathParam, res) {
  const url = PP_BASE + decodeURIComponent(pathParam);
  let lastErr = null;

  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) {
      await new Promise(r => setTimeout(r, 300 * Math.pow(2, attempt)));
    }
    try {
      const r = await fetch(url, {
        headers: {
          "Accept":           "application/json, text/plain, */*",
          "Accept-Language":  "en-US,en;q=0.9",
          "User-Agent":       randomUA(attempt),
          "Referer":          "https://app.prizepicks.com/",
          "Origin":           "https://app.prizepicks.com",
          "X-Device-ID":      randomUUID(),
          "X-App-Version":    "9.0.0",
          "Cache-Control":    "no-cache",
          "Pragma":           "no-cache",
          "Sec-Fetch-Dest":   "empty",
          "Sec-Fetch-Mode":   "cors",
          "Sec-Fetch-Site":   "same-site",
        },
      });

      // 429 / 403 from PrizePicks → retry with different UA
      if ((r.status === 429 || r.status === 403) && attempt < 2) {
        lastErr = new Error(`PrizePicks returned ${r.status}`);
        continue;
      }

      const buf = Buffer.from(await r.arrayBuffer());
      res.writeHead(r.status, cors(res, {
        "Content-Type":  r.headers.get("content-type") || "application/json",
        "Cache-Control": "public, max-age=90",
        "X-PP-Status":   String(r.status),
        "X-PP-Attempt":  String(attempt + 1),
      }));
      res.end(buf);
      return;
    } catch (err) {
      lastErr = err;
    }
  }

  res.writeHead(502, cors(res, { "Content-Type": "application/json" }));
  res.end(JSON.stringify({ error: String(lastErr?.message || lastErr) }));
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);

  if (req.method === "OPTIONS") {
    res.writeHead(204, cors(res));
    res.end();
    return;
  }

  if (u.pathname === "/.netlify/functions/pp") {
    const pathParam = u.searchParams.get("path");
    if (!pathParam) {
      res.writeHead(400, cors(res, { "Content-Type": "application/json" }));
      res.end(JSON.stringify({ error: "Missing ?path= parameter" }));
      return;
    }
    await proxyPrizePicks(pathParam, res);
    return;
  }

  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405);
    res.end();
    return;
  }

  let rel = u.pathname === "/" ? "index.html" : u.pathname.slice(1);
  if (rel.includes("..") || path.isAbsolute(rel)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  const filePath = path.join(__dirname, rel);
  if (!filePath.startsWith(__dirname)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    const ext = path.extname(filePath);
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(data);
  });
});

server.listen(PORT, () => {
  console.log(`MLB Edge → http://localhost:${PORT}`);
  console.log("PrizePicks proxy: /.netlify/functions/pp (same as Netlify)");
});
