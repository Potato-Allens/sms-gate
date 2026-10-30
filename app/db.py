# -*- coding: utf-8 -*-
"""SQLite storage layer (WAL, per-thread connections)."""
from __future__ import annotations
import sqlite3, threading, time
from contextlib import contextmanager
from . import config

_local = threading.local()
_init_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  totp_secret TEXT DEFAULT '',
  totp_on INTEGER NOT NULL DEFAULT 0,
  pending_secret TEXT DEFAULT '',
  setup_done INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  last_login_at INTEGER DEFAULT 0,
  last_login_ip TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS admin_sessions(
  sid TEXT PRIMARY KEY,
  admin_id INTEGER NOT NULL,
  csrf TEXT NOT NULL,
  ip TEXT DEFAULT '',
  ua TEXT DEFAULT '',
  stage TEXT NOT NULL DEFAULT 'active',
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS card_sessions(
  sid TEXT PRIMARY KEY,
  card_id INTEGER NOT NULL,
  csrf TEXT NOT NULL,
  ip TEXT DEFAULT '',
  ua TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS batches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_no TEXT NOT NULL,
  count INTEGER NOT NULL,
  note TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cards(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key_hash TEXT UNIQUE NOT NULL,
  key_prefix TEXT NOT NULL,
  note TEXT DEFAULT '',
  max_codes INTEGER NOT NULL DEFAULT 1,
  used_codes INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  batch_id INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL,
  created_by TEXT DEFAULT '',
  expires_at INTEGER DEFAULT 0,
  first_used_at INTEGER DEFAULT 0,
  last_used_at INTEGER DEFAULT 0,
  last_ip TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_cards_status ON cards(status, id);
CREATE INDEX IF NOT EXISTS ix_cards_batch ON cards(batch_id);
CREATE TABLE IF NOT EXISTS redemptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id INTEGER NOT NULL,
  ip TEXT DEFAULT '',
  ua TEXT DEFAULT '',
  country TEXT DEFAULT '',
  result TEXT NOT NULL,
  detail TEXT DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_redemptions_time ON redemptions(created_at);
CREATE TABLE IF NOT EXISTS card_choices(
  card_id INTEGER PRIMARY KEY,
  choice TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  card_type TEXT NOT NULL DEFAULT '',
  number_id INTEGER NOT NULL DEFAULT 0,
  alloc_at INTEGER NOT NULL DEFAULT 0,
  ip TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS numbers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  country TEXT NOT NULL DEFAULT 'us',
  provider TEXT NOT NULL DEFAULT 'pool',
  sms_url TEXT DEFAULT '',
  ext_id TEXT DEFAULT '',
  choice TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'available',
  card_id INTEGER DEFAULT 0,
  allocated_at INTEGER DEFAULT 0,
  used_at INTEGER DEFAULT 0,
  note TEXT DEFAULT '',
  price REAL DEFAULT 0,
  card_type TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_numbers_pick ON numbers(status, country, id);
CREATE INDEX IF NOT EXISTS ix_numbers_card ON numbers(card_id);
CREATE TABLE IF NOT EXISTS sms_records(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id INTEGER NOT NULL,
  number_id INTEGER NOT NULL,
  phone TEXT DEFAULT '',
  country TEXT DEFAULT '',
  code TEXT DEFAULT '',
  raw TEXT DEFAULT '',
  polls INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'waiting',
  created_at INTEGER NOT NULL,
  got_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_sms_time ON sms_records(created_at);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  level TEXT NOT NULL DEFAULT 'info',
  ip TEXT DEFAULT '',
  actor TEXT DEFAULT '',
  card_id INTEGER DEFAULT 0,
  detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, ts);
CREATE TABLE IF NOT EXISTS ips(
  ip TEXT PRIMARY KEY,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  req_count INTEGER NOT NULL DEFAULT 0,
  fail_count INTEGER NOT NULL DEFAULT 0,
  blocked_until INTEGER NOT NULL DEFAULT 0,
  block_reason TEXT DEFAULT '',
  note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS login_guard(
  key TEXT PRIMARY KEY,
  fails INTEGER NOT NULL DEFAULT 0,
  first_fail INTEGER NOT NULL DEFAULT 0,
  last_fail INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rate_limits(
  bucket TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,
  cnt INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locks(
  name TEXT PRIMARY KEY,
  owner TEXT DEFAULT '',
  until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS nex_queue(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT NOT NULL,
  card_id INTEGER NOT NULL,
  country TEXT NOT NULL,
  choice TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '',
  card_type TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'waiting',
  created_at INTEGER NOT NULL,
  started_at INTEGER NOT NULL DEFAULT 0,
  done_at INTEGER NOT NULL DEFAULT 0,
  number_id INTEGER NOT NULL DEFAULT 0,
  tries INTEGER NOT NULL DEFAULT 0,
  err TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS relay_cards(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT UNIQUE NOT NULL,
  phone TEXT NOT NULL,
  country TEXT NOT NULL DEFAULT '',
  upstream_enc TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  batch_id INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL DEFAULT '',
  expires_at INTEGER NOT NULL DEFAULT 0,
  reads INTEGER NOT NULL DEFAULT 0,
  last_read_at INTEGER NOT NULL DEFAULT 0,
  last_ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_nexq_status ON nex_queue(status, id);
CREATE INDEX IF NOT EXISTS ix_nexq_card ON nex_queue(card_id, status);
CREATE INDEX IF NOT EXISTS ix_numbers_pool ON numbers(provider,status,country,id);
CREATE INDEX IF NOT EXISTS ix_sms_card ON sms_records(card_id,id);
CREATE INDEX IF NOT EXISTS ix_sms_number ON sms_records(number_id,id);
CREATE INDEX IF NOT EXISTS ix_relay_status ON relay_cards(status, id);
CREATE INDEX IF NOT EXISTS ix_relay_phone ON relay_cards(phone);
CREATE TABLE IF NOT EXISTS rl(
  bucket TEXT PRIMARY KEY,
  start INTEGER NOT NULL,
  cnt INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cards_prefix ON cards(key_prefix);
CREATE INDEX IF NOT EXISTS ix_redemptions_created ON redemptions(created_at);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);

-- ============================ 号码商城 ============================
CREATE TABLE IF NOT EXISTS shop_users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  balance_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  note TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  last_login_at INTEGER DEFAULT 0,
  last_ip TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS shop_sessions(
  sid TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  csrf TEXT NOT NULL,
  ip TEXT DEFAULT '',
  ua TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_shop_sess_user ON shop_sessions(user_id);
CREATE TABLE IF NOT EXISTS shop_products(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id INTEGER NOT NULL,
  ptype INTEGER NOT NULL DEFAULT 1,
  name TEXT NOT NULL,
  icon TEXT DEFAULT '',
  cate_id INTEGER DEFAULT 0,
  cate_name TEXT DEFAULT '',
  country TEXT DEFAULT 'us',
  card_type TEXT DEFAULT '',
  upstream_price_usd REAL DEFAULT 0,
  success REAL DEFAULT 0,
  stock INTEGER DEFAULT 0,
  price_cents INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  sort INTEGER NOT NULL DEFAULT 0,
  note TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_shop_prod_key ON shop_products(app_id, ptype);
CREATE INDEX IF NOT EXISTS ix_shop_prod_on ON shop_products(enabled, sort DESC, id);
CREATE TABLE IF NOT EXISTS shop_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  app_id INTEGER DEFAULT 0,
  app_name TEXT DEFAULT '',
  country TEXT DEFAULT '',
  card_type TEXT DEFAULT '',
  price_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  relay_card_id INTEGER DEFAULT 0,
  relay_token TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  link TEXT DEFAULT '',
  idem TEXT DEFAULT '',
  error TEXT DEFAULT '',
  ip TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  done_at INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_shop_order_idem ON shop_orders(user_id, idem);
CREATE INDEX IF NOT EXISTS ix_shop_order_user ON shop_orders(user_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_shop_order_time ON shop_orders(created_at);
CREATE TABLE IF NOT EXISTS shop_rcodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash TEXT UNIQUE NOT NULL,
  code_prefix TEXT DEFAULT '',
  amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  batch TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  used_by INTEGER DEFAULT 0,
  used_at INTEGER DEFAULT 0,
  expires_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_shop_rcode_batch ON shop_rcodes(batch, id);
CREATE INDEX IF NOT EXISTS ix_shop_rcode_status ON shop_rcodes(status, id);
CREATE TABLE IF NOT EXISTS shop_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  delta_cents INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT DEFAULT '',
  note TEXT DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_shop_ledger_user ON shop_ledger(user_id, id DESC);
CREATE TABLE IF NOT EXISTS shop_stock(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL DEFAULT 'card',
  ref_id INTEGER NOT NULL DEFAULT 0,
  link TEXT NOT NULL DEFAULT '',
  phone TEXT DEFAULT '',
  country TEXT DEFAULT '',
  cate_name TEXT DEFAULT '',
  card_type TEXT DEFAULT '',
  price_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'available',
  batch TEXT DEFAULT '',
  order_id INTEGER DEFAULT 0,
  note TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  sold_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_shop_stock_pick ON shop_stock(status, cate_name, card_type, price_cents, id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_shop_stock_uniq ON shop_stock(kind, ref_id);
"""

DEFAULTS = {
    "provider": "pool",
    "poll_interval": "15",
    "max_numbers_per_card": "1",
    "max_alloc_attempts": "3",
    "number_ttl_min": "20",
    "sms_activate_key": "",
    "sms_activate_service": "go",
    "fivesim_key": "",
    "fivesim_product": "any",
    "fivesim_operator": "any",
    "nexsms_key": "",
    "nexsms_base": "https://api.nexsms.net",
    "nexsms_project": "豆包",
    "nexsms_sms_prefix": "[豆包]",
    "nexsms_type": "1",
    "nexsms_type_strict": "1",
    "nexsms_expiry": "0",
    "nexsms_prefix": "",
    "nexsms_app_id": "",
    "nexsms_batch_qty": "5",
    "nexsms_stock_target": "5",
    "nexsms_pool_ttl_min": "1440",
    "nexsms_buy_wait_sec": "12",
    "nexsms_proxy": "",
    "nexsms_queue_concurrency": "5",
    "nexsms_queue_max": "200",
    "nexsms_queue_timeout_sec": "600",
    "nexsms_cooldown_sec": "30",
    "nexsms_cooldown_until": "0",
    "nexsms_429_streak": "0",
    "site_note": "卡密仅可接一次码，取码成功后自动核销。",
    "shop_url": "https://wzyp.cn/shop/63EZ1CJK",
    "relay_base": "https://api.12456.cfd",
    "relay_rate": "60",
    "relay_ip_rate": "600",
    "relay_bad_rate": "300",
    "relay_on": "1",
    "allow_countries": "us,gb,hk",
    "allow_choices": "us,gb,hk,cn_real,cn_virtual",
    "d1jiema_enabled": "1",
    "d1jiema_token": "",
    "d1jiema_base": "https://api.d1jiema.com/zc/data.php",
    "d1jiema_keyword": "豆包",
    "d1jiema_sms_prefix": "[豆包]",
    "d1jiema_gap_ms": "300",
    "auto_ban_enabled": "1",
    "admin_ip_allow": "",
    "exit_grace_sec": "8",
    "shop_on": "1",
    "shop_register_on": "1",
    "shop_usd_cny": "7.20",
    "shop_markup_pct": "40",
    "shop_allow_oos": "0",
    "shop_order_days": "0",
    "shop_buy_per_hour": "20",
    "shop_min_recharge": "1000",
    "shop_sync_cates": "",
    "shop_sync_pages": "1",
    "shop_max_orders_per_user": "3",
    "shop_stock_on": "1",
    "shop_register_need_code": "1",
}


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(str(config.DB_PATH), timeout=20, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=20000")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA cache_size=-32000")
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA mmap_size=268435456")
        c.execute("PRAGMA wal_autocheckpoint=1000")
        _local.conn = c
    return c


def init() -> None:
    with _init_lock:
        c = conn()
        c.executescript(SCHEMA)
        def _addcol(table, col, ddl):
            cols = [r['name'] for r in c.execute('PRAGMA table_info(%s)' % table)]
            if col not in cols:
                try:
                    c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, ddl))
                except Exception:
                    pass

        _addcol('admin_sessions', 'stage', "TEXT NOT NULL DEFAULT 'active'")
        _addcol('numbers', 'choice', "TEXT NOT NULL DEFAULT ''")
        _addcol('numbers', 'card_type', "TEXT NOT NULL DEFAULT ''")
        _addcol('relay_cards', 'card_type', "TEXT NOT NULL DEFAULT ''")
        _addcol('nex_queue', 'choice', "TEXT NOT NULL DEFAULT ''")
        _addcol('nex_queue', 'provider', "TEXT NOT NULL DEFAULT ''")
        _addcol('nex_queue', 'card_type', "TEXT NOT NULL DEFAULT ''")
        _addcol('card_choices', 'alloc_at', "INTEGER NOT NULL DEFAULT 0")
        _addcol('cards', 'choice', "TEXT NOT NULL DEFAULT ''")
        try:
            c.execute("CREATE INDEX IF NOT EXISTS ix_nexq_choice ON nex_queue(status,choice,id)")
        except Exception:
            pass
        # An admin row is only "ready" once Google Authenticator is bound.
        c.execute("UPDATE admins SET setup_done=0 WHERE totp_on=0")
        now = int(time.time())
        for k, v in DEFAULTS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        # touch
        c.execute("SELECT 1")


@contextmanager
def tx():
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise


def q(sql: str, args=()):
    return conn().execute(sql, args).fetchall()


def q1(sql: str, args=()):
    return conn().execute(sql, args).fetchone()


def get_setting(key: str, default: str = "") -> str:
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    conn().execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def all_settings() -> dict:
    return {r["key"]: r["value"] for r in q("SELECT key,value FROM settings")}