import os, sys, threading, time, re, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_adminui")
if os.path.isdir(TESTDATA):
    shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
sys.path.insert(0, BASE)

import uvicorn
from app.main import app
from app import security, db
db.init()
cfg = uvicorn.Config(app, host="127.0.0.1", port=18901, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(80):
    if server.started:
        break
    time.sleep(0.1)

import requests
B = "http://127.0.0.1:18901"
adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})
csrf = adm.get(B + "/admin/api/me").json()["csrf"]

from playwright.sync_api import sync_playwright
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if extra else ""))


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 1440, "height": 1100})
    # reuse admin session cookie by logging in through the UI? simpler: copy cookie from requests session
    for c in adm.cookies:
        ctx.add_cookies([{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"}])
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(B + "/admin#/settings", wait_until="load")
    pg.wait_for_timeout(1500)
    check("settings form rendered", pg.is_visible("#settingsForm"))
    option = pg.eval_on_selector("select[name=provider]", "el => [...el.options].map(o => o.value)")
    check("nexsms provider option", "nexsms_premium" in option, option)
    check("nexsms key field", pg.is_visible("input[name=nexsms_key]"))
    check("sms prefix default", pg.input_value("input[name=nexsms_sms_prefix]") == "[豆包]", pg.input_value("input[name=nexsms_sms_prefix]"))
    check("project default", pg.input_value("input[name=nexsms_project]") == "豆包", pg.input_value("input[name=nexsms_project]"))
    pg.select_option("select[name=provider]", "nexsms_premium")
    pg.fill("input[name=nexsms_key]", "test1234567890abcdef")
    pg.fill("input[name=nexsms_sms_prefix]", "[豆包]")
    pg.click("button[type=submit]")
    pg.wait_for_timeout(1200)
    toast = pg.inner_text("#toast") if pg.is_visible("#toast") else ""
    check("settings saved via UI", "已保存" in toast, toast)
    saved = adm.get(B + "/admin/api/settings").json()["settings"]
    check("provider persisted", saved.get("provider") == "nexsms_premium", saved.get("provider"))
    check("key stored masked", saved.get("nexsms_key_set") is True and saved.get("nexsms_key") == "", saved.get("nexsms_key_hint"))
    check("prefix persisted", saved.get("nexsms_sms_prefix") == "[豆包]", saved.get("nexsms_sms_prefix"))
    pg.screenshot(path=os.path.join(BASE, "shots", "16_admin_nexsms_settings.png"), full_page=True)
    check("no page errors", not errs, errs[:3])
    br.close()

failed = [x for x in results if not x[1]]
print("\n=== %d/%d passed ===" % (len(results) - len(failed), len(results)))
if failed:
    print("FAILED:", [x[0] for x in failed])
sys.exit(1 if failed else 0)