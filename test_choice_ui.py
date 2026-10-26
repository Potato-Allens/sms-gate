# -*- coding: utf-8 -*-
"""UI check: bound card shows one fixed segment, no other countries."""
import os, sys, threading, time, re, shutil
BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_choice_ui")
shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18972"
sys.path.insert(0, BASE)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"C:\Users\Administrator\AppData\Local\ms-playwright")

import uvicorn
from app.main import app
from app import security, db
db.init()
db.set_setting("provider", "pool")
db.set_setting("allow_choices", "us,gb,hk,cn_real,cn_virtual")
now = int(time.time())
with db.tx() as c:
    c.execute("INSERT INTO numbers(phone,country,provider,status,created_at) VALUES('+12025550123','us','pool','available',?)", (now,))

cfg = uvicorn.Config(app, host="127.0.0.1", port=18972, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(80):
    if server.started: break
    time.sleep(0.1)

import requests
B = "http://127.0.0.1:18972"
adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})
csrf = adm.get(B + "/admin/api/me").json()["csrf"]
us_key = adm.post(B + "/admin/api/cards/create", json={"count": 1, "choice": "us", "note": "uius"},
                  headers={"X-CSRF": csrf}).json()["keys"][0]
gb_key = adm.post(B + "/admin/api/cards/create", json={"count": 1, "choice": "gb", "note": "uigb"},
                  headers={"X-CSRF": csrf}).json()["keys"][0]

from playwright.sync_api import sync_playwright
results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)[:200]) if extra else ""))

with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 1280, "height": 1000})
    for ck in adm.cookies:
        ctx.add_cookies([{"name": ck.name, "value": ck.value, "domain": "127.0.0.1", "path": "/"}])
    pg = ctx.new_page()
    pg.goto(B + "/admin#/cards", wait_until="load", timeout=45000)
    pg.wait_for_selector("#createForm", timeout=20000)
    pg.wait_for_timeout(300)
    sel = pg.query_selector("#createForm select[name=choice]")
    check("admin form has choice select", bool(sel))
    if sel:
        opts = [o.get_attribute("value") for o in sel.query_selector_all("option")]
        check("admin options", opts[:5] == ["", "us", "gb", "hk", "cn_real"], opts)
    pg.screenshot(path=os.path.join(BASE, "shot_admin_choice.png"), full_page=True)

    ctx2 = br.new_context(viewport={"width": 520, "height": 1000})
    pub = ctx2.new_page()
    pub.goto(B + "/c/" + us_key, wait_until="load", timeout=45000)
    pub.wait_for_timeout(2500)
    badge = pub.query_selector("#choiceFixed")
    visible_badge = bool(badge) and badge.is_visible()
    check("bound: fixed badge visible", visible_badge)
    check("bound: grid hidden", not pub.query_selector("#countryGrid").is_visible())
    others = pub.query_selector_all("#countryGrid .country")
    shown = [b for b in others if b.is_visible()]
    check("bound: no country buttons shown", len(shown) == 0, len(shown))
    txt = pub.inner_text("#choiceFixed") if badge else ""
    check("bound: badge says US", "美国" in txt, txt)
    pub.screenshot(path=os.path.join(BASE, "shot_public_bound.png"), full_page=True)
    btn = pub.query_selector("#btnNumber")
    check("bound: take button enabled", bool(btn) and not btn.is_disabled())
    ctx2.close()

    ctx3 = br.new_context(viewport={"width": 520, "height": 1000})
    pub2 = ctx3.new_page()
    pub2.goto(B + "/c/" + gb_key, wait_until="load", timeout=45000)
    pub2.wait_for_timeout(2000)
    check("gb card badge", "英国" in pub2.inner_text("#choiceFixed"))
    pub2.screenshot(path=os.path.join(BASE, "shot_public_gb.png"), full_page=True)
    ctx3.close()
    br.close()

failed = [n for n, okk in results if not okk]
print("\n=== %d/%d passed ===" % (len(results) - len(failed), len(results)))
if failed:
    print("FAILED:", failed)
    sys.exit(1)
