# -*- coding: utf-8 -*-
import os, sys, threading, time, re, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_relayui")
shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18997"
os.environ["SMSGATE_RELAY_ALLOW_PRIVATE"] = "1"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"C:\Users\Administrator\AppData\Local\ms-playwright")
sys.path.insert(0, BASE)

import uvicorn, requests
from app.main import app
from app import db, security

MOCK_PORT = 18998


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("1|【豆包】验证码 654321".encode())


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), H).serve_forever, daemon=True).start()
db.init()
db.set_setting("relay_base", "https://api.12456.cfd")
cfg = uvicorn.Config(app, host="127.0.0.1", port=18997, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started: break
    time.sleep(0.1)

B = "http://127.0.0.1:18997"
adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})

from playwright.sync_api import sync_playwright
results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)[:260]) if extra else ""))

with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 1440, "height": 1100})
    for ck in adm.cookies:
        ctx.add_cookies([{"name": ck.name, "value": ck.value, "domain": "127.0.0.1", "path": "/"}])
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(B + "/admin#/relay", wait_until="load")
    pg.wait_for_selector("#relayForm", timeout=10000)
    check("nav relay active", pg.is_visible("a[data-route='relay']"))
    check("relay hint uses own domain", "api.12456.cfd" in pg.inner_text("#relayForm"), pg.inner_text("#relayForm")[:120])
    ph = pg.get_attribute("#relayForm textarea[name=lines]", "placeholder") or ""
    check("placeholder is generic", "xxxx" not in ph and "例如" in ph, ph)
    pg.click("#relayForm button[type=submit]")
    pg.wait_for_timeout(500)
    check("empty submit blocked", pg.locator("#modal:not(.hidden)").count() == 0)
    check("empty toast", "空的" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.fill("#relayForm textarea[name=lines]",
            "卡号:+17798001455----http://127.0.0.1:%d/sms?token=ok\n+447700900123----http://127.0.0.1:%d/sms?token=ok" % (MOCK_PORT, MOCK_PORT))
    pg.fill("#relayForm input[name=note]", "ui-test")
    pg.click("#relayForm button[type=submit]")
    pg.wait_for_selector("#allLinks", timeout=15000)
    txt = pg.input_value("#allLinks")
    check("modal lines 2", txt.count("卡号:+") == 2, txt[:120])
    check("modal hides upstream", "127.0.0.1" not in txt, txt[:120])
    check("modal own domain", "https://api.12456.cfd/smsrecord?token=" in txt, txt[:120])
    pg.click("#modalClose")
    pg.wait_for_timeout(600)
    rows = pg.locator("#relayTable tbody tr").count()
    check("table rows >= 2", rows >= 2, rows)
    check("table shows upstream host (admin)", "127.0.0.1" in pg.inner_text("#relayTable"), pg.inner_text("#relayTable")[:160])
    pg.screenshot(path=r"C:\Users\Administrator\Documents\Codex\2026-10-24\root-ucpwuogj5963-22\outputs\relay-admin.png", full_page=True)
    check("no page errors", not errs, errs)
    br.close()

fails = [x for x in results if not x[1]]
print("\nSUMMARY %d/%d" % (len(results) - len(fails), len(results)))
if fails:
    print("FAILED:", [x[0] for x in fails]); sys.exit(1)