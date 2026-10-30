# -*- coding: utf-8 -*-
"""构建商城真实品牌图标：Simple Icons（SVG，品牌色）优先，Google favicon（PNG）兜底。

用法（在服务器或本机有外网的环境执行）：
    python tools/build_icons.py [--db data/app.db] [--dir app/static/icons] [--update-db]

产出：
  app/static/icons/*.svg|*.png   图标文件
  app/static/icons/index.json   关键词 -> 文件名（shop.icon_for 读取它）
数据库里的 shop_products.icon 使用 "i:<文件名>" 前缀，前端渲染成 <img>。
"""
from __future__ import annotations
import argparse, json, os, sqlite3, struct, urllib.request

SI = {  # simpleicons slug -> 文件名
    "claude": "claude.svg", "uber": "uber.svg", "telegram": "telegram.svg",
    "whatsapp": "whatsapp.svg", "paypal": "paypal.svg", "coinbase": "coinbase.svg",
    "github": "github.svg", "klarna": "klarna.svg", "naver": "naver.svg",
    "tiktok": "tiktok.svg", "vk": "vk.svg", "vercel": "vercel.svg", "viber": "viber.svg",
    "gojek": "gojek.svg", "bilibili": "bilibili.svg", "netflix": "netflix.svg",
    "xiaohongshu": "xiaohongshu.svg", "baidu": "baidu.svg", "apple": "apple.svg",
    "ebay": "ebay.svg", "facebook": "facebook.svg", "kakaotalk": "kakaotalk.svg",
    "qwen": "qwen.svg", "pubg": "pubg.svg", "payoneer": "payoneer.svg",
    "tripdotcom": "tripdotcom.svg", "neteasecloudmusic": "neteasecloudmusic.svg",
}
GF = {  # 域名 -> 文件名
    "dola.ai": "dola.png", "skype.com": "skype.png", "truthsocial.com": "truthsocial.png",
    "amazon.com": "amazon.png", "twilio.com": "twilio.png", "coupang.com": "coupang.png",
    "shein.com": "shein.png", "plaid.com": "plaid.png", "moonpay.com": "moonpay.png",
    "yuewen.com": "yuewen.png", "toutiao.com": "toutiao.png", "doubao.com": "doubao.png",
    "seagm.com": "seagm.png", "zeabur.com": "zeabur.png", "mchat.com": "mchat.png",
    "shanda.com": "shengqu.png", "163.com": "netease163.png",
}
KEYWORDS = {
    "claude": "claude.svg", "dola": "dola.png", "uber": "uber.svg", "千问": "qwen.svg",
    "携程": "tripdotcom.svg", "skype": "skype.png", "微软": "skype.png",
    "网易": "neteasecloudmusic.svg", "whatsapp": "whatsapp.svg", "mchat": "mchat.png",
    "coinbase": "coinbase.svg", "github": "github.svg", "kakao": "kakaotalk.svg",
    "klarna": "klarna.svg", "moonpay": "moonpay.png", "naver": "naver.svg",
    "paypal": "paypal.svg", "plaid": "plaid.png", "seagm": "seagm.png",
    "tiktok": "tiktok.svg", "truth": "truthsocial.png", "twilio": "twilio.png",
    "vkontakte": "vk.svg", "vercel": "vercel.svg", "viber": "viber.svg",
    "gojek": "gojek.svg", "gopay": "gojek.svg", "亚马逊": "amazon.png",
    "amazon": "amazon.png", "头条": "toutiao.png", "哔哩": "bilibili.svg",
    "bilibili": "bilibili.svg", "netflix": "netflix.svg", "奈飞": "netflix.svg",
    "小红书": "xiaohongshu.svg", "shein": "shein.png", "希音": "shein.png",
    "派安盈": "payoneer.svg", "百度": "baidu.svg", "pubg": "pubg.svg",
    "绝地": "pubg.svg", "coupang": "coupang.png", "쿠팡": "coupang.png",
    "zeabur": "zeabur.png", "apple": "apple.svg", "ebay": "ebay.svg",
    "facebook": "facebook.svg", "豆包": "doubao.png", "阅文": "yuewen.png",
    "telegram": "telegram.svg", "电报": "telegram.svg", "飞机": "telegram.svg",
    "盛趣": "shengqu.png",
}


def fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="app/static/icons")
    ap.add_argument("--db", default="data/app.db")
    ap.add_argument("--update-db", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    done = 0
    for slug, fn in SI.items():
        try:
            raw = fetch("https://cdn.simpleicons.org/" + slug)
            if raw.startswith(b"<svg") and len(raw) > 80:
                open(os.path.join(a.dir, fn), "wb").write(raw)
                done += 1
        except Exception as e:
            print("skip si", slug, e)
    for dom, fn in GF.items():
        try:
            raw = fetch("https://www.google.com/s2/favicons?domain=%s&sz=128" % dom)
            if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                w, h = struct.unpack(">II", raw[16:24])
                if w >= 24 and h >= 24:
                    open(os.path.join(a.dir, fn), "wb").write(raw)
                    done += 1
        except Exception as e:
            print("skip gf", dom, e)
    idx = {k: v for k, v in KEYWORDS.items() if os.path.exists(os.path.join(a.dir, v))}
    open(os.path.join(a.dir, "index.json"), "w", encoding="utf-8").write(
        json.dumps(idx, ensure_ascii=False, indent=1, sort_keys=True))
    print("icons", done, "keywords", len(idx))
    if a.update_db and os.path.exists(a.db):
        db = sqlite3.connect(a.db)
        db.row_factory = sqlite3.Row
        n = 0
        for r in db.execute("SELECT id, name, icon FROM shop_products"):
            low = (r["name"] or "").lower()
            for kw in sorted(idx, key=len, reverse=True):
                if kw in low:
                    ico = "i:" + idx[kw]
                    if ico != r["icon"]:
                        db.execute("UPDATE shop_products SET icon=? WHERE id=?", (ico, r["id"]))
                        n += 1
                    break
        db.commit()
        print("db updated", n)


if __name__ == "__main__":
    main()
