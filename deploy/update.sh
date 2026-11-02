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
    # резервный сервер (deploy/standby.sh): код обновляем, но CRM не запускаем, пока не включили crm-activate
    [ -f /etc/abonement-plus.standby ] || systemctl restart abonement-plus abonement-plus-backup
    echo "Обновлено до $(git rev-parse --short HEAD)"
  else
    git reset -q --hard HEAD@{1}
    echo "Новая версия не прошла проверку, оставлена прежняя" >&2
  fi
fi

# Свои домены CRM: перечислены в deploy/domains.txt (по одному в строке). Когда домен уже указывает на этот сервер,
# добавляем его в nginx и получаем сертификат https. Проверка дешёвая, работает при каждом запуске таймера.
NGX=/etc/nginx/sites-available/abonement-plus
# большие файлы (выгрузки ERP, экспорт Telegram, картинки постов): первоначальная настройка пускала только 2 МБ
if [ -f "$NGX" ] && grep -q "client_max_body_size 2m;" "$NGX"; then
  sed -i "s/client_max_body_size 2m;/client_max_body_size 100m;/" "$NGX" && nginx -t -q 2>/dev/null && systemctl reload nginx
fi
# Домен может указывать на этот сервер или на мост в Нидерландах (deploy/relay/passthrough.sh пересылает зашифрованный
# трафик сюда, чтобы CRM открывалась и с VPN): в обоих случаях подключаем домен здесь
RELAY_IP=$(cat "$DIR/deploy/relay/relay_ip.txt" 2>/dev/null | tr -d '[:space:]')
[ -f /etc/abonement-plus.standby ] && exit 0
if [ -f "$DIR/deploy/domains.txt" ] && [ -f "$NGX" ]; then
  MYIP=$(curl -s -4 --max-time 5 https://ifconfig.me || true)
  while read -r D; do
    D=$(echo "$D" | tr -d '[:space:]')
    [ -z "$D" ] && continue
    case "$D" in \#*) continue ;; esac
    grep -q "server_name.*\b$D\b" "$NGX" && continue
    IP=$(getent ahostsv4 "$D" | awk 'NR==1{print $1}' || true)
    if [ -n "$IP" ] && { [ "$IP" = "$MYIP" ] || [ "$IP" = "$RELAY_IP" ]; }; then
      sed -i "0,/server_name /s/server_name \(.*\);/server_name \1 $D;/" "$NGX"
      if nginx -t -q 2>/dev/null; then
        systemctl reload nginx
        NAMES=$(grep -m1 "server_name" "$NGX" | sed 's/.*server_name //; s/;//')
        FIRST=$(echo "$NAMES" | awk '{print $1}')
        ARGS=""; for N in $NAMES; do ARGS="$ARGS -d $N"; done
        # один сертификат на все имена: старый адрес продолжает работать
        certbot --nginx --expand --cert-name "$FIRST" $ARGS --non-interactive --agree-tos --register-unsafely-without-email --redirect -q \
          && echo "Домен $D подключён с https" || echo "Домен $D: сертификат не получился" >&2
      fi
    fi
  done < "$DIR/deploy/domains.txt"
fi
