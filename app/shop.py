# -*- coding: utf-8 -*-
"""号码商城：用户账号 / 充值码 / 商品定价 / 直链卡发货 / 后台管理。

下单流程（一次请求内完成）：
  1) BEGIN IMMEDIATE 扣余额 + 落 pending 订单 + 记流水（幂等键去重）
  2) 按商品 appId 向上游采购 1 个号码
  3) 生成 卡号:手机号----https://api.<域名>/smsrecord?token=... 直链卡
  4) 订单置 done 并返回链接；任一步失败立即冲正退款

价格单位统一为「分」(price_cents)；USD 单价只作为后台参考展示。
"""
from __future__ import annotations
import hashlib, re, secrets, threading, time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import config, db, providers, relay_api, security

router = APIRouter()        # 用户侧：/shop/api/*
admin_router = APIRouter()  # 后台：/admin/api/shop/*

COOKIE = config.SHOP_COOKIE
ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
USER_RE = re.compile(r"^[A-Za-z0-9_]{4,20}$")

REGION_COUNTRY = [
    ("美国", "us"), ("英国", "gb"), ("香港", "hk"), ("加拿大", "ca"), ("印尼", "id"),
    ("马来", "my"), ("菲律宾", "ph"), ("越南", "vn"), ("泰国", "th"), ("日本", "jp"),
    ("韩国", "kr"), ("德国", "de"), ("法国", "fr"), ("巴西", "br"), ("俄罗斯", "ru"),
    ("印度", "in"), ("土耳其", "tr"), ("墨西哥", "mx"), ("西班牙", "es"), ("意大利", "it"),
]
FLAGS = {"us": "🇺🇸", "gb": "🇬🇧", "hk": "🇭🇰", "ca": "🇨🇦", "id": "🇮🇩", "my": "🇲🇾",
         "ph": "🇵🇭", "vn": "🇻🇳", "th": "🇹🇭", "jp": "🇯🇵", "kr": "🇰🇷", "de": "🇩🇪",
         "fr": "🇫🇷", "br": "🇧🇷", "ru": "🇷🇺", "in": "🇮🇳", "tr": "🇹🇷", "mx": "🇲🇽",
         "es": "🇪🇸", "it": "🇮🇹"}
TYPE_NAMES = {1: "首次卡", 2: "重启卡", 3: "续费卡"}

ICON_RULES = [
    ("whatsapp", "💬"), ("telegram", "✈️"), ("tiktok", "🎵"), ("douyin", "🎵"),
    ("google", "🔍"), ("apple", "🍎"), ("amazon", "📦"), ("facebook", "📘"),
    ("instagram", "📸"), ("twitter", "🐦"), ("paypal", "💳"), ("cash app", "💵"),
    ("tinder", "🔥"), ("bilibili", "📺"), ("shein", "🛍️"), ("bumble", "🐝"),
    ("chime", "🏦"), ("curtsy", "👗"), ("ashley", "💔"), ("豆包", "🫘"),
    ("阿里", "🛒"), ("alipay", "💰"), ("wechat", "💚"), ("微信", "💚"), ("qq", "🐧"),
    ("line", "🟢"), ("discord", "🎮"), ("netflix", "🎬"), ("uber", "🚗"),
    ("airbnb", "🏠"), ("binance", "🪙"), ("coinbase", "🪙"), ("trust", "🛡️"),
    ("steam", "🎮"), ("microsoft", "🪟"), ("yahoo", "📧"), ("gmail", "📧"),
    ("twitch", "🎥"), ("snapchat", "👻"), ("linkedin", "💼"), ("reddit", "👽"),
    ("shopee", "🛍️"), ("lazada", "🛍️"), ("grab", "🚕"), ("gojek", "🛵"),
    ("zoom", "📹"), ("signal", "🔐"), ("viber", "📞"), ("zalo", "💬"),
]


def ok(**kw):
    kw.setdefault("ok", True)
    return JSONResponse(kw)


def err(msg: str, code: int = 400, **kw):
    kw.update({"ok": False, "error": msg})
    return JSONResponse(kw, status_code=code)


class ShopError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


# ------------------------------------------------------------------ 小工具
def money(cents) -> str:
    try:
        return "%.2f" % (int(cents) / 100.0)
    except Exception:
        return "0.00"


def yuan_to_cents(raw, allow_neg: bool = False) -> int:
    try:
        v = float(str(raw).strip().replace("￥", "").replace("¥", ""))
    except Exception:
        raise ShopError("价格格式不正确")
    if v < 0 and not allow_neg:
        raise ShopError("价格不能为负数")
    if abs(v) > 100000:
        raise ShopError("金额超出范围")
    return int(round(v * 100))


def icon_for(name: str) -> str:
    low = (name or "").lower()
    for key, ico in ICON_RULES:
        if key in low:
            return ico
    return "🔑"


def country_of(cate_name: str, name: str = "") -> str:
    s = (cate_name or "").strip() or (name or "")
    for word, code in REGION_COUNTRY:
        if s.startswith(word) or word in s:
            return code
    m = re.search(r"\+(\d{1,3})", s)
    if m:
        return {"1": "us", "44": "gb", "852": "hk", "86": "cn"}.get(m.group(1), "")
    return ""


def type_name(ptype) -> str:
    try:
        return TYPE_NAMES.get(int(ptype), "")
    except Exception:
        return ""
# ------------------------------------------------------------------ 用户会话
def _cookie(resp: JSONResponse, request: Request, sid: str) -> None:
    resp.set_cookie(COOKIE, sid, max_age=config.SHOP_SESSION_TTL, httponly=True,
                    samesite="lax", secure=security.is_https(request), path="/")


def _new_sid() -> str:
    return secrets.token_urlsafe(32)


def create_session(user_id: int, ip: str, ua: str) -> tuple[str, str]:
    sid, csrf = _new_sid(), secrets.token_urlsafe(24)
    now = int(time.time())
    db.conn().execute(
        "INSERT INTO shop_sessions(sid,user_id,csrf,ip,ua,created_at,expires_at,last_seen) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (sid, user_id, csrf, ip, ua, now, now + config.SHOP_SESSION_TTL, now))
    return sid, csrf


def get_user(request: Request) -> Optional[dict]:
    sid = request.cookies.get(COOKIE) or ""
    if not sid:
        return None
    now = int(time.time())
    r = db.q1(
        "SELECT s.sid,s.csrf,s.expires_at,s.user_id,u.username,u.balance_cents,u.status,"
        "u.last_login_at FROM shop_sessions s JOIN shop_users u ON u.id=s.user_id "
        "WHERE s.sid=? AND s.expires_at>?", (sid, now))
    if not r:
        return None
    if r["status"] != "active":
        return None
    if security._seen_due("shop:" + sid, 300):
        db.conn().execute("UPDATE shop_sessions SET last_seen=? WHERE sid=?", (now, sid))
    return dict(r)


def require_user(request: Request) -> dict:
    u = get_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="请先登录")
    return u


def _csrf(request: Request, sess: dict, body: dict) -> None:
    security.require_csrf(request, request.headers.get("x-csrf", "") or body.get("csrf", ""),
                          sess["csrf"])


async def _body(request: Request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _ua(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:200]


def _ip(request: Request) -> str:
    return security.client_ip(request)


def user_public(u) -> dict:
    uid = u["user_id"] if "user_id" in u.keys() else u["id"]
    return {"id": uid, "username": u["username"],
            "balance_cents": int(u["balance_cents"]),
            "balance": money(u["balance_cents"])}
# ------------------------------------------------------------------ 商品
def _product_row(p) -> dict:
    flag = FLAGS.get(p["country"] or "", "🌐")
    return {
        "id": p["id"], "name": p["name"], "icon": p["icon"] or icon_for(p["name"]),
        "cate_name": p["cate_name"] or "", "cate_id": p["cate_id"],
        "country": p["country"] or "", "flag": flag,
        "card_type": p["card_type"] or type_name(p["ptype"]),
        "success": round(float(p["success"] or 0), 2),
        "usd": round(float(p["upstream_price_usd"] or 0), 3),
        "stock": int(p["stock"] or 0),
        "price_cents": int(p["price_cents"] or 0), "price": money(p["price_cents"]),
        "enabled": int(p["enabled"] or 0), "note": p["note"] or "",
    }


def visible_products(region: str = "", ctype: str = "", q: str = "") -> list:
    sql = "SELECT * FROM shop_products WHERE enabled=1"
    args = []
    if region:
        sql += " AND (country=? OR cate_name=?)"
        args += [region, region]
    if ctype:
        sql += " AND (card_type=?)"
        args += [ctype]
    if q:
        sql += " AND (name LIKE ? OR cate_name LIKE ?)"
        args += ["%" + q + "%", "%" + q + "%"]
    sql += " ORDER BY success DESC, price_cents ASC, id ASC LIMIT 500"
    return [_product_row(r) for r in db.q(sql, tuple(args))]


def catalog_meta() -> dict:
    rows = db.q("SELECT DISTINCT cate_name, country FROM shop_products WHERE enabled=1")
    regions = []
    seen = set()
    for r in rows:
        key = r["cate_name"] or r["country"]
        if not key or key in seen:
            continue
        seen.add(key)
        regions.append({"key": key, "label": key, "flag": FLAGS.get(r["country"] or "", "🌐")})
    types = []
    for r in db.q("SELECT DISTINCT card_type FROM shop_products WHERE enabled=1"):
        if r["card_type"] and r["card_type"] not in types:
            types.append(r["card_type"])
    return {"regions": regions, "types": types}


# ------------------------------------------------------------------ 用户 API
@router.get("/shop/api/me")
def api_me(request: Request):
    u = get_user(request)
    if not u:
        return ok(user=None, csrf="")
    orders = db.q("SELECT * FROM shop_orders WHERE user_id=? ORDER BY id DESC LIMIT 30",
                  (u["user_id"],))
    return ok(user=user_public(u), csrf=u["csrf"],
              orders=[order_public(o) for o in orders])


@router.get("/shop/api/products")
def api_products(request: Request, region: str = "", type: str = "", q: str = ""):
    if db.get_setting("shop_on", "1") != "1":
        return err("商城维护中，请稍后再来", 503)
    u = get_user(request)
    return ok(user=user_public(u) if u else None,
              products=visible_products(region.strip(), type.strip(), q.strip()[:40]),
              **catalog_meta())


@router.post("/shop/api/register")
async def api_register(request: Request):
    if db.get_setting("shop_register_on", "1") != "1":
        return err("当前未开放注册", 403)
    ip = _ip(request)
    allowed, retry = security.hit_shared("shopreg:%s" % ip, 5, 3600)
    if not allowed:
        return err("注册过于频繁，请 %d 分钟后重试" % max(1, retry // 60), 429)
    body = await _body(request)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not USER_RE.match(username):
        return err("账号需 4-20 位字母、数字或下划线")
    if len(password) < 8 or len(password) > 64:
        return err("密码至少 8 位")
    if password.isdigit():
        return err("密码不能是纯数字")
    now = int(time.time())
    try:
        with db.tx() as c:
            if c.execute("SELECT 1 FROM shop_users WHERE username=?", (username,)).fetchone():
                raise ShopError("该账号已被注册")
            cur = c.execute(
                "INSERT INTO shop_users(username,pass_hash,created_at,last_login_at,last_ip) "
                "VALUES(?,?,?,?,?)",
                (username, security.hash_password(password), now, now, ip))
            uid = cur.lastrowid
    except ShopError as e:
        return err(str(e))
    sid, csrf = create_session(uid, ip, _ua(request))
    security.audit("shop_register", "info", ip=ip, actor=username, detail={"uid": uid})
    resp = ok(user={"id": uid, "username": username, "balance_cents": 0, "balance": "0.00"},
              csrf=csrf)
    _cookie(resp, request, sid)
    return resp


@router.post("/shop/api/login")
async def api_login(request: Request):
    ip = _ip(request)
    body = await _body(request)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    allowed, retry = security.hit_shared("shoplogin:%s" % ip, 12, 600)
    if not allowed:
        return err("尝试次数过多，请 %d 秒后再试" % retry, 429)
    userguard = "shoplg:%s:%s" % (ip, username.lower()[:32])
    gok, left = security.guard_check(userguard)
    if not gok:
        return err("该账号暂时锁定，请 %d 分钟后再试" % max(1, left // 60), 429)
    row = db.q1("SELECT * FROM shop_users WHERE username=?", (username,)) if username else None
    if not row:
        security.dummy_verify(password)
    if not row or not security.verify_password(password, row["pass_hash"]):
        security.guard_fail(userguard, threshold=6, window=900, base_lock=300)
        security.record_failure(ip, "shop_login_fail", threshold=12, window=900,
                                ban_seconds=900, actor=username[:32])
        return err("账号或密码错误", 401)
    if row["status"] != "active":
        return err("账号已被禁用，请联系客服", 403)
    security.guard_ok(userguard)
    now = int(time.time())
    db.conn().execute("UPDATE shop_users SET last_login_at=?, last_ip=? WHERE id=?",
                      (now, ip, row["id"]))
    sid, csrf = create_session(row["id"], ip, _ua(request))
    security.audit("shop_login", "info", ip=ip, actor=username)
    resp = ok(user={"id": row["id"], "username": row["username"],
                    "balance_cents": int(row["balance_cents"]),
                    "balance": money(row["balance_cents"])}, csrf=csrf)
    _cookie(resp, request, sid)
    return resp


@router.post("/shop/api/logout")
async def api_logout(request: Request):
    sid = request.cookies.get(COOKIE) or ""
    if sid:
        db.conn().execute("DELETE FROM shop_sessions WHERE sid=?", (sid,))
    resp = ok()
    resp.delete_cookie(COOKIE, path="/")
    return resp
@router.post("/shop/api/recharge")
async def api_recharge(request: Request):
    u = require_user(request)
    body = await _body(request)
    _csrf(request, u, body)
    ip = _ip(request)
    allowed, retry = security.hit_shared("shoprc:%s" % u["user_id"], 10, 600)
    if not allowed:
        return err("操作过于频繁，请 %d 秒后重试" % retry, 429)
    code = re.sub(r"[^A-Z0-9]", "", str(body.get("code") or "").upper())
    if len(code) < 12:
        return err("充值码格式不正确")
    h = hashlib.sha256(("shoprc:" + code).encode()).hexdigest()
    now = int(time.time())
    try:
        with db.tx() as c:
            row = c.execute("SELECT * FROM shop_rcodes WHERE code_hash=?", (h,)).fetchone()
            if not row:
                raise ShopError("充值码不存在，请核对后重试")
            if row["status"] != "active":
                raise ShopError("该充值码已被使用")
            if row["expires_at"] and int(row["expires_at"]) < now:
                raise ShopError("该充值码已过期")
            cur = c.execute("UPDATE shop_rcodes SET status='used', used_by=?, used_at=? "
                            "WHERE id=? AND status='active'", (u["user_id"], now, row["id"]))
            if cur.rowcount != 1:
                raise ShopError("该充值码已被使用")
            amount = int(row["amount_cents"])
            uu = c.execute("SELECT balance_cents FROM shop_users WHERE id=?",
                           (u["user_id"],)).fetchone()
            bal = int(uu["balance_cents"]) + amount
            c.execute("UPDATE shop_users SET balance_cents=? WHERE id=?", (bal, u["user_id"]))
            c.execute("INSERT INTO shop_ledger(user_id,delta_cents,balance_after,kind,ref,note,"
                      "created_at) VALUES(?,?,?,?,?,?,?)",
                      (u["user_id"], amount, bal, "recharge", str(row["id"]),
                       "充值码 " + (row["code_prefix"] or ""), now))
    except ShopError as e:
        return err(str(e))
    security.audit("shop_recharge", "info", ip=ip, actor=u["username"],
                   detail={"amount_cents": amount, "rcode": row["id"], "balance": bal})
    return ok(balance=money(bal), balance_cents=bal, amount=money(amount))


@router.post("/shop/api/password")
async def api_password(request: Request):
    u = require_user(request)
    body = await _body(request)
    _csrf(request, u, body)
    old = str(body.get("old") or "")
    new = str(body.get("new") or "")
    if len(new) < 8 or len(new) > 64 or new.isdigit():
        return err("新密码至少 8 位且不能是纯数字")
    row = db.q1("SELECT * FROM shop_users WHERE id=?", (u["user_id"],))
    if not row or not security.verify_password(old, row["pass_hash"]):
        return err("原密码不正确", 403)
    db.conn().execute("UPDATE shop_users SET pass_hash=? WHERE id=?",
                      (security.hash_password(new), u["user_id"]))
    db.conn().execute("DELETE FROM shop_sessions WHERE user_id=? AND sid<>?",
                      (u["user_id"], u["sid"]))
    security.audit("shop_password", "warn", ip=_ip(request), actor=u["username"])
    return ok()


# ------------------------------------------------------------------ 下单
def order_public(o) -> dict:
    return {
        "id": o["id"], "name": o["app_name"], "country": o["country"],
        "card_type": o["card_type"], "price": money(o["price_cents"]),
        "status": o["status"], "phone": o["phone"], "link": o["link"],
        "error": o["error"], "created_at": o["created_at"], "done_at": o["done_at"],
    }


def _reap_pending(uid: int) -> None:
    """Crash safety: refund orders stuck in pending for over 10 minutes."""
    cutoff = int(time.time()) - 600
    rows = db.q("SELECT * FROM shop_orders WHERE user_id=? AND status='pending' AND created_at<?",
                (uid, cutoff))
    for o in rows:
        try:
            with db.tx() as c:
                cur = c.execute("UPDATE shop_orders SET status='failed', error=? "
                                "WHERE id=? AND status='pending'",
                                ("超时未完成，已自动退款", o["id"]))
                if cur.rowcount != 1:
                    continue
                uu = c.execute("SELECT balance_cents FROM shop_users WHERE id=?",
                               (uid,)).fetchone()
                bal = int(uu["balance_cents"]) + int(o["price_cents"])
                c.execute("UPDATE shop_users SET balance_cents=? WHERE id=?", (bal, uid))
                c.execute("INSERT INTO shop_ledger(user_id,delta_cents,balance_after,kind,ref,"
                          "note,created_at) VALUES(?,?,?,?,?,?,?)",
                          (uid, int(o["price_cents"]), bal, "refund", str(o["id"]),
                           "超时自动退款", int(time.time())))
        except Exception:
            pass


def _buy_upstream(p) -> dict:
    payload = {
        "appId": int(p["app_id"]), "type": int(p["ptype"]), "quantity": 1,
        "expiry": int(db.get_setting("nexsms_expiry", "0") or 0),
        "prefix": (db.get_setting("nexsms_prefix", "") or "").strip() or None,
        "exclude_prefix": None,
    }
    data = providers._nex_http("POST", "/api/premium/purchase", json=payload)
    items = providers._nex_extract_items(data)
    for it in items:
        access = it.get("smsAccess") or {}
        url = str(access.get("url") or "").strip()
        if not url and access.get("token"):
            url = (providers._nex_base() + "/api/number-access/sms-records?token="
                   + str(access.get("token")))
        tel = str(it.get("tel") or "").strip()
        if tel and url:
            phone = tel if tel.startswith("+") else "+" + tel.lstrip("+")
            return {"phone": phone, "upstream": url}
    raise ShopError("上游暂无库存，请稍后重试或换一个平台", 503)


def _create_relay(phone: str, upstream: str, country: str, note: str, days: int,
                  actor: str, card_type: str = "") -> dict:
    """One relay card per order (no batch row): 卡号:+phone----https://api.../smsrecord?token=..."""
    now = int(time.time())
    with db.tx() as c:
        token = relay_api._new_token(c)
        expires = now + days * 86400 if days else 0
        cur = c.execute(
            "INSERT INTO relay_cards(token,phone,country,upstream_enc,note,status,batch_id,"
            "created_at,created_by,expires_at,card_type) VALUES(?,?,?,?,?,'active',0,?,?,?,?)",
            (token, phone, country, security.enc(upstream), note[:120], now, actor,
             expires, card_type[:16]))
        return {"id": cur.lastrowid, "token": token, "expires_at": expires,
                "line": relay_api._line(phone, token)}


def _buy_sync(uid: int, pid: int, idem: str, ip: str) -> dict:
    now = int(time.time())
    with db.tx() as c:
        if idem:
            ex = c.execute("SELECT * FROM shop_orders WHERE user_id=? AND idem=?",
                           (uid, idem)).fetchone()
            if ex:
                return {"order": order_public(dict(ex)), "replay": True}
        p = c.execute("SELECT * FROM shop_products WHERE id=?", (pid,)).fetchone()
        if not p:
            raise ShopError("商品不存在", 404)
        if not int(p["enabled"]):
            raise ShopError("该商品已下架")
        price = int(p["price_cents"] or 0)
        if price <= 0:
            raise ShopError("商品价格未设置，请联系客服")
        u = c.execute("SELECT * FROM shop_users WHERE id=?", (uid,)).fetchone()
        if not u or u["status"] != "active":
            raise ShopError("账号不可用", 403)
        if int(u["balance_cents"]) < price:
            raise ShopError("余额不足，请先充值", 402)
        bal = int(u["balance_cents"]) - price
        c.execute("UPDATE shop_users SET balance_cents=? WHERE id=?", (bal, uid))
        cur = c.execute(
            "INSERT INTO shop_orders(user_id,product_id,app_id,app_name,country,card_type,"
            "price_cents,status,idem,ip,created_at) VALUES(?,?,?,?,?,?,?,'pending',?,?,?)",
            (uid, pid, int(p["app_id"]), p["name"], p["country"], p["card_type"],
             price, idem, ip, now))
        oid = cur.lastrowid
        c.execute("INSERT INTO shop_ledger(user_id,delta_cents,balance_after,kind,ref,note,"
                  "created_at) VALUES(?,?,?,?,?,?,?)",
                  (uid, -price, bal, "buy", str(oid), "购买 %s" % p["name"], now))

    try:
        got = _buy_upstream(p)
        try:
            days = max(0, min(3650, int(db.get_setting("shop_order_days", "0") or 0)))
        except Exception:
            days = 0
        note = "商城 | %s | %s | %s" % (p["name"], p["cate_name"] or "", p["card_type"] or "")
        row = _create_relay(got["phone"], got["upstream"], p["country"] or "", note,
                            days, "shop:" + str(uid), p["card_type"] or "")
        done = int(time.time())
        db.conn().execute(
            "UPDATE shop_orders SET status='done', relay_card_id=?, relay_token=?, phone=?, "
            "link=?, done_at=? WHERE id=?",
            (row["id"], row["token"], got["phone"], row["line"], done, oid))
        security.audit("shop_order", "info", ip=ip, actor="u%d" % uid,
                       detail={"order": oid, "product": p["name"], "price": price})
        o = db.q1("SELECT * FROM shop_orders WHERE id=?", (oid,))
        return {"order": order_public(dict(o)), "replay": False}
    except Exception as e:
        msg = str(e) or "发货失败"
        with db.tx() as c:
            cur = c.execute("UPDATE shop_orders SET status='failed', error=? "
                            "WHERE id=? AND status='pending'", (msg[:180], oid))
            if cur.rowcount == 1:
                uu = c.execute("SELECT balance_cents FROM shop_users WHERE id=?",
                               (uid,)).fetchone()
                bal = int(uu["balance_cents"]) + price
                c.execute("UPDATE shop_users SET balance_cents=? WHERE id=?", (bal, uid))
                c.execute("INSERT INTO shop_ledger(user_id,delta_cents,balance_after,kind,ref,"
                          "note,created_at) VALUES(?,?,?,?,?,?,?)",
                          (uid, price, bal, "refund", str(oid), "发货失败退款",
                           int(time.time())))
        security.audit("shop_order_fail", "warn", ip=ip, actor="u%d" % uid,
                       detail={"order": oid, "error": msg[:160]})
        raise ShopError(msg, 503 if isinstance(e, providers.ProviderBusy) else 500)
@router.post("/shop/api/buy")
async def api_buy(request: Request):
    if db.get_setting("shop_on", "1") != "1":
        return err("商城维护中，请稍后再来", 503)
    u = require_user(request)
    body = await _body(request)
    _csrf(request, u, body)
    try:
        pid = int(body.get("product_id") or 0)
    except Exception:
        return err("商品参数错误")
    idem = str(body.get("idem") or "").strip()[:64]
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", idem or ""):
        idem = secrets.token_hex(12)
    try:
        limit = max(1, min(200, int(db.get_setting("shop_buy_per_hour", "20") or 20)))
    except Exception:
        limit = 20
    allowed, retry = security.hit_shared("shopbuy:%s" % u["user_id"], limit, 3600)
    if not allowed:
        return err("下单过于频繁，请 %d 分钟后重试" % max(1, retry // 60), 429)
    _reap_pending(u["user_id"])
    try:
        res = await run_in_threadpool(_buy_sync, u["user_id"], pid, idem, _ip(request))
    except ShopError as e:
        return err(str(e), e.code)
    except providers.ProviderBusy as e:
        return err(str(e), 503, retry_after=getattr(e, "retry_after", 8))
    except providers.ProviderError as e:
        return err(str(e), 502)
    except HTTPException:
        raise
    except Exception as e:
        return err("下单异常：%s" % type(e).__name__, 500)
    me = db.q1("SELECT balance_cents FROM shop_users WHERE id=?", (u["user_id"],))
    return ok(order=res["order"], replay=res.get("replay", False),
              balance=money(me["balance_cents"]), balance_cents=int(me["balance_cents"]))


@router.get("/shop/api/orders")
def api_orders(request: Request, page: int = 1, size: int = 20):
    u = require_user(request)
    page = max(1, int(page or 1))
    size = max(5, min(50, int(size or 20)))
    total = db.q1("SELECT COUNT(*) AS n FROM shop_orders WHERE user_id=?", (u["user_id"],))["n"]
    rows = db.q("SELECT * FROM shop_orders WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (u["user_id"], size, (page - 1) * size))
    return ok(orders=[order_public(o) for o in rows], total=int(total), page=page, size=size)


# ================================================================== 后台
def _admin(request: Request) -> dict:
    s = security.get_admin_session(request)
    if not s or s.get("stage") != "active":
        raise HTTPException(status_code=401, detail="未登录")
    return s


def _ac(request: Request, s: dict, body: dict) -> None:
    security.require_csrf(request, request.headers.get("x-csrf", "") or body.get("csrf", ""),
                          s["csrf"])


SHOP_SETTING_KEYS = ("shop_on", "shop_register_on", "shop_usd_cny", "shop_markup_pct",
                     "shop_allow_oos", "shop_order_days", "shop_buy_per_hour",
                     "shop_min_recharge", "shop_sync_cates", "shop_sync_pages",
                     "shop_max_orders_per_user")


@admin_router.get("/api/shop/overview")
def adm_overview(request: Request):
    _admin(request)
    now = int(time.time())
    day = now - 86400

    def one(sql, args=()):
        r = db.q1(sql, args)
        return int(list(r)[0]) if r else 0

    return ok(
        users=one("SELECT COUNT(*) FROM shop_users"),
        users_today=one("SELECT COUNT(*) FROM shop_users WHERE created_at>?", (day,)),
        balance_total=one("SELECT COALESCE(SUM(balance_cents),0) FROM shop_users"),
        orders=one("SELECT COUNT(*) FROM shop_orders"),
        orders_today=one("SELECT COUNT(*) FROM shop_orders WHERE created_at>?", (day,)),
        revenue=one("SELECT COALESCE(SUM(price_cents),0) FROM shop_orders WHERE status='done'"),
        revenue_today=one("SELECT COALESCE(SUM(price_cents),0) FROM shop_orders "
                          "WHERE status='done' AND created_at>?", (day,)),
        products=one("SELECT COUNT(*) FROM shop_products"),
        products_on=one("SELECT COUNT(*) FROM shop_products WHERE enabled=1"),
        rcodes=one("SELECT COUNT(*) FROM shop_rcodes"),
        rcodes_active=one("SELECT COUNT(*) FROM shop_rcodes WHERE status='active'"),
        recharge_total=one("SELECT COALESCE(SUM(amount_cents),0) FROM shop_rcodes "
                           "WHERE status='used'"),
    )


@admin_router.get("/api/shop/products")
def adm_products(request: Request, q: str = "", status: str = "all", page: int = 1,
                 size: int = 50):
    _admin(request)
    where, args = "1=1", []
    if status == "on":
        where += " AND enabled=1"
    elif status == "off":
        where += " AND enabled=0"
    elif status == "oos":
        where += " AND stock<=0"
    if q:
        where += " AND (name LIKE ? OR cate_name LIKE ?)"
        args += ["%" + q + "%", "%" + q + "%"]
    total = db.q1("SELECT COUNT(*) AS n FROM shop_products WHERE " + where, tuple(args))["n"]
    page = max(1, int(page or 1))
    size = max(10, min(200, int(size or 50)))
    rows = db.q("SELECT * FROM shop_products WHERE " + where +
                " ORDER BY enabled DESC, sort DESC, success DESC, id LIMIT ? OFFSET ?",
                tuple(args) + (size, (page - 1) * size))
    out = []
    for r in rows:
        d = _product_row(r)
        d["app_id"] = r["app_id"]
        d["ptype"] = r["ptype"]
        d["sort"] = r["sort"]
        out.append(d)
    return ok(products=out, total=int(total), page=page, size=size)


@admin_router.post("/api/shop/product")
async def adm_product_save(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        pid = int(body.get("id") or 0)
    except Exception:
        return err("参数错误")
    row = db.q1("SELECT * FROM shop_products WHERE id=?", (pid,))
    if not row:
        return err("商品不存在", 404)
    sets, args = [], []
    if body.get("price_yuan") not in (None, ""):
        sets.append("price_cents=?")
        args.append(yuan_to_cents(body.get("price_yuan")))
    if body.get("name") not in (None, ""):
        sets.append("name=?")
        args.append(str(body.get("name"))[:60])
    if body.get("icon") not in (None, ""):
        sets.append("icon=?")
        args.append(str(body.get("icon"))[:8])
    if body.get("note") not in (None, ""):
        sets.append("note=?")
        args.append(str(body.get("note"))[:120])
    if body.get("sort") not in (None, ""):
        try:
            sets.append("sort=?")
            args.append(max(-9999, min(9999, int(body.get("sort")))))
        except Exception:
            pass
    if body.get("enabled") is not None:
        sets.append("enabled=?")
        args.append(1 if str(body.get("enabled")) in ("1", "true", "on") else 0)
    if not sets:
        return err("没有需要修改的字段")
    sets.append("updated_at=?")
    args.append(int(time.time()))
    args.append(pid)
    db.conn().execute("UPDATE shop_products SET " + ",".join(sets) + " WHERE id=?", tuple(args))
    security.audit("shop_product_save", "alert", ip=_ip(request), actor=s["username"],
                   detail={"id": pid, "fields": body})
    return ok()
@admin_router.post("/api/shop/product/add")
async def adm_product_add(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        app_id = int(body.get("app_id") or 0)
        ptype = max(1, min(3, int(body.get("ptype") or 1)))
    except Exception:
        return err("appId / 卡类型不正确")
    if app_id <= 0:
        return err("appId 不正确")
    name = str(body.get("name") or "").strip()[:60]
    if not name:
        return err("请填写平台名称")
    cate_name = str(body.get("cate_name") or "").strip()[:40]
    country = country_of(cate_name, name)
    price = yuan_to_cents(body.get("price_yuan") or 0)
    now = int(time.time())
    try:
        with db.tx() as c:
            ex = c.execute("SELECT id FROM shop_products WHERE app_id=? AND ptype=?",
                           (app_id, ptype)).fetchone()
            if ex:
                raise ShopError("该 appId + 卡类型已存在（ID %d）" % ex["id"])
            c.execute("INSERT INTO shop_products(app_id,ptype,name,icon,cate_name,country,"
                      "card_type,price_cents,enabled,sort,note,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,1,0,'',?,?)",
                      (app_id, ptype, name, icon_for(name), cate_name, country,
                       type_name(ptype), price, now, now))
    except ShopError as e:
        return err(str(e))
    return ok()


@admin_router.post("/api/shop/product/delete")
async def adm_product_delete(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        pid = int(body.get("id") or 0)
    except Exception:
        return err("参数错误")
    db.conn().execute("DELETE FROM shop_products WHERE id=?", (pid,))
    security.audit("shop_product_delete", "alert", ip=_ip(request), actor=s["username"],
                   detail={"id": pid})
    return ok()


SYNC = {"running": False, "started": 0, "done": 0, "total": 0, "found": 0,
        "msg": "", "error": "", "added": 0, "updated": 0}
SYNC_LOCK = threading.Lock()


def _round_price(cents: float) -> int:
    v = int(round(cents))
    return max(10, int(round(v / 10.0)) * 10)


def _sync_once() -> None:
    try:
        SYNC.update({"done": 0, "found": 0, "added": 0, "updated": 0, "error": "",
                     "msg": "拉取分类…"})
        cats = providers._nex_http("GET", "/api/premium/categories") or {}
        cate_list = [c for c in (cats.get("list") or []) if c.get("id")]
        only = (db.get_setting("shop_sync_cates", "") or "").strip()
        if only:
            want = {x.strip() for x in only.split(",") if x.strip()}
            cate_list = [c for c in cate_list
                         if str(c.get("id")) in want or str(c.get("name") or "") in want]
        if not cate_list:
            raise ShopError("上游没有可用分类")
        try:
            pages = max(1, min(6, int(db.get_setting("shop_sync_pages", "1") or 1)))
        except Exception:
            pages = 1
        rate = float(db.get_setting("shop_usd_cny", "7.20") or 7.2)
        markup = float(db.get_setting("shop_markup_pct", "40") or 40)
        SYNC["total"] = len(cate_list) * 3
        found = {}
        for cat in cate_list:
            cid = int(cat["id"])
            cname = str(cat.get("name") or "")
            SYNC["msg"] = "扫描 " + cname
            for ptype in (1, 2, 3):
                for page in range(1, pages + 1):
                    data = providers._nex_http("GET", "/api/premium/list", params={
                        "cateId": cid, "type": ptype, "page": page, "pageSize": 100,
                        "sortBy": "quantity", "sortOrder": "desc"}) or {}
                    items = data.get("list") or []
                    for item in items:
                        try:
                            success = float((item.get("successInfo") or {})
                                            .get("successRate") or 0)
                            app_id = int(item.get("id"))
                        except Exception:
                            continue
                        if success <= 0:
                            continue
                        try:
                            usd = float(item.get("price") or 0)
                        except Exception:
                            usd = 0.0
                        try:
                            qty = int(item.get("quantity") or 0)
                        except Exception:
                            qty = 0
                        key = (app_id, ptype)
                        if key in found:
                            found[key]["stock"] = max(found[key]["stock"], qty)
                            continue
                        found[key] = {
                            "app_id": app_id, "ptype": ptype,
                            "name": str(item.get("name") or "未命名")[:60],
                            "cate_id": cid, "cate_name": cname[:40],
                            "country": country_of(cname),
                            "card_type": type_name(item.get("type") or ptype),
                            "usd": usd, "success": success, "stock": qty,
                            "price": _round_price(usd * rate * (1.0 + markup / 100.0) * 100),
                        }
                    total = int(data.get("total") or 0)
                    if not items or page * 100 >= total:
                        break
                    time.sleep(0.2)
                    if not SYNC["running"]:
                        break
                SYNC["done"] += 1
                time.sleep(0.15)
        SYNC["found"] = len(found)
        now = int(time.time())
        added = updated = 0
        with db.tx() as c:
            for it in found.values():
                ex = c.execute("SELECT id FROM shop_products WHERE app_id=? AND ptype=?",
                               (it["app_id"], it["ptype"])).fetchone()
                if ex:
                    c.execute("UPDATE shop_products SET name=?,cate_id=?,cate_name=?,country=?,"
                              "card_type=?,upstream_price_usd=?,success=?,stock=?,updated_at=? "
                              "WHERE id=?",
                              (it["name"], it["cate_id"], it["cate_name"], it["country"],
                               it["card_type"], it["usd"], it["success"], it["stock"], now,
                               ex["id"]))
                    updated += 1
                else:
                    c.execute("INSERT INTO shop_products(app_id,ptype,name,icon,cate_id,"
                              "cate_name,country,card_type,upstream_price_usd,success,stock,"
                              "price_cents,enabled,sort,note,created_at,updated_at) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,0,'',?,?)",
                              (it["app_id"], it["ptype"], it["name"], icon_for(it["name"]),
                               it["cate_id"], it["cate_name"], it["country"],
                               it["card_type"], it["usd"], it["success"], it["stock"],
                               it["price"], now, now))
                    added += 1
        SYNC["added"], SYNC["updated"] = added, updated
        SYNC["msg"] = "同步完成：新增 %d / 更新 %d" % (added, updated)
        security.audit("shop_sync", "alert", actor="admin",
                       detail={"added": added, "updated": updated, "found": len(found)})
    except Exception as e:
        SYNC["error"] = str(e)[:200]
        SYNC["msg"] = "同步失败：" + str(e)[:160]
    finally:
        SYNC["running"] = False


@admin_router.post("/api/shop/sync")
async def adm_sync(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    if not providers._nex_key():
        return err("请先在系统设置里填写 NexSMS API Key")
    with SYNC_LOCK:
        if SYNC["running"]:
            return ok(running=True, msg="同步进行中")
        SYNC.update({"running": True, "started": int(time.time()), "done": 0, "total": 0,
                     "found": 0, "msg": "启动中…", "error": "", "added": 0, "updated": 0})
    threading.Thread(target=_sync_once, daemon=True).start()
    return ok(running=True)


@admin_router.get("/api/shop/sync/status")
def adm_sync_status(request: Request):
    _admin(request)
    return ok(**dict(SYNC))


@admin_router.get("/api/shop/settings")
def adm_settings(request: Request):
    _admin(request)
    return ok(settings={k: db.get_setting(k, "") for k in SHOP_SETTING_KEYS})


@admin_router.post("/api/shop/settings")
async def adm_settings_save(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    changed = []
    for k in SHOP_SETTING_KEYS:
        if k in body:
            v = str(body.get(k) if body.get(k) is not None else "").strip()[:200]
            db.set_setting(k, v)
            changed.append(k + "=" + v)
    security.audit("shop_settings", "alert", ip=_ip(request), actor=s["username"],
                   detail={"changed": changed})
    return ok(changed=changed)
@admin_router.get("/api/shop/orders")
def adm_orders(request: Request, status: str = "", q: str = "", page: int = 1, size: int = 50):
    _admin(request)
    where, args = "1=1", []
    if status:
        where += " AND o.status=?"
        args.append(status)
    if q:
        where += " AND (u.username LIKE ? OR o.phone LIKE ? OR o.app_name LIKE ?)"
        args += ["%" + q + "%"] * 3
    total = db.q1("SELECT COUNT(*) AS n FROM shop_orders o LEFT JOIN shop_users u "
                  "ON u.id=o.user_id WHERE " + where, tuple(args))["n"]
    page = max(1, int(page or 1))
    size = max(10, min(200, int(size or 50)))
    rows = db.q("SELECT o.*, u.username FROM shop_orders o LEFT JOIN shop_users u "
                "ON u.id=o.user_id WHERE " + where +
                " ORDER BY o.id DESC LIMIT ? OFFSET ?",
                tuple(args) + (size, (page - 1) * size))
    out = []
    for r in rows:
        d = order_public(r)
        d["username"] = r["username"] or ("#" + str(r["user_id"]))
        d["user_id"] = r["user_id"]
        out.append(d)
    return ok(orders=out, total=int(total), page=page, size=size)


@admin_router.post("/api/shop/order/refund")
async def adm_order_refund(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        oid = int(body.get("id") or 0)
    except Exception:
        return err("参数错误")
    o = db.q1("SELECT * FROM shop_orders WHERE id=?", (oid,))
    if not o:
        return err("订单不存在", 404)
    if o["status"] == "refunded":
        return err("该订单已退款")
    if o["status"] == "pending":
        return err("订单还在处理中，请稍后")
    bal = 0
    with db.tx() as c:
        cur = c.execute("UPDATE shop_orders SET status='refunded', error=? "
                        "WHERE id=? AND status<>'refunded'", ("管理员退款", oid))
        if cur.rowcount != 1:
            return err("该订单已退款")
        uu = c.execute("SELECT balance_cents FROM shop_users WHERE id=?",
                       (o["user_id"],)).fetchone()
        bal = int(uu["balance_cents"]) + int(o["price_cents"])
        c.execute("UPDATE shop_users SET balance_cents=? WHERE id=?", (bal, o["user_id"]))
        c.execute("INSERT INTO shop_ledger(user_id,delta_cents,balance_after,kind,ref,note,"
                  "created_at) VALUES(?,?,?,?,?,?,?)",
                  (o["user_id"], int(o["price_cents"]), bal, "refund", str(oid),
                   "管理员退款", int(time.time())))
    if o["relay_card_id"]:
        db.conn().execute("UPDATE relay_cards SET status='disabled' WHERE id=?",
                          (o["relay_card_id"],))
    security.audit("shop_refund", "alert", ip=_ip(request), actor=s["username"],
                   detail={"order": oid, "amount": o["price_cents"]})
    return ok(balance=money(bal))


@admin_router.get("/api/shop/rcodes")
def adm_rcodes(request: Request, batch: str = "", status: str = "", page: int = 1,
               size: int = 50):
    _admin(request)
    where, args = "1=1", []
    if batch:
        where += " AND r.batch=?"
        args.append(batch)
    if status:
        where += " AND r.status=?"
        args.append(status)
    total = db.q1("SELECT COUNT(*) AS n FROM shop_rcodes r WHERE " +
                  where.replace("r.batch", "batch").replace("r.status", "status"),
                  tuple(args))["n"]
    page = max(1, int(page or 1))
    size = max(10, min(200, int(size or 50)))
    rows = db.q("SELECT r.*, u.username FROM shop_rcodes r LEFT JOIN shop_users u "
                "ON u.id=r.used_by WHERE " + where + " ORDER BY r.id DESC LIMIT ? OFFSET ?",
                tuple(args) + (size, (page - 1) * size))
    out = [{"id": r["id"], "prefix": r["code_prefix"], "amount": money(r["amount_cents"]),
            "amount_cents": int(r["amount_cents"]), "status": r["status"],
            "batch": r["batch"], "created_at": r["created_at"], "used_at": r["used_at"],
            "used_by": r["username"] or "", "expires_at": r["expires_at"]} for r in rows]
    return ok(rcodes=out, total=int(total), page=page, size=size)


def _new_code() -> str:
    raw = "".join(secrets.choice(ALPHA) for _ in range(16))
    return "SG-" + "-".join(raw[i:i + 4] for i in range(0, 16, 4))


@admin_router.post("/api/shop/rcodes")
async def adm_rcodes_create(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        count = max(1, min(500, int(body.get("count") or 10)))
    except Exception:
        count = 10
    try:
        amount = yuan_to_cents(body.get("amount_yuan") or 0)
    except ShopError as e:
        return err(str(e))
    if amount < 100:
        return err("单张充值码金额至少 1 元")
    try:
        days = max(0, min(3650, int(body.get("expires_days") or 0)))
    except Exception:
        days = 0
    now = int(time.time())
    batch = "B" + time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2).upper()
    codes = []
    with db.tx() as c:
        for _ in range(count):
            code = _new_code()
            h = hashlib.sha256(("shoprc:" + code.replace("-", "")).encode()).hexdigest()
            c.execute("INSERT INTO shop_rcodes(code_hash,code_prefix,amount_cents,status,batch,"
                      "created_by,created_at,expires_at) VALUES(?,?,?,'active',?,?,?,?)",
                      (h, code[:8], amount, batch, s["username"], now,
                       now + days * 86400 if days else 0))
            codes.append(code)
    security.audit("shop_rcodes", "alert", ip=_ip(request), actor=s["username"],
                   detail={"count": count, "amount": amount, "batch": batch})
    return ok(batch=batch, codes=codes, amount=money(amount), count=count)


@admin_router.post("/api/shop/rcodes/disable")
async def adm_rcodes_disable(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        rid = int(body.get("id") or 0)
    except Exception:
        return err("参数错误")
    db.conn().execute("UPDATE shop_rcodes SET status='disabled' WHERE id=? AND status='active'",
                      (rid,))
    security.audit("shop_rcode_disable", "warn", ip=_ip(request), actor=s["username"],
                   detail={"id": rid})
    return ok()


@admin_router.get("/api/shop/users")
def adm_users(request: Request, q: str = "", page: int = 1, size: int = 50):
    _admin(request)
    where, args = "1=1", []
    if q:
        where += " AND username LIKE ?"
        args.append("%" + q + "%")
    total = db.q1("SELECT COUNT(*) AS n FROM shop_users WHERE " + where, tuple(args))["n"]
    page = max(1, int(page or 1))
    size = max(10, min(200, int(size or 50)))
    rows = db.q("SELECT u.*, (SELECT COUNT(*) FROM shop_orders o WHERE o.user_id=u.id) AS orders, "
                "(SELECT COALESCE(SUM(price_cents),0) FROM shop_orders o WHERE o.user_id=u.id "
                "AND o.status='done') AS spent FROM shop_users u WHERE " + where +
                " ORDER BY u.id DESC LIMIT ? OFFSET ?", tuple(args) + (size, (page - 1) * size))
    return ok(users=[{"id": r["id"], "username": r["username"],
                      "balance": money(r["balance_cents"]),
                      "balance_cents": int(r["balance_cents"]), "status": r["status"],
                      "orders": int(r["orders"] or 0), "spent": money(r["spent"]),
                      "created_at": r["created_at"], "last_login_at": r["last_login_at"],
                      "last_ip": r["last_ip"]} for r in rows],
              total=int(total), page=page, size=size)


@admin_router.post("/api/shop/user/balance")
async def adm_user_balance(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        uid = int(body.get("id") or 0)
    except Exception:
        return err("参数错误")
    try:
        delta = yuan_to_cents(body.get("delta_yuan") or 0, allow_neg=True)
    except ShopError as e:
        return err(str(e))
    if delta == 0:
        return err("调整金额不能为 0")
    note = str(body.get("note") or "管理员调整")[:60]
    with db.tx() as c:
        u = c.execute("SELECT * FROM shop_users WHERE id=?", (uid,)).fetchone()
        if not u:
            return err("用户不存在", 404)
        bal = int(u["balance_cents"]) + delta
        if bal < 0:
            return err("调整后余额不能为负")
        c.execute("UPDATE shop_users SET balance_cents=? WHERE id=?", (bal, uid))
        c.execute("INSERT INTO shop_ledger(user_id,delta_cents,balance_after,kind,ref,note,"
                  "created_at) VALUES(?,?,?,?,?,?,?)",
                  (uid, delta, bal, "adjust", s["username"], note, int(time.time())))
    security.audit("shop_balance", "alert", ip=_ip(request), actor=s["username"],
                   detail={"uid": uid, "delta": delta, "note": note})
    return ok(balance=money(bal), balance_cents=bal)


@admin_router.post("/api/shop/user/status")
async def adm_user_status(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        uid = int(body.get("id") or 0)
    except Exception:
        return err("参数错误")
    status = "active" if str(body.get("status")) == "active" else "disabled"
    db.conn().execute("UPDATE shop_users SET status=? WHERE id=?", (status, uid))
    if status != "active":
        db.conn().execute("DELETE FROM shop_sessions WHERE user_id=?", (uid,))
    security.audit("shop_user_status", "alert", ip=_ip(request), actor=s["username"],
                   detail={"uid": uid, "status": status})
    return ok()