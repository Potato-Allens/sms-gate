import os, sys, threading, time, re, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "uidata_exit")
if os.path.isdir(TESTDATA):
    shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
sys.path.insert(0, BASE)

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"1|864209"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

threading.Thread(target=HTTPServer(("127.0.0.1", 18762), H).serve_forever, daemon=True).start()

import uvicorn
from app.main import app
from app import security, db
db.init()
cfg = uvicorn.Config(app, host="127.0.0.1", port=18761, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(80):
    if server.started: break
    time.sleep(0.1)

import requests
B = "http://127.0.0.1:18761"
adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})
csrf = adm.get(B + "/admin/api/me").json()["csrf"]
d = adm.post(B + "/admin/api/cards/create", json={"count": 1, "max_codes": 1, "note": "ui-exit"}, headers={"X-CSRF": csrf}).json()
key = d["keys"][0]
adm.post(B + "/admin/api/numbers/import",
         json={"country": "us", "lines": "+1 2025550188----http://127.0.0.1:18762/sms?token=uiexit", "note": "ui-exit"},
         headers={"X-CSRF": csrf})

from playwright.sync_api import sync_playwright
shots = os.path.join(BASE, "shots")
os.makedirs(shots, exist_ok=True)
errors = []
with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 430, "height": 900}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.on("dialog", lambda d: d.accept())
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    bad = []
    pg.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
    pg.goto(B + "/c/" + key)
    pg.wait_for_timeout(1200)
    pg.wait_for_selector('#countryGrid button.country[data-code="us"]', timeout=10000)
    pg.click('#countryGrid button.country[data-code="us"]')
    pg.wait_for_timeout(900)
    pg.click("#btnNumber")
    pg.wait_for_timeout(1200)
    pg.wait_for_selector("#btnExitNow", timeout=25000)
    pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(shots, "exit-countdown.png"), full_page=True)
    state = pg.inner_text("#pollState")
    btn = pg.inner_text("#btnExitNow")
    code = pg.inner_text("#codeBox") if pg.query_selector("#codeBox") else ""
    print("STATE:", state)
    print("BUTTON:", btn)
    print("CODE:", code.replace("\n", ""))
    pg.wait_for_timeout(9500)
    print("URL after countdown:", pg.url)
    print("console errors:", errors[:5])
    print("bad responses:", bad[:8])
    pg.screenshot(path=os.path.join(shots, "exit-home.png"), full_page=True)
    br.close()

assert "秒后退出" in state or "已核销" in state, state
assert "立即退出" in btn, btn
assert "864209" in code.replace("\n", ""), code
assert pg.url.rstrip("/") == B, pg.url
print("LANDING AFTER EXIT OK")
print("UI EXIT OK")
