#!/bin/bash
# Резервный сервер CRM у другого провайдера (Юлия, 02.11: «чтобы не было, как с ERP»).
# На чистом сервере Ubuntu 24.04 от root, один раз:
#   curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/standby.sh | bash
# Что делает:
#   - ставит CRM так же, как основная, но НЕ запускает её (чтобы не было двух CRM, которые обе пишут клиентам);
#   - каждый час забирает свежую зашифрованную копию базы и фото с Яндекс Диска и раскладывает их (это же и
#     ежечасная проверка, что копии действительно восстанавливаются);
#   - при аварии основной CRM: на мосту  crm-switch IP_ЭТОГО_СЕРВЕРА , здесь  crm-activate  - и ab-plus.ru работает отсюда.
# Спросит токен Яндекс Диска и пароль шифрования копий (CRM → Настройки → Резервные копии). Они хранятся только здесь.
set -e
curl -fsSL https://raw.githubusercontent.com/repina-abonement/abonement-plus/main/deploy/install.sh | bash
systemctl disable -q --now abonement-plus abonement-plus-backup || true
touch /etc/abonement-plus.standby

if [ ! -s /etc/abonement-plus-standby.env ]; then
  echo
  echo "Вставьте токен Яндекс Диска (тот же аккаунт, куда CRM делает копии) и нажмите Enter:"
  read -r -s YD_TOKEN </dev/tty
  echo "Вставьте пароль шифрования копий (CRM → Настройки → Резервные копии → «Копировать») и нажмите Enter:"
  read -r -s BK_KEY </dev/tty
  printf "YD_TOKEN=%s\nBK_KEY=%s\n" "$YD_TOKEN" "$BK_KEY" > /etc/abonement-plus-standby.env
  chmod 600 /etc/abonement-plus-standby.env
fi

cat > /usr/local/bin/crm-standby-sync <<'PY'
#!/usr/bin/env python3
"""Забрать последнюю копию базы и новые фото с Яндекс Диска и разложить для CRM (резервный сервер)."""
import gzip, json, os, shutil, sqlite3, subprocess, sys, urllib.parse, urllib.request
env = dict(l.strip().split("=", 1) for l in open("/etc/abonement-plus-standby.env") if "=" in l)
TOK, KEY = env["YD_TOKEN"], env["BK_KEY"]
DATA = "/var/lib/abonement-plus"
YD = "https://cloud-api.yandex.net/v1/disk"
def yd(path):
    req = urllib.request.Request(YD + path, headers={"Authorization": "OAuth " + TOK})
    return json.load(urllib.request.urlopen(req, timeout=60))
def fetch(remote, dst):
    href = yd("/resources/download?path=" + urllib.parse.quote(remote))["href"]
    with urllib.request.urlopen(href, timeout=600) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
def dec(src, dst):
    subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-in", src, "-out", dst,
                    "-pass", "pass:" + KEY], check=True, capture_output=True)
items = yd("/resources?path=app:/&limit=1000&sort=name")["_embedded"]["items"]
copies = sorted(x["name"] for x in items if x["name"].startswith("crm-") and x["name"].endswith(".enc"))
if not copies:
    sys.exit("На Яндекс Диске нет копий")
last = copies[-1]
stamp = os.path.join(DATA, ".standby-last")
if open(stamp).read().strip() != last if os.path.exists(stamp) else True:
    tmp = os.path.join(DATA, "standby.enc")
    fetch("app:/" + last, tmp)
    dec(tmp, tmp + ".gz")
    with gzip.open(tmp + ".gz") as g, open(tmp + ".db", "wb") as f:
        shutil.copyfileobj(g, f)
    if sqlite3.connect(tmp + ".db").execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("Копия повреждена: " + last)
    os.replace(tmp + ".db", os.path.join(DATA, "crm.sqlite3"))
    for p in (tmp, tmp + ".gz", os.path.join(DATA, "crm.sqlite3-wal"), os.path.join(DATA, "crm.sqlite3-shm")):
        if os.path.exists(p):
            os.remove(p)
    open(stamp, "w").write(last)
photos = os.path.join(DATA, "photos")
os.makedirs(photos, exist_ok=True)
got = 0
try:
    for x in yd("/resources?path=app:/photos&limit=5000")["_embedded"]["items"]:
        name = x["name"].rsplit("_", 1)[0]            # r12.jpg_1730000000.enc → r12.jpg
        if not os.path.exists(os.path.join(photos, name)):
            fetch("app:/photos/" + x["name"], "/tmp/ph.enc"); dec("/tmp/ph.enc", os.path.join(photos, name)); got += 1
except Exception as e:  # noqa: BLE001
    print("фото:", e)
subprocess.run(["chown", "-R", "crm:crm", DATA])
print(f"Резерв обновлён: {last}, новых фото: {got}")
PY
chmod 700 /usr/local/bin/crm-standby-sync

cat > /usr/local/bin/crm-activate <<'SH'
#!/bin/bash
# Авария: сделать этот сервер основной CRM. Сначала на мосту: crm-switch IP_ЭТОГО_СЕРВЕРА
set -e
/usr/local/bin/crm-standby-sync || echo "Свежую копию взять не удалось, работаем с последней"
rm -f /etc/abonement-plus.standby
sed -i 's/^CRM_DEMO=.*/CRM_DEMO=0/' /etc/abonement-plus.env
systemctl enable -q --now abonement-plus abonement-plus-backup
systemctl disable -q --now crm-standby-sync.timer || true
bash /opt/abonement-plus/deploy/update.sh || true
echo "Готово: CRM работает на этом сервере. Сертификат для ab-plus.ru выдастся в течение 5-10 минут."
SH
chmod 700 /usr/local/bin/crm-activate

cat > /etc/systemd/system/crm-standby-sync.service <<'UNIT'
[Unit]
Description=CRM standby: copy from Yandex Disk
[Service]
Type=oneshot
ExecStart=/usr/local/bin/crm-standby-sync
UNIT
cat > /etc/systemd/system/crm-standby-sync.timer <<'UNIT'
[Unit]
Description=CRM standby: hourly sync
[Timer]
OnCalendar=*:25
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload && systemctl enable -q --now crm-standby-sync.timer
echo "== Первая загрузка копии =="
/usr/local/bin/crm-standby-sync
echo
echo "=============================================="
echo " Резервный сервер готов. Копия обновляется каждый час (в :25)."
echo " IP этого сервера: $(curl -fsS4 https://ifconfig.me)"
echo "=============================================="
