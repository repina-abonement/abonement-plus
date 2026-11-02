#!/bin/bash
# Подготовка сервера к закрытому репозиторию GitHub (чтобы код CRM не был виден всем). Запуск от root на сервере CRM
# и на резервном сервере, ДО того как репозиторий сделают закрытым:
#   curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/private-repo.sh | bash
# Создаёт ключ «только чтение» для этого сервера и показывает его. Ключ добавляется в GitHub:
#   репозиторий → Settings → Deploy keys → Add deploy key (галочку «Allow write access» НЕ ставить).
# Потом сервер берёт обновления по ключу, и репозиторий можно закрыть.
set -e
DIR=/opt/abonement-plus
KEY=/root/.ssh/github_deploy
mkdir -p /root/.ssh && chmod 700 /root/.ssh
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "abonement-plus $(hostname)" -f "$KEY"
grep -q "github_deploy" /root/.ssh/config 2>/dev/null || printf "Host github.com\n  IdentityFile %s\n  IdentitiesOnly yes\n" "$KEY" >> /root/.ssh/config
ssh-keyscan -q github.com >> /root/.ssh/known_hosts 2>/dev/null
echo
echo "=============================================="
echo " Ключ этого сервера (скопируйте строку целиком):"
echo
cat "$KEY.pub"
echo
echo " Добавьте его в GitHub: репозиторий → Settings → Deploy keys → Add deploy key"
echo " (название любое, например имя сервера; «Allow write access» не отмечать) → Add key."
echo "=============================================="
echo "Когда добавите ключ в GitHub, вернитесь сюда и нажмите Enter"
read -r _ </dev/tty
if git ls-remote -q git@github.com:repina-abonement/abonement-plus.git HEAD >/dev/null 2>&1; then
  git -C "$DIR" remote set-url origin git@github.com:repina-abonement/abonement-plus.git
  git -C "$DIR" fetch -q origin main && echo "Готово: сервер берёт обновления по ключу. Репозиторий можно делать закрытым."
else
  echo "Ключ пока не принят GitHub. Ничего не поменяла, обновления идут как раньше. Проверьте, что ключ добавлен, и запустите команду ещё раз."
fi
