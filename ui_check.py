import os, sys, threading, time, re, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "uidata")
if os.path.isdir(TESTDATA):
    shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
sys.path.insert(0, BASE)

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"1|864209"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

httpd = HTTPServer(("127.0.0.1", 18896), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

import uvicorn
from app.main import app
from app import security, db
db.init()
cfg = uvicorn.Config(app, host="127.0.0.1", port=18897, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(80):
    if server.started: break
    time.sleep(0.1)

import requests
B = "http://127.0.0.1:18897"
adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})
me = adm.get(B + "/admin/api/me").json()
csrf = me["csrf"]
adm.post(B + "/admin/api/settings", json={"site_url": "https://12456.cfd", "site_name": "SMS Gate"}, headers={"X-CSRF": csrf})
d = adm.post(B + "/admin/api/cards/create", json={"count": 6, "note": "娓犻亾 A"}, headers={"X-CSRF": csrf}).json()
keys = d["keys"]
lines = "".join("+1 2025550%03d----http://127.0.0.1:18896/sms?token=%d\n" % (i, i) for i in range(1, 13))
lines += "".join("+44 7700900%03d----http://127.0.0.1:18896/sms?token=gb%d\n" % (i, i) for i in range(1, 5))
adm.post(B + "/admin/api/numbers/import", json={"country": "us", "lines": lines, "note": "US"}, headers={"X-CSRF": csrf})
# mark a couple of cards used for realistic list
for k in keys[:2]:
    s = requests.Session(); r = s.post(B + "/api/redeem", json={"key": k}).json()
    s.post(B + "/api/number", json={"country": "us", "csrf": r["csrf"]}, headers={"X-CSRF": r["csrf"]})
    s.post(B + "/api/poll", json={"csrf": r["csrf"]}, headers={"X-CSRF": r["csrf"]})

from playwright.sync_api import sync_playwright
shots = os.path.join(BASE, "shots")
os.makedirs(shots, exist_ok=True)

with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 1440, "height": 940}, device_scale_factor=1)
    pg = ctx.new_page()
    # public auth
    pg.goto(B + "/"); pg.wait_for_timeout(900)
    pg.screenshot(path=os.path.join(shots, "01_public_auth.png"), full_page=True)
    # card link flow
    pg.goto(B + "/c/" + keys[4]); pg.wait_for_timeout(1200)
    pg.screenshot(path=os.path.join(shots, "02_public_ready.png"), full_page=True)
    pg.click("#btnNumber"); pg.wait_for_timeout(1500)
    pg.screenshot(path=os.path.join(shots, "03_public_number.png"), full_page=True)
    pg.wait_for_timeout(1500)
    pg.screenshot(path=os.path.join(shots, "04_public_code.png"), full_page=True)
    # mobile
    m = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
    mp = m.new_page(); mp.goto(B + "/c/" + keys[5]); mp.wait_for_timeout(1200)
    mp.screenshot(path=os.path.join(shots, "05_mobile.png"), full_page=True)
    # login page
    pg2 = ctx.new_page()
    pg2.goto(B + "/admin/login"); pg2.wait_for_timeout(400)
    pg2.screenshot(path=os.path.join(shots, "06_login.png"))
    pg2.fill('input[name=username]', "admin"); pg2.fill('input[name=password]', "StrongPass123")
    pg2.click("#btnLogin"); pg2.wait_for_timeout(600)
    pg2.screenshot(path=os.path.join(shots, "07_login_totp.png"))
    code = security.totp_at(sec)
    boxes = pg2.query_selector_all("#totpBoxes input")
    for i, ch in enumerate(code):
        boxes[i].fill(ch)
    pg2.click("#btnTotp"); pg2.wait_for_timeout(1600)
    pg2.screenshot(path=os.path.join(shots, "08_admin_dashboard.png"), full_page=True)
    for route, name in [("cards", "09_admin_cards"), ("numbers", "10_admin_numbers"), ("codes", "11_admin_codes"), ("ips", "12_admin_ips"), ("settings", "13_admin_settings")]:
        pg2.goto(B + "/admin#/" + route); pg2.wait_for_timeout(1200)
        pg2.screenshot(path=os.path.join(shots, name + ".png"), full_page=True)
    br.close()

print("shots done")
