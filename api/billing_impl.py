"""
Stripe subscriptions — Base $9.99 (site) and +EV $14.99 (includes +EV tabs).
Uses Stripe REST (no SDK). Keys only from env: STRIPE_SECRET_KEY, optional
STRIPE_PRICE_BASE / STRIPE_PRICE_EV, optional BILLING_SECRET.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

COOKIE = "pp_sub"
COOKIE_MAX_AGE = 30 * 24 * 3600
PLANS = {
    "base": {
        "id": "base",
        "name": "PropPulse Base",
        "amount": 999,
        "label": "Base",
    },
    "ev": {
        "id": "ev",
        "name": "PropPulse +EV",
        "amount": 1499,
        "label": "+EV",
    },
}
LIVE_STATUSES = {"active", "trialing"}
TRIAL_DAYS = 3
COMP_CUSTOMER = "comp"


def stripe_configured() -> bool:
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    return bool(key) and key.startswith("sk_")


def _secret() -> str:
    return (
        (os.environ.get("BILLING_SECRET") or "").strip()
        or (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
        or "dev-only-not-for-production"
    )


def _stripe_key() -> str:
    return (os.environ.get("STRIPE_SECRET_KEY") or "").strip()


def _price_id(plan: str) -> str:
    if plan == "ev":
        return (os.environ.get("STRIPE_PRICE_EV") or os.environ.get("STRIPE_PRICE_PRO") or "").strip()
    return (os.environ.get("STRIPE_PRICE_BASE") or "").strip()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_session(email: str, plan: str, customer_id: str, exp: Optional[int] = None) -> str:
    payload = {
        "e": (email or "").strip().lower(),
        "p": plan if plan in PLANS else "base",
        "c": customer_id or "",
        "x": int(exp or (time.time() + COOKIE_MAX_AGE)),
    }
    raw = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_secret().encode("utf-8"), raw.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def read_session(token: str) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    expect = hmac.new(_secret().encode("utf-8"), raw.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(_unb64(raw).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("x") or 0) < time.time() - 60:
        return None
    plan = payload.get("p") if payload.get("p") in PLANS else None
    if not plan:
        return None
    return {
        "email": payload.get("e") or "",
        "plan": plan,
        "customerId": payload.get("c") or "",
        "exp": int(payload.get("x") or 0),
    }


def cookie_header(token: str, clear: bool = False) -> str:
    if clear:
        return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    parts = [
        f"{COOKIE}={token}",
        "Path=/",
        f"Max-Age={COOKIE_MAX_AGE}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if os.environ.get("VERCEL"):
        parts.append("Secure")
    return "; ".join(parts)


def parse_cookie(header: str) -> str:
    if not header:
        return ""
    for part in header.split(";"):
        part = part.strip()
        if part.startswith(COOKIE + "="):
            return part.split("=", 1)[1].strip()
    return ""


def _flatten(obj: Any, prefix: str = "") -> list:
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}[{k}]" if prefix else str(k)
            items.extend(_flatten(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            items.extend(_flatten(v, f"{prefix}[{i}]"))
    elif obj is True:
        items.append((prefix, "true"))
    elif obj is False:
        items.append((prefix, "false"))
    elif obj is not None:
        items.append((prefix, str(obj)))
    return items


def _stripe(path: str, method: str = "GET", data: Optional[dict] = None) -> Tuple[Optional[dict], Optional[str]]:
    key = _stripe_key()
    if not key:
        return None, "billing_not_configured"
    url = "https://api.stripe.com/v1/" + path.lstrip("/")
    body = urllib.parse.urlencode(_flatten(data or {})).encode("utf-8") if method != "GET" and data is not None else None
    if method == "GET" and data:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(_flatten(data), doseq=True)
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "PropPulse-Billing/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=22) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            msg = (err.get("error") or {}).get("message") or str(e)
        except Exception:
            msg = str(e)
        return None, _friendly_stripe(e.code, msg)
    except Exception as e:
        return None, str(e)[:240]


def _friendly_stripe(code: int, msg: str) -> str:
    text = (msg or "").strip()
    low = text.lower()
    if code == 401:
        return "stripe_bad_key: That Stripe secret key was rejected. Use the Secret key (sk_test_ or sk_live_), not the publishable pk_ key."
    if "no such customer" in low:
        return "stripe_no_customer"
    if any(s in low for s in ("activate", "account is not connected", "charges are disabled", "responsibilities")):
        return f"stripe_account: {text}"[:280]
    if "billing portal" in low and "configuration" in low:
        return "stripe_portal_config"
    return f"stripe_{code}: {text}"[:280]


def _plan_from_sub(sub: Optional[dict]) -> Optional[str]:
    if not isinstance(sub, dict):
        return None
    meta = sub.get("metadata") if isinstance(sub.get("metadata"), dict) else {}
    plan = (meta.get("plan") or "").strip()
    if plan in PLANS:
        return plan
    items = ((sub.get("items") or {}).get("data")) if isinstance(sub.get("items"), dict) else []
    amounts = []
    for it in items or []:
        price = it.get("price") if isinstance(it, dict) else None
        if isinstance(price, dict) and price.get("unit_amount") is not None:
            amounts.append(int(price["unit_amount"]))
    if any(a >= 1499 for a in amounts):
        return "ev"
    if any(a >= 999 for a in amounts):
        return "base"
    return None


def _active_plan_for_customer(customer_id: str) -> Tuple[Optional[str], Optional[dict]]:
    data, err = _stripe(
        "subscriptions",
        "GET",
        {"customer": customer_id, "status": "all", "limit": 10},
    )
    if err or not isinstance(data, dict):
        return "keep", None
    best = None
    best_plan = None
    for sub in data.get("data") or []:
        if not isinstance(sub, dict):
            continue
        if (sub.get("status") or "") not in LIVE_STATUSES:
            continue
        plan = _plan_from_sub(sub)
        if not plan:
            continue
        if plan == "ev":
            return "ev", sub
        best = sub
        best_plan = plan
    return best_plan, best


def me_from_token(token: str) -> Tuple[Dict[str, Any], Optional[str]]:
    if not stripe_configured():
        return {"ok": True, "enabled": False, "plan": "ev", "email": "", "customerId": ""}, None
    sess = read_session(token)
    if not sess:
        return {"ok": True, "enabled": True, "plan": None, "email": "", "customerId": ""}, None
    plan = sess["plan"]
    tok_out: Optional[str] = token
    if (sess.get("customerId") or "") == COMP_CUSTOMER:
        return (
            {
                "ok": True,
                "enabled": True,
                "plan": plan,
                "email": sess.get("email") or "",
                "customerId": COMP_CUSTOMER,
            },
            tok_out,
        )
    if sess.get("customerId"):
        live, _ = _active_plan_for_customer(sess["customerId"])
        if live == "keep":
            plan = sess["plan"]
        elif live in PLANS:
            plan = live
            tok_out = sign_session(sess.get("email") or "", plan, sess.get("customerId") or "")
        else:
            return (
                {"ok": True, "enabled": True, "plan": None, "email": sess.get("email") or "", "customerId": ""},
                "",
            )
    return (
        {
            "ok": True,
            "enabled": True,
            "plan": plan,
            "email": sess.get("email") or "",
            "customerId": sess.get("customerId") or "",
        },
        tok_out,
    )


def create_checkout(plan: str, origin: str, customer_id: str = "", email: str = "") -> Dict[str, Any]:
    if plan not in PLANS:
        return {"ok": False, "error": "bad_plan"}
    if not stripe_configured():
        return {"ok": False, "error": "billing_not_configured"}
    spec = PLANS[plan]
    origin = (origin or "").rstrip("/")
    email = (email or "").strip().lower()
    if customer_id == COMP_CUSTOMER:
        customer_id = ""
    success = f"{origin}/?billing=success&session_id={{CHECKOUT_SESSION_ID}}"
    cancel = f"{origin}/?billing=cancel"
    payload: Dict[str, Any] = {
        "mode": "subscription",
        "success_url": success,
        "cancel_url": cancel,
        "allow_promotion_codes": True,
        "billing_address_collection": "auto",
        "payment_method_collection": "always",
        "client_reference_id": plan,
        "metadata": {"plan": plan},
        "subscription_data": {
            "metadata": {"plan": plan},
            "trial_period_days": TRIAL_DAYS,
        },
        "managed_payments": {"enabled": False},
        "line_items": [{"quantity": 1}],
    }
    price = _price_id(plan)
    if price:
        payload["line_items"][0]["price"] = price
    else:
        payload["line_items"][0]["price_data"] = {
            "currency": "usd",
            "unit_amount": spec["amount"],
            "recurring": {"interval": "month"},
            "product_data": {
                "name": spec["name"],
                "description": "Monthly access to PropPulse player-prop tools"
                + (" including the +EV scanner." if plan == "ev" else "."),
                "tax_code": "txcd_10103000",
            },
        }
    if customer_id:
        payload["customer"] = customer_id
    elif email:
        payload["customer_email"] = email
    sess, err = _stripe("checkout/sessions", "POST", payload)
    if err and customer_id and (err == "stripe_no_customer" or "no such customer" in (err or "").lower()):
        payload.pop("customer", None)
        if email:
            payload["customer_email"] = email
        sess, err = _stripe("checkout/sessions", "POST", payload)
    if err:
        return {"ok": False, "error": err}
    url = (sess or {}).get("url")
    if not url:
        return {"ok": False, "error": "no_checkout_url"}
    return {"ok": True, "url": url}


def complete_checkout(session_id: str) -> Tuple[Dict[str, Any], Optional[str]]:
    if not session_id or not session_id.startswith("cs_"):
        return {"ok": False, "error": "bad_session"}, None
    sess, err = _stripe(f"checkout/sessions/{session_id}", "GET", {"expand[0]": "subscription"})
    if err:
        return {"ok": False, "error": err}, None
    if not isinstance(sess, dict):
        return {"ok": False, "error": "bad_session"}, None
    if (sess.get("status") or "") not in ("complete", "paid") and (sess.get("payment_status") or "") not in (
        "paid",
        "no_payment_required",
    ):
        # Stripe marks completed checkouts as status=complete
        if (sess.get("status") or "") != "complete":
            return {"ok": False, "error": "checkout_incomplete"}, None
    customer_id = sess.get("customer") if isinstance(sess.get("customer"), str) else ""
    if isinstance(sess.get("customer"), dict):
        customer_id = sess["customer"].get("id") or ""
    email = (
        ((sess.get("customer_details") or {}) if isinstance(sess.get("customer_details"), dict) else {}).get("email")
        or sess.get("customer_email")
        or ""
    )
    sub = sess.get("subscription")
    if isinstance(sub, str) and sub:
        sub_obj, _ = _stripe(f"subscriptions/{sub}", "GET")
        sub = sub_obj
    plan = (sess.get("metadata") or {}).get("plan") if isinstance(sess.get("metadata"), dict) else None
    if plan not in PLANS:
        plan = _plan_from_sub(sub if isinstance(sub, dict) else None)
    if plan not in PLANS:
        plan = "base"
    if customer_id and plan == "ev":
        _cancel_other_plans(customer_id, keep="ev")
    token = sign_session(email, plan, customer_id)
    return (
        {
            "ok": True,
            "plan": plan,
            "email": email,
            "customerId": customer_id,
            "enabled": True,
        },
        token,
    )


def _cancel_other_plans(customer_id: str, keep: str) -> None:
    data, err = _stripe("subscriptions", "GET", {"customer": customer_id, "status": "all", "limit": 10})
    if err or not isinstance(data, dict):
        return
    for sub in data.get("data") or []:
        if not isinstance(sub, dict):
            continue
        if (sub.get("status") or "") not in LIVE_STATUSES:
            continue
        sid = sub.get("id")
        plan = _plan_from_sub(sub)
        if sid and plan and plan != keep:
            _stripe(f"subscriptions/{sid}", "POST", {"cancel_at_period_end": True})


def restore_email(email: str) -> Tuple[Dict[str, Any], Optional[str]]:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "error": "bad_email"}, None
    if not stripe_configured():
        return {"ok": False, "error": "billing_not_configured"}, None
    data, err = _stripe("customers", "GET", {"email": email, "limit": 5})
    if err:
        return {"ok": False, "error": err}, None
    best_plan = None
    best_cid = ""
    for cust in (data or {}).get("data") or []:
        if not isinstance(cust, dict) or not cust.get("id"):
            continue
        plan, _ = _active_plan_for_customer(cust["id"])
        if plan == "ev":
            best_plan, best_cid = "ev", cust["id"]
            break
        if plan == "base" and best_plan is None:
            best_plan, best_cid = "base", cust["id"]
    if not best_plan:
        return {"ok": False, "error": "no_active_subscription"}, None
    token = sign_session(email, best_plan, best_cid)
    return (
        {"ok": True, "plan": best_plan, "email": email, "customerId": best_cid, "enabled": True},
        token,
    )


def _portal_configuration_id() -> str:
    env_id = (os.environ.get("STRIPE_PORTAL_CONFIGURATION") or "").strip()
    if env_id:
        return env_id
    listed, err = _stripe("billing_portal/configurations", "GET", {"limit": 5, "active": True})
    if not err and isinstance(listed, dict):
        for cfg in listed.get("data") or []:
            if isinstance(cfg, dict) and cfg.get("id") and cfg.get("active") is not False:
                return str(cfg["id"])
    created, _ = _stripe(
        "billing_portal/configurations",
        "POST",
        {
            "business_profile": {"headline": "PropPulse billing"},
            "features": {
                "customer_update": {
                    "enabled": True,
                    "allowed_updates": ["email", "address", "name"],
                },
                "invoice_history": {"enabled": True},
                "payment_method_update": {"enabled": True},
                "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
            },
        },
    )
    if isinstance(created, dict) and created.get("id"):
        return str(created["id"])
    return ""


def redeem_promo(code: str, email: str = "") -> Tuple[Dict[str, Any], Optional[str]]:
    want = (os.environ.get("OWNER_PROMO_CODE") or "").strip()
    got = (code or "").strip()
    if not want or not got:
        return {"ok": False, "error": "invalid_code"}, None
    a = want.upper().encode("utf-8")
    b = got.upper().encode("utf-8")
    if len(a) != len(b) or not hmac.compare_digest(a, b):
        return {"ok": False, "error": "invalid_code"}, None
    email = (email or "").strip().lower() or "owner"
    token = sign_session(email, "ev", COMP_CUSTOMER)
    return (
        {"ok": True, "plan": "ev", "email": email, "customerId": COMP_CUSTOMER, "enabled": True},
        token,
    )


def create_portal(customer_id: str, origin: str) -> Dict[str, Any]:
    if not customer_id or customer_id == COMP_CUSTOMER:
        return {"ok": False, "error": "not_signed_in" if not customer_id else "complimentary"}
    origin = (origin or "").rstrip("/")
    payload: Dict[str, Any] = {"customer": customer_id, "return_url": origin + "/"}
    cfg = _portal_configuration_id()
    if cfg:
        payload["configuration"] = cfg
    sess, err = _stripe("billing_portal/sessions", "POST", payload)
    if err:
        return {"ok": False, "error": err}
    url = (sess or {}).get("url")
    if not url:
        return {"ok": False, "error": "no_portal_url"}
    return {"ok": True, "url": url}


def public_config() -> Dict[str, Any]:
    key = _stripe_key()
    return {
        "ok": True,
        "enabled": stripe_configured(),
        "provider": "stripe" if stripe_configured() else "none",
        "testMode": key.startswith("sk_test_"),
        "trialDays": TRIAL_DAYS,
        "plans": {
            "base": {"id": "base", "name": "Base", "price": 9.99, "cents": 999},
            "ev": {"id": "ev", "name": "+EV", "price": 14.99, "cents": 1499},
        },
    }
