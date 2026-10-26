# -*- coding: utf-8 -*-
"""NexSMS premium provider tests: 豆包 lookup + [豆包] prefix filter + full code flow."""
import os, sys, threading, time, json, shutil, hashlib, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_nex")
if os.path.isdir(TESTDATA):
    shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18896"
sys.path.insert(0, BASE)

MOCK = "http://127.0.0.1:18897"
STATE = {"stage": 0, "purchase": [], "access_calls": 0}

CATS = {"code": 0, "message": "success", "data": {
    "list": [{"id": 4, "name": "美国"}, {"id": 9, "name": "英国"}, {"id": 21, "name": "香港"}],
    "types": [{"id": 1, "name": "首次卡"}, {"id": 2, "name": "重启卡"}, {"id": 3, "name": "续费卡"}],
}}


def app_list(cate_id, qty, ptype=1):
    return {"code": 0, "message": "success", "data": {"total": 1, "list": [
        {"id": 72 if cate_id == 4 else (108 if cate_id == 9 else 233),
         "type": ptype, "cateId": cate_id, "name": "豆包", "price": 0.35, "quantity": qty},
    ]}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _send(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if q.get("apiKey", [""])[0] != "test1234567890abcdef" and not u.path.startswith("/api/number-access"):
            return self._send({"code": 401, "message": "API密钥无效"}, 401)
        if u.path == "/api/balance":
            return self._send({"code": 0, "data": {"balance": 12.34}})
        if u.path == "/api/premium/categories":
            return self._send(CATS)
        if u.path == "/api/premium/list":
            cid = int(q.get("cateId", ["0"])[0])
            ptype = int(q.get("type", ["1"])[0])
            qty = {4: 120, 9: 7, 21: (0 if ptype == 1 else 50)}.get(cid, 0)
            return self._send(app_list(cid, qty, ptype))
        if u.path == "/api/number-access/sms-records":
            STATE["access_calls"] += 1
            if STATE["stage"] == 0:
                recs = [{"sender": "淘宝", "content": "[淘宝] 验证码 999999", "code": "999999",
                         "receivedAt": "2026-10-24T10:00:00Z"}]
            else:
                recs = [{"sender": "豆包", "content": "[豆包] 您的验证码为 246810，5 分钟内有效。",
                         "receivedAt": "2026-10-24T10:01:00Z"}]
            return self._send({"code": 0, "message": "success", "data": {"records": recs}})
        self._send({"code": 404, "message": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        if q.get("apiKey", [""])[0] != "test1234567890abcdef":
            return self._send({"code": 401, "message": "API密钥无效"}, 401)
        if u.path == "/api/premium/purchase":
            STATE["purchase"].append(body)
            cate = {72: 4, 108: 9, 233: 21}.get(int(body.get("appId") or 0), 4)
            tel = "12025550199" if cate == 4 else "447700900123"
            return self._send({"code": 0, "message": "success", "data": [{
                "tel": tel, "endTime": "2026-11-20 10:00:00", "smsCount": 0, "voiceCount": 0,
                "status": 1, "cateId": cate, "type": 1, "appName": "豆包",
                "smsAccess": {"accessId": "acc-%d" % len(STATE["purchase"]), "token": "tok1",
                              "url": MOCK + "/api/number-access/sms-records?token=tok1"},
            }]})
        self._send({"code": 404, "message": "not found"}, 404)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 18897), Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

import uvicorn
from app.main import app
from app import db, providers

db.init()
for k, v in {
    "provider": "nexsms_premium",
    "nexsms_key": "test1234567890abcdef",
    "nexsms_base": MOCK,
    "nexsms_project": "豆包",
    "nexsms_sms_prefix": "[豆包]",
    "nexsms_type": "1",
    "nexsms_expiry": "0",
    "nexsms_prefix": "",
    "nexsms_app_id": "",
    "allow_countries": "us,gb,hk",
    "site_url": "https://12456.cfd",
}.items():
    db.set_setting(k, v)

cfg = uvicorn.Config(app, host="127.0.0.1", port=18896, log_level="warning")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(50):
    if server.started:
        break
    time.sleep(0.1)

import requests
B = "http://127.0.0.1:18896"
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if extra else ""))


def j(r):
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:300], "_status": r.status_code}


# ---------------- prefix matcher
check("prefix [豆包] hit", providers._nex_prefix_ok("[豆包] 验证码 123456", "[豆包]"))
check("prefix 【豆包】 hit", providers._nex_prefix_ok("【豆包】验证码 123456", "[豆包]"))
check("prefix BOM/space tolerant", providers._nex_prefix_ok("\ufeff  [豆包] 验证码 123456", "[豆包]"))
check("prefix 淘宝 rejected", not providers._nex_prefix_ok("[淘宝] 验证码 123456", "[豆包]"))
check("prefix 豆包X rejected", not providers._nex_prefix_ok("[豆包X] 验证码 123456", "[豆包]"))

# ---------------- app lookup & provider test
appinfo = providers._nex_find_app("us")
check("find 豆包 app us", appinfo.get("appId") == 72 and appinfo.get("type") == 1, appinfo)
gb = providers._nex_find_app("gb")
check("find 豆包 app gb prefers UK cate", gb.get("appId") == 108, gb)
hk = providers._nex_find_app("hk")
check("find 豆包 app hk falls back to type 2", hk.get("appId") == 233 and hk.get("type") == 2 and hk.get("quantity") == 50, hk)
tp = providers.test_provider()
check("provider test ok", tp.get("ok") and "appId=72" in tp.get("message", ""), tp.get("message"))

# ---------------- card + HTTP flow
key = "NEXS-TEST-0001-0001"
kh = hashlib.sha256(("sg:" + key).encode()).hexdigest()
now = int(time.time())
with db.tx() as c:
    c.execute("INSERT INTO cards(key_hash,key_prefix,note,max_codes,status,batch_id,created_at,created_by) "
              "VALUES(?,?,?,?, 'active', 0, ?, 'test')", (kh, key[:9], "nexsms", 1, now))

pub = requests.Session()
d = j(pub.post(B + "/api/redeem", json={"key": key}))
check("redeem ok", d.get("ok"), d.get("error"))
sess = j(pub.get(B + "/api/session"))
csrf = sess.get("csrf", "")
check("session countries", [c["code"] for c in sess.get("countries", [])] == ["us", "gb", "hk"], sess.get("countries"))

r = pub.post(B + "/api/number", json={"country": "us", "csrf": csrf}, headers={"X-CSRF": csrf})
d = j(r)
if d.get("queued"):
    tick = d["ticket"]
    deadline = time.time() + 20
    while time.time() < deadline:
        s2 = j(pub.post(B + "/api/queue/status",
                        json={"ticket": tick["id"], "token": tick["token"], "csrf": csrf},
                        headers={"X-CSRF": csrf}))
        if s2.get("number") or s2.get("state") in ("error", "expired"):
            d = s2
            break
        time.sleep(0.3)
phone = str((d.get("number") or {}).get("phone") or "")
check("take number from nexsms", d.get("ok") and phone.startswith("+1202555"), d)
check("purchase payload", STATE["purchase"] and STATE["purchase"][0].get("appId") == 72
      and int(STATE["purchase"][0].get("quantity") or 0) >= 1, STATE["purchase"][:1])
row = db.q1("SELECT * FROM numbers ORDER BY id DESC LIMIT 1")
check("number row provider", row and row["provider"] == "nexsms_premium" and row["sms_url"], dict(row) if row else None)
check("number row country us", row and row["country"] == "us", row["country"] if row else None)

# stage 0: only 淘宝 sms -> no code
res = providers.poll(dict(row))
check("non-豆包 sms ignored", res.get("ok") and not res.get("code") and "均不以" in (res.get("raw") or ""), res)

# stage 1: [豆包] sms -> code parsed through API poll
STATE["stage"] = 1
providers._NEX_POLL_CACHE.clear()  # simulate the 3s poll cache window passing
r = pub.post(B + "/api/poll", json={"csrf": csrf}, headers={"X-CSRF": csrf})
d = j(r)
check("poll returns 豆包 code", d.get("state") == "code" and d.get("code") == "246810", d)
rec = db.q1("SELECT * FROM sms_records ORDER BY id DESC LIMIT 1")
check("sms_record stored", rec and rec["code"] == "246810" and rec["status"] == "received", dict(rec) if rec else None)
card = db.q1("SELECT * FROM cards WHERE key_prefix=?", (key[:9],))
check("card consumed once", card["used_codes"] == 1, card["used_codes"])
r = requests.post(B + "/api/redeem", json={"key": key})
check("card cannot redeem twice", r.status_code == 409, r.status_code)

# ---------------- admin settings surface
check("db default sms prefix", db.get_setting("nexsms_sms_prefix") == "[豆包]")

failed = [x for x in results if not x[1]]
print("\n=== %d/%d passed ===" % (len(results) - len(failed), len(results)))
if failed:
    print("FAILED:", [x[0] for x in failed])
srv.shutdown()
sys.stdout.flush()
os._exit(1 if failed else 0)