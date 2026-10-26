# -*- coding: utf-8 -*-
# Landing page + /redeem split regression.
import os, sys, threading, time, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_landing")
shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18981"
sys.path.insert(0, BASE)

import uvicorn
from app.main import app
from app import db

db.init()
cfg = uvicorn.Config(app, host="127.0.0.1", port=18981, log_level="warning")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(120):
    if server.started:
        break
    time.sleep(0.1)

import requests

B = "http://127.0.0.1:18981"
SHOP = "https://wzyp.cn/shop/63EZ1CJK"
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if extra else ""))


r = requests.get(B + "/", timeout=10)
check("landing 200", r.status_code == 200, r.status_code)
html = r.text
check("landing shop url", SHOP in html, html.count(SHOP))
check("landing shop buttons >= 4", html.count(SHOP) >= 4, html.count(SHOP))
check("landing buy label", "购买号码" in html)
check("landing redeem link", 'href="/redeem"' in html)
for name in ["美国", "英国", "香港", "国内 · 实卡", "国内 · 虚卡"]:
    check("segment " + name, name in html)
check("landing no script tag", "<script" not in html.lower())
check("landing csp", "default-src 'self'" in r.headers.get("Content-Security-Policy", ""))
check("landing noindex", "noindex" in r.headers.get("X-Robots-Tag", ""))
check("landing target blank", 'target="_blank"' in html)
check("landing rel noopener", 'rel="noopener noreferrer"' in html)

c = requests.get(B + "/static/landing.css", timeout=10)
check("landing.css 200", c.status_code == 200, c.status_code)
check("landing.css size", len(c.content) > 4000, len(c.content))

r2 = requests.get(B + "/redeem", timeout=10)
check("redeem 200", r2.status_code == 200, r2.status_code)
h2 = r2.text
check("redeem key input", 'id="keyInput"' in h2)
check("redeem auth form", 'id="authForm"' in h2)
check("redeem data-key empty", 'data-key=""' in h2)
check("redeem public.js", "public.js" in h2)

KEY = "ABCD-EFGH-IJKL-MNOP"
r3 = requests.get(B + "/c/" + KEY, timeout=10)
check("card link 200", r3.status_code == 200, r3.status_code)
check("card link key injected", KEY in r3.text)

db.set_setting("shop_url", "https://example.com/shop/xyz")
r4 = requests.get(B + "/", timeout=10)
check("shop url configurable", "https://example.com/shop/xyz" in r4.text)
check("old shop url gone", SHOP not in r4.text)
db.set_setting("shop_url", SHOP)
r5 = requests.get(B + "/", timeout=10)
check("shop url restored", SHOP in r5.text)

h = requests.get(B + "/api/health", timeout=10)
check("health ok", h.status_code == 200 and h.json().get("ok") is True, h.text[:120])
check("style.css 200", requests.get(B + "/static/style.css", timeout=10).status_code == 200)

ok_n = sum(1 for _, passed in results if passed)
print("")
print("SUMMARY %d/%d" % (ok_n, len(results)))
for name, passed in results:
    if not passed:
        print("  FAILED: " + name)
sys.exit(0 if ok_n == len(results) else 1)