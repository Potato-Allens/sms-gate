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
import hashlib, json, re, secrets, sqlite3, threading, time
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
    ("中国", "cn"), ("国内", "cn"), ("大陆", "cn"),
]
FLAGS = {"us": "🇺🇸", "gb": "🇬🇧", "hk": "🇭🇰", "ca": "🇨🇦", "id": "🇮🇩", "my": "🇲🇾",
         "ph": "🇵🇭", "vn": "🇻🇳", "th": "🇹🇭", "jp": "🇯🇵", "kr": "🇰🇷", "de": "🇩🇪",
         "fr": "🇫🇷", "br": "🇧🇷", "ru": "🇷🇺", "in": "🇮🇳", "tr": "🇹🇷", "mx": "🇲🇽",
         "es": "🇪🇸", "it": "🇮🇹", "cn": "🇨🇳"}
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


_ICON_INDEX = None


def _icon_index() -> dict:
    """static/icons/index.json：关键词 -> 图标文件名（真实品牌图标，构建时生成）。"""
    global _ICON_INDEX
    if _ICON_INDEX is None:
        try:
            p = config.STATIC_DIR / "icons" / "index.json"
            _ICON_INDEX = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:
            _ICON_INDEX = {}
    return _ICON_INDEX


def icon_for(name: str) -> str:
    low = (name or "").lower()
    idx = _icon_index()
    for kw in sorted(idx, key=len, reverse=True):
        if kw in low:
            return "i:" + idx[kw]
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


def flag_of(country: str) -> str:
    """国旗：已知的用表，未知的按 ISO2 现算（避免每加一个国家都要改表）。"""
    c = (country or "").strip().lower()
    if c in FLAGS:
        return FLAGS[c]
    if len(c) == 2 and c.isalpha():
        try:
            return "".join(chr(0x1F1E6 + ord(ch) - 97) for ch in c)
        except Exception:
            pass
    return "🌐"


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
    try:
        security.require_csrf(request, request.headers.get("x-csrf", "") or body.get("csrf", ""),
                              sess["csrf"])
    except HTTPException:
        # 已登录用户 token 失效要留痕，方便排查「用户下单失败」
        security.audit("shop_csrf_stale", "warn", ip=_ip(request),
                       actor="u%s" % sess.get("user_id", ""),
                       detail={"path": request.url.path})
        raise


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
    flag = flag_of(p["country"] or "")
    country = p["country"] or ""
    ctype = p["card_type"] or type_name(p["ptype"])
    note = p["note"] or ""
    if country == "cn":
        # 国内商品按「实卡 / 虚拟卡」售卖：标签和说明都跟随商品本身，
        # 不能出现「筛选=国内(实卡)、说明=实卡/虚卡自动匹配」这种自相矛盾。
        req = _cn_card_type(p["cate_name"], p["card_type"])
        if req == "实卡":
            ctype = "实卡"
            note = note or "国内实卡通道 · 只发实卡号码"
        elif req == "虚卡":
            ctype = "虚拟卡"
            note = note or "国内虚拟卡通道 · 只发虚拟运营商号码"
        else:
            ctype = "国内号码"
    return {
        "id": p["id"], "name": p["name"], "icon": p["icon"] or icon_for(p["name"]),
        "cate_name": p["cate_name"] or "", "cate_id": p["cate_id"],
        "country": country, "flag": flag,
        "card_type": ctype,
        "success": round(float(p["success"] or 0), 2),
        "usd": round(float(p["upstream_price_usd"] or 0), 3),
        "stock": int(p["stock"] or 0),
        "price_cents": int(p["price_cents"] or 0), "price": money(p["price_cents"]),
        "enabled": int(p["enabled"] or 0), "note": note,
        "provider": (p["provider"] if "provider" in p.keys() else "nexsms") or "nexsms",
        "sms_keyword": (p["sms_keyword"] if "sms_keyword" in p.keys() else "") or "",
        "sms_prefix": (p["sms_prefix"] if "sms_prefix" in p.keys() else "") or "",
        "service_code": (p["service_code"] if "service_code" in p.keys() else "") or "",
        "country_id": (p["country_id"] if "country_id" in p.keys() else 0) or 0,
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


# 国内实卡 / 虚卡：货源 d1jiema（按「豆包」关键词取号），appId 用负数避免和上游 ID 撞车
CN_SEED = [
    (-1, 1, "豆包", "国内(实卡)", "cn", 200, "实卡"),
    (-2, 1, "豆包", "国内(虚拟卡)", "cn", 150, "虚拟卡"),
]


def seed_cn_products() -> None:
    """补上国内实卡/虚卡两个商品（仅缺失时插入，不动已有定价与开关）。"""
    now = int(time.time())
    for app_id, ptype, name, cate, country, price, ctype in CN_SEED:
        try:
            db.conn().execute(
                "INSERT INTO shop_products(app_id,ptype,name,icon,cate_id,cate_name,country,"
                "card_type,upstream_price_usd,success,stock,price_cents,enabled,sort,note,"
                "created_at,updated_at,provider) "
                "SELECT ?,?,?,?,0,?,?,?,0,0,?,?,1,0,'',?,?,'d1jiema' "
                "WHERE NOT EXISTS (SELECT 1 FROM shop_products WHERE cate_name=? AND name=?)",
                (app_id, ptype, name, icon_for(name), cate, country, ctype,
                 9999, price, now, now, cate, name))
        except Exception:
            pass
        try:
            # 历史遗留的「首次卡/续费卡」标签纠正成实卡/虚拟卡（幂等）
            db.conn().execute(
                "UPDATE shop_products SET card_type=? WHERE cate_name=? AND name=? "
                "AND (card_type IS NULL OR card_type<>?)", (ctype, cate, name, ctype))
        except Exception:
            pass


def catalog_meta() -> dict:
    rows = db.q("SELECT DISTINCT cate_name, country FROM shop_products WHERE enabled=1")
    regions = []
    seen = set()
    multi = {}
    for r in rows:
        key = r["cate_name"] or r["country"]
        if not key:
            continue
        multi.setdefault(key, set()).add((r["country"] or "").strip())
        if key in seen:
            continue
        seen.add(key)
        regions.append({"key": key, "label": key, "flag": flag_of(r["country"] or "")})
    for reg in regions:
        # 一个分类横跨多个国家（例如「普通号 · Google」）时用地球图标，别误导成单个国旗
        if len(multi.get(reg["key"]) or ()) > 1:
            reg["flag"] = "🌐"
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
              csrf=(u["csrf"] if u else ""),
              products=visible_products(region.strip(), type.strip(), q.strip()[:40]),
              **catalog_meta())


# ------------------------------------------------------------------ 现货库存（回收卡密）
STOCK_CATES = {"us": "美国", "gb": "英国卡", "hk": "香港卡", "ca": "加拿大", "id": "印尼",
               "cn": "中国"}


def _cate_of(country: str, fallback: str = "") -> str:
    return (fallback or "").strip() or STOCK_CATES.get((country or "").lower(), country or "通用")


def _site() -> str:
    return (db.get_setting("site_url", "") or "https://12456.cfd").rstrip("/")


def stock_groups() -> list:
    rows = db.q(
        "SELECT cate_name, card_type, country, COUNT(*) AS n, MIN(price_cents) AS p "
        "FROM shop_stock WHERE status='available' GROUP BY cate_name, card_type, country "
        "ORDER BY n DESC, cate_name")
    out = []
    for r in rows:
        n = int(r["n"] or 0)
        if n <= 0:
            continue
        out.append({
            "cate_name": r["cate_name"] or "通用", "card_type": r["card_type"] or "",
            "country": r["country"] or "", "count": n,
            "price_cents": int(r["p"] or 0), "price": money(r["p"]),
            "flag": FLAGS.get((r["country"] or "").lower(), "🌐"),
        })
    return out


@router.get("/shop/api/stock")
def api_stock(request: Request):
    if db.get_setting("shop_stock_on", "1") != "1":
        return ok(groups=[])
    return ok(groups=stock_groups())


@router.post("/shop/api/stock/buy")
async def api_stock_buy(request: Request):
    if db.get_setting("shop_stock_on", "1") != "1":
        return err("现货专区未开放", 403)
    u = require_user(request)
    body = await _body(request)
    _csrf(request, u, body)
    cate = str(body.get("cate_name") or "").strip()[:40]
    ctype = str(body.get("card_type") or "").strip()[:16]
    idem = str(body.get("idem") or "").strip()[:64]
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", idem or ""):
        idem = secrets.token_hex(12)
    cap_msg = _daily_cap_blocked(u["user_id"])
    if cap_msg:
        return err(cap_msg, 429)
    allowed, retry = security.hit_shared("shopstock:%s" % u["user_id"], 60, 3600)
    if not allowed:
        return err("下单过于频繁，请 %d 分钟后重试" % max(1, retry // 60), 429)
    now = int(time.time())
    try:
        with db.tx() as c:
            ex = c.execute("SELECT * FROM shop_orders WHERE user_id=? AND idem=?",
                           (u["user_id"], idem)).fetchone()
            if ex:
                bal_now = c.execute("SELECT balance_cents FROM shop_users WHERE id=?",
                                    (u["user_id"],)).fetchone()
                return ok(order=order_public(dict(ex)), replay=True,
                          balance=money(bal_now["balance_cents"]),
                          balance_cents=int(bal_now["balance_cents"]))
            row = c.execute(
                "SELECT * FROM shop_stock WHERE status='available' AND cate_name=? "
                "AND card_type=? ORDER BY price_cents, id LIMIT 1", (cate, ctype)).fetchone()
            if not row:
                raise ShopError("该现货已售完，请从商城选购", 409)
            price = int(row["price_cents"] or 0)
            if price <= 0:
                raise ShopError("该现货未定价，请联系客服", 409)
            uu = c.execute("SELECT * FROM shop_users WHERE id=?", (u["user_id"],)).fetchone()
            if int(uu["balance_cents"]) < price:
                raise ShopError("余额不足，请先充值", 402)
            cur = c.execute("UPDATE shop_stock SET status='sold', order_id=?, sold_at=? "
                            "WHERE id=? AND status='available'", (0, now, row["id"]))
            if cur.rowcount != 1:
                raise ShopError("手慢了，刚被抢走，请重新下单", 409)
            bal = int(uu["balance_cents"]) - price
            c.execute("UPDATE shop_users SET balance_cents=? WHERE id=?", (bal, u["user_id"]))
            label = "现货 · %s%s" % (row["cate_name"] or "通用",
                                     (" · " + row["card_type"]) if row["card_type"] else "")
            cur2 = c.execute(
                "INSERT INTO shop_orders(user_id,product_id,app_id,app_name,country,card_type,"
                "price_cents,status,relay_card_id,relay_token,phone,link,idem,ip,created_at,done_at) "
                "VALUES(?,0,0,?,?,?,?,'done',?,?,?,?,?,?,?,?)",
                (u["user_id"], label, row["country"], row["card_type"], price,
                 int(row["ref_id"] or 0) if row["kind"] == "relay" else 0,
                 "", row["phone"] or "", row["link"] or "", idem, _ip(request), now, now))
            oid = cur2.lastrowid
            c.execute("UPDATE shop_stock SET order_id=? WHERE id=?", (oid, row["id"]))
            c.execute("INSERT INTO shop_ledger(user_id,delta_cents,balance_after,kind,ref,note,"
                      "created_at) VALUES(?,?,?,?,?,?,?)",
                      (u["user_id"], -price, bal, "buy_stock", str(oid), label, now))
            order = c.execute("SELECT * FROM shop_orders WHERE id=?", (oid,)).fetchone()
    except ShopError as e:
        return err(str(e), e.code)
    security.audit("shop_stock_order", "info", ip=_ip(request), actor="u%d" % u["user_id"],
                   detail={"order": oid, "cate": cate, "type": ctype, "price": price})
    return ok(order=order_public(dict(order)), balance=money(bal), balance_cents=bal)


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
    need_code = db.get_setting("shop_register_need_code", "1") == "1"
    code = re.sub(r"[^A-Z0-9]", "", str(body.get("code") or "").upper())
    if need_code and len(code) < 12:
        return err("请先填写购买到的充值码，注册后自动充进余额")
    now = int(time.time())
    amount = 0
    try:
        with db.tx() as c:
            if c.execute("SELECT 1 FROM shop_users WHERE username=?", (username,)).fetchone():
                raise ShopError("该账号已被注册")
            if need_code:
                h = hashlib.sha256(("shoprc:" + code).encode()).hexdigest()
                row = c.execute("SELECT * FROM shop_rcodes WHERE code_hash=?", (h,)).fetchone()
                if not row:
                    raise ShopError("充值码不存在，请核对后重试")
                if row["status"] != "active":
                    raise ShopError("该充值码已被使用")
                if row["expires_at"] and int(row["expires_at"]) < now:
                    raise ShopError("该充值码已过期")
                cur = c.execute("UPDATE shop_rcodes SET status='used', used_at=? "
                                "WHERE id=? AND status='active'", (now, row["id"]))
                if cur.rowcount != 1:
                    raise ShopError("该充值码已被使用")
                amount = int(row["amount_cents"])
            cur = c.execute(
                "INSERT INTO shop_users(username,pass_hash,balance_cents,created_at,last_login_at,last_ip) "
                "VALUES(?,?,?,?,?,?)",
                (username, security.hash_password(password), amount, now, now, ip))
            uid = cur.lastrowid
            if need_code:
                c.execute("UPDATE shop_rcodes SET used_by=? WHERE id=?", (uid, row["id"]))
                c.execute("INSERT INTO shop_ledger(user_id,delta_cents,balance_after,kind,ref,note,"
                          "created_at) VALUES(?,?,?,?,?,?,?)",
                          (uid, amount, amount, "recharge", str(row["id"]),
                           "注册充值码 " + (row["code_prefix"] or ""), now))
    except ShopError as e:
        return err(str(e))
    sid, csrf = create_session(uid, ip, _ua(request))
    security.audit("shop_register", "info", ip=ip, actor=username,
                   detail={"uid": uid, "rcode_amount": amount})
    resp = ok(user={"id": uid, "username": username, "balance_cents": amount,
                    "balance": money(amount)}, csrf=csrf, amount=money(amount))
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


def _daily_cap_blocked(uid: int) -> str:
    """每个用户 24 小时下单上限（shop_max_orders_per_user，0=不限）。"""
    try:
        cap = max(0, min(500, int(db.get_setting("shop_max_orders_per_user", "0") or 0)))
    except Exception:
        cap = 0
    if not cap:
        return ""
    n = db.q1("SELECT COUNT(*) AS n FROM shop_orders WHERE user_id=? AND created_at>?",
              (uid, int(time.time()) - 86400))["n"]
    if int(n) >= cap:
        return "24 小时内下单已达上限 %d 单，请稍后再试" % cap
    return ""


def _cn_card_type(cate_name: str, card_type: str = "") -> str:
    """国内商品分类 -> d1jiema cardType（实卡 / 虚卡 / 全部）。"""
    tag = (cate_name or "") + " " + (card_type or "")
    if "虚" in tag:
        return "虚卡"
    if "实" in tag:
        return "实卡"
    return "全部"


_CN_VIRT_PREFIX = ("162", "165", "167", "170", "171")


def cn_sim_type(phone: str) -> str:
    """国内号码 -> 实卡 / 虚拟卡（162/165/167/170/171 为虚商号段）。"""
    num = re.sub(r"\D", "", phone or "")
    if num.startswith("86") and len(num) > 11:
        num = num[2:]
    return "虚拟卡" if num[:3] in _CN_VIRT_PREFIX else "实卡"


def fix_cn_card_types() -> dict:
    """给历史国内直链卡/库存补齐实卡 / 虚拟卡标记（幂等，启动时跑）。"""
    rl = st = 0
    try:
        with db.tx() as c:
            for r in c.execute("SELECT id, phone FROM relay_cards WHERE country='cn' "
                               "AND (card_type IS NULL OR card_type='')").fetchall():
                c.execute("UPDATE relay_cards SET card_type=? WHERE id=?",
                          (cn_sim_type(r["phone"]), r["id"]))
                rl += 1
            for r in c.execute("SELECT id, phone FROM shop_stock WHERE country='cn' "
                               "AND (card_type IS NULL OR card_type='')").fetchall():
                c.execute("UPDATE shop_stock SET card_type=? WHERE id=?",
                          (cn_sim_type(r["phone"]), r["id"]))
                st += 1
    except Exception:
        pass
    return {"relay": rl, "stock": st}


def _buy_cn(p) -> dict:
    """国内实卡/虚卡：从 d1jiema 按「豆包」关键词取一个号。"""
    from . import d1jiema
    if not d1jiema.enabled():
        raise ShopError("国内通道维护中，请稍后再试", 503)
    ct = _cn_card_type(p["cate_name"], p["card_type"])
    try:
        kw = (p["sms_keyword"] or "").strip() if "sms_keyword" in p.keys() else ""
    except Exception:
        kw = ""
    try:
        phone = d1jiema.get_phone(kw, ct)
    except d1jiema.D1Busy as e:
        raise ShopError(str(e) or "国内通道繁忙，请稍后重试", 503)
    except d1jiema.D1Error as e:
        raise ShopError(str(e) or "国内取号失败，请稍后重试", 502)
    return {"phone": "+86" + phone, "upstream": "d1jiema:" + phone}


def _buy_upstream(p) -> dict:
    prov = ""
    try:
        prov = str(p["provider"] or "")
    except Exception:
        prov = ""
    if prov == "nexsms_regular":
        return _buy_regular(p)
    if prov == "d1jiema" or (p["country"] or "") == "cn":
        return _buy_cn(p)
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


def _buy_regular(p) -> dict:
    """普通号码（NexSMS activate）：按「项目 + 国家」买 1 个号，取码走本站转发。"""
    try:
        svc = str(p["service_code"] or "go")
    except Exception:
        svc = "go"
    try:
        cid = int(p["country_id"] or 0)
    except Exception:
        cid = 0
    if cid <= 0:
        raise ShopError("商品配置异常，请联系客服", 500)
    price = 0.0
    try:
        cat = providers.regular_by_country(svc, cid)
    except Exception:
        cat = None
    if cat:
        price = float(cat.get("price_usd") or 0)
    if price <= 0:
        try:
            price = float(p["upstream_price_usd"] or 0)
        except Exception:
            price = 0.0
    phone = providers.regular_buy(svc, cid, price, 1)
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        raise ShopError("上游返回的号码异常，请重试", 502)
    return {"phone": "+" + digits, "upstream": "nexreg:%s:%s" % (svc, digits)}


def _create_relay(phone: str, upstream: str, country: str, note: str, days: int,
                  actor: str, card_type: str = "", sms_keyword: str = "",
                  sms_prefix: str = "") -> dict:
    """One relay card per order (no batch row): 卡号:+phone----https://api.../smsrecord?token=..."""
    now = int(time.time())
    with db.tx() as c:
        token = relay_api._new_token(c)
        expires = now + days * 86400 if days else 0
        cur = c.execute(
            "INSERT INTO relay_cards(token,phone,country,upstream_enc,note,status,batch_id,"
            "created_at,created_by,expires_at,card_type,sms_keyword,sms_prefix) "
            "VALUES(?,?,?,?,?,'active',0,?,?,?,?,?,?)",
            (token, phone, country, security.enc(upstream), note[:120], now, actor,
             expires, card_type[:16], (sms_keyword or "")[:40], (sms_prefix or "")[:40]))
        return {"id": cur.lastrowid, "token": token, "expires_at": expires,
                "line": relay_api._line(phone, token)}


def _buy_sync(uid: int, pid: int, idem: str, ip: str) -> dict:
    now = int(time.time())
    if not idem:
        idem = secrets.token_hex(12)      # user_id+idem 唯一：空串不能复用
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
        try:
            synced = (float(p["upstream_price_usd"] or 0) > 0
                      or float(p["success"] or 0) > 0)
        except Exception:
            synced = False
        if synced and int(p["stock"] or 0) <= 0 and db.get_setting("shop_allow_oos", "0") != "1":
            raise ShopError("该商品暂时缺货，请稍后再来或换个平台", 409)
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
        ctype_now = p["card_type"] or ""
        if (p["country"] or "") == "cn":
            # 以「我们向上游请求的类型」为准：号段反推会跟实际售卖的卡型打架
            req = _cn_card_type(p["cate_name"], p["card_type"])
            ctype_now = "虚拟卡" if req == "虚卡" else ("实卡" if req == "实卡" else "")
        pkw = (p["sms_keyword"] if "sms_keyword" in p.keys() else "") or ""
        ppx = (p["sms_prefix"] if "sms_prefix" in p.keys() else "") or ""
        row = _create_relay(got["phone"], got["upstream"], p["country"] or "", note,
                            days, "shop:" + str(uid), ctype_now, pkw, ppx)
        done = int(time.time())
        cur = db.conn().execute(
            "UPDATE shop_orders SET status='done', relay_card_id=?, relay_token=?, phone=?, "
            "link=?, done_at=? WHERE id=? AND status='pending'",
            (row["id"], row["token"], got["phone"], row["line"], done, oid))
        if cur.rowcount != 1:
            # 订单已被超时回收/退款：作废刚生成的卡，避免白拿一个号
            db.conn().execute("UPDATE relay_cards SET status='disabled' WHERE id=?",
                              (row["id"],))
            raise ShopError("订单已超时关闭并退款，请重新下单", 409)
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
    cap_msg = _daily_cap_blocked(u["user_id"])
    if cap_msg:
        return err(cap_msg, 429)
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
                     "shop_max_orders_per_user", "shop_stock_on", "shop_register_need_code",
                     "shop_url", "shop_rcode_url")


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
        d["service_code"] = (r["service_code"] if "service_code" in r.keys() else "") or ""
        d["country_id"] = (r["country_id"] if "country_id" in r.keys() else 0) or 0
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
        args.append(str(body.get("icon"))[:64])
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
    if "sms_keyword" in body:
        sets.append("sms_keyword=?")
        args.append(str(body.get("sms_keyword") or "").strip()[:40])
    if "sms_prefix" in body:
        sets.append("sms_prefix=?")
        args.append(str(body.get("sms_prefix") or "").strip()[:40])
    if not sets:
        return err("没有需要修改的字段")
    sets.append("updated_at=?")
    args.append(int(time.time()))
    args.append(pid)
    db.conn().execute("UPDATE shop_products SET " + ",".join(sets) + " WHERE id=?", tuple(args))
    security.audit("shop_product_save", "alert", ip=_ip(request), actor=s["username"],
                   detail={"id": pid, "fields": body})
    r2 = db.q1("SELECT price_cents FROM shop_products WHERE id=?", (pid,))
    return ok(price=money(r2["price_cents"]) if r2 else "")
@admin_router.post("/api/shop/product/add")
async def adm_product_add(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    prov = "d1jiema" if str(body.get("provider") or "") == "d1jiema" else "nexsms"
    try:
        app_id = int(body.get("app_id") or 0)
        ptype = max(1, min(3, int(body.get("ptype") or 1)))
    except Exception:
        return err("appId / 卡类型不正确")
    if app_id == 0:
        return err("appId 不正确")
    if prov == "nexsms" and app_id < 0:
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
                      "card_type,price_cents,enabled,sort,note,created_at,updated_at,provider,"
                      "sms_keyword,sms_prefix) VALUES(?,?,?,?,?,?,?,?,1,0,'',?,?,?,?,?)",
                      (app_id, ptype, name, icon_for(name), cate_name, country,
                       type_name(ptype), price, now, now, prov,
                       str(body.get("sms_keyword") or "").strip()[:40],
                       str(body.get("sms_prefix") or "").strip()[:40]))
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


# ------------------------------------------------- 普通号码（NexSMS activate）
REG_SERVICES = providers.REG_SERVICES


def _reg_label(svc: str) -> str:
    for code, label in REG_SERVICES:
        if code == svc:
            return label.split(",")[0]
    return svc or "普通号"


def _reg_plan(service: str, force: bool = False) -> list:
    """映射成商城商品：只保留有成功率的国家。"""
    rate = float(db.get_setting("shop_usd_cny", "7.20") or 7.2)
    markup = float(db.get_setting("shop_markup_pct", "40") or 40)
    label = _reg_label(service)
    out = []
    for r in providers.regular_catalog(service, force=force):
        if r["success"] <= 0:
            continue
        usd = float(r["price_usd"] or 0)
        out.append({
            "country_id": r["country_id"],
            "name_chn": r["name_chn"] or r["name"],
            "iso": r["iso"] or "",
            "phone_code": r["phone_code"],
            "success": r["success"],
            "usd": usd,
            "stock": int(r["stock"]),
            "available": bool(r["available"]),
            "name": ("%s · %s" % (label, r["name_chn"] or r["name"]))[:60],
            # 分类只按「项目」聚合：不然 40 个国家会把商城地区筛选塞满
            "cate_name": ("普通号 · " + label)[:40],
            "price_cents": _round_price(usd * rate * (1.0 + markup / 100.0) * 100),
            "app_id": -(10000 + int(r["country_id"])),
        })
    return out


@admin_router.get("/api/shop/regular/catalog")
def adm_reg_catalog(request: Request, service: str = "go", refresh: int = 0,
                    all: int = 0):
    _admin(request)
    svc = (service or "go").strip()[:8] or "go"
    try:
        prov = _reg_plan(svc, force=bool(int(refresh or 0)))
    except providers.ProviderBusy as e:
        return err(str(e), 503, retry_after=getattr(e, "retry_after", 8))
    except providers.ProviderError as e:
        return err(str(e), 502)
    if not int(all or 0):
        prov = [p for p in prov if p["success"] > 0]
    rows = db.q("SELECT id,country_id,enabled,price_cents FROM shop_products "
                "WHERE provider='nexsms_regular' AND service_code=?", (svc,))
    used = {int(r["country_id"] or 0): r for r in rows}
    out = []
    for p in prov:
        ex = used.get(p["country_id"])
        out.append(dict(p, product_id=(ex["id"] if ex else 0),
                        enabled=(int(ex["enabled"]) if ex else 0),
                        price=(money(ex["price_cents"]) if ex else "")))
    out.sort(key=lambda r: (-(r["success"] or 0), r["usd"] or 0))
    return ok(service=svc, total=len(out), list=out,
              services=[{"code": c, "label": l} for c, l in REG_SERVICES],
              rate=float(db.get_setting("shop_usd_cny", "7.20") or 7.2))


@admin_router.post("/api/shop/regular/sync")
async def adm_reg_sync(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    svc = str(body.get("service") or "go").strip()[:8] or "go"
    try:
        plan = _reg_plan(svc, force=True)
    except providers.ProviderBusy as e:
        return err(str(e), 503, retry_after=getattr(e, "retry_after", 8))
    except providers.ProviderError as e:
        return err(str(e), 502)
    plan = [p for p in plan if p["success"] > 0]
    if not plan:
        return err("上游暂无有成功率的国家")
    now = int(time.time())
    added = updated = 0
    with db.tx() as c:
        for p in plan:
            ex = c.execute("SELECT id FROM shop_products WHERE provider='nexsms_regular' "
                           "AND service_code=? AND country_id=?",
                           (svc, p["country_id"])).fetchone()
            if ex:
                # 只刷新上游数据，绝不动管理员定好的售价与上下架状态
                c.execute("UPDATE shop_products SET app_id=?,name=?,cate_name=?,country=?,"
                          "card_type='普通号',upstream_price_usd=?,success=?,stock=?,"
                          "updated_at=? WHERE id=?",
                          (p["app_id"], p["name"], p["cate_name"], p["iso"],
                           p["usd"], p["success"], p["stock"], now, ex["id"]))
                updated += 1
            else:
                c.execute("INSERT INTO shop_products(app_id,ptype,name,icon,cate_id,cate_name,"
                          "country,card_type,upstream_price_usd,success,stock,price_cents,"
                          "enabled,sort,note,created_at,updated_at,provider,service_code,"
                          "country_id) VALUES(?,1,?,?,0,?,?,'普通号',?,?,?,?,1,0,'',?,?,"
                          "'nexsms_regular',?,?)",
                          (p["app_id"], p["name"], icon_for(p["name"]), p["cate_name"],
                           p["iso"], p["usd"], p["success"], p["stock"], p["price_cents"],
                           now, now, svc, p["country_id"]))
                added += 1
    security.audit("shop_regular_sync", "alert", ip=_ip(request), actor=s["username"],
                   detail={"service": svc, "added": added, "updated": updated})
    return ok(service=svc, added=added, updated=updated, total=len(plan),
              rate=float(db.get_setting("shop_usd_cny", "7.20") or 7.2))


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
    try:
        floor_yuan = float(db.get_setting("shop_min_recharge", "5") or 5)
    except Exception:
        floor_yuan = 5.0
    floor = max(10, min(100000, int(round(floor_yuan * 100))))
    if amount < floor:
        return err("单张充值码金额不能低于 %s 元（商城设置里的最低充值额）" % money(floor))
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


# ---------------------------------------------------------------- 后台：卡密库存
def _norm_card_key(raw: str) -> str:
    s = (raw or "").strip().upper().replace(" ", "")
    if "/C/" in s:
        s = s.split("/C/", 1)[1]
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return s


def _card_key_hash(key: str) -> str:
    return hashlib.sha256(("sg:" + key).encode()).hexdigest()


_RE_C_LINK = re.compile(r"/c/([0-9A-Za-z][0-9A-Za-z-]{7,63})", re.I)
_RE_TOKEN = re.compile(r"token=([0-9a-fA-F]{16,64})", re.I)
_RE_INVIS = re.compile(r"[\u200b-\u200f\u2028\u2029\ufeff]")


def _extract_links(text: str):
    """从任意粘贴文本里挑出 /c/<卡密> 和 token=<直链>，其它文字（时间、状态、备注）自动忽略。"""
    t = _RE_INVIS.sub("", str(text or ""))
    keys, seen, toks, seen2 = [], set(), [], set()
    for m in _RE_C_LINK.finditer(t):
        k = m.group(1).strip("-").upper()
        if len(k) >= 8 and k not in seen:
            seen.add(k)
            keys.append(k)
    for m in _RE_TOKEN.finditer(t):
        v = m.group(1).lower()
        if v not in seen2:
            seen2.add(v)
            toks.append(v)
    return keys, toks


def _stock_insert(c, kind, ref_id, link, phone, country, cate, ctype, price, batch, note="",
                  sms_keyword="", sms_prefix="") -> bool:
    try:
        c.execute(
            "INSERT INTO shop_stock(kind,ref_id,link,phone,country,cate_name,card_type,price_cents,"
            "status,batch,note,created_at,sms_keyword,sms_prefix) "
            "VALUES(?,?,?,?,?,?,?,?,'available',?,?,?,?,?)",
            (kind, int(ref_id), link, phone or "", country or "", cate or "", ctype or "",
             int(price), batch, note[:120], int(time.time()),
             (sms_keyword or "")[:40], (sms_prefix or "")[:40]))
        return True
    except sqlite3.IntegrityError:
        return False


@admin_router.get("/api/shop/stock/preview")
def adm_stock_preview(request: Request, cate_name: str = "", card_type: str = "", days: int = 0):
    """回收前预览：按运营商 / 卡类型 / 创建时间统计仍可回收的直链卡。"""
    _admin(request)
    now = int(time.time())
    where = ("status='active' AND (expires_at=0 OR expires_at>?) AND reads=0 "
             "AND created_by NOT LIKE 'shop:%' "
             "AND id NOT IN (SELECT ref_id FROM shop_stock WHERE kind='relay')")
    args = [now]
    country = country_of(cate_name)
    if country:
        where += " AND country=?"
        args.append(country)
    if card_type:
        where += " AND card_type=?"
        args.append(card_type)
    days = max(0, min(3650, int(days or 0)))
    if days:
        where += " AND created_at<=?"
        args.append(now - days * 86400)
    rows = db.q("SELECT country, card_type, COUNT(*) AS n FROM relay_cards WHERE " + where +
                " GROUP BY country, card_type ORDER BY n DESC", tuple(args))
    groups = [{"country": r["country"] or "", "card_type": r["card_type"] or "",
               "count": int(r["n"] or 0)} for r in rows]
    total = sum(g["count"] for g in groups)
    return ok(total=total, groups=groups, country=country, days=days)


@admin_router.post("/api/shop/stock/recover")
async def adm_stock_recover(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    mode = str(body.get("mode") or "links")
    # 保险：前端忘了切模式时，只要粘了链接就按粘贴回收处理
    if mode == "relay" and str(body.get("text") or "").strip():
        mode = "links"
    cate = str(body.get("cate_name") or "").strip()[:40]
    ctype = str(body.get("card_type") or "").strip()[:16]
    country = country_of(cate)
    cate = cate or _cate_of(country)
    if mode == "relay" and not country:
        return err("请先填写运营商（例如 中国 / 美国 / 香港卡），避免不同国家的卡混进同一库存")
    try:
        price = yuan_to_cents(body.get("price_yuan") or 0)
    except ShopError as e:
        return err(str(e))
    if price < 10:
        return err("请填写现货售价（至少 0.1 元），否则买家无法下单")
    try:
        limit = max(1, min(500, int(body.get("limit") or 50)))
    except Exception:
        limit = 50
    try:
        days = max(0, min(3650, int(body.get("days") or 0)))
    except Exception:
        days = 0
    note = str(body.get("note") or "")[:120]
    batch = "S" + time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2).upper()
    now = int(time.time())
    added = dup = 0
    bad = []
    keys, toks = [], []
    # 同分组统一售价，避免同货不同价
    try:
        db.conn().execute("UPDATE shop_stock SET price_cents=? WHERE status='available' "
                          "AND cate_name=? AND card_type=?", (price, cate, ctype))
    except Exception:
        pass

    if mode == "relay":
        sql = ("SELECT * FROM relay_cards WHERE status='active' AND (expires_at=0 OR expires_at>?) "
               "AND reads=0 AND created_by NOT LIKE 'shop:%' "
               "AND id NOT IN (SELECT ref_id FROM shop_stock WHERE kind='relay')")
        args = [now]
        if country:
            sql += " AND country=?"
            args.append(country)
        if ctype:
            sql += " AND card_type=?"
            args.append(ctype)
        if days:
            sql += " AND created_at<=?"
            args.append(now - days * 86400)
        sql += " ORDER BY id LIMIT ?"
        args.append(limit)
        rows = db.q(sql, tuple(args))
        with db.tx() as c:
            for r in rows:
                line = relay_api._line(r["phone"], r["token"])
                rt = ctype or (r["card_type"] or "")
                if not rt and (r["country"] or country) == "cn":
                    rt = cn_sim_type(r["phone"])
                if _stock_insert(c, "relay", r["id"], line, r["phone"], r["country"] or country,
                                 cate, rt, price, batch, note,
                                 r["sms_keyword"] or "", r["sms_prefix"] or ""):
                    added += 1
                else:
                    dup += 1
    else:
        text = str(body.get("text") or "")
        keys, toks = _extract_links(text)
        if not keys and not toks:
            return err("没识别到卡密链接：支持 https://…/c/XXXX-XXXX-XXXX-XXXX 和 "
                       "卡号:…----…?token=…；时间、已售出、备注等文字会自动忽略", 400)
        with db.tx() as c:
            for key in keys[:600]:
                card = c.execute("SELECT * FROM cards WHERE key_hash=?",
                                 (_card_key_hash(key),)).fetchone()
                if not card:
                    bad.append("不在本站:" + key)
                    continue
                if card["status"] != "active":
                    bad.append(("已售出:" if card["status"] == "used" else "已停用:") + key)
                    continue
                if int(card["used_codes"] or 0) >= int(card["max_codes"] or 1):
                    bad.append("已使用:" + key)
                    continue
                link = _site() + "/c/" + key
                ctype2 = ctype or (card["card_type"] if "card_type" in card.keys() else "") or ""
                if _stock_insert(c, "card", card["id"], link, "", country, cate, ctype2,
                                 price, batch, note):
                    added += 1
                else:
                    dup += 1
            for tok in toks[:600]:
                rc = c.execute("SELECT * FROM relay_cards WHERE token=?", (tok,)).fetchone()
                if not rc:
                    bad.append("找不到直链:" + tok[:16])
                    continue
                line = relay_api._line(rc["phone"], rc["token"])
                rt2 = ctype or (rc["card_type"] or "")
                if not rt2 and (rc["country"] or country) == "cn":
                    rt2 = cn_sim_type(rc["phone"])
                if _stock_insert(c, "relay", rc["id"], line, rc["phone"],
                                 rc["country"] or country, cate,
                                 rt2, price, batch, note,
                                 rc["sms_keyword"] or "", rc["sms_prefix"] or ""):
                    added += 1
                else:
                    dup += 1
    if not added and not dup:
        return err("没有可回收的卡密：请检查链接是否属于本站、是否已被使用", 400, bad=bad[:20])
    security.audit("shop_stock_recover", "alert", ip=_ip(request), actor=s["username"],
                   detail={"mode": mode, "added": added, "dup": dup, "batch": batch,
                           "cate": cate, "type": ctype, "price": price})
    return ok(added=added, dup=dup, bad=bad[:20], batch=batch, cate=cate, card_type=ctype,
              parsed=len(keys) + len(toks) if mode != "relay" else added + dup,
              price=money(price), available=sum(g["count"] for g in stock_groups()))


@admin_router.get("/api/shop/stock")
def adm_stock(request: Request, status: str = "available", cate: str = "", page: int = 1,
              size: int = 50):
    _admin(request)
    where, args = "1=1", []
    if status:
        where += " AND status=?"
        args.append(status)
    if cate:
        where += " AND (cate_name=? OR country=?)"
        args += [cate, cate]
    total = db.q1("SELECT COUNT(*) AS n FROM shop_stock WHERE " + where, tuple(args))["n"]
    page = max(1, int(page or 1))
    size = max(10, min(200, int(size or 50)))
    rows = db.q("SELECT * FROM shop_stock WHERE " + where +
                " ORDER BY id DESC LIMIT ? OFFSET ?", tuple(args) + (size, (page - 1) * size))
    out = [{"id": r["id"], "kind": r["kind"], "link": r["link"], "phone": r["phone"] or "",
            "country": r["country"] or "", "cate_name": r["cate_name"] or "",
            "card_type": r["card_type"] or "", "price": money(r["price_cents"]),
            "status": r["status"], "batch": r["batch"] or "", "order_id": r["order_id"],
            "created_at": r["created_at"], "sold_at": r["sold_at"]} for r in rows]
    return ok(stock=out, total=int(total), page=page, size=size,
              groups=stock_groups())


@admin_router.post("/api/shop/stock/remove")
async def adm_stock_remove(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    try:
        sid = int(body.get("id") or 0)
    except Exception:
        return err("参数错误")
    db.conn().execute("UPDATE shop_stock SET status='removed' WHERE id=? AND status='available'",
                      (sid,))
    security.audit("shop_stock_remove", "warn", ip=_ip(request), actor=s["username"],
                   detail={"id": sid})
    return ok()


@admin_router.post("/api/shop/stock/price")
async def adm_stock_price(request: Request):
    s = _admin(request)
    body = await _body(request)
    _ac(request, s, body)
    cate = str(body.get("cate_name") or "").strip()[:40]
    ctype = str(body.get("card_type") or "").strip()[:16]
    try:
        price = yuan_to_cents(body.get("price_yuan") or 0)
    except ShopError as e:
        return err(str(e))
    if price < 10:
        return err("售价至少 0.1 元")
    db.conn().execute("UPDATE shop_stock SET price_cents=? WHERE status='available' "
                      "AND cate_name=? AND card_type=?", (price, cate, ctype))
    security.audit("shop_stock_price", "alert", ip=_ip(request), actor=s["username"],
                   detail={"cate": cate, "type": ctype, "price": price})
    return ok(price=money(price))


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