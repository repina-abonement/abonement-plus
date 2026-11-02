#!/bin/bash
# Вход в CRM через мост в Нидерландах, чтобы CRM открывалась у всех: с VPN и без (Юлия, 02.11).
# Запуск на сервере моста (VDSina) от root, один раз:
#   curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/relay/passthrough.sh | bash
# Что делает:
#   - порт 443: для имён CRM (deploy/domains.txt) пересылает ЗАШИФРОВАННЫЙ трафик на сервер CRM в России, не расшифровывая
#     (сертификат и ключ только на сервере CRM, мост данных не видит); остальное, как раньше, мосту Telegram;
#   - порт 80: для имён CRM пересылает на сервер CRM (нужно, чтобы там выдался сертификат https и работала переадресация).
set -e
CRM_IP=185.207.65.245
NAMES=$(curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/domains.txt | grep -v '^#' | tr -d '\r' | xargs)
[ -z "$NAMES" ] && { echo "Нет доменов в deploy/domains.txt"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq libnginx-mod-stream >/dev/null 2>&1 || apt-get install -y -qq nginx-full >/dev/null

# мост Telegram (https) переезжает с 443 на внутренний порт 8443: на 443 теперь распределитель по имени сайта
RELAY=/etc/nginx/sites-available/relay
if [ -f "$RELAY" ]; then
  sed -i -E '/listen \[::\]:443/d' "$RELAY"
  sed -i -E 's/listen 443 ssl( http2)?;/listen 127.0.0.1:8443 ssl;/' "$RELAY"
  awk '!(/listen 127.0.0.1:8443 ssl;/ && seen++)' "$RELAY" > /tmp/relay && cat /tmp/relay > "$RELAY"
fi

MAP=""; for N in $NAMES; do MAP="$MAP    $N crm;\n"; done
mkdir -p /etc/nginx/stream.d
printf "map \$ssl_preread_server_name \$crm_up {\n$MAP    default relay;\n}\nupstream crm { server $CRM_IP:443; }\nupstream relay { server 127.0.0.1:8443; }\nserver {\n    listen 443;\n    ssl_preread on;\n    proxy_pass \$crm_up;\n    proxy_connect_timeout 10s;\n}\n" > /etc/nginx/stream.d/crm.conf
grep -q "stream.d" /etc/nginx/nginx.conf || printf "\nstream {\n    include /etc/nginx/stream.d/*.conf;\n}\n" >> /etc/nginx/nginx.conf

cat > /etc/nginx/sites-available/crm-http <<NGX
server {
  listen 80;
  server_name $NAMES;
  client_max_body_size 100m;
  location / {
    proxy_pass http://$CRM_IP;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
  }
}
NGX
ln -sf /etc/nginx/sites-available/crm-http /etc/nginx/sites-enabled/crm-http
# сторож: CRM раз в минуту присылает «жива», при сбое руководителю в Telegram (deploy/relay/watch.py)
mkdir -p /opt/crm-watch
curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/relay/watch.py -o /opt/crm-watch/watch.py
cat > /etc/systemd/system/crm-watch.service <<UNIT
[Unit]
Description=CRM watchdog
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 /opt/crm-watch/watch.py
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable -q --now crm-watch && systemctl restart crm-watch
if [ -f "$RELAY" ] && ! grep -q "location /watch/" "$RELAY"; then
  sed -i "0,/location = \/ {/s//location \/watch\/ {\n    allow $CRM_IP; deny all;\n    proxy_pass http:\/\/127.0.0.1:8099;\n  }\n  location = \/ {/" "$RELAY"
fi

# переключение на резервный сервер CRM одной командой: crm-switch НОВЫЙ_IP
cat > /usr/local/bin/crm-switch <<'SW'
#!/bin/bash
set -e
NEW="$1"; [ -z "$NEW" ] && { echo "Использование: crm-switch IP_сервера_CRM"; exit 1; }
sed -i -E "s/upstream crm \{ server [0-9.]+:443; \}/upstream crm { server $NEW:443; }/" /etc/nginx/stream.d/crm.conf
sed -i -E "s#proxy_pass http://[0-9.]+;#proxy_pass http://$NEW;#" /etc/nginx/sites-available/crm-http
nginx -t && systemctl reload nginx && echo "Готово: ab-plus.ru теперь ведёт на $NEW"
SW
chmod +x /usr/local/bin/crm-switch

command -v ufw >/dev/null && ufw status | grep -q active && { ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; }

if nginx -t; then
  systemctl reload nginx
  echo
  echo "=============================================="
  echo " Готово. Мост пересылает в CRM: $NAMES"
  echo " Мост Telegram работает как раньше."
  echo "=============================================="
else
  echo "Ошибка в настройке веб-сервера, ничего не перезапущено. Пришлите этот экран."
  exit 1
fi
