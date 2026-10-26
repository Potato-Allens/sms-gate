# -*- coding: utf-8 -*-
# UI: buy numbers from the relay page and get 卡号:phone----own-domain lines.
import os, sys, threading, time, re, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_relayallocui")
shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18992"
os.environ["SMSGATE_RELAY_ALLOW_PRIVATE"] = "1"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"C:\Users\Administrator\AppData\Local\ms-playwright")
sys.path.insert(0, BASE)

import uvicorn, requests
from app.main import app
from app import db, security

MOCK_PORT = 18999


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("1|【豆包】验证码 778899".encode())


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), H).serve_forever, daemon=True).start()
db.init()
db.set_setting("relay_base", "https://api.12456.cfd")
db.set_setting("provider", "pool")
db.set_setting("allow_choices", "us,gb,hk")
now = int(time.time())
with db.tx() as c:
    for i, phone in enumerate(("+12025550171", "+12025550172")):
        c.execute("INSERT INTO numbers(phone,country,provider,sms_url,status,created_at) "
                  "VALUES(?,?,?,?,'available',?)", (phone, "us", "pool", "http://127.0.0.1:%d/sms?t=%d" % (MOCK_PORT, i), now))

cfg = uvicorn.Config(app, host="127.0.0.1", port=18992, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started: break
    time.sleep(0.1)

B = "http://127.0.0.1:18992"
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
    ctx = br.new_context(viewport={"width": 1440, "height": 1200},
                         permissions=["clipboard-read", "clipboard-write"])
    for ck in adm.cookies:
        ctx.add_cookies([{"name": ck.name, "value": ck.value, "domain": "127.0.0.1", "path": "/"}])
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda dlg: dlg.accept())
    pg.goto(B + "/admin#/relay", wait_until="load")
    pg.wait_for_selector("#relayAllocForm", timeout=10000)
    check("alloc form visible", pg.is_visible("#relayAllocForm"))
    check("buy button text", "购买并生成直链卡" in pg.inner_text("#relayAllocForm"))
    check("choice options", pg.locator("#relayAllocForm select[name=choice] option").count() == 5,
          pg.locator("#relayAllocForm select[name=choice] option").count())
    pg.select_option("#relayAllocForm select[name=choice]", "us")
    pg.fill("#relayAllocForm input[name=count]", "2")
    pg.click("#relayAllocForm button[type=submit]")
    pg.wait_for_selector("#allLinks", timeout=20000)
    txt = pg.input_value("#allLinks")
    check("two lines generated", txt.count("卡号:+") == 2, txt)
    check("phones from pool", "+12025550171" in txt and "+12025550172" in txt, txt)
    check("own domain", "https://api.12456.cfd/smsrecord?token=" in txt, txt[:100])
    check("no upstream leak", "127.0.0.1" not in txt, txt[:100])

    # A stray backdrop click used to destroy the freshly generated list.
    pg.click("#modal", position={"x": 6, "y": 6})
    pg.wait_for_timeout(400)
    check("backdrop click keeps dialog", pg.is_visible("#allLinks"), pg.is_visible("#modalBody"))

    btn_ok = pg.is_visible('[data-act="copy-all"]')
    pg.click('[data-act="copy-all"]')
    clip = ""
    for _ in range(12):
        pg.wait_for_timeout(250)
        try:
            clip = pg.evaluate("navigator.clipboard.readText()")
        except Exception as e:
            clip = "ERR " + type(e).__name__
        if "token=" in (clip or ""):
            break
    toast_txt = pg.inner_text("#toast")
    check("copy-all button visible", btn_ok)
    check("copy-all puts 2 lines on clipboard", clip.count("卡号:+") == 2 and "token=" in clip,
          (clip[:80], toast_txt))

    pg.click("#modalClose")
    pg.wait_for_timeout(800)
    check("table rows", pg.locator("#relayTable tbody tr").count() >= 2, pg.locator("#relayTable tbody tr").count())
    opts = pg.locator("#relayBatch option").count()
    check("batch selector has entries", opts >= 1, opts)
    pg.click('[data-act="relay-batch-copy"]')
    pg.wait_for_timeout(500)
    try:
        clip2 = pg.evaluate("navigator.clipboard.readText()")
    except Exception as e:
        clip2 = "ERR " + type(e).__name__
    check("batch copy after closing dialog", clip2.count("卡号:+") == 2, clip2[:80])

    check("numbers marked relay", db.q1("SELECT COUNT(*) AS n FROM numbers WHERE status='relay'")["n"] == 2,
          db.q1("SELECT COUNT(*) AS n FROM numbers WHERE status='relay'")["n"])
    pg.screenshot(path=r"C:\Users\Administrator\Documents\Codex\2026-10-24\root-ucpwuogj5963-22\outputs\relay-alloc-ui.png", full_page=True)
    check("no page errors", not errs, errs)
    br.close()

fails = [x for x in results if not x[1]]
print("\nSUMMARY %d/%d" % (len(results) - len(fails), len(results)))
if fails:
    print("FAILED:", [x[0] for x in fails]); sys.exit(1)