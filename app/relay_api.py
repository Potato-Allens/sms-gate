# -*- coding: utf-8 -*-
"""Relay cards: 卡号:+phone----https://<our-domain>/smsrecord?token=...

The real upstream SMS URL is stored encrypted and never leaves the server; the
public endpoint proxies it under our own domain, so buyers cannot learn where
the numbers actually come from.  Relay cards are reusable: the same link keeps
returning the newest SMS until it is disabled or expires.
"""
from __future__ import annotations
import ipaddress, os, re, secrets, socket, threading, time
from urllib.parse import urljoin, urlparse

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import db, providers, security

router = APIRouter()
S = requests.Session()
S.trust_env = False

PUBLIC_PATH = "/smsrecord"
DEFAULT_BASE = "https://api.12456.cfd"
MAX_BODY = 16384
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}

_HOST_CACHE: dict[str, tuple[float, str]] = {}
_HOST_LOCK = threading.Lock()


def ok(**kw):
    kw.setdefault("ok", True)
    return JSONResponse(kw)


def err(msg, code: int = 400, **kw):
    kw.update({"ok": False, "error": msg})
    return JSONResponse(kw, status_code=code)


def _admin(request: Request) -> dict:
    s = security.get_admin_session(request)
    if not s or s.get("stage") != "active":
        raise HTTPException(status_code=401, detail="未登录")
    return s


def _csrf(request: Request, sess: dict, body: dict) -> None:
    security.require_csrf(request, request.headers.get("x-csrf", "") or body.get("csrf", ""), sess["csrf"])


def _base() -> str:
    return (db.get_setting("relay_base", DEFAULT_BASE) or DEFAULT_BASE).rstrip("/")


def _line(phone: str, token: str) -> str:
    return "卡号:%s----%s%s?token=%s" % (phone, _base(), PUBLIC_PATH, token)


def _phone(raw: str) -> str:
    s = (raw or "").strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits or len(digits) < 6 or len(digits) > 20:
        return ""
    return ("+" if plus else "") + digits


def _clean_url(raw: str) -> str:
    s = (raw or "").strip().strip("<>\"'")
    m = re.search(r"\((https?://[^)\s]+)\)", s)          # markdown [text](url)
    if m:
        s = m.group(1)
    s = s.rstrip("),.;，。；")
    return s


def _allow_private() -> bool:
    # Test / self-host escape hatch only; production leaves this unset.
    return os.environ.get("SMSGATE_RELAY_ALLOW_PRIVATE") == "1"


def _host_ok(host: str, port: int) -> str:
    if _allow_private():
        return ""
    key = "%s:%d" % (host, port)
    now = time.time()
    with _HOST_LOCK:
        hit = _HOST_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        problem = "域名无法解析"
    else:
        problem = ""
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                problem = "域名解析异常"
                break
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                    or ip.is_reserved or ip.is_unspecified):
                problem = "不允许内网地址"
                break
    with _HOST_LOCK:
        _HOST_CACHE[key] = (now + 60, problem)
        if len(_HOST_CACHE) > 2000:
            _HOST_CACHE.clear()
    return problem


def url_problem(url: str) -> str:
    try:
        p = urlparse(url)
    except Exception:
        return "地址格式不正确"
    if p.scheme not in ("http", "https"):
        return "仅支持 http/https"
    host = (p.hostname or "").strip().lower()
    if not host:
        return "缺少域名"
    if not _allow_private() and (host in ("localhost", "localhost.localdomain") or host.endswith(
            (".local", ".localhost", ".internal", ".lan"))):
        return "不允许内网地址"
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return "端口不正确"
    return _host_ok(host, port)


def _upstream_label(up: str) -> str:
    if not up:
        return "—"
    if up.startswith("d1jiema:"):
        return "d1jiema"
    if up.startswith("nexreg:"):
        return "NexSMS 普通号"
    try:
        return urlparse(up).hostname or "—"
    except Exception:
        return "—"


def parse_line(line: str):
    s = (line or "").strip().strip("\ufeff")
    if not s or s.startswith("#"):
        return None
    s = re.sub(r"^(?:卡\s*号|卡密|手机号)\s*[:：]\s*", "", s)
    if "----" in s:
        parts = [x.strip() for x in s.split("----")]
    elif "|" in s:
        parts = [x.strip() for x in s.split("|")]
    else:
        m = re.match(r"^(\+?\d[\d\s\-]{4,24})\s+(https?://\S+)$", s)
        if not m:
            return None
        parts = [m.group(1), m.group(2)]
    if len(parts) < 2:
        return None
    phone = _phone(parts[0])
    url = _clean_url(parts[1])
    if not phone or not url:
        return None
    return {"phone": phone, "upstream": url, "note": (parts[2] if len(parts) > 2 else "")[:80]}


def parse_lines(text: str, limit: int = 500):
    items, bad = [], []
    for no, line in enumerate((text or "").splitlines(), 1):
        if len(items) >= limit:
            bad.append({"line": no, "why": "单次最多 %d 条" % limit, "text": line[:120]})
            break
        it = parse_line(line)
        if not it:
            if line.strip():
                bad.append({"line": no, "why": "格式应为 卡号:+手机号----接码地址", "text": line[:120]})
            continue
        it["line"] = no
        items.append(it)
    return items, bad


def _extract_code(text: str) -> str:
    t = (text or "").strip()
    if "|" in t:
        for part in reversed([p.strip() for p in t.split("|")]):
            if re.fullmatch(r"\d{4,8}", part) and part != "0":
                return part
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", t)
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{5,8})(?!\d)", t)
    return m.group(1) if m else ""


def _fetch(url: str, timeout: int = 15, kw: str = "", meta: dict = None):
    """GET the upstream SMS URL with SSRF guards; return (ok, reason, text)."""
    if url.startswith("d1jiema:"):
        from . import d1jiema
        try:
            parts = url.split(":", 2)
            phone = parts[1] if len(parts) > 1 else ""
            text = d1jiema.clean(d1jiema.get_msg(phone, kw or ""))
            return True, "", (text or "0|0")
        except Exception:
            return False, "upstream_unreachable", ""
    if url.startswith("nexreg:"):
        from . import providers
        try:
            parts = url.split(":", 2)
            svc = parts[1] if len(parts) > 1 and parts[1] else "go"
            phone = parts[2] if len(parts) > 2 else ""
            r = providers.regular_sms(phone, svc)
            if meta is not None:
                meta["expire_at"] = int(r.get("expire_at") or 0)
                meta["sms_at"] = int(r.get("sms_at") or 0)
            body = (r.get("text") or "").strip()
            return True, "", (("1|" + body) if body else "0|0")
        except Exception:
            return False, "upstream_unreachable", ""
    cur = url
    for _ in range(4):
        problem = url_problem(cur)
        if problem:
            return False, "url_blocked", ""
        try:
            r = S.get(cur, timeout=(6, timeout), headers={"User-Agent": UA, "Accept": "*/*"},
                      allow_redirects=False)
        except Exception:
            return False, "upstream_unreachable", ""
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location")
            if not loc:
                return False, "bad_redirect", ""
            cur = urljoin(cur, loc)
            continue
        if r.status_code >= 400:
            return False, "upstream_%d" % r.status_code, ""
        raw = (r.content or b"")[:MAX_BODY]
        for enc in ("utf-8", "gb18030"):
            try:
                return True, "", raw.decode(enc)
            except Exception:
                continue
        return True, "", raw.decode("utf-8", "replace")
    return False, "too_many_redirects", ""


# --------------------------------------------------------------- admin: create
def _new_token(c) -> str:
    for _ in range(10):
        t = secrets.token_hex(16)
        if not c.execute("SELECT 1 FROM relay_cards WHERE token=?", (t,)).fetchone():
            return t
    raise RuntimeError("token collision")


def _create_sync(items, note: str, days: int, actor: str):
    now = int(time.time())
    batch_no = "R" + time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2).upper()
    out = []
    with db.tx() as c:
        cur = c.execute("INSERT INTO batches(batch_no,count,note,created_by,created_at) VALUES(?,?,?,?,?)",
                        (batch_no, len(items), note, actor, now))
        bid = cur.lastrowid
        try:
            d1_ttl = max(1, min(120, int(db.get_setting("d1jiema_ttl_min", "20") or 20)))
        except Exception:
            d1_ttl = 20
        for it in items:
            token = _new_token(c)
            expires = now + days * 86400 if days else 0
            num_expire = now + d1_ttl * 60 if str(it.get("upstream") or "").startswith("d1jiema:") else 0
            cur2 = c.execute(
                "INSERT INTO relay_cards(token,phone,country,upstream_enc,note,status,batch_id,"
                "created_at,created_by,expires_at,card_type,sms_keyword,sms_prefix,num_expire_at) "
                "VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,?,?)",
                (token, it["phone"], it.get("country", ""), security.enc(it["upstream"]),
                 (it.get("note") or note)[:120], bid, now, actor, expires,
                 (it.get("card_type") or "")[:16], (it.get("sms_keyword") or "")[:40],
                 (it.get("sms_prefix") or "")[:40], num_expire))
            out.append({"id": cur2.lastrowid, "phone": it["phone"], "token": token,
                        "expires_at": expires, "line": _line(it["phone"], token)})
    return batch_no, out


@router.post("/admin/api/relay/create")
async def relay_create(request: Request):
    s = _admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _csrf(request, s, body)
    text = str(body.get("lines") or "")
    note = str(body.get("note") or "")[:120]
    kw = str(body.get("sms_keyword") or "").strip()[:40]
    pref = str(body.get("sms_prefix") or "").strip()[:40]
    try:
        days = max(0, min(3650, int(body.get("expires_days") or 0)))
    except Exception:
        days = 0
    if not text.strip():
        return err("输入框为空：请粘贴 卡号:手机号----接码地址（每行一条）", 400, bad=[])
    items, bad = parse_lines(text)
    if not items:
        return err("没有解析到有效号码，格式应为 卡号:+手机号----接码地址", 400, bad=bad)
    for it in items:
        it["sms_keyword"] = kw
        it["sms_prefix"] = pref
    good, rejected = await run_in_threadpool(_checked, items)
    bad += rejected
    if not good:
        return err("接码地址全部不可用", 400, bad=bad)
    batch_no, rows = await run_in_threadpool(_create_sync, good, note, days, s["username"])
    security.audit("relay_create", "alert", ip=security.client_ip(request), actor=s["username"],
                   detail={"batch": batch_no, "count": len(rows), "bad": len(bad)})
    return ok(batch=batch_no, count=len(rows), items=rows,
              lines=[r["line"] for r in rows], bad=bad, base=_base())


def _checked(items):
    good, bad = [], []
    for it in items:
        problem = url_problem(it["upstream"])
        if problem:
            bad.append({"line": it.get("line", 0), "why": problem, "text": it["phone"]})
        else:
            good.append(it)
    return good, bad


@router.post("/admin/api/relay/from_pool")
async def relay_from_pool(request: Request):
    s = _admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _csrf(request, s, body)
    try:
        count = max(1, min(200, int(body.get("count") or 10)))
    except Exception:
        count = 10
    country = str(body.get("country") or "").strip().lower()
    note = str(body.get("note") or "号池转直链")[:120]
    sql = "SELECT * FROM numbers WHERE status='available' AND sms_url<>''"
    args = []
    if country:
        sql += " AND country=?"
        args.append(country)
    sql += " ORDER BY id LIMIT ?"
    args.append(count)
    nums = [dict(r) for r in db.q(sql, tuple(args))]
    if not nums:
        return err("号池里没有可用的号码（需带接码地址）")
    items = [{"phone": _phone(n["phone"]), "upstream": n["sms_url"],
              "country": n["country"], "note": note} for n in nums if _phone(n["phone"])]
    good, bad = await run_in_threadpool(_checked, items)
    if not good:
        return err("号池号码的接码地址不可用", 400, bad=bad)
    batch_no, rows = await run_in_threadpool(_create_sync, good, note, 0, s["username"])
    ids = [n["id"] for n in nums]
    with db.tx() as c:
        for nid in ids:
            c.execute("UPDATE numbers SET status='relay' WHERE id=? AND status='available'", (nid,))
    security.audit("relay_from_pool", "alert", ip=security.client_ip(request), actor=s["username"],
                   detail={"batch": batch_no, "count": len(rows)})
    return ok(batch=batch_no, count=len(rows), items=rows, lines=[r["line"] for r in rows], bad=bad)


# --------------------------------------------------------------- admin: allocate
def _alloc_one(choice: str, info: dict, prov: str, seq: int) -> dict:
    if prov == "d1jiema":
        # Negative synthetic id: d1jiema's per-card lease and "already taken" check
        # need a unique owner that is not a real card.
        neg = -(int(time.time() * 1000) % 900000000 + seq + 1)
        num = providers.allocate_d1(choice, info.get("card_type", ""), neg)
        upstream = "d1jiema:%s:%s" % (num["phone"], info.get("card_type", ""))
    elif prov == "nexsms_premium":
        num = providers.allocate_nex(info.get("country") or choice, 0)
        upstream = (num.get("sms_url") or "").strip()
    else:
        num = providers.allocate(info.get("country") or choice, 0)
        upstream = (num.get("sms_url") or "").strip()
    return {"number_id": int(num["id"]), "phone": _phone(num.get("phone") or "") or num.get("phone") or "",
            "upstream": upstream, "country": num.get("country") or "",
            "card_type": num.get("card_type") or ""}


def _allocate_sync(choice: str, count: int):
    info = providers.CHOICES.get(choice) or {}
    prov = providers.provider_for_choice(choice)
    out, errs = [], []
    for i in range(count):
        try:
            it = _alloc_one(choice, info, prov, i)
        except providers.ProviderBusy as e:
            errs.append({"phone": "", "why": str(e)[:160]})
            break                      # supply blocked: stop instead of burning retries
        except providers.ProviderError as e:
            errs.append({"phone": "", "why": str(e)[:160]})
            continue
        except Exception as e:
            errs.append({"phone": "", "why": (type(e).__name__ + ": " + str(e))[:160]})
            continue
        if not it["upstream"]:
            db.conn().execute("UPDATE numbers SET status='available', card_id=0 WHERE id=?",
                              (it["number_id"],))
            errs.append({"phone": it["phone"], "why": "上游未返回接码地址"})
            continue
        out.append(it)
    return out, errs


@router.post("/admin/api/relay/allocate")
async def relay_allocate(request: Request):
    s = _admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _csrf(request, s, body)
    choice = str(body.get("choice") or "us").strip().lower()
    if choice not in providers.CHOICES:
        return err("无效的号段")
    if choice not in providers.enabled_choices():
        return err("该号段暂未开放", 403)
    try:
        count = max(1, min(50, int(body.get("count") or 1)))
    except Exception:
        count = 1
    try:
        days = max(0, min(3650, int(body.get("expires_days") or 0)))
    except Exception:
        days = 0
    note = str(body.get("note") or "")[:120]
    kw = str(body.get("sms_keyword") or "").strip()[:40]
    pref = str(body.get("sms_prefix") or "").strip()[:40]
    items, bad = await run_in_threadpool(_allocate_sync, choice, count)
    if not items:
        why = bad[0]["why"] if bad else "上游暂无可用号码"
        return err("取号失败：" + why, 429 if "补货" in why or "余额" in why else 502, bad=bad)
    batch_no, rows = await run_in_threadpool(
        _create_sync, [{"phone": it["phone"], "upstream": it["upstream"],
                        "country": it["country"], "note": note,
                        "card_type": it.get("card_type") or "",
                        "sms_keyword": kw, "sms_prefix": pref} for it in items],
        note, days, s["username"])
    with db.tx() as c:
        for it in items:
            c.execute("UPDATE numbers SET status='relay', card_id=0, choice=? WHERE id=?",
                      (choice, it["number_id"]))
    security.audit("relay_allocate", "alert", ip=security.client_ip(request), actor=s["username"],
                   detail={"batch": batch_no, "choice": choice, "count": len(rows), "bad": len(bad)})
    return ok(batch=batch_no, count=len(rows), items=rows, lines=[r["line"] for r in rows],
              bad=bad, choice=choice)


# ---------------------------------------------------------------- admin: manage
@router.get("/admin/api/relay")
def relay_list(request: Request, status: str = "", q: str = "", page: int = 1, size: int = 50):
    _admin(request)
    where, args = [], []
    now = int(time.time())
    if status == "active":
        where.append("status='active' AND (expires_at=0 OR expires_at>?)")
        args.append(now)
    elif status == "disabled":
        where.append("status='disabled'")
    elif status == "expired":
        where.append("expires_at>0 AND expires_at<?")
        args.append(now)
    if q:
        where.append("(phone LIKE ? OR note LIKE ? OR token LIKE ?)")
        args += ["%" + q + "%"] * 3
    w = (" WHERE " + " AND ".join(where)) if where else ""
    page = max(1, page)
    size = max(1, min(size, 200))
    total = db.q1("SELECT COUNT(*) AS n FROM relay_cards" + w, tuple(args))["n"]
    rows = db.q("SELECT * FROM relay_cards" + w + " ORDER BY id DESC LIMIT ? OFFSET ?",
                tuple(args) + (size, (page - 1) * size))
    items = []
    for r in rows:
        up = security.dec(r["upstream_enc"])
        items.append({
            "id": r["id"], "phone": r["phone"], "country": r["country"],
            "token_prefix": r["token"][:8] + "…", "status": r["status"],
            "reads": r["reads"], "last_read_at": r["last_read_at"], "last_ip": r["last_ip"],
            "created_at": r["created_at"], "expires_at": r["expires_at"],
            "note": r["note"], "card_type": r["card_type"],
            "num_expire_at": (r["num_expire_at"] if "num_expire_at" in r.keys() else 0) or 0,
            "upstream_host": _upstream_label(up),
            "line": _line(r["phone"], r["token"]),
        })
    return {"ok": True, "total": total, "page": page, "items": items, "now": now}


@router.post("/admin/api/relay/{rid}/action")
async def relay_action(rid: int, request: Request):
    s = _admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _csrf(request, s, body)
    action = str(body.get("action") or "")
    row = db.q1("SELECT * FROM relay_cards WHERE id=?", (rid,))
    if not row:
        return err("记录不存在", 404)
    if action == "disable":
        db.conn().execute("UPDATE relay_cards SET status='disabled' WHERE id=?", (rid,))
    elif action == "enable":
        db.conn().execute("UPDATE relay_cards SET status='active' WHERE id=?", (rid,))
    elif action == "reset":
        new_token = None
        with db.tx() as c:
            new_token = _new_token(c)
            c.execute("UPDATE relay_cards SET token=? WHERE id=?", (new_token, rid))
        security.audit("relay_reset", "warn", ip=security.client_ip(request), actor=s["username"],
                       detail={"id": rid})
        return ok(line=_line(row["phone"], new_token), token=new_token)
    elif action == "delete":
        db.conn().execute("DELETE FROM relay_cards WHERE id=?", (rid,))
    else:
        return err("未知操作")
    security.audit("relay_" + action, "warn", ip=security.client_ip(request), actor=s["username"],
                   detail={"id": rid, "phone": row["phone"]})
    return ok()


@router.get("/admin/api/relay/batches")
def relay_batches(request: Request, limit: int = 20):
    """Recent batches that still have relay cards, so any batch can be re-exported."""
    _admin(request)
    limit = max(1, min(50, int(limit or 20)))
    rows = db.q(
        "SELECT b.id, b.batch_no, b.count, b.note, b.created_at, "
        "(SELECT COUNT(*) FROM relay_cards r WHERE r.batch_id=b.id) AS live "
        "FROM batches b WHERE EXISTS (SELECT 1 FROM relay_cards r2 WHERE r2.batch_id=b.id) "
        "ORDER BY b.id DESC LIMIT ?", (limit,))
    items = [{"id": r["id"], "batch_no": r["batch_no"], "count": r["count"], "live": r["live"],
              "note": r["note"], "created_at": r["created_at"]} for r in rows]
    return ok(items=items)


@router.get("/admin/api/relay/export")
def relay_export(request: Request, ids: str = "", batch: int = 0):
    _admin(request)
    where, args = [], []
    if batch:
        where.append("batch_id=?")
        args.append(int(batch))
    if ids:
        try:
            want = [int(x) for x in ids.split(",") if x.strip()]
        except Exception:
            want = []
        if want:
            where.append("id IN (%s)" % ",".join("?" * len(want)))
            args += want
    w = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.q("SELECT * FROM relay_cards" + w + " ORDER BY id", tuple(args))
    lines = [_line(r["phone"], r["token"]) for r in rows]
    return {"ok": True, "count": len(lines), "text": "\n".join(lines)}


_RELAY_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>接码 · __PHONE__</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(1200px 600px at 50% -10%,#12314a 0%,#070b14 60%);
color:#e8f1ff;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
display:flex;align-items:center;justify-content:center;padding:20px}
.card{width:100%;max-width:520px;background:rgba(14,24,40,.88);border:1px solid #1e3350;border-radius:18px;
padding:26px 24px;box-shadow:0 24px 60px rgba(0,0,0,.45)}
.brand{font-size:12px;letter-spacing:.16em;color:#6f8fb8;text-transform:uppercase}
.phone{font-size:23px;font-weight:600;margin:8px 0 2px;font-variant-numeric:tabular-nums}
.meta{font-size:12.5px;color:#8fa8c6;min-height:18px}
.code{margin:18px 0 6px;padding:18px;border-radius:14px;background:#0a1420;border:1px dashed #24405f;text-align:center;
min-height:92px;display:flex;align-items:center;justify-content:center}
.code b{font-size:40px;letter-spacing:.16em;color:#5ef0c4;font-variant-numeric:tabular-nums}
.wait{font-size:15px;color:#9fb4cf}
.msg{margin-top:8px;font-size:12.5px;color:#93a9c4;word-break:break-all;white-space:pre-wrap}
.warn{margin-top:14px;padding:10px 12px;border-radius:10px;background:#3a2a12;border:1px solid #6b4a1c;color:#ffcf8a;
font-size:12.5px;display:none}
.st{margin-top:14px;font-size:12.5px;display:flex;gap:9px;align-items:center;color:#8fa8c6}
.dot{width:8px;height:8px;border-radius:50%;background:#5ef0c4;box-shadow:0 0 0 4px rgba(94,240,196,.13)}
button{margin-top:16px;width:100%;padding:12px;border-radius:12px;border:1px solid #2a4a2f;background:#123a2a;
color:#7bf0c0;font-size:14px;cursor:pointer}
</style></head><body data-token="__TOKEN__" data-exp="__EXPIRE__" data-meta="__META__">
<div class="card">
  <div class="brand">SMS GATE · 接码</div>
  <div class="phone">__PHONE__</div>
  <div class="meta" id="meta">__META__</div>
  <div class="code" id="box"><span class="wait">等待短信到达…</span></div>
  <div class="msg" id="text"></div>
  <div class="warn" id="warn">__WARN__</div>
  <div class="st"><span class="dot"></span><span id="state">每 15 秒自动刷新</span></div>
  <button id="cp" type="button">复制验证码</button>
</div>
<script src="/smsrecord.js?v=1"></script>
</body></html>"""

# 页面脚本走同源外链：站点 CSP 是 script-src 'self'，内联脚本会被浏览器拦截。
_RELAY_JS = """(function(){
  var body = document.body;
  var token = body.dataset.token || "";
  var exp = Number(body.dataset.exp || 0);
  var baseMeta = body.dataset.meta || "";
  var lastCode = "";
  function pad(n){ return ("0" + n).slice(-2); }
  function fmt(s){ s = String(s || ""); return s.indexOf("1|") === 0 ? s.slice(2) : s; }
  function tick(){
    var el = document.getElementById("meta"), w = document.getElementById("warn");
    if(!el) return;
    if(!exp){ el.textContent = baseMeta; return; }
    var left = exp - Math.floor(Date.now() / 1000);
    if(left > 0){
      el.textContent = "预估有效期剩余 " + Math.floor(left / 60) + " 分 " + pad(left % 60) + " 秒";
      if(w) w.style.display = "none";
    } else {
      el.textContent = "预估有效期已过";
      if(w) w.style.display = "block";
    }
  }
  function render(d){
    var box = document.getElementById("box"), tx = document.getElementById("text"), st = document.getElementById("state");
    if(!box) return;
    var code = (d && d.code) || "";
    if(code){
      box.innerHTML = "<b>" + code + "</b>";
      lastCode = code;
      if(tx) tx.textContent = fmt(d.text);
      if(st) st.textContent = "已收到短信";
    } else if(!lastCode){
      box.innerHTML = '<span class="wait">等待短信到达…</span>';
      if(tx && d && d.text && d.text !== "0|0"){ tx.textContent = fmt(d.text); }
    }
  }
  function poll(){
    var url = location.pathname + "?token=" + encodeURIComponent(token) + "&fmt=json&_=" + Date.now();
    fetch(url, {headers: {"Accept": "application/json"}, cache: "no-store"})
      .then(function(r){ return r.json(); })
      .then(render).catch(function(){});
  }
  var cp = document.getElementById("cp");
  if(cp) cp.addEventListener("click", function(){
    if(!lastCode) return;
    if(navigator.clipboard){ navigator.clipboard.writeText(lastCode); return; }
    var t = document.createElement("textarea");
    t.value = lastCode; document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); } catch(e) {}
    t.remove();
  });
  tick(); setInterval(tick, 1000); poll(); setInterval(poll, 15000);
})();
"""


@router.get("/smsrecord.js")
def relay_js():
    return PlainTextResponse(_RELAY_JS, media_type="application/javascript",
                             headers={"Cache-Control": "public, max-age=300",
                                      "X-Content-Type-Options": "nosniff"})


def _wants_html(request) -> bool:
    """浏览器直接打开链接时给一个看不到 0|0 的页面；脚本/工具照旧拿纯文本。"""
    if (request.query_params.get("fmt") or "").strip():
        return False
    acc = (request.headers.get("accept") or "").lower()
    return "text/html" in acc


def _relay_page(phone: str, token: str, expire_at: int, meta: str, warn: str) -> str:
    import html as _h
    return (_RELAY_PAGE
            .replace("__PHONE__", _h.escape(phone or ""))
            .replace("__TOKEN__", _h.escape(token or ""))
            .replace("__META__", _h.escape(meta or ""))
            .replace("__WARN__", _h.escape(warn or ""))
            .replace("__EXPIRE__", str(int(expire_at or 0))))

# ---------------------------------------------------------------- public relay
@router.options(PUBLIC_PATH)
def relay_options():
    return Response(status_code=204, headers=CORS)


@router.get(PUBLIC_PATH)
async def relay_sms(request: Request, token: str = "", fmt: str = "", code: str = ""):
    if (db.get_setting("relay_on", "1") or "1") != "1":
        return PlainTextResponse("error|relay_disabled", status_code=503, headers=CORS)
    token = (token or "").strip()
    ip = security.client_ip(request)
    # Token spray / enumeration guard: cap invalid-token lookups per IP across all workers.
    try:
        badlimit = max(1, int(db.get_setting("relay_bad_rate", "300") or 300))
    except Exception:
        badlimit = 300
    def _bad_token():
        # Only malformed / unknown tokens consume this bucket, so busy customers
        # with many valid cards are never throttled by it.
        allowed, retry = security.hit_shared("relaybad:%s" % ip, badlimit, 60)
        if not allowed:
            return PlainTextResponse("error|rate_limited", status_code=429,
                                     headers={**CORS, "Retry-After": str(retry)})
        return PlainTextResponse("error|bad_token", status_code=404, headers=CORS)

    if not re.fullmatch(r"[0-9a-fA-F]{24,64}", token):
        return _bad_token()
    row = db.q1("SELECT * FROM relay_cards WHERE token=?", (token,))
    if not row:
        return _bad_token()
    now = int(time.time())
    if row["status"] != "active":
        return PlainTextResponse("error|disabled", status_code=403, headers=CORS)
    if row["expires_at"] and row["expires_at"] < now:
        return PlainTextResponse("error|expired", status_code=410, headers=CORS)

    try:
        limit = max(1, int(db.get_setting("relay_rate", "60") or 60))
        iplimit = max(1, int(db.get_setting("relay_ip_rate", "600") or 600))
    except Exception:
        limit, iplimit = 60, 600
    allowed, retry = security.hit_shared("relay:%d" % row["id"], limit, 60)
    if not allowed:
        return PlainTextResponse("error|rate_limited", status_code=429,
                                 headers={**CORS, "Retry-After": str(retry)})
    allowed, retry = security.hit_shared("relayip:%s" % ip, iplimit, 60)
    if not allowed:
        return PlainTextResponse("error|rate_limited", status_code=429,
                                 headers={**CORS, "Retry-After": str(retry)})

    upstream = security.dec(row["upstream_enc"])
    if not upstream:
        return PlainTextResponse("error|relay_broken", status_code=500, headers=CORS)
    kw = (row["sms_keyword"] if "sms_keyword" in row.keys() else "") or ""
    pref = (row["sms_prefix"] if "sms_prefix" in row.keys() else "") or ""
    meta = {}
    good, why, text = await run_in_threadpool(_fetch, upstream, 15, kw, meta)
    if good and (pref or kw) and upstream.startswith(("d1jiema:", "nexreg:")):
        # 只把属于该平台的短信交给买家：不匹配前缀且不含关键词 → 当作还没收到
        try:
            from .providers import _nex_prefix_ok
            t = (text or "").strip()
            body = t.split("|", 1)[1].strip() if ("|" in t and t.split("|", 1)[0].isdigit()) else t
            if t and t != "0|0":
                # 前缀与关键词是「或」的关系；只填关键词时，关键词就是唯一门槛。
                hit_pref = bool(pref) and _nex_prefix_ok(body, pref)
                hit_kw = bool(kw) and (kw in t)
                if not hit_pref and not hit_kw:
                    text = "0|0"
        except Exception:
            pass
    exp = int(meta.get("expire_at") or 0)
    if good and exp and 60 < exp - now <= 3 * 86400:
        # 上游只在短信到达后才给 expiresTime；那一刻起用真实过期时间校准展示
        try:
            db.conn().execute("UPDATE relay_cards SET num_expire_at=? WHERE id=?", (exp, row["id"]))
            from .providers import regular_note_ttl
            created = int(row["created_at"] or now)
            regular_note_ttl(row["country"] or "", max(1.0, (exp - created) / 60.0))
        except Exception:
            pass
    if not good:
        security.audit("relay_error", "warn", ip=ip, detail={"id": row["id"], "why": why})
        return PlainTextResponse("error|" + why, status_code=502, headers=CORS)

    db.conn().execute("UPDATE relay_cards SET reads=reads+1, last_read_at=?, last_ip=? WHERE id=?",
                      (now, ip, row["id"]))
    num_exp = int(row["num_expire_at"] or 0) if "num_expire_at" in row.keys() else 0
    expired = bool(num_exp and num_exp < now)
    xh = {**CORS, "X-Card-Expires": str(num_exp), "X-Card-Expired": "1" if expired else "0"}
    if fmt == "json":
        return JSONResponse({"ok": True, "phone": row["phone"], "code": _extract_code(text),
                             "text": text.strip(), "at": now,
                             "expire_at": num_exp, "expired": expired}, headers=xh)
    if code in ("1", "true", "yes"):
        return PlainTextResponse(_extract_code(text), headers=xh)
    if _wants_html(request):
        meta = "\u9884\u4f30\u6709\u6548\u671f\u672a\u77e5" if not num_exp else ""
        warn = ("\u9884\u4f30\u6709\u6548\u671f\u5df2\u8fc7\uff0c\u4e0a\u6e38\u53ef\u80fd\u5df2\u56de\u6536\u8be5\u53f7\u7801\uff1b"
                "\u5982\u679c\u4e00\u76f4\u6536\u4e0d\u5230\uff0c\u8bf7\u91cd\u65b0\u8d2d\u4e70\u3002")
        return HTMLResponse(_relay_page(row["phone"], token, num_exp, meta, warn), headers=xh)
    return PlainTextResponse(text, headers=xh)