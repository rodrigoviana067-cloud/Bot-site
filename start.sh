#!/bin/bash

# Instalar Node.js (se não existir)
if ! command -v node &> /dev/null; then
    echo "📦 Instalando Node.js..."
    apt-get update -qq && apt-get install -y -qq nodejs npm
fi

# Iniciar Bridge Node.js em background
node server.js &
BRIDGE_PID=$!

# Aguardar bridge subir
sleep 5

# Iniciar Backend Python
exec python app.py
