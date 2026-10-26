import os, sys, threading, time, re, shutil, hashlib, sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_adminui4")
if os.path.isdir(TESTDATA):
    shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18971"
sys.path.insert(0, BASE)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"C:\Users\Administrator\AppData\Local\ms-playwright")

import uvicorn
from app.main import app
from app import security, db
db.init()
db.set_setting("provider", "pool")
db.set_setting("allow_countries", "us,gb,hk")
db.set_setting("allow_choices", "us,gb,hk,cn_real,cn_virtual")
db.set_setting("d1jiema_token", "tok-ui-test")
db.set_setting("d1jiema_enabled", "1")
now = int(time.time())
with db.tx() as c:
    c.execute("INSERT INTO numbers(phone,country,provider,status,created_at) VALUES('+12025550111','us','pool','available',?)", (now,))

cfg = uvicorn.Config(app, host="127.0.0.1", port=18971, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(80):
    if server.started:
        break
    time.sleep(0.1)

import requests
B = "http://127.0.0.1:18971"
adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})

from playwright.sync_api import sync_playwright
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)[:300]) if extra else ""))


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 1440, "height": 1200})
    for ck in adm.cookies:
        ctx.add_cookies([{"name": ck.name, "value": ck.value, "domain": "127.0.0.1", "path": "/"}])
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(B + "/admin#/settings", wait_until="load")
    pg.wait_for_selector("#settingsForm", timeout=15000)
    opts = pg.eval_on_selector("select[name=choices]", "el => [...el.options].map(o => o.value)")
    check("choices multi-select has 5 options", opts == ["us", "gb", "hk", "cn_real", "cn_virtual"], opts)
    sel = pg.eval_on_selector("select[name=choices]", "el => [...el.selectedOptions].map(o => o.value)")
    check("all choices selected by default", len(sel) == 5, sel)
    check("d1jiema token field", pg.is_visible("input[name=d1jiema_token]"))
    check("d1jiema base default", pg.input_value("input[name=d1jiema_base]").endswith("zc/data.php"),
          pg.input_value("input[name=d1jiema_base]"))
    check("d1jiema keyword default", pg.input_value("input[name=d1jiema_keyword]") == "豆包")
    check("d1jiema test button", pg.is_visible("[data-act=test-d1jiema]"))
    check("provider has d1jiema aware options", "nexsms_premium" in pg.eval_on_selector(
        "select[name=provider]", "el => [...el.options].map(o => o.value)"))
    pg.select_option("select[name=choices]", ["us", "cn_virtual"])
    pg.click("#settingsForm button[type=submit]")
    pg.wait_for_timeout(1500)
    check("saved allow_choices", db.get_setting("allow_choices") == "us,cn_virtual", db.get_setting("allow_choices"))
    pg.screenshot(path=os.path.join(BASE, "shots", "round4-admin-settings.png"), full_page=True)
    check("no page errors", not errs, errs[:3])
    br.close()

# card reset flow
key = "RESET-UI01-2026-TEST"
kh = hashlib.sha256(("sg:" + key).encode()).hexdigest()
with db.tx() as c:
    c.execute("INSERT INTO cards(key_hash,key_prefix,note,max_codes,status,batch_id,created_at,created_by) "
              "VALUES(?,?,?,1,'active',0,?, 'ui4')", (kh, key[:9], "ui4", int(time.time())))
cid = db.q1("SELECT id FROM cards WHERE key_hash=?", (kh,))["id"]

pub = requests.Session()
d = pub.post(B + "/api/redeem", json={"key": key}).json()
csrf = d["csrf"]
r = pub.post(B + "/api/number", json={"choice": "us", "csrf": csrf}, headers={"X-CSRF": csrf})
check("public lock via number", db.q1("SELECT choice FROM card_choices WHERE card_id=?", (cid,))["choice"] == "us", r.status_code)
me = adm.get(B + "/admin/api/me").json()
r = adm.post(B + "/admin/api/cards/%d/action" % cid, json={"action": "reset_choice", "csrf": me["csrf"]},
             headers={"X-CSRF": me["csrf"]})
check("admin reset ok", r.status_code == 200 and r.json().get("ok"), r.text[:150])
check("choice cleared", db.q1("SELECT * FROM card_choices WHERE card_id=?", (cid,)) is None)
check("numbers released", db.q1("SELECT COUNT(*) n FROM numbers WHERE card_id=?", (cid,))["n"] == 0)

print("\n=== %d/%d passed ===" % (sum(1 for r in results if r[1]), len(results)))
sys.exit(0 if all(r[1] for r in results) else 1)