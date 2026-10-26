import os, sys, threading, time, re, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_bare")
if os.path.isdir(TESTDATA):
    shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
sys.path.insert(0, BASE)

import uvicorn
from app.main import app
from app import security, db, config
db.init()
cfg = uvicorn.Config(app, host="127.0.0.1", port=18899, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(80):
    if server.started:
        break
    time.sleep(0.1)

import requests
B = "http://127.0.0.1:18899"
adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})
csrf = adm.get(B + "/admin/api/me").json()["csrf"]
adm.post(B + "/admin/api/settings", json={"site_url": "https://12456.cfd", "poll_interval": "15"}, headers={"X-CSRF": csrf})
keys = adm.post(B + "/admin/api/cards/create", json={"count": 3, "note": "bare"}, headers={"X-CSRF": csrf}).json()["keys"]

# session for card A (simulates a tab that already redeemed)
s = requests.Session()
s.post(B + "/api/redeem", json={"key": keys[0]})
sid = s.cookies.get(config.CARD_COOKIE)
print("cookie sid acquired:", bool(sid))

from playwright.sync_api import sync_playwright
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if extra else ""))


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": config.CARD_COOKIE, "value": sid, "domain": "127.0.0.1", "path": "/"}])
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))

    pg.goto(B + "/", wait_until="load")
    pg.wait_for_timeout(1200)
    auth_visible = pg.is_visible("#view-auth")
    work_visible = pg.is_visible("#view-work")
    check("bare / shows auth only", auth_visible and not work_visible, {"auth": auth_visible, "work": work_visible})
    check("bare / no card prefix rendered", not pg.is_visible("#cardPrefix") or pg.inner_text("#cardPrefix").strip() == "",
          pg.inner_text("#cardPrefix") if pg.is_visible("#cardPrefix") else "")
    sess = requests.get(B + "/api/session", cookies={config.CARD_COOKIE: sid}).json()
    check("bare / keeps existing session (other tabs alive)", sess.get("ok") is True, sess.get("error"))
    pg.screenshot(path=os.path.join(BASE, "shots", "14_bare_root_auth.png"), full_page=True)

    # manual key entry on bare / must enter the workspace and switch URL to the card link
    pg.fill("#keyInput", keys[2])
    pg.click("#btnAuth")
    pg.wait_for_timeout(1800)
    check("manual key enters workspace", pg.is_visible("#view-work") and not pg.is_visible("#view-auth"),
          {"work": pg.is_visible("#view-work"), "auth": pg.is_visible("#view-auth")})
    check("manual key rewrites url to /c/<key>", pg.url.endswith("/c/" + keys[2]), pg.url)
    pg.reload(wait_until="load")
    pg.wait_for_timeout(1800)
    check("refresh keeps workspace via card link", pg.is_visible("#view-work"), pg.is_visible("#view-auth"))
    pg.screenshot(path=os.path.join(BASE, "shots", "15_card_link_workspace.png"), full_page=True)

    # a fresh card link still auto-redeems
    pg2 = ctx.new_page()
    pg2.on("pageerror", lambda e: errs.append(str(e)))
    pg2.goto(B + "/c/" + keys[1], wait_until="load")
    pg2.wait_for_timeout(1800)
    check("card link enters workspace", pg2.is_visible("#view-work") and not pg2.is_visible("#view-auth"),
          {"work": pg2.is_visible("#view-work"), "auth": pg2.is_visible("#view-auth")})
    check("card prefix shown", pg2.inner_text("#cardPrefix").strip() == keys[1][:9], pg2.inner_text("#cardPrefix"))
    check("no page errors", not errs, errs[:3])
    br.close()

failed = [x for x in results if not x[1]]
print("\n=== %d/%d passed ===" % (len(results) - len(failed), len(results)))
if failed:
    print("FAILED:", [x[0] for x in failed])
sys.exit(1 if failed else 0)