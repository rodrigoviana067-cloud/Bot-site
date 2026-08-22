#!/bin/bash
cd /app
node server.js &
/app/.venv/bin/python app.py
