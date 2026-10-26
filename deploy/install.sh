#!/usr/bin/env bash
# Установка «Абонемент Плюс» на чистый сервер Ubuntu 24.04.
# Запуск:
#   curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/install.sh | bash
# Свой домен (необязательно):  ... | bash -s crm.example.ru
set -euo pipefail

REPO="${CRM_REPO:-https://github.com/repina-abonement/abonement-plus.git}"
DIR=/opt/abonement-plus
IP=$(curl -fsS4 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
DOMAIN="${1:-${IP//./-}.sslip.io}"

echo "== Ставлю нужные программы =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 nginx certbot python3-certbot-nginx ufw >/dev/null

echo "== Скачиваю код =="
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q
else
  git clone -q "$REPO" "$DIR"
fi

id -u crm >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin crm
mkdir -p /var/lib/abonement-plus/backups
chown -R crm:crm /var/lib/abonement-plus
chmod 700 /var/lib/abonement-plus

if [ ! -f /etc/abonement-plus.env ]; then
  PW=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 14 || true)
  cat > /etc/abonement-plus.env <<ENV
CRM_HOST=127.0.0.1
CRM_PORT=8000
CRM_DB=/var/lib/abonement-plus/crm.sqlite3
CRM_BACKUP_DIR=/var/lib/abonement-plus/backups
CRM_ADMIN_LOGIN=admin
CRM_ADMIN_PASSWORD=$PW
CRM_DEMO=1
CRM_SECURE_COOKIE=1
ENV
  chmod 600 /etc/abonement-plus.env
  NEW_PW=$PW
fi

cat > /etc/systemd/system/abonement-plus.service <<UNIT
[Unit]
Description=Abonement Plus CRM
After=network.target

[Service]
User=crm
WorkingDirectory=$DIR
EnvironmentFile=/etc/abonement-plus.env
ExecStart=/usr/bin/python3 -m crm.server
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/var/lib/abonement-plus

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/abonement-plus-backup.service <<UNIT
[Unit]
Description=Abonement Plus daily backup

[Service]
User=crm
WorkingDirectory=$DIR
EnvironmentFile=/etc/abonement-plus.env
ExecStart=/usr/bin/python3 -m crm.backup
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/abonement-plus-update.service <<UNIT
[Unit]
Description=Abonement Plus auto-update from GitHub

[Service]
Type=oneshot
ExecStart=/bin/bash $DIR/deploy/update.sh
UNIT

cat > /etc/systemd/system/abonement-plus-update.timer <<UNIT
[Unit]
Description=Check GitHub for Abonement Plus updates every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable -q --now abonement-plus abonement-plus-backup abonement-plus-update.timer
systemctl restart abonement-plus

echo "== Настраиваю веб-сервер =="
cat > /etc/nginx/sites-available/abonement-plus <<NGX
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 2m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGX
ln -sf /etc/nginx/sites-available/abonement-plus /etc/nginx/sites-enabled/abonement-plus
rm -f /etc/nginx/sites-enabled/default
nginx -t -q && systemctl reload nginx

ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

echo "== Получаю сертификат для https =="
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect -q \
  || echo "!! Сертификат не получился, пока сайт доступен по http://$DOMAIN — напишите Claude"

echo
echo "=================================================="
echo " Готово! Откройте:  https://$DOMAIN"
echo " Автообновление с GitHub включено (каждые 5 минут)."
echo " Логин:   admin"
if [ -n "${NEW_PW:-}" ]; then
  echo " Пароль:  $NEW_PW"
  echo " Запишите пароль. После входа смените его в Настройках."
else
  echo " Пароль:  прежний (посмотреть: cat /etc/abonement-plus.env)"
fi
echo "=================================================="
