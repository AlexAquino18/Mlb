(function () {
  "use strict";
  const PLANS = { base: { rank: 1, name: "Base" }, ev: { rank: 2, name: "+EV" } };

  function apiUrl(qs) {
    const q = qs ? "?" + qs : "";
    if (typeof location === "undefined" || location.protocol === "file:") return "";
    const host = location.hostname;
    const isLocal = host === "localhost" || host === "127.0.0.1";
    if (isLocal) return `http://127.0.0.1:8080/api/billing${q}`;
    return `${location.origin}/api/billing${q}`;
  }

  async function call(action, body) {
    const url = body ? apiUrl() : apiUrl("action=" + encodeURIComponent(action));
    if (!url) return { ok: false, error: "no_server" };
    const opts = { credentials: "include", headers: { Accept: "application/json" } };
    if (body) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify({ action, origin: location.origin, ...body });
    }
    const r = await fetch(url, opts);
    try {
      return await r.json();
    } catch (_) {
      return { ok: false, error: "bad_json" };
    }
  }

  const state = { me: { ok: true, enabled: false, plan: "ev" }, ready: null };

  function can(need) {
    if (!state.me || state.me.enabled === false) return true;
    const have = state.me.plan;
    if (!need) return !!have;
    if (!have) return false;
    return (PLANS[have]?.rank || 0) >= (PLANS[need]?.rank || 0);
  }

  function injectStyles() {
    if (document.getElementById("pp-billing-css")) return;
    const s = document.createElement("style");
    s.id = "pp-billing-css";
    s.textContent = `
      .pp-acct { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
      .pp-acct-chip { font-family:var(--font-mono, ui-monospace, monospace); font-size:10px; font-weight:700; letter-spacing:.06em; padding:5px 10px; border-radius:999px; border:1px solid rgba(52,211,153,.4); background:rgba(52,211,153,.12); color:#34d399; }
      .pp-acct button, .pp-btn { font-family:var(--font-ui, Outfit, system-ui, sans-serif); font-size:12px; font-weight:600; padding:7px 12px; border-radius:10px; cursor:pointer; border:1px solid rgba(34,211,238,.45); background:rgba(34,211,238,.14); color:#22d3ee; }
      .pp-acct button.ghost { background:transparent; color:#8b9cb3; border-color:rgba(139,156,179,.28); }
      .pp-gate, .pp-modal-bg { position:fixed; inset:0; z-index:400; background:rgba(7,11,20,.82); backdrop-filter:blur(10px); display:flex; align-items:center; justify-content:center; padding:24px 16px; }
      .pp-panel { max-width:420px; width:100%; background:rgba(17,24,39,.96); border:1px solid rgba(56,189,248,.22); border-radius:16px; padding:28px 24px; box-shadow:0 12px 40px rgba(0,0,0,.45); }
      .pp-panel h3 { font-family:var(--font-display, "Bebas Neue", sans-serif); font-size:32px; letter-spacing:.08em; color:#22d3ee; margin-bottom:8px; }
      .pp-panel p { color:#8b9cb3; font-size:14px; line-height:1.55; margin-bottom:16px; }
      .pp-panel .pp-price { font-size:28px; font-weight:700; color:#e8edf5; margin-bottom:14px; }
      .pp-panel .pp-price span { font-size:14px; color:#8b9cb3; font-weight:500; }
      .pp-panel .pp-actions { display:flex; flex-wrap:wrap; gap:10px; }
      .pp-btn.primary { background:linear-gradient(145deg, rgba(34,211,238,.95), rgba(56,189,248,.88)); color:#061016; border:none; padding:10px 16px; }
      .pp-err { color:#fb7185; font-size:12px; margin-top:10px; }
      .tab-btn.pp-locked { position:relative; }
      .tab-btn.pp-locked::after { content:"🔒"; font-size:10px; margin-left:4px; }
    `;
    document.head.appendChild(s);
  }

  function ensureAccount() {
    injectStyles();
    let host = document.getElementById("header-right");
    if (!host) {
      const inner = document.getElementById("header-inner");
      if (!inner) return;
      host = document.createElement("div");
      host.id = "header-right";
      inner.appendChild(host);
    }
    let box = document.getElementById("pp-account");
    if (!box) {
      box = document.createElement("div");
      box.id = "pp-account";
      box.className = "pp-acct";
      host.appendChild(box);
    }
    const me = state.me;
    if (!me.enabled) {
      box.innerHTML = "";
      return;
    }
    if (me.plan) {
      const label = me.plan === "ev" ? "+EV" : "BASE";
      box.innerHTML = `<span class="pp-acct-chip">${label}</span>
        <button type="button" class="ghost" data-pp="portal">Manage</button>
        <button type="button" class="ghost" data-pp="logout">Log out</button>`;
    } else {
      box.innerHTML = `<button type="button" data-pp="home">Subscribe</button>`;
    }
    box.querySelectorAll("[data-pp]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const act = btn.getAttribute("data-pp");
        if (act === "home") { location.href = "/#pricing"; return; }
        if (act === "logout") { await call("logout", { dummy: 1 }); location.reload(); return; }
        if (act === "portal") {
          const j = await call("portal", {});
          if (j.url) location.href = j.url;
          else alert(j.error === "not_signed_in" ? "Subscribe first, then manage billing here." : (j.error || "Could not open billing portal."));
        }
      });
    });
  }

  function closeModal() {
    document.getElementById("pp-modal")?.remove();
  }

  function showModal({ title, body, price, cta, plan, extra }) {
    injectStyles();
    closeModal();
    const wrap = document.createElement("div");
    wrap.id = "pp-modal";
    wrap.className = "pp-modal-bg";
    wrap.innerHTML = `<div class="pp-panel">
      <h3>${title}</h3>
      <p>${body}</p>
      ${price ? `<div class="pp-price">${price}</div>` : ""}
      <div class="pp-actions">
        ${cta && plan ? `<button type="button" class="pp-btn primary" data-go="${plan}">${cta}</button>` : ""}
        ${extra || ""}
        <button type="button" class="pp-btn ghost" data-close>Not now</button>
      </div>
      <div class="pp-err" hidden></div>
    </div>`;
    document.body.appendChild(wrap);
    wrap.addEventListener("click", (e) => { if (e.target === wrap) closeModal(); });
    wrap.querySelector("[data-close]")?.addEventListener("click", closeModal);
    wrap.querySelector("[data-go]")?.addEventListener("click", async (e) => {
      const p = e.currentTarget.getAttribute("data-go");
      const err = wrap.querySelector(".pp-err");
      e.currentTarget.disabled = true;
      const j = await checkout(p);
      if (!j.ok) {
        e.currentTarget.disabled = false;
        err.hidden = false;
        err.textContent = j.error === "billing_not_configured"
          ? "Stripe is not connected yet. Add STRIPE_SECRET_KEY on the server."
          : (j.error || "Checkout failed.");
      }
    });
  }

  async function checkout(plan) {
    const j = await call("checkout", { plan });
    if (j.url) location.href = j.url;
    return j;
  }

  function promptUpgrade() {
    showModal({
      title: "+EV access",
      body: "The +EV scanner grades PrizePicks vs FanDuel and DraftKings. It’s on the $14.99 plan (includes everything in Base).",
      price: "$14.99 <span>/ month</span>",
      cta: "Unlock +EV",
      plan: "ev",
    });
  }

  function gatePage() {
    injectStyles();
    document.getElementById("pp-gate")?.remove();
    const g = document.createElement("div");
    g.id = "pp-gate";
    g.className = "pp-gate";
    g.innerHTML = `<div class="pp-panel">
      <h3>Subscribe to continue</h3>
      <p>Base unlocks MLB, NFL, and CS2. +EV adds the PrizePicks vs FanDuel / DraftKings scanner.</p>
      <div class="pp-price">$9.99 <span>/ month</span></div>
      <div class="pp-actions">
        <button type="button" class="pp-btn primary" data-go="base">Start Base</button>
        <button type="button" class="pp-btn" data-go="ev">+EV $14.99</button>
        <a class="pp-btn ghost" href="/#pricing">See plans</a>
      </div>
      <div class="pp-err" hidden></div>
    </div>`;
    document.body.appendChild(g);
    g.querySelectorAll("[data-go]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const err = g.querySelector(".pp-err");
        btn.disabled = true;
        const j = await checkout(btn.getAttribute("data-go"));
        if (!j.ok) {
          btn.disabled = false;
          err.hidden = false;
          err.textContent = j.error === "billing_not_configured"
            ? "Stripe is not connected yet. Add STRIPE_SECRET_KEY on the server."
            : (j.error || "Checkout failed.");
        }
      });
    });
  }

  function markEvLock() {
    document.querySelectorAll('[data-tab="ev"]').forEach((btn) => {
      if (can("ev")) btn.classList.remove("pp-locked");
      else btn.classList.add("pp-locked");
    });
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.('[data-tab="ev"]');
    if (!btn) return;
    if (can("ev")) return;
    e.preventDefault();
    e.stopPropagation();
    promptUpgrade();
  }, true);

  async function refreshMe() {
    if (typeof location !== "undefined" && location.protocol === "file:") {
      state.me = { ok: true, enabled: false, plan: "ev" };
      ensureAccount();
      return state.me;
    }
    const qs = new URLSearchParams(location.search);
    if (qs.get("billing") === "success" && qs.get("session_id")) {
      const url = apiUrl("action=complete&session_id=" + encodeURIComponent(qs.get("session_id")));
      if (url) {
        try {
          const r = await fetch(url, { credentials: "include", headers: { Accept: "application/json" } });
          const j = await r.json();
          if (j && j.ok) state.me = j;
        } catch (_) {}
      }
      qs.delete("billing");
      qs.delete("session_id");
      const next = location.pathname + (qs.toString() ? "?" + qs.toString() : "") + (location.hash || "");
      history.replaceState({}, "", next);
    } else {
      const j = await call("me");
      if (j && j.ok) state.me = j;
    }
    ensureAccount();
    markEvLock();
    const need = document.body.getAttribute("data-billing") || "";
    if (need && need !== "public" && !can(need)) gatePage();
    return state.me;
  }

  state.ready = refreshMe();
  window.PropPulseBilling = {
    ready: state.ready,
    can,
    checkout,
    promptUpgrade,
    gatePage,
    call,
    get me() { return state.me; },
  };
})();
