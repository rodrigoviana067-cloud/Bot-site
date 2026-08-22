#!/bin/bash
# Instala dependências da Bridge (se necessário)
npm install --production

# Inicia a Bridge em background
node server.js &

# Inicia o Flask (processo principal)
python app.py
