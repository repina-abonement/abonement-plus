#!/bin/bash
# Мост для Telegram. Запуск на чистом сервере Ubuntu/Debian за границей, от root:
#   curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/relay/setup.sh | bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get install -y nginx certbot python3-certbot-nginx curl
CRM_IP=185.207.65.245
CRM_HOST=185-207-65-245.sslip.io
IP=$(curl -s4 https://ifconfig.me || hostname -I | awk '{print $1}')
HOST=$(echo "$IP" | tr . -).sslip.io
cat > /etc/nginx/sites-available/relay <<NGX
server {
  listen 80;
  server_name $HOST;
  client_max_body_size 50m;
  location /tg/ {
    allow $CRM_IP; deny all;
    proxy_pass https://api.telegram.org/;
    proxy_ssl_server_name on;
    proxy_set_header Host api.telegram.org;
    proxy_read_timeout 120s;
  }
  location /hook/ {
    allow 149.154.160.0/20; allow 91.108.4.0/22; deny all;
    proxy_pass https://$CRM_HOST/hooks/;
    proxy_ssl_server_name on;
    proxy_set_header Host $CRM_HOST;
  }
  location = / { return 200 "ok\n"; }
}
NGX
ln -sf /etc/nginx/sites-available/relay /etc/nginx/sites-enabled/relay
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d "$HOST" --non-interactive --agree-tos --register-unsafely-without-email --redirect || true
echo "https://$HOST" > /root/relay-url.txt
