# -*- coding: utf-8 -*-
"""Regression: an interrupted first-run setup can be resumed and bound."""
import os, re, shutil, sys, threading, time

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "testdata_resume")
if os.path.isdir(DATA):
    shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = DATA
os.environ["SMSGATE_PORT"] = "18897"
sys.path.insert(0, BASE)

import requests
import uvicorn
from app.main import app
from app import db, security

db.init()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18897, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(60):
    if server.started:
        break
    time.sleep(0.1)

B = "http://127.0.0.1:18897"
s = requests.Session()
s.trust_env = False

def j(r):
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:300], "_status": r.status_code}

ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if extra else ""))
    ok = ok and bool(cond)

boot = j(s.get(B + "/admin/api/bootstrap"))
check("fresh needs_setup", boot.get("needs_setup") is True, boot)
token = open(os.path.join(DATA, "setup.token"), encoding="utf-8").read().strip()
r = s.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"})
d = j(r)
check("step1 creates admin", d.get("ok"), d)
boot = j(s.get(B + "/admin/api/bootstrap"))
check("setup_pending after step1", boot.get("setup_pending") is True, boot)
pend = j(s.get(B + "/admin/api/setup/pending"))
check("pending QR available", pend.get("ok") and "<svg" in (pend.get("qr_svg") or ""), pend.get("error"))

s2 = requests.Session()
s2.trust_env = False
r = s2.post(B + "/admin/api/setup/resume", json={"token": token, "username": "admin", "password": "StrongPass123"})
d = j(r)
check("resume ok", d.get("ok"), d)
m = re.search(r"secret=([A-Z2-7]+)", d.get("otpauth") or "")
check("resume rotates secret", bool(m))
secret = m.group(1) if m else ""
csrf = d.get("csrf", "")
check("old setup session invalidated", s.get(B + "/admin/api/setup/pending").status_code == 401)
r = s2.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(secret)}, headers={"X-CSRF": csrf})
d = j(r)
check("verify after resume", d.get("ok"), d)
boot = j(s2.get(B + "/admin/api/bootstrap"))
check("setup complete", boot.get("setup_pending") is False and boot.get("logged_in") is True, boot)
row = db.q1("SELECT totp_on,setup_done FROM admins WHERE username='admin'")
check("db state", row["totp_on"] == 1 and row["setup_done"] == 1, dict(row))

server.should_exit = True
print("=== %s ===" % ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)