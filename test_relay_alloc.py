# -*- coding: utf-8 -*-
# Admin generates relay cards by BUYING numbers (not pasting upstream).
import os, sys, threading, time, re, shutil, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_relayalloc")
shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18993"
os.environ["SMSGATE_RELAY_ALLOW_PRIVATE"] = "1"
sys.path.insert(0, BASE)

import uvicorn, requests
from app.main import app
from app import db, security, relay_api, providers, d1jiema

MOCK_PORT = 18994
HITS = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        HITS.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("1|【豆包】验证码 112233".encode())


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), H).serve_forever, daemon=True).start()

db.init()
db.set_setting("relay_base", "https://api.12456.cfd")
db.set_setting("relay_rate", "60")
db.set_setting("provider", "pool")
db.set_setting("allow_choices", "us,gb,hk,cn_real,cn_virtual")
db.set_setting("allow_countries", "us,gb,hk")

cfg = uvicorn.Config(app, host="127.0.0.1", port=18993, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started: break
    time.sleep(0.1)

B = "http://127.0.0.1:18993"
M = "http://127.0.0.1:%d" % MOCK_PORT
results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)[:280]) if extra else ""))

adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})
csrf = d["csrf"]

# stock 3 pool numbers with upstream sms urls
now = int(time.time())
with db.tx() as c:
    for i, phone in enumerate(("+12025550101", "+12025550102", "+12025550103")):
        c.execute("INSERT INTO numbers(phone,country,provider,sms_url,status,created_at) "
                  "VALUES(?,?,?,?,'available',?)", (phone, "us", "pool", M + "/sms?token=ok%d" % i, now))

r = adm.post(B + "/admin/api/relay/allocate",
             json={"choice": "us", "count": 3, "note": "buy-test"}, headers={"X-CSRF": csrf})
d = r.json()
check("allocate 3 status", r.status_code == 200 and d.get("ok"), d.get("error"))
check("allocate 3 count", d.get("count") == 3, d.get("count"))
lines = d.get("lines") or []
check("allocate lines", len(lines) == 3 and all("https://api.12456.cfd/smsrecord?token=" in x for x in lines), lines[:2])
check("allocate lines no upstream", all("127.0.0.1" not in x for x in lines), lines[:1])
check("phones match stock", sorted([x.split("----")[0].replace("卡号:", "") for x in lines]) ==
      ["+12025550101", "+12025550102", "+12025550103"], lines)
st = db.q("SELECT phone,status,card_id FROM numbers WHERE status='relay' ORDER BY phone")
check("numbers marked relay", len(st) == 3 and all(x["status"] == "relay" and x["card_id"] == 0 for x in st),
      [dict(x) for x in st])

# ---- batch re-export (recovers lines even if the dialog was closed) ----
r = adm.get(B + "/admin/api/relay/batches")
d = r.json()
check("batches listed", d.get("ok") and bool(d.get("items")) and d["items"][0]["live"] >= 3, (d.get("items") or [])[:1])
bid = d["items"][0]["id"]
r = adm.get(B + "/admin/api/relay/export", params={"batch": bid})
d2 = r.json()
check("batch export works", d2.get("ok") and d2.get("count") == 3 and
      d2.get("text", "").count("https://api.12456.cfd/smsrecord?token=") == 3, d2.get("count"))
r = adm.get(B + "/admin/api/relay/export", params={"batch": 999999})
check("empty batch export", r.json().get("ok") and r.json().get("count") == 0, r.json().get("count"))

tok = lines[0].split("token=")[1]
r = requests.get(B + "/smsrecord", params={"token": tok})
check("generated token works", r.status_code == 200 and "112233" in r.text, r.text)

r = adm.post(B + "/admin/api/relay/allocate",
             json={"choice": "us", "count": 5, "note": "empty"}, headers={"X-CSRF": csrf})
check("no stock -> error", r.status_code in (429, 502) and r.json().get("ok") is False, r.text[:160])

r = adm.post(B + "/admin/api/relay/allocate",
             json={"choice": "xx", "count": 1}, headers={"X-CSRF": csrf})
check("bad choice -> 400", r.status_code == 400, r.status_code)

r = adm.post(B + "/admin/api/relay/allocate",
             json={"choice": "us", "count": 999}, headers={"X-CSRF": csrf})
check("count clamped/error", r.status_code in (200, 429, 502), r.status_code)

# d1jiema marker path (CN numbers have no sms_url; relay calls d1jiema.get_msg)
orig = d1jiema.get_msg
d1jiema.get_msg = lambda phone, kw="": "1935823747|0/0.26/【豆包】验证码 445566，5分钟内有效。"
try:
    batch, rows = relay_api._create_sync(
        [{"phone": "+8613800000000", "upstream": "d1jiema:+8613800000000:实卡"}], "cn", 0, "admin")
    rr = requests.get(B + "/smsrecord", params={"token": rows[0]["token"]})
    check("d1jiema relay", rr.status_code == 200 and "445566" in rr.text, rr.text)
    check("relay hides price", "0.26" not in rr.text and "1935823747" not in rr.text, rr.text)
    check("relay keeps 1| shape", rr.text.strip().startswith("1|"), rr.text[:60])
finally:
    d1jiema.get_msg = orig

# admin list shows d1jiema as host
r = adm.get(B + "/admin/api/relay", params={"q": "+8613800000000"})
row = (r.json().get("items") or [{}])[0]
check("d1jiema host label", row.get("upstream_host") == "d1jiema", row.get("upstream_host"))

fails = [x for x in results if not x[1]]
print("\nSUMMARY %d/%d" % (len(results) - len(fails), len(results)))
if fails:
    print("FAILED:", [x[0] for x in fails]); sys.exit(1)