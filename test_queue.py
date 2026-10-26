# -*- coding: utf-8 -*-
"""Concurrency / burst tests for the NexSMS premium pool."""
import os, sys, threading, time, json, shutil, hashlib, statistics
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_queue")
if os.path.isdir(TESTDATA):
    shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18920"
sys.path.insert(0, BASE)

MOCK = "http://127.0.0.1:18921"
STATE = {"seq": 0, "purchases": [], "access_calls": 0, "lock": threading.Lock(), "purchase_delay": 0.25, "force_429": False}

CATS = {"code": 0, "message": "success", "data": {
    "list": [{"id": 4, "name": "美国"}],
    "types": [{"id": 1, "name": "首次卡"}],
}}


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
        if u.path == "/api/premium/categories":
            return self._send(CATS)
        if u.path == "/api/premium/list":
            return self._send({"code": 0, "data": {"total": 1, "list": [
                {"id": 72, "type": 1, "cateId": 4, "name": "豆包", "price": 0.068, "quantity": 100000,
                 "successInfo": {"successRate": 99.0}}]}})
        if u.path == "/api/balance":
            return self._send({"code": 0, "data": {"balance": 100}})
        if u.path == "/api/number-access/sms-records":
            with STATE["lock"]:
                STATE["access_calls"] += 1
                n = STATE["access_calls"]
            time.sleep(0.05)
            recs = [] if n % 2 else [{"sender": "豆包", "content": "[豆包] 验证码 778899，5 分钟内有效。"}]
            return self._send({"code": 0, "data": {"records": recs, "expiresAt": "2026-12-31 00:00:00"}})
        return self._send({"code": 404, "message": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if u.path == "/api/premium/purchase":
            if STATE.get("force_429"):
                return self._send({"code": 429, "message": "too many requests"}, 429)
            qty = int(body.get("quantity") or 1)
            time.sleep(STATE["purchase_delay"])
            items = []
            with STATE["lock"]:
                STATE["purchases"].append({"qty": qty, "appId": body.get("appId"), "t": time.time()})
                for _ in range(qty):
                    STATE["seq"] += 1
                    seq = STATE["seq"]
                    items.append({
                        "tel": "1202555%04d" % (seq % 10000),
                        "appName": "豆包", "status": 1,
                        "smsAccess": {"accessId": "acc%d" % seq, "token": "tok%d" % seq,
                                      "url": MOCK + "/api/number-access/sms-records?token=tok%d" % seq},
                    })
            return self._send({"code": 0, "message": "success", "data": items})
        return self._send({"code": 404, "message": "not found"}, 404)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 18921), Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()


import uvicorn
from app.main import app
from app import db, nexq, providers

db.init()
for k, v in {
    "provider": "nexsms_premium", "nexsms_key": "k", "nexsms_base": MOCK,
    "nexsms_project": "豆包", "nexsms_sms_prefix": "[豆包]", "nexsms_type": "1",
    "nexsms_expiry": "0", "nexsms_prefix": "", "nexsms_app_id": "",
    "nexsms_batch_qty": "5", "nexsms_stock_target": "5", "nexsms_buy_wait_sec": "20",
    "nexsms_queue_concurrency": "5", "nexsms_queue_max": "200",
    "nexsms_queue_timeout_sec": "120", "nexsms_cooldown_sec": "10",
    "nexsms_cooldown_until": "0", "nexsms_429_streak": "0",
    "allow_countries": "us", "max_numbers_per_card": "3", "site_url": "https://12456.cfd",
}.items():
    db.set_setting(k, v)

cfg = uvicorn.Config(app, host="127.0.0.1", port=18920, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.1)

import requests, hashlib
B = "http://127.0.0.1:18920"
results = []

def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)[:400]) if extra else ""))

# 20 cards
keys = []
now = int(time.time())
with db.tx() as c:
    for i in range(20):
        key = "QUEUE-%04d-TEST-%04d" % (i, i)
        kh = hashlib.sha256(("sg:" + key).encode()).hexdigest()
        c.execute("INSERT INTO cards(key_hash,key_prefix,note,max_codes,status,batch_id,created_at,created_by) "
                  "VALUES(?,?,?,?, 'active', 0, ?, 'qtest')", (kh, key[:9], "qtest", 1, now))
        keys.append(key)

def open_card(i):
    h = {"X-Forwarded-For": "10.7.%d.%d" % (i // 200, (i % 200) + 1)}
    s = requests.Session()
    d = s.post(B + "/api/redeem", json={"key": keys[i]}, headers=h, timeout=30).json()
    return s, d, h

claims = []
lat0 = time.time()
for i in range(20):
    s, d, h = open_card(i)
    t1 = time.time()
    r = s.post(B + "/api/number", json={"country": "us", "csrf": d["csrf"]},
               headers={"X-CSRF": d["csrf"], **h}, timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {}
    claims.append((i, r.status_code, round(time.time() - t1, 2), body))
elapsed = time.time() - lat0

bad = [c for c in claims if c[1] != 200 or not (c[3].get("number") or {}).get("phone")]
check("all 20 allocated without queue", not bad, [(c[0], c[1], c[3].get("error")) for c in bad][:3])
check("no queued response", all(not c[3].get("queued") for c in claims))
phones = [(c[3].get("number") or {}).get("phone") for c in claims]
check("numbers are unique", len(set(phones)) == len(phones), len(set(phones)))
check("20 claims under 30s", elapsed < 30, round(elapsed, 2))
slow = sorted(c[2] for c in claims)[-1]
check("slowest claim under 10s", slow < 10, slow)
qty = [p["qty"] for p in STATE["purchases"]]
check("purchases batched", len(STATE["purchases"]) <= 8, {"calls": len(STATE["purchases"]), "qty": qty})
check("one purchase serves several", max(qty or [0]) >= 5, qty)

sms = db.q1("SELECT COUNT(*) AS n FROM sms_records WHERE card_id>0")
check("sms records created", sms and int(sms["n"]) >= 20, dict(sms) if sms else None)

# cooldown behaviour: force 429, handler must surface a retry hint (no requeue)
STATE["force_429"] = True
db.set_setting("nexsms_cooldown_until", "0")
db.set_setting("nexsms_429_streak", "0")
try:
    providers._nex_purchase("us", 2)
    check("429 raises ProviderBusy", False, "no exception")
except providers.ProviderBusy as e:
    check("429 raises ProviderBusy", True, str(e)[:80])
check("cooldown armed", providers.cooldown_left() > 0, providers.cooldown_left())
STATE["force_429"] = False
providers.cooldown_clear()
check("cooldown cleared", providers.cooldown_left() == 0, providers.cooldown_left())

check("queue table stays empty", nexq.stats()["waiting"] == 0 and nexq.stats()["serving"] == 0, nexq.stats())

print("\n=== %d/%d passed ===" % (sum(1 for r in results if r[1]), len(results)))
failed = [r[0] for r in results if not r[1]]
if failed:
    print("FAILED:", failed)
    raise SystemExit(1)
