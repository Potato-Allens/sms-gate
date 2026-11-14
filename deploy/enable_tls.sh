#!/usr/bin/env bash
# Issue a Let's Encrypt certificate for 12456.cfd (+www/api) and enable HTTPS.
set -euo pipefail
DOMAIN="${1:-12456.cfd}"
ROOT=/opt/smsgate
IP="45.207.214.163"

echo "== 1. DNS check =="
IPS=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u || true)
echo "resolved: ${IPS:-none}"
if ! echo "$IPS" | grep -q "^${IP}$"; then
  echo "!! $DOMAIN 还没有解析到 ${IP}，先到 DNS 服务商添加 A 记录再执行本脚本。"
  exit 1
fi

echo "== 2. install vhosts =="
install -m 0644 "$ROOT/deploy/nginx-limits.conf" /etc/nginx/conf.d/smsgate-limits.conf
install -m 0644 "$ROOT/deploy/nginx-smsgate-http.conf" /etc/nginx/sites-available/smsgate-http
install -m 0644 "$ROOT/deploy/nginx-smsgate-domain.conf" /etc/nginx/sites-available/smsgate-domain
install -m 0644 "$ROOT/deploy/nginx-smsgate-api.conf" /etc/nginx/sites-available/smsgate-api
ln -sfn /etc/nginx/sites-available/smsgate-http /etc/nginx/sites-enabled/smsgate-http
ln -sfn /etc/nginx/sites-available/smsgate-domain /etc/nginx/sites-enabled/smsgate-domain
ln -sfn /etc/nginx/sites-available/smsgate-api /etc/nginx/sites-enabled/smsgate-api
nginx -t
systemctl reload nginx

echo "== 3. certbot =="
certbot certonly --nginx -d "$DOMAIN" -d "www.$DOMAIN" -d "api.$DOMAIN" \
  --non-interactive --agree-tos -m "admin@$DOMAIN" --keep-until-expiring

echo "== 4. verify =="
nginx -t
systemctl reload nginx
curl -sS -o /dev/null -w "https status: %{http_code}\n" "https://$DOMAIN/api/health" || true
echo "OK: https://$DOMAIN"
