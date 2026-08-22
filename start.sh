#!/bin/bash

echo "══════════════════════════════════════════════════════════════"
echo "  WA Affiliate Pro v6.0 — Docker Container"
echo "══════════════════════════════════════════════════════════════"
echo ""

# Verificar ambiente
echo "🔍 Ambiente:"
echo "   Node.js: $(node --version)"
echo "   npm: $(npm --version)"
echo "   Python: $(python3 --version)"
echo "   Porta: $PORT"
echo ""

# Iniciar Bridge Node.js em background
echo "🚀 Iniciando WhatsApp Bridge..."
node server.js 2>&1 | while read line; do
    echo "[BRIDGE] $line"
done &
BRIDGE_PID=$!

# Aguardar bridge subir
echo "⏳ Aguardando bridge..."
for i in {1..30}; do
    sleep 1
    if curl -s http://127.0.0.1:3000/health > /dev/null 2>&1; then
        echo "✅ Bridge online!"
        break
    fi
done

echo ""
echo "🚀 Iniciando Backend Python..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 30
