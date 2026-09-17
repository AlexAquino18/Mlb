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
      .pp-acct-chip { font-family:var(--font-mono, ui-monospace, monospace); font-size:11px; font-weight:700; letter-spacing:.06em; padding:6px 12px; border-radius:999px; border:1px solid rgba(52,211,153,.4); background:rgba(52,211,153,.12); color:#34d399; }
      .pp-acct button, .pp-btn { font-family:var(--font-ui, Outfit, system-ui, sans-serif); font-size:14px; font-weight:600; padding:9px 14px; border-radius:10px; cursor:pointer; border:1px solid rgba(34,211,238,.45); background:rgba(34,211,238,.14); color:#22d3ee; }
      .pp-acct button.ghost { background:transparent; color:#8b9cb3; border-color:rgba(139,156,179,.28); }
      .pp-gate, .pp-modal-bg { position:fixed; inset:0; z-index:400; background:rgba(7,11,20,.82); backdrop-filter:blur(10px); display:flex; align-items:center; justify-content:center; padding:24px 16px; }
      .pp-panel { max-width:480px; width:100%; background:rgba(17,24,39,.97); border:1px solid rgba(56,189,248,.22); border-radius:18px; padding:28px 26px; box-shadow:0 12px 40px rgba(0,0,0,.45); }
      .pp-panel h3 { font-family:var(--font-display, "Bebas Neue", sans-serif); font-size:36px; letter-spacing:.08em; color:#22d3ee; margin-bottom:8px; }
      .pp-panel p { color:#9fb0c6; font-size:16px; line-height:1.55; margin-bottom:16px; }
      .pp-panel .pp-price { font-size:28px; font-weight:700; color:#e8edf5; margin-bottom:14px; }
      .pp-panel .pp-price span { font-size:15px; color:#8b9cb3; font-weight:500; }
      .pp-panel .pp-actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:6px; }
      .pp-btn.primary { background:linear-gradient(145deg, rgba(34,211,238,.95), rgba(56,189,248,.88)); color:#061016; border:none; padding:11px 16px; }
      .pp-err { color:#fb7185; font-size:13px; margin-top:10px; }
      .pp-ok { color:#34d399; font-size:13px; margin-top:10px; }
      .pp-field { width:100%; margin:0 0 10px; background:rgba(7,11,20,.85); border:1px solid rgba(56,189,248,.28); border-radius:10px; padding:11px 12px; color:#e8edf5; font-size:15px; font-family:inherit; }
      .pp-field::placeholder { color:#64748b; }
      .pp-trial { display:inline-block; font-family:var(--font-mono, ui-monospace, monospace); font-size:11px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; color:#34d399; background:rgba(52,211,153,.12); border:1px solid rgba(52,211,153,.32); border-radius:999px; padding:5px 11px; margin-bottom:10px; }
      .pp-plans { display:grid; gap:10px; margin:0 0 14px; }
      .pp-plan { background:rgba(7,11,20,.55); border:1px solid rgba(56,189,248,.16); border-radius:12px; padding:12px 14px; }
      .pp-plan.featured { border-color:rgba(52,211,153,.4); }
      .pp-plan strong { display:block; font-size:16px; color:#e8edf5; margin-bottom:4px; }
      .pp-plan span { color:#22d3ee; font-weight:700; }
      .pp-plan p { margin:0; font-size:14px; color:#8b9cb3; }
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
    const login = document.querySelector(".header-login");
    const me = state.me;
    if (login) login.hidden = !!(me && me.enabled && me.plan);
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
        if (act === "home") { promptSubscribe(); return; }
        if (act === "logout") { await call("logout", { dummy: 1 }); location.reload(); return; }
        if (act === "portal") {
          const j = await call("portal", {});
          if (j.url) location.href = j.url;
          else alert(j.error === "complimentary" ? "This account is complimentary — no billing portal." : (j.error === "not_signed_in" ? "Subscribe first, then manage billing here." : (j.error || "Could not open billing portal.")));
        }
      });
    });
  }

  function closeModal() {
    document.getElementById("pp-modal")?.remove();
  }

  function showModal({ title, body, price, extra, actions }) {
    injectStyles();
    closeModal();
    const wrap = document.createElement("div");
    wrap.id = "pp-modal";
    wrap.className = "pp-modal-bg";
    wrap.innerHTML = `<div class="pp-panel">
      <span class="pp-trial">3-day free trial</span>
      <h3>${title}</h3>
      <p>${body}</p>
      ${price ? `<div class="pp-price">${price}</div>` : ""}
      ${extra || ""}
      <div class="pp-actions">
        ${actions || ""}
        <button type="button" class="pp-btn ghost" data-close>Not now</button>
      </div>
      <div class="pp-err" hidden></div>
    </div>`;
    document.body.appendChild(wrap);
    wrap.addEventListener("click", (e) => { if (e.target === wrap) closeModal(); });
    wrap.querySelector("[data-close]")?.addEventListener("click", () => {
      try { localStorage.setItem("pp_sub_popup", "1"); } catch (_) {}
      closeModal();
    });
    wrap.querySelectorAll("[data-go]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const err = wrap.querySelector(".pp-err");
        e.currentTarget.disabled = true;
        const j = await checkout(e.currentTarget.getAttribute("data-go"));
        if (!j.ok) {
          e.currentTarget.disabled = false;
          err.hidden = false;
          err.textContent = checkoutError(j);
        }
      });
    });
  }

  function checkoutEmail() {
    const modal = document.getElementById("pp-email");
    const el = modal || document.getElementById("checkout-email") || document.getElementById("restore-email");
    return (el && el.value ? el.value : "").trim();
  }

  function promoCode() {
    const el = document.getElementById("pp-promo");
    return (el && el.value ? el.value : "").trim();
  }

  function checkoutError(j) {
    const err = String((j && j.error) || "");
    if (err === "billing_not_configured") {
      return "Stripe is not connected yet.";
    }
    if (err.startsWith("stripe_bad_key")) {
      return "That Stripe key was rejected. Paste the Secret key (starts with sk_test_ or sk_live_), not the pk_ publishable key.";
    }
    if (err.startsWith("stripe_account")) {
      return "Stripe Checkout is blocked until the account is activated.";
    }
    if (err === "invalid_code") return "That promo code is not valid.";
    return err || "Checkout failed.";
  }

  async function checkout(plan) {
    const code = promoCode();
    if (code) {
      const redeemed = await call("redeem", { code, email: checkoutEmail() });
      if (redeemed && redeemed.ok && redeemed.plan) {
        location.href = "/mlb/";
        return redeemed;
      }
      if (code && redeemed && redeemed.error === "invalid_code") {
        return redeemed;
      }
    }
    const j = await call("checkout", { plan, email: checkoutEmail() });
    if (j.url) location.href = j.url;
    return j;
  }

  function fieldsHtml() {
    return `<div class="pp-plans">
        <div class="pp-plan">
          <strong>Base <span>$9.99/mo</span></strong>
          <p>MLB, NFL, CS2, and FanDuel / DraftKings lines.</p>
        </div>
        <div class="pp-plan featured">
          <strong>+EV <span>$14.99/mo</span></strong>
          <p>Everything in Base, plus PrizePicks vs FanDuel / DraftKings +EV.</p>
        </div>
      </div>
      <input class="pp-field" id="pp-email" type="email" placeholder="Email" autocomplete="email" />
      <input class="pp-field" id="pp-promo" type="text" placeholder="Promo code (optional)" autocomplete="off" />`;
  }

  function promptSubscribe() {
    showModal({
      title: "Start free",
      body: "3 days free on both plans. Card on file — cancel before day 4 and you won’t be charged.",
      extra: fieldsHtml(),
      actions: `<button type="button" class="pp-btn primary" data-go="base">Start Base</button>
        <button type="button" class="pp-btn" data-go="ev">Start +EV</button>`,
    });
  }

  function promptUpgrade() {
    showModal({
      title: "+EV access",
      body: "The +EV scanner grades PrizePicks vs FanDuel and DraftKings. 3-day free trial, then $14.99/month (includes Base).",
      price: "$14.99 <span>/ month after trial</span>",
      extra: fieldsHtml(),
      actions: `<button type="button" class="pp-btn primary" data-go="ev">Start +EV trial</button>`,
    });
  }

  function gatePage() {
    injectStyles();
    document.getElementById("pp-gate")?.remove();
    const g = document.createElement("div");
    g.id = "pp-gate";
    g.className = "pp-gate";
    g.innerHTML = `<div class="pp-panel">
      <span class="pp-trial">3-day free trial</span>
      <h3>Subscribe to continue</h3>
      <p>Base unlocks MLB, NFL, and CS2. +EV adds the PrizePicks vs FanDuel / DraftKings scanner. 3 days free, then monthly.</p>
      ${fieldsHtml()}
      <div class="pp-actions">
        <button type="button" class="pp-btn primary" data-go="base">Start Base trial</button>
        <button type="button" class="pp-btn" data-go="ev">+EV $14.99</button>
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
          err.textContent = checkoutError(j);
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
    maybeOfferSubscribe();
    return state.me;
  }

  function maybeOfferSubscribe() {
    const need = document.body.getAttribute("data-billing") || "";
    if (need !== "public") return;
    if (!state.me || state.me.enabled === false || state.me.plan) return;
    if (new URLSearchParams(location.search).get("billing")) return;
    try { if (localStorage.getItem("pp_sub_popup") === "1") return; } catch (_) {}
    setTimeout(() => {
      if (!document.getElementById("pp-modal")) promptSubscribe();
    }, 700);
  }

  state.ready = refreshMe();
  window.PropPulseBilling = {
    ready: state.ready,
    can,
    checkout,
    promptSubscribe,
    promptUpgrade,
    gatePage,
    call,
    get me() { return state.me; },
  };
})();
