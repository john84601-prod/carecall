#!/usr/bin/env bash
# CareCall setup script — run on Linux or Raspberry Pi OS
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "======================================"
echo "  CareCall Setup"
echo "======================================"
echo ""

# ── System dependencies ────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "[*] Installing Python 3..."
  sudo apt-get update -qq
  sudo apt-get install -y python3 python3-pip python3-venv
fi

if ! command -v ffmpeg &>/dev/null; then
  echo "[*] Installing ffmpeg (required for microphone recording)..."
  sudo apt-get update -qq
  sudo apt-get install -y ffmpeg
else
  echo "[✓] ffmpeg already installed"
fi

PY_VER=$(python3 -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo "unknown")
echo "[✓] Python 3 found: $PY_VER"

# ── Virtual environment ────────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "[*] Creating virtual environment..."
  python3 -m venv venv
fi

echo "[*] Installing Python dependencies..."
./venv/bin/pip install --upgrade pip --quiet
./venv/bin/pip install -r requirements.txt --quiet
echo "[✓] Dependencies installed"

# ── Directories & config ───────────────────────────────────────────────────────
mkdir -p uploads

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "  IMPORTANT: Edit .env before starting CareCall:"
  echo ""
  echo "    nano .env"
  echo ""
  echo "  You will need:"
  echo "    - TWILIO_ACCOUNT_SID  (from console.twilio.com)"
  echo "    - TWILIO_AUTH_TOKEN"
  echo "    - TWILIO_FROM_NUMBER  (your Twilio phone number)"
  echo "    - FLASK_SECRET_KEY    (any long random string)"
  echo "    - NGROK_AUTH_TOKEN    (free at ngrok.com — needed if behind a router)"
  echo ""
else
  echo "[✓] .env already exists"
fi

# ── Systemd service (optional) ─────────────────────────────────────────────────
if [ "$1" == "--install-service" ] && command -v systemctl &>/dev/null; then
  echo ""
  echo "[*] Installing systemd service..."
  SERVICE_DEST="/etc/systemd/system/carecall.service"
  sudo cp carecall.service "$SERVICE_DEST"
  # Patch paths in the service file
  sudo sed -i "s|/opt/carecall|$SCRIPT_DIR|g"     "$SERVICE_DEST"
  sudo sed -i "s|carecall_user|$USER|g"            "$SERVICE_DEST"
  sudo systemctl daemon-reload
  sudo systemctl enable carecall
  echo "[✓] Service installed"
  echo ""
  echo "  Start:   sudo systemctl start carecall"
  echo "  Stop:    sudo systemctl stop carecall"
  echo "  Logs:    journalctl -u carecall -f"
fi

echo ""
echo "======================================"
echo "  Setup complete!"
echo "======================================"
echo ""
echo "  To start CareCall:"
echo "    source venv/bin/activate"
echo "    python run.py"
echo ""
echo "  Then open http://localhost:5000 in your browser."
echo ""
