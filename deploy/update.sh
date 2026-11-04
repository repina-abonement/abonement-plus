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
# сжатие: страницы CRM (около 800 КБ скриптов и стилей) уходят в 4 раза меньше, на слабом мобильном интернете это заметно
GZ=/etc/nginx/conf.d/crm-gzip.conf
if [ ! -f "$GZ" ] && [ ! -f "$GZ.off" ] && [ -d /etc/nginx/conf.d ]; then
  printf "gzip_vary on;\ngzip_proxied any;\ngzip_comp_level 5;\ngzip_min_length 1024;\ngzip_types text/css application/javascript text/javascript application/json image/svg+xml;\n" > "$GZ"
  if nginx -t -q 2>/dev/null; then systemctl reload nginx; else rm -f "$GZ"; touch "$GZ.off"; fi
fi
# Проверка соединений (Юлия, 04.11: у части сотрудников CRM не открывается). Журнал: кто подключился, по какому TLS и ключу,
# сколько отправлено, сколько длилось; ошибки рукопожатий. Без паролей и данных клиентов. Читает только владелец в CRM
DIAG=/etc/nginx/conf.d/crm-diag.conf
if [ ! -f "$DIAG.off" ] && [ -d /etc/nginx/conf.d ]; then
  mkdir -p /var/log/crm-diag && chmod 755 /var/log/crm-diag
  WANT_DIAG='log_format crmdiag '"'"'$time_iso8601 $remote_addr $ssl_protocol $ssl_curve $status $bytes_sent $request_time $connection $connection_requests "$request" "$http_user_agent"'"'"';
access_log /var/log/crm-diag/access.log crmdiag;
error_log /var/log/crm-diag/error.log info;'
  if [ "$(cat "$DIAG" 2>/dev/null)" != "$WANT_DIAG" ]; then
    echo "$WANT_DIAG" > "$DIAG"
    if nginx -t -q 2>/dev/null; then systemctl reload nginx; else rm -f "$DIAG"; touch "$DIAG.off"; fi
  fi
  for f in /var/log/crm-diag/*.log; do  # журнал не больше 30 МБ
    [ -f "$f" ] && chmod 644 "$f" && [ "$(stat -c %s "$f")" -gt 31457280 ] && tail -n 20000 "$f" > "$f.tmp" && cat "$f.tmp" > "$f" && rm -f "$f.tmp"
  done
fi
# Автоподбор размера пакета: если большие пакеты где-то по дороге теряются, ядро само уменьшает их, а не ждёт вечно.
# Стандартная безопасная настройка, у кого всё работает, ничего не меняется
if [ "$(sysctl -n net.ipv4.tcp_mtu_probing 2>/dev/null)" != "1" ]; then
  echo "net.ipv4.tcp_mtu_probing = 1" > /etc/sysctl.d/90-crm-mtu.conf && sysctl -q -p /etc/sysctl.d/90-crm-mtu.conf || true
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
      nginx -t -q 2>/dev/null && systemctl reload nginx || true
    fi
  done < "$DIR/deploy/domains.txt"
  # сертификат https на все имена. Если в прошлый раз не получился (домен только что перенесли, DNS ещё не обновился),
  # пробуем снова, но не чаще раза в 30 минут: Let's Encrypt блокирует частые неудачные попытки
  NAMES=$(grep -m1 "server_name" "$NGX" | sed 's/.*server_name //; s/;//')
  FIRST=$(echo "$NAMES" | awk '{print $1}')
  CERT=/etc/letsencrypt/live/$FIRST/cert.pem
  HAVE=$(openssl x509 -in "$CERT" -noout -ext subjectAltName 2>/dev/null || true)
  MISS=""; for N in $NAMES; do echo "$HAVE" | grep -q "DNS:$N\b" || MISS="$MISS $N"; done
  STAMP=/var/lib/abonement-plus/.cert-try
  if [ -n "$MISS" ] && { [ ! -f "$STAMP" ] || [ -n "$(find "$STAMP" -mmin +30)" ]; }; then
    touch "$STAMP"
    ARGS=""; for N in $NAMES; do ARGS="$ARGS -d $N"; done
    # один сертификат на все имена: старый адрес продолжает работать
    if certbot --nginx --expand --cert-name "$FIRST" $ARGS --non-interactive --agree-tos --register-unsafely-without-email --redirect -q; then
      echo "Сертификат https выдан для:$MISS"; rm -f "$STAMP"
    else
      echo "Сертификат для$MISS не получился, следующая попытка через 30 минут" >&2
    fi
  fi
  # http://ab-plus.ru → https://ab-plus.ru. certbot переадресует только первое имя, остальные получали «404 Not Found»
  RED=/etc/nginx/sites-available/crm-https-redirect
  EXTRA=$(echo "$NAMES" | awk '{$1=""; print}' | xargs)
  if [ -n "$EXTRA" ] && [ -z "$MISS" ]; then
    WANT="server {
  listen 80;
  server_name $EXTRA;
  location / { return 301 https://\$host\$request_uri; }
}"
    if [ "$(cat "$RED" 2>/dev/null)" != "$WANT" ]; then
      echo "$WANT" > "$RED" && ln -sf "$RED" /etc/nginx/sites-enabled/crm-https-redirect
      if nginx -t -q 2>/dev/null; then systemctl reload nginx; else rm -f "$RED" /etc/nginx/sites-enabled/crm-https-redirect; fi
    fi
  fi
fi
