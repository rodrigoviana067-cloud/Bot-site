#!/bin/bash
set -e

echo "🚀 WA Affiliate Pro v6.0"

# Porta do backend (definida pelo Railway)
BACKEND_PORT=${PORT:-8080}
# Porta do bridge (sempre 3000)
BRIDGE_PORT=3000

echo "📡 Backend porta: $BACKEND_PORT"
echo "📡 Bridge porta: $BRIDGE_PORT"

# Iniciar Bridge em background com nohup (não morre quando o shell continua)
if command -v node &> /dev/null; then
    echo "✅ Node.js: $(node --version)"
    
    # Usar nohup para manter o bridge vivo
    nohup bash -c "PORT=$BRIDGE_PORT node server.js" > /tmp/bridge.log 2>&1 &
    BRIDGE_PID=$!
    
    # Disown para remover do job control do shell
    disown $BRIDGE_PID 2>/dev/null || true
    
    echo "✅ Bridge iniciado (PID: $BRIDGE_PID)"
else
    echo "⚠️  Node.js não encontrado"
    exit 1
fi

# Aguardar bridge ficar pronto
echo "⏳ Aguardando bridge..."
sleep 5

# Verificar se bridge está rodando
if ! kill -0 $BRIDGE_PID 2>/dev/null; then
    echo "❌ Bridge morreu! Logs:"
    cat /tmp/bridge.log | tail -50
    exit 1
fi

echo "✅ Bridge está vivo"

# Iniciar Backend na porta do Railway (foreground - processo principal)
echo "🚀 Iniciando Backend na porta $BACKEND_PORT..."
export PATH="/app/venv/bin:$PATH"
exec /app/venv/bin/gunicorn app:app --bind 0.0.0.0:$BACKEND_PORT --workers 1 --timeout 30
