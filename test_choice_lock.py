# -*- coding: utf-8 -*-
"""Segment pre-binding regression: a US card must never see or use GB."""
import os, sys, threading, time, re, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_choice")
shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "8931"
sys.path.insert(0, BASE)


class SMSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"1|445566"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 18931), SMSHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

import uvicorn
from app.main import app
from app import security, db

db.init()
cfg = uvicorn.Config(app, host="127.0.0.1", port=8931, log_level="warning")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(60):
    if server.started:
        break
    time.sleep(0.1)

import requests

B = "http://127.0.0.1:8931"
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if extra else ""))


def j(r):
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:300], "_status": r.status_code}


adm = requests.Session()
boot = j(adm.get(B + "/admin/api/bootstrap"))
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = j(adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}))
sec = re.search(r"secret=([A-Z2-7]+)", d.get("otpauth") or "")
secret = sec.group(1)
csrf = d.get("csrf", "")
d = j(adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(secret)}, headers={"X-CSRF": csrf}))
csrf = j(adm.get(B + "/admin/api/me")).get("csrf", "")
check("admin ready", bool(csrf))

# two cards: one pre-bound to us, one free
d = j(adm.post(B + "/admin/api/cards/create",
               json={"count": 1, "max_codes": 1, "choice": "us", "note": "bound-us"},
               headers={"X-CSRF": csrf}))
check("bound card created", d.get("ok") and len(d.get("keys", [])) == 1, d.get("error"))
check("bound echo", d.get("choice") == "us", d.get("choice"))
us_key = (d.get("keys") or [""])[0]

d = j(adm.post(B + "/admin/api/cards/create",
               json={"count": 1, "max_codes": 1, "note": "free"},
               headers={"X-CSRF": csrf}))
free_key = (d.get("keys") or [""])[0]
check("free card created", bool(free_key), d.get("error"))

# invalid segment rejected
r = adm.post(B + "/admin/api/cards/create", json={"count": 1, "choice": "jp"}, headers={"X-CSRF": csrf})
check("bad choice rejected", r.status_code == 400, r.status_code)

lines = "+1 2025550199----http://127.0.0.1:18931/sms?token=us\n"
d = j(adm.post(B + "/admin/api/numbers/import", json={"country": "us", "lines": lines}, headers={"X-CSRF": csrf}))
check("numbers imported", d.get("ok") and d.get("added") == 1, d)

# --- bound card: only US is visible
pub = requests.Session()
d = j(pub.post(B + "/api/redeem", json={"key": us_key}))
check("redeem bound", d.get("ok"), d.get("error"))
s = j(pub.get(B + "/api/session"))
check("session bound flag", s.get("bound") == "us", s.get("bound"))
check("only us offered", [c["code"] for c in s.get("choices", [])] == ["us"], s.get("choices"))
check("choice pre-locked", (s.get("choice") or {}).get("code") == "us", s.get("choice"))
pcsrf = s.get("csrf", "")

r = pub.post(B + "/api/choice", json={"choice": "gb", "csrf": pcsrf}, headers={"X-CSRF": pcsrf})
check("choose gb blocked", r.status_code == 409 and j(r).get("locked") == "us", r.status_code)
r = pub.post(B + "/api/number", json={"choice": "gb", "csrf": pcsrf}, headers={"X-CSRF": pcsrf})
check("take gb blocked", r.status_code == 409, r.status_code)
r = pub.post(B + "/api/number", json={"choice": "us", "csrf": pcsrf}, headers={"X-CSRF": pcsrf})
d = j(r)
check("take us ok", d.get("ok") and d.get("number", {}).get("choice") == "us", d.get("error"))
s = j(pub.get(B + "/api/session"))
check("still only us", [c["code"] for c in s.get("choices", [])] == ["us"], s.get("choices"))

# --- free card: everything visible, then locked segment hides the rest
pub2 = requests.Session()
d = j(pub2.post(B + "/api/redeem", json={"key": free_key}))
p2 = s = j(pub2.get(B + "/api/session"))
codes = [c["code"] for c in s.get("choices", [])]
check("free card offers many", len(codes) >= 2, codes)
check("free card no bound", s.get("bound") == "", s.get("bound"))
p2csrf = s.get("csrf", "")
r = pub2.post(B + "/api/choice", json={"choice": "gb", "csrf": p2csrf}, headers={"X-CSRF": p2csrf})
check("free choose gb", j(r).get("ok"), r.text[:160])
s = j(pub2.get(B + "/api/session"))
check("locked hides others", [c["code"] for c in s.get("choices", [])] == ["gb"], s.get("choices"))

# --- admin list exposes the binding
d = j(adm.get(B + "/admin/api/cards?size=50"))
rows = {x["key_prefix"]: x for x in d.get("items", [])}
row = rows.get(us_key[:9])
check("list shows bound_choice", row and row.get("bound_choice") == "us", row)

failed = [n for n, okk in results if not okk]
print("\n=== %d/%d passed ===" % (len(results) - len(failed), len(results)))
if failed:
    print("FAILED:", failed)
    sys.exit(1)
