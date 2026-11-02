#!/bin/bash
# Защита сервера (CRM в Timeweb, мост в VDSina, резервный сервер). Запуск от root, можно повторять:
#   curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/harden.sh | bash
# Что делает:
#   - автоматические обновления безопасности системы (unattended-upgrades);
#   - защита от подбора пароля к серверу: 5 неверных попыток - адрес закрыт на час (fail2ban);
#   - брандмауэр: открыты только вход на сервер (22), сайт (80, 443);
#   - вход на сервер по паролю выключается ТОЛЬКО если уже добавлен ключ (иначе можно потерять доступ).
set -e
export DEBIAN_FRONTEND=noninteractive
echo "== Обновления безопасности =="
apt-get update -qq
apt-get install -y -qq unattended-upgrades fail2ban ufw >/dev/null
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable -q --now unattended-upgrades

echo "== Защита от подбора пароля =="
cat > /etc/fail2ban/jail.d/crm.local <<'EOF'
[sshd]
enabled = true
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable -q fail2ban && systemctl restart fail2ban

echo "== Брандмауэр =="
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "== Вход на сервер =="
if [ -s /root/.ssh/authorized_keys ]; then
  mkdir -p /etc/ssh/sshd_config.d
  printf "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n" > /etc/ssh/sshd_config.d/10-crm-keys.conf
  if sshd -t; then systemctl reload ssh 2>/dev/null || systemctl reload sshd; echo "Вход теперь только по ключу (пароль выключен)"; fi
else
  echo "Ключа для входа ещё нет: вход по паролю оставлен, подбор пароля закрыт fail2ban."
fi
echo
echo "=============================================="
echo " Готово: обновления, защита от подбора, брандмауэр."
echo "=============================================="
