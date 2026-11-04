#!/bin/bash
# Проверка соединений сервера CRM (Юлия, 04.11: у части сотрудников CRM не открывается). Только смотрит, ничего не меняет.
# Запуск на сервере CRM от root:
#   curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/diag.sh | bash
# Паролей, ключей и данных клиентов не выводит: результат можно присылать скриншотом.
H() { echo; echo "===== $* ====="; }
D=ab-plus.ru

H "1. Веб-сервер: версия, порты, HTTP/2, HTTP/3"
nginx -v 2>&1
grep -rhoE "listen[^;]*;|http2[^;]*;|http3[^;]*;|quic[^;]*;|Alt-Svc[^;]*" /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null | sort | uniq -c
echo "-- заголовки ответа (ищем alt-svc / h3):"
curl -sk -o /dev/null -D - --max-time 10 "https://$D/ping.txt" --resolve "$D:443:127.0.0.1" | grep -iE "^HTTP|alt-svc|server" || echo "нет ответа"

H "2. Адреса: IPv4 и IPv6"
echo "A:    $(getent ahostsv4 $D | awk '{print $1}' | sort -u | xargs)"
echo "AAAA: $(getent ahostsv6 $D 2>/dev/null | awk '{print $1}' | grep : | sort -u | xargs)"
ip -br addr | grep -v "^lo"

H "3. Размер пакета (MTU) и автоподбор"
ip -o link | awk '{print $2, $4, $5}' | grep -v "^lo"
echo "tcp_mtu_probing = $(sysctl -n net.ipv4.tcp_mtu_probing)  (1 = включён)"
for s in 1472 1452 1400 1360 1300; do
  if ping -c1 -W2 -M do -s $s ya.ru >/dev/null 2>&1; then echo "пакет $((s+28)) байт до ya.ru: проходит"; else echo "пакет $((s+28)) байт до ya.ru: НЕ проходит"; fi
done
command -v tracepath >/dev/null && tracepath -n -m 8 ya.ru 2>/dev/null | tail -3

H "4. Брандмауэр (ICMP нужен, чтобы большие пакеты не терялись)"
ufw status 2>/dev/null | head -12
iptables -S 2>/dev/null | grep -iE "icmp|mss|DROP" | head -12

H "5. Сертификат: цепочка, OCSP, ключи"
echo | openssl s_client -connect 127.0.0.1:443 -servername $D -showcerts 2>/dev/null | grep -c "BEGIN CERTIFICATE" | sed 's/^/сертификатов в цепочке: /'
echo | openssl s_client -connect 127.0.0.1:443 -servername $D 2>/dev/null | grep -E "Protocol|Cipher is|Verify return|Server Temp Key" | head -5
echo | openssl s_client -connect 127.0.0.1:443 -servername $D -status 2>/dev/null | grep -iE "OCSP Response Status|no response sent" | head -2
if openssl s_client -help 2>&1 | grep -q groups && echo | openssl s_client -connect 127.0.0.1:443 -servername $D -groups X25519MLKEM768:X25519 >/dev/null 2>&1; then
  echo "постквантовый ключ X25519MLKEM768: проверка прошла"
else
  echo "постквантовый ключ: openssl сервера его не умеет (это нормально, браузер тогда берёт X25519)"
fi
openssl version

H "6. Скорость ответа самому себе"
for u in ping.txt ping-big.txt waitlist.js; do
  curl -sk -o /dev/null --resolve "$D:443:127.0.0.1" -w "$u: код %{http_code}, %{size_download} байт, %{time_total} с\n" --max-time 15 "https://$D/$u"
done

H "7. Нагрузка и лимиты"
uptime
free -m | head -2
ss -s | head -3
echo "полуоткрытых соединений (SYN): $(ss -tn state syn-recv 2>/dev/null | tail -n +2 | wc -l)"
grep -rh "worker_connections" /etc/nginx/nginx.conf
df -h / | tail -1

H "8. Ошибки за последние дни"
echo "-- nginx:"
tail -n 15 /var/log/nginx/error.log 2>/dev/null
echo "-- CRM:"
systemctl is-active abonement-plus
journalctl -u abonement-plus --since "3 days ago" --no-pager 2>/dev/null | grep -iE "error|traceback|exception" | tail -n 10
echo "-- журнал соединений CRM:"
ls -la /var/log/crm-diag 2>/dev/null || echo "ещё не включён (включится сам в течение 5 минут после обновления)"
echo
echo "===== Готово. Пришлите скриншот всего окна (можно двумя-тремя частями) ====="
