#!/usr/bin/env bash
# Автообновление «Абонемент Плюс»: берёт новую версию с GitHub и перезапускает CRM.
# Запускается таймером abonement-plus-update.timer каждые 5 минут.
set -euo pipefail
DIR=/opt/abonement-plus
cd "$DIR"
git fetch -q origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  git reset -q --hard origin/main
  # проверка перед перезапуском: если код не собирается, остаёмся на старой версии
  if python3 -m compileall -q crm >/dev/null; then
    systemctl restart abonement-plus abonement-plus-backup
    echo "Обновлено до $(git rev-parse --short HEAD)"
  else
    git reset -q --hard HEAD@{1}
    echo "Новая версия не прошла проверку, оставлена прежняя" >&2
  fi
fi
