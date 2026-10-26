# -*- coding: utf-8 -*-
# UI check: landing page, purchase link, /redeem split.
import os, sys, threading, time, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
TESTDATA = os.path.join(BASE, "testdata_landing_ui")
shutil.rmtree(TESTDATA, ignore_errors=True)
os.makedirs(TESTDATA, exist_ok=True)
os.environ["SMSGATE_ROOT"] = BASE
os.environ["SMSGATE_DATA"] = TESTDATA
os.environ["SMSGATE_PORT"] = "18982"
sys.path.insert(0, BASE)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"C:\Users\Administrator\AppData\Local\ms-playwright")

import uvicorn
from app.main import app
from app import db

db.init()
db.set_setting("shop_url", "https://wzyp.cn/shop/63EZ1CJK")
cfg = uvicorn.Config(app, host="127.0.0.1", port=18982, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.1)

B = "http://127.0.0.1:18982"
SHOP = "https://wzyp.cn/shop/63EZ1CJK"
OUT = r"C:\Users\Administrator\Documents\Codex\2026-10-24\root-ucpwuogj5963-22\outputs"
os.makedirs(OUT, exist_ok=True)
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)[:200]) if extra else ""))
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    br = pw.chromium.launch()
    ctx = br.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(B + "/", wait_until="networkidle")
    check("landing title", "稳定接码" in pg.title(), pg.title())
    links = pg.query_selector_all('a[href="%s"]' % SHOP)
    check("shop links >= 4", len(links) >= 4, len(links))
    check("shop links new tab", all(l.get_attribute("target") == "_blank" for l in links))
    check("shop links noopener", all("noopener" in (l.get_attribute("rel") or "") for l in links))
    check("landing script tags 0", pg.eval_on_selector_all("script", "els => els.length") == 0)
    check("nav anchors", pg.eval_on_selector_all(".lp-links a", "els => els.length") == 4)
    check("segment tiles 5", pg.eval_on_selector_all(".lp-tile", "els => els.length") == 5)
    check("faq items 5", pg.eval_on_selector_all(".lp-faq details", "els => els.length") == 5)
    no_overflow = pg.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2")
    check("desktop no h-overflow", no_overflow)
    pg.screenshot(path=os.path.join(OUT, "landing-desktop.png"), full_page=True)

    pg.click('.lp-cta a[href="/redeem"]')
    pg.wait_for_url("**/redeem", timeout=8000)
    pg.wait_for_selector("#keyInput", timeout=8000)
    check("redeem url", pg.url.endswith("/redeem"), pg.url)
    check("redeem form visible", pg.is_visible("#authForm"))
    check("redeem input visible", pg.is_visible("#keyInput"))
    pg.screenshot(path=os.path.join(OUT, "redeem-entry.png"), full_page=True)

    pg.goto(B + "/c/ABCD-EFGH-IJKL-MNOP", wait_until="networkidle")
    check("card url", "/c/ABCD-EFGH-IJKL-MNOP" in pg.url, pg.url)
    check("card key in dom", pg.get_attribute("#app", "data-key") == "ABCD-EFGH-IJKL-MNOP")
    pg.wait_for_timeout(1200)
    pg.screenshot(path=os.path.join(OUT, "card-entry.png"), full_page=True)

    mob = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    mp = mob.new_page()
    mob_errors = []
    mp.on("pageerror", lambda e: mob_errors.append(str(e)))
    mp.goto(B + "/", wait_until="networkidle")
    m_overflow = mp.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2")
    check("mobile no h-overflow", m_overflow)
    check("mobile shop links >= 3", len(mp.query_selector_all('a[href="%s"]' % SHOP)) >= 3)
    check("mobile hero visible", mp.is_visible(".lp-hero h1"))
    mp.screenshot(path=os.path.join(OUT, "landing-mobile.png"), full_page=True)
    mp.click('.lp-cta a[href="/redeem"]')
    mp.wait_for_url("**/redeem", timeout=8000)
    check("mobile redeem", mp.url.endswith("/redeem"), mp.url)
    mp.screenshot(path=os.path.join(OUT, "redeem-mobile.png"), full_page=True)

    check("no desktop errors", not errors, errors[:3])
    check("no mobile errors", not mob_errors, mob_errors[:3])
    br.close()

ok_n = sum(1 for _, passed in results if passed)
print("")
print("SUMMARY %d/%d" % (ok_n, len(results)))
for name, passed in results:
    if not passed:
        print("  FAILED: " + name)
sys.exit(0 if ok_n == len(results) else 1)