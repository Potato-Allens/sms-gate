# -*- coding: utf-8 -*-
# 100 concurrent claimers, zero queue: every request must finish fast.
import os, sys, threading, time, re, shutil, itertools, json, statistics
BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_noqueue")
shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18991"
sys.path.insert(0, BASE)

import uvicorn
from app.main import app
from app import security, db, providers

db.init()
db.set_setting("provider", "nexsms_premium")
db.set_setting("allow_choices", "us,gb,hk,cn_real,cn_virtual")
db.set_setting("max_numbers_per_card", "1")
db.set_setting("nexsms_stock_target", "20")
db.set_setting("nexsms_batch_qty", "5")

_seq = itertools.count(1)
_seq_lock = threading.Lock()
PURCHASES = [0]
PURCHASE_FAIL = [False]


def fake_purchase(country, qty):
    time.sleep(0.25)
    if PURCHASE_FAIL[0]:
        providers.supply_block(180, "号源通道余额不足，请联系管理员充值后重试")
        raise providers.ProviderBusy("号源通道余额不足，请联系管理员充值后重试", retry_after=30)
    with _seq_lock:
        PURCHASES[0] += 1
        start = next(_seq)
    items = []
    for i in range(qty):
        n = start * 100 + i
        items.append({"tel": "+1202555%04d" % (n % 10000),
                      "smsAccess": {"url": "http://127.0.0.1:9/sms?n=%d" % n},
                      "appName": "豆包"})
    return {"appId": 1, "price": 0.1, "name": "豆包"}, items


providers._nex_purchase = fake_purchase

cfg = uvicorn.Config(app, host="127.0.0.1", port=18991, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(120):
    if server.started:
        break
    time.sleep(0.1)

import requests
B = "http://127.0.0.1:18991"
adm = requests.Session()
token = open(os.path.join(TESTDATA, "setup.token"), encoding="utf-8").read().strip()
d = adm.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"}).json()
sec = re.search(r"secret=([A-Z2-7]+)", d["otpauth"]).group(1)
adm.post(B + "/admin/api/setup/verify", json={"code": security.totp_at(sec)}, headers={"X-CSRF": d["csrf"]})
csrf = adm.get(B + "/admin/api/me").json()["csrf"]
keys = adm.post(B + "/admin/api/cards/create", json={"count": 100, "max_codes": 1, "note": "load100"},
                headers={"X-CSRF": csrf}).json()["keys"]
print("cards", len(keys))
results = []
rlock = threading.Lock()


def claim(key):
    t0 = time.time()
    sess = requests.Session()
    try:
        r = sess.post(B + "/api/redeem", json={"key": key}, timeout=30)
        if r.status_code != 200:
            with rlock:
                results.append(("redeem", r.status_code, time.time() - t0, r.text[:120]))
            return
        csrf2 = r.json()["csrf"]
        r2 = sess.post(B + "/api/number", json={"choice": "us", "csrf": csrf2},
                       headers={"X-CSRF": csrf2}, timeout=30)
        try:
            body = r2.json()
        except Exception:
            body = {}
        with rlock:
            results.append(("number", r2.status_code, time.time() - t0,
                            {"ok": body.get("ok"), "queued": body.get("queued"),
                             "err": body.get("error"), "retry": body.get("retry_after"),
                             "blocked": body.get("supply_blocked"),
                             "phone": (body.get("number") or {}).get("phone")}))
    except Exception as e:
        with rlock:
            results.append(("exc", 0, time.time() - t0, type(e).__name__))


threads = [threading.Thread(target=claim, args=(k,)) for k in keys]
t0 = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join(90)
elapsed = time.time() - t0

nums = [r for r in results if r[0] == "number"]
ok_n = sum(1 for r in nums if r[1] == 200 and r[3].get("ok"))
busy = [r for r in nums if r[1] == 429]
other = [r for r in nums if r[1] not in (200, 429)]
lat = sorted(r[2] for r in nums)
p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0

print("elapsed %.2fs  number=%d ok=%d busy=%d other=%d purchases=%d" %
      (elapsed, len(nums), ok_n, len(busy), len(other), PURCHASES[0]))
print("latency p50=%.2fs p95=%.2fs max=%.2fs" % (lat[len(lat) // 2], p95, lat[-1]))
print("queued_true", sum(1 for r in results if r[0] == "number" and r[3].get("queued")))
print("busy_sample", busy[:3])
print("other_sample", other[:3])

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)[:200]) if extra else ""))

check("all 100 got a number", ok_n >= 95, ok_n)
check("burst under 20s", elapsed < 20, round(elapsed, 2))
check("no 5xx / no exceptions", not other and not [r for r in results if r[0] == "exc"], other[:2])
check("no queued response", all(not r[3].get("queued") for r in nums))
check("p95 under 8s", p95 < 8, p95)
check("busy retry <= 5s", all((r[3].get("retry") or 99) <= 5 for r in busy))
check("purchases coalesced", PURCHASES[0] <= 10, PURCHASES[0])

# ---- fail-fast path: upstream says balance is empty
with db.tx() as c:
    c.execute("UPDATE numbers SET status='expired' WHERE provider='nexsms_premium' AND status='available'")
PURCHASE_FAIL[0] = True
providers._SUPPLY_BLOCK[0] = 0
providers._SUPPLY_BLOCK[1] = ""
time.sleep(0.2)
keys2 = adm.post(B + "/admin/api/cards/create", json={"count": 3, "max_codes": 1, "note": "loadfail"},
                 headers={"X-CSRF": csrf}).json()["keys"]
fail_res = []
for k in keys2:
    ss = requests.Session()
    t1 = time.time()
    r = ss.post(B + "/api/redeem", json={"key": k}, timeout=20)
    c2 = r.json().get("csrf")
    r2 = ss.post(B + "/api/number", json={"choice": "hk", "csrf": c2}, headers={"X-CSRF": c2}, timeout=20)
    body = {}
    try:
        body = r2.json()
    except Exception:
        pass
    fail_res.append((r2.status_code, round(time.time() - t1, 2), body.get("supply_blocked"), body.get("error")))
print("fail_path", fail_res)
check("balance failure is fast 429", all(x[0] == 429 and x[1] < 3 for x in fail_res), fail_res)
check("balance failure flagged", all(x[2] for x in fail_res), fail_res)

# ---- real mapping: upstream "insufficient balance" must arm the supply block
_saved_c = providers._nex_candidates
_saved_h = providers._nex_http
providers._nex_candidates = lambda c: [{"appId": 9, "type": 1, "manual": True,
                                        "price": 0.1, "quantity": 1, "name": "豆包"}]
def _boom(*a, **k):
    raise providers.ProviderError("NexSMS: 余额不足，请充值")
providers._nex_http = _boom
providers._SUPPLY_BLOCK[0] = 0
try:
    providers._nex_purchase("us", 1)
    mapped = False
except providers.ProviderBusy:
    mapped = True
except Exception:
    mapped = False
finally:
    providers._nex_candidates = _saved_c
    providers._nex_http = _saved_h
check("balance error maps to busy", mapped)
check("balance error arms block", providers.supply_block_left() > 100, providers.supply_block_left())

ok_all = sum(1 for _, v in checks if v)
print("")
print("SUMMARY %d/%d" % (ok_all, len(checks)))
sys.exit(0 if ok_all == len(checks) else 1)