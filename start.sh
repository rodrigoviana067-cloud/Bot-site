#!/bin/bash

echo "🔍 Node.js: $(node --version)"
echo "🔍 Python: $(python3 --version)"
echo "🔍 Porta: $PORT"

# Usar o virtual environment
export PATH="/app/venv/bin:$PATH"

# Iniciar Bridge
echo "🚀 Iniciando Bridge..."
node server.js &
sleep 5

# Iniciar Backend
echo "🚀 Iniciando Backend..."
exec /app/venv/bin/gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 30
