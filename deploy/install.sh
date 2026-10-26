#!/usr/bin/env bash
# Установка Абонемент Плюс на чистый сервер Ubuntu.
# Запуск: bash install.sh crm.ваш-домен.ru
set -euo pipefail
DOMAIN="${1:?Укажите домен, например: bash install.sh crm.example.ru}"
REPO="${CRM_REPO:-}"
DIR=/opt/abonement-plus

if ! command -v docker >/dev/null; then
  echo "Ставлю Docker…"
  curl -fsSL https://get.docker.com | sh
fi

if [ -n "$REPO" ] && [ ! -d "$DIR/.git" ]; then
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

if [ ! -f .env ]; then
  PW=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 14)
  cat > .env <<ENV
CRM_DOMAIN=$DOMAIN
CRM_ADMIN_LOGIN=admin
CRM_ADMIN_PASSWORD=$PW
CRM_DEMO=1
ENV
  chmod 600 .env
  echo
  echo "=============================================="
  echo " Логин администратора: admin"
  echo " Пароль администратора: $PW"
  echo " Запишите его и смените после первого входа."
  echo "=============================================="
  echo
fi

# Брандмауэр: только SSH и веб
if command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
fi

docker compose up -d --build
echo "Готово. Откройте https://$DOMAIN"
