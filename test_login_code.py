# -*- coding: utf-8 -*-
"""Code-only admin login: page shape + happy path + wrong code."""
import os, sys, shutil, time, threading
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "testdata_logincode")
if os.path.isdir(DATA):
    shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = DATA
os.environ["SMSGATE_PORT"] = "18940"
sys.path.insert(0, BASE)

import uvicorn, requests, pyotp
from app.main import app
from app import db, security

db.init()
secret = pyotp.random_base32()
with db.tx() as c:
    c.execute("DELETE FROM admins")
    c.execute("INSERT INTO admins(username,pass_hash,totp_secret,totp_on,setup_done,created_at) "
              "VALUES('admin','x',?,1,1,?)", (security.enc(secret), int(time.time())))

cfg = uvicorn.Config(app, host="127.0.0.1", port=18940, log_level="error")
srv = uvicorn.Server(cfg)
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(100):
    if srv.started:
        break
    time.sleep(0.1)

B = "http://127.0.0.1:18940"
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if not extra else " :: " + str(extra)[:200]))

html = requests.get(B + "/admin/login", timeout=20).text
check("login page has no password field", 'name="password"' not in html and "codePanel" in html)
check("login page uses new js", "/static/login.js?v=20261024" in html)

s = requests.Session()
r = s.post(B + "/admin/api/login/code", json={"code": "000000"}, timeout=20)
check("wrong code rejected", r.status_code == 401, r.text[:120])

code = pyotp.TOTP(secret).now()
r = s.post(B + "/admin/api/login/code", json={"code": code}, timeout=20)
d = r.json()
check("correct code logs in", r.status_code == 200 and d.get("ok") and d.get("csrf"), r.text[:160])
me = s.get(B + "/admin/api/me", timeout=20).json()
check("session is active", me.get("ok") and me.get("username") == "admin", me)
r2 = requests.post(B + "/admin/api/login/code", json={"code": "12345"}, timeout=20)
check("short code rejected", r2.status_code == 400, r2.text[:100])

print("\n=== %d/%d passed ===" % (sum(1 for _, ok in res if ok), len(res)))
if not all(ok for _, ok in res):
    raise SystemExit(1)
