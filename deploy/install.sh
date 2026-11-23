#!/usr/bin/env bash
# Развёртывание бота на Ubuntu 22.04/24.04 (VPS в РФ).
# Запуск: sudo bash deploy/install.sh afisha.example.ru
set -euo pipefail

DOMAIN="${1:-}"
APP_DIR="/opt/afisha"
SERVICE_USER="afisha"

if [[ -z "$DOMAIN" ]]; then
  echo "Укажите домен: sudo bash deploy/install.sh afisha.example.ru"
  exit 1
fi

echo "== 1. Пакеты =="
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx sqlite3

echo "== 2. Пользователь и каталог =="
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$APP_DIR/data/logs" "$APP_DIR/data/backups"
rsync -a --exclude '.env' --exclude 'data' --exclude '__pycache__' ./ "$APP_DIR"/
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo "== 3. Виртуальное окружение =="
sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "!!! Заполните $APP_DIR/.env и перезапустите службу."
fi

echo "== 4. Nginx и сертификат =="
sed "s/afisha.example.ru/$DOMAIN/g" "$APP_DIR/deploy/nginx-afisha.conf" > /etc/nginx/sites-available/afisha
ln -sf /etc/nginx/sites-available/afisha /etc/nginx/sites-enabled/afisha
rm -f /etc/nginx/sites-enabled/default
mkdir -p /var/www/certbot
nginx -t
systemctl reload nginx

if [[ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" --redirect || true
fi

echo "== 5. Служба =="
cp "$APP_DIR/deploy/afisha-bot.service" /etc/systemd/system/afisha-bot.service
systemctl daemon-reload
systemctl enable --now afisha-bot

echo "== 6. Проверка =="
sleep 3
curl -fsS "https://$DOMAIN/health" || echo "Проверьте логи: journalctl -u afisha-bot -n 50"
echo
echo "Готово. Дальше: заполните .env, укажите WEBHOOK_URL=https://$DOMAIN и ADMIN_IDS, затем:"
echo "  systemctl restart afisha-bot"
