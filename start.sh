#!/bin/bash

echo "🚀 WA Affiliate Pro v6.0"

# Iniciar Bridge em background (não bloqueia)
if command -v node &> /dev/null; then
    echo "✅ Node.js: $(node --version)"
    PORT=3000 node server.js &
else
    echo "⚠️  Node.js não encontrado"
fi

# Iniciar Backend imediatamente (não espera bridge)
echo "🚀 Iniciando Backend na porta $PORT..."
export PATH="/app/venv/bin:$PATH"
exec /app/venv/bin/gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 30
