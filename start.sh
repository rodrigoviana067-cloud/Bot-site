#!/bin/bash

# Iniciar Bridge Node.js em background
while true; do
    echo "🚀 [$(date)] Iniciando Bridge Node.js..."
    node server.js 2>&1 | sed 's/^/[BRIDGE] /' &
    BRIDGE_PID=$!
    
    # Aguardar bridge subir (tenta 10 vezes)
    for i in {1..10}; do
        sleep 2
        if curl -s http://127.0.0.1:3000/health > /dev/null 2>&1; then
            echo "✅ [$(date)] Bridge online na porta 3000!"
            break
        fi
        echo "⏳ [$(date)] Aguardando bridge... ($i/10)"
    done
    
    # Se bridge cair, reinicia
    wait $BRIDGE_PID
    echo "⚠️  [$(date)] Bridge caiu! Reiniciando em 5s..."
    sleep 5
done &

# Aguardar bridge ficar pronto
sleep 8

# Iniciar Backend Python em foreground
echo "🚀 [$(date)] Iniciando Backend Python..."
exec gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 30
