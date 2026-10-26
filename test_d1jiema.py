# -*- coding: utf-8 -*-
"""d1jiema client tests against a local mock of the real upstream contract."""
import os, sys, threading, time, json, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_d1")
if os.path.isdir(TESTDATA):
    shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
sys.path.insert(0, BASE)

PORT = 18951
STATE = {"calls": [], "lock": threading.Lock(), "msg_delay": 0.0, "mode": "ok", "seq": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, text, status=200):
        body = str(text).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        with STATE["lock"]:
            STATE["calls"].append({"q": q, "headers": dict(self.headers)})
        code = q.get("code")
        if STATE["mode"] == "error_balance":
            return self._send("ERROR:余额不足，请充值")
        if STATE["mode"] == "error_param":
            return self._send("ERROR:参数错误")
        if code == "leftAmount":
            return self._send("68.06")
        if code == "getPhone":
            with STATE["lock"]:
                STATE["seq"] += 1
                seq = STATE["seq"]
            return self._send("1380000%04d" % seq)
        if code == "getMsg":
            if STATE["msg_delay"]:
                time.sleep(STATE["msg_delay"])
            if q.get("phone", "").endswith("9999"):
                return self._send("尚未收到包含关键字“豆包”的短信，请5秒后再收取。请确保设置了正确的关键字。[尚未收到]")
            return self._send("1935823746|0/0.26/【豆包】验证码 654321，用于手机验证码登录，5分钟内有效。")
        return self._send("ERROR:unknown")

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

from app import db, d1jiema, providers

db.init()
for k, v in {
    "d1jiema_token": "test-token", "d1jiema_enabled": "1",
    "d1jiema_base": "http://127.0.0.1:%d/zc/data.php" % PORT,
    "d1jiema_keyword": "豆包", "d1jiema_sms_prefix": "[豆包]", "d1jiema_gap_ms": "120",
}.items():
    db.set_setting(k, v)

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)[:300]) if extra else ""))


def last_call():
    with STATE["lock"]:
        return STATE["calls"][-1] if STATE["calls"] else {}


check("balance", d1jiema.balance() == "68.06")
c = last_call()
check("sends browser UA", "Mozilla" in c["headers"].get("User-Agent", ""), c["headers"].get("User-Agent"))
check("sends referer", c["headers"].get("Referer", "").endswith("/api.html"), c["headers"].get("Referer"))

n = d1jiema.get_phone("豆包", "实卡")
check("phone normalized", n.startswith("138") and len(n) == 11, n)
c = last_call()
check("keyword url encoded upstream", c["q"].get("keyWord") == "豆包", c["q"])
check("cardType forwarded", c["q"].get("cardType") == "实卡", c["q"])
check("token forwarded", c["q"].get("token") == "test-token", c["q"].get("token"))

n2 = d1jiema.get_phone("豆包", "虚卡")
check("second phone differs", n2 != n, (n, n2))

raw = d1jiema.get_msg(n)
check("get msg returns body", "654321" in raw, raw[:60])
check("price/id stripped", "0.26" not in raw and "1935823746" not in raw and raw.startswith("1|"), raw[:80])
check("clean is idempotent", d1jiema.clean(raw) == raw)
check("parse_code on real body", providers.parse_code(raw) == "654321", raw[:80])
check("d1 parse on real body", providers._d1_parse(raw) == "654321", raw[:80])
check("empty mailbox normalized", d1jiema.get_msg("13800009999") == "0|0", d1jiema.get_msg("13800009999"))
check("has_msg false for placeholder", d1jiema.has_msg("0|0") is False)
check("clean keeps foreign text", d1jiema.clean("1|【豆包】验证码 654321") == "1|【豆包】验证码 654321")

STATE["mode"] = "error_balance"
try:
    d1jiema.get_phone("豆包", "实卡")
    busy = False
except d1jiema.D1Busy:
    busy = True
except Exception:
    busy = False
check("balance error -> busy", busy)
STATE["mode"] = "error_param"
try:
    d1jiema.get_phone("豆包", "实卡")
    perr = False
except d1jiema.D1Error:
    perr = True
except Exception:
    perr = False
check("param error -> D1Error", perr)
STATE["mode"] = "ok"

check("parse prefix ok", providers._d1_parse("【豆包】验证码 654321") == "654321")
check("parse rejects other project", providers._d1_parse("【其他】验证码 654321") is None)
check("parse ignores empty", providers._d1_parse("[尚未收到]") is None)
check("parse ignores no code", providers._d1_parse("【豆包】欢迎使用") is None)

# provider poll path: cache + lease means two rapid polls hit upstream once
with STATE["lock"]:
    STATE["calls"] = []
STATE["msg_delay"] = 0.25
num = {"phone": n, "provider": "d1jiema", "country": "cn", "choice": "cn_real"}
r1 = providers.poll(num)
r2 = providers.poll(num)
msg_calls = len([x for x in STATE["calls"] if x["q"].get("code") == "getMsg"])
check("poll returns code", r1.get("code") == "654321", r1)
check("poll cached (single upstream call)", msg_calls == 1, msg_calls)

num2 = {"phone": "13800009999", "provider": "d1jiema", "country": "cn", "choice": "cn_real"}
r3 = providers.poll(num2)
check("empty mailbox is not an error", r3.get("ok") and not r3.get("code"), r3)

check("enabled() true", d1jiema.enabled() is True)
check("test() ok", d1jiema.test().get("ok") is True, d1jiema.test())

print("\n=== %d/%d passed ===" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)