# -*- coding: utf-8 -*-
"""Local preview of the queue UI against a mock NexSMS upstream."""
import os, sys, threading, time, json, shutil, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "testdata_uiqueue")
if os.path.isdir(DATA):
    shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = DATA
os.environ["SMSGATE_PORT"] = "18930"
sys.path.insert(0, BASE)

MOCK = "http://127.0.0.1:18931"
STATE = {"seq": 0, "lock": threading.Lock()}
CATS = {"code": 0, "message": "success", "data": {
    "list": [{"id": 4, "name": "美国"}, {"id": 5, "name": "美国实卡"}],
    "types": [{"id": 1, "name": "首次卡"}]}}
LIST = {"code": 0, "data": {"total": 1, "list": [
    {"id": 72, "type": 1, "cateId": 4, "name": "豆包", "price": 0.068, "quantity": 100000,
     "successInfo": {"successRate": 99.0}}]}}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _send(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/premium/categories"):
            return self._send(CATS)
        if self.path.startswith("/api/premium/list"):
            return self._send(LIST)
        if self.path.startswith("/api/balance"):
            return self._send({"code": 0, "data": {"balance": 8.88}})
        if self.path.startswith("/api/number-access/sms-records"):
            return self._send({"code": 0, "data": {"records": []}})
        return self._send({"code": 404}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if self.path.startswith("/api/premium/purchase"):
            qty = int(body.get("quantity") or 1)
            time.sleep(0.45)
            items = []
            with STATE["lock"]:
                for _ in range(qty):
                    STATE["seq"] += 1
                    s = STATE["seq"]
                    items.append({"tel": "1202555%04d" % (s % 10000), "appName": "豆包", "status": 1,
                                  "smsAccess": {"accessId": "acc%d" % s, "token": "tok%d" % s,
                                                "url": MOCK + "/api/number-access/sms-records?token=tok%d" % s}})
            return self._send({"code": 0, "message": "success", "data": items})
        return self._send({"code": 404}, 404)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 18931), H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

import uvicorn
from app.main import app
from app import db, providers

db.init()
for k, v in {"provider": "nexsms_premium", "nexsms_key": "k", "nexsms_base": MOCK,
             "nexsms_project": "豆包", "nexsms_sms_prefix": "[豆包]", "nexsms_type": "1",
             "nexsms_expiry": "0", "nexsms_prefix": "", "nexsms_app_id": "",
             "nexsms_batch_qty": "5", "nexsms_stock_target": "0",
             "nexsms_queue_concurrency": "2", "nexsms_queue_max": "200",
             "nexsms_queue_timeout_sec": "600", "nexsms_cooldown_sec": "30",
             "nexsms_cooldown_until": "0", "allow_countries": "us", "max_numbers_per_card": "3",
             "site_url": "http://127.0.0.1:18930"}.items():
    db.set_setting(k, v)

keys = []
with db.tx() as c:
    for i in range(6):
        key = "PREVIEW-QUEUE-%04d" % i
        kh = hashlib.sha256(("sg:" + key).encode()).hexdigest()
        c.execute("DELETE FROM cards WHERE key_hash=?", (kh,))
        c.execute("INSERT INTO cards(key_hash,key_prefix,note,max_codes,status,batch_id,created_at,created_by) "
                  "VALUES(?,?,?,?, 'active', 0, ?, 'preview')", (kh, key[:9], "preview", 1, int(time.time())))
        keys.append(key)

cfg = uvicorn.Config(app, host="127.0.0.1", port=18930, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.1)

with db.tx() as c:
    c.execute("UPDATE numbers SET status='used' WHERE provider='nexsms_premium' AND status='available'")
db.set_setting("nexsms_cooldown_until", str(int(time.time()) + 120))
print("pool drained", providers._nex_available_count("us"))

from playwright.sync_api import sync_playwright
SHOTS = os.path.join(BASE, "shots")
os.makedirs(SHOTS, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pages = []
    for key in keys:
        pg = b.new_page(viewport={"width": 430, "height": 1100}, device_scale_factor=2)
        pg.goto("http://127.0.0.1:18930/c/" + key, wait_until="networkidle")
        pg.wait_for_timeout(600)
        if pg.is_visible("#btnAuth"):
            pg.fill("#keyInput", key)
            pg.click("#btnAuth")
        pg.wait_for_selector("#btnNumber:not([disabled])", timeout=20000)
        pg.click("button.country[data-code=us]")
        pages.append(pg)
    for pg in pages:
        pg.click("#btnNumber")
    hero = pages[4]
    hero.wait_for_selector(".queue-box", timeout=8000)
    time.sleep(1.2)
    hero.wait_for_timeout(300)
    hero.screenshot(path=os.path.join(SHOTS, "queue_waiting.png"), full_page=True)
    print("QUEUE UI:", hero.inner_text(".queue-box").replace("\n", " | "))
    providers.cooldown_clear()
    hero.wait_for_selector("#numValue", timeout=40000)
    hero.wait_for_timeout(400)
    hero.screenshot(path=os.path.join(SHOTS, "queue_ready.png"), full_page=True)
    print("NUMBER:", hero.inner_text("#numValue"))
    b.close()
print("PREVIEW OK")
