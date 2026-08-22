#!/bin/bash
cd /app
npm install --production
node server.js &
python3 app.py
