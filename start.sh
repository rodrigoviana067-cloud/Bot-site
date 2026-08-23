#!/bin/bash

echo "🔍 Node.js: $(node --version)"
echo "🔍 Python: $(python3 --version)"
echo "🔍 Porta Railway: $PORT"

export PATH="/app/venv/bin:$PORT"

# Iniciar Bridge na porta 3000 (fixa, NÃO usa $PORT)
echo "🚀 Iniciando Bridge na porta 3000..."
PORT=3000 node server.js &
sleep 5

# Iniciar Backend na porta $PORT (Railway)
echo "🚀 Iniciando Backend na porta $PORT..."
exec /app/venv/bin/gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 30
