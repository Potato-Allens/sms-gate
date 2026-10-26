# -*- coding: utf-8 -*-
"""UI regression: interrupted setup shows resume form, binds TOTP, enters admin."""
import os, shutil, sys, threading, time
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "testdata_ui")
if os.path.isdir(DATA):
    shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = DATA
os.environ["SMSGATE_PORT"] = "18896"
sys.path.insert(0, BASE)
import requests, uvicorn
from app.main import app
from app import db, security
db.init()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18896, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(60):
    if server.started: break
    time.sleep(0.1)
B = "http://127.0.0.1:18896"
s = requests.Session(); s.trust_env = False
token = open(os.path.join(DATA, "setup.token"), encoding="utf-8").read().strip()
r = s.post(B + "/admin/api/setup", json={"token": token, "username": "admin", "password": "StrongPass123"})
print("create pending:", r.status_code, r.json().get("ok"))
from playwright.sync_api import sync_playwright
ok = True
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(B + "/admin/setup?t=" + token)
    page.wait_for_timeout(700)
    title = page.locator("#setupTitle").inner_text()
    btn = page.locator("#btnSetup").inner_text()
    print("resume title:", title, "| button:", btn)
    ok = ok and title == "继续完成初始化" and btn == "继续绑定验证器"
    page.fill("input[name=username]", "admin")
    page.fill("input[name=password]", "StrongPass123")
    page.fill("input[name=password2]", "StrongPass123")
    page.click("#btnSetup")
    page.wait_for_selector("#setup2:not(.hidden)", timeout=5000)
    secret = page.locator("#secretText").inner_text().strip()
    print("secret length:", len(secret))
    ok = ok and len(secret) >= 16
    code = security.totp_at(secret)
    boxes = page.locator("#totpBoxes input")
    for i, ch in enumerate(code):
        boxes.nth(i).fill(ch)
    page.click("#btnVerify")
    page.wait_for_url("**/admin", timeout=8000)
    print("after bind url:", page.url)
    ok = ok and page.url.rstrip("/").endswith("/admin")
    # pending hint on login page (separate context, no setup cookie)
    ctx2 = browser.new_context()
    p2 = ctx2.new_page()
    p2.goto(B + "/admin/login")
    p2.wait_for_timeout(700)
    hint_visible = p2.locator("#setupHint").is_visible()
    print("login setup hint (should be false after bind):", hint_visible)
    ok = ok and (hint_visible is False)
    browser.close()
server.should_exit = True
print("=== %s ===" % ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)