#!/usr/bin/env bash
# Instalador para Oracle Cloud Always Free (Ubuntu 24.04 LTS)
# Uso:  ./install_oracle.sh
# Pide las claves solo la primera vez (crea /opt/optcg-bot/.env). Si el .env ya
# existe, lo reutiliza. Instala el bot como servicio systemd con auto-reinicio.
set -euo pipefail

APP_DIR="/opt/optcg-bot"
REPO_URL="https://github.com/almondiga/TeamVictus.git"
RUN_USER="${USER:-ubuntu}"

echo "=== [1/6] Dependencias del sistema ==="
sudo apt-get update -qq
sudo apt-get install -y -qq python3.12 python3.12-venv git curl > /dev/null

echo "=== [2/6] Clonando el bot ==="
if [ ! -d "$APP_DIR/.git" ]; then
  sudo git clone --quiet "$REPO_URL" "$APP_DIR"
fi
sudo chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
cd "$APP_DIR"

echo "=== [3/6] Entorno virtual + dependencias Python ==="
if [ ! -d venv ]; then
  python3.12 -m venv venv
fi
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo "=== [4/6] Variables de entorno (.env) ==="
if [ -f .env ]; then
  echo "  .env ya existe -> se reutiliza (edítalo con: nano $APP_DIR/.env)"
else
  read -r -s -p "  DISCORD_TOKEN (no se muestra al teclear): " DISCORD_TOKEN; echo
  read -r -p "  BERRYWALLET_API_KEY (Enter para omitir): " BERRYWALLET_API_KEY
  read -r -p "  RAPIDAPI_KEY (Enter para omitir): " RAPIDAPI_KEY
  read -r -p "  OPTCG_API_KEY (Enter para omitir): " OPTCG_API_KEY
  if [ -z "${DISCORD_TOKEN:-}" ]; then
    echo "  ⚠️  DISCORD_TOKEN vacío: el bot no arrancará. Edítalo luego con: nano .env"
  fi
  {
    echo "DISCORD_TOKEN=$DISCORD_TOKEN"
    echo "BERRYWALLET_API_KEY=$BERRYWALLET_API_KEY"
    echo "RAPIDAPI_KEY=$RAPIDAPI_KEY"
    echo "OPTCG_API_KEY=$OPTCG_API_KEY"
    echo "PRICE_PROVIDER=auto"
    echo "DB_PATH=optcg_bot.db"
  } > .env
  chmod 600 .env
fi

echo "=== [5/6] Servicio systemd (auto-reinicio) ==="
sudo tee /etc/systemd/system/optcg-bot.service > /dev/null <<EOF
[Unit]
Description=OPTCG Discord Bot (TeamVictus)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/bot.py
Restart=always
RestartSec=5
EnvironmentFile=$APP_DIR/.env
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now optcg-bot
sleep 4

echo "=== [6/6] Verificación ==="
systemctl --no-pager status optcg-bot --lines=3 || true
echo
echo "--- Health endpoint (local) ---"
curl -s -m 5 http://127.0.0.1:8080/health || echo "(el health aún está arrancando; repite: curl http://127.0.0.1:8080/health)"
echo
echo "--- Últimos logs ---"
sudo journalctl -u optcg-bot -n 20 --no-pager || true
echo
echo "✅ Listo. Comandos útiles:"
echo "   sudo journalctl -u optcg-bot -f              # seguir logs en vivo"
echo "   sudo systemctl restart optcg-bot             # reiniciar el bot"
echo "   cd $APP_DIR && git pull && sudo systemctl restart optcg-bot   # actualizar"
