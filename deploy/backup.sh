#!/usr/bin/env bash
# SMS Gate 每日备份：sqlite 在线备份 + 密钥 + 配置
set -euo pipefail
DATA=/opt/smsgate/data
DEST=/opt/smsgate/backups
KEEP=14
mkdir -p "$DEST"
TS=$(date +%Y%m%d_%H%M%S)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

/opt/smsgate/venv/bin/python - "$TMP/app.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect("/opt/smsgate/data/app.db")
dst = sqlite3.connect(sys.argv[1])
with dst:
    src.backup(dst)
dst.close(); src.close()
PY

cp -a "$DATA/fernet.key" "$DATA/secret.key" "$TMP/" 2>/dev/null || true
cp -a /opt/smsgate/data/setup.token "$TMP/" 2>/dev/null || true
tar -czf "$DEST/smsgate_$TS.tar.gz" -C "$TMP" .
find "$DEST" -name 'smsgate_*.tar.gz' -type f -mtime +$KEEP -delete
echo "backup ok: $DEST/smsgate_$TS.tar.gz ($(du -h "$DEST/smsgate_$TS.tar.gz" | cut -f1))"
