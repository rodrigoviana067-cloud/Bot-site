#!/data/data/com.termux/files/usr/bin/bash
echo "🚀 Instalando WA Affiliate Pro v6.0..."
pkg update -y
pkg install -y python python-pip sqlite
python -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env 2>/dev/null || true
mkdir -p logs
echo "✅ Pronto! Edite .env e rode: python app.py"
