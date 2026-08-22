#!/bin/bash

echo "══════════════════════════════════════════════════════════════"
echo "  WA Affiliate Pro — Iniciando"
echo "══════════════════════════════════════════════════════════════"

# Verificar Node.js
echo "🔍 Verificando Node.js..."
if command -v node &> /dev/null; then
    echo "✅ Node.js: $(node --version)"
    echo "✅ npm: $(npm --version)"
    
    # Instalar dependências se necessário
    if [ ! -d "node_modules" ]; then
        echo "📦 Instalando dependências do bridge..."
        npm install
    fi
    
    # Iniciar bridge em background
    echo "🚀 Iniciando Bridge..."
    node server.js &
    BRIDGE_PID=$!
    sleep 5
    echo "✅ Bridge PID: $BRIDGE_PID"
else
    echo "❌ Node.js não encontrado!"
    echo "   O bridge WhatsApp não vai funcionar."
fi

echo ""
echo "🚀 Iniciando Backend Python na porta $PORT..."
exec gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 30
