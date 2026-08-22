#!/usr/bin/env python3
"""WhatsApp Bridge simplificada em Python"""
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "sessions": 0})

@app.route('/pairing-code', methods=['POST'])
def pairing_code():
    return jsonify({"success": False, "error": "Bridge em modo simplificado. Configure uma bridge real."})

@app.route('/send', methods=['POST'])
def send():
    return jsonify({"success": False, "error": "Bridge em modo simplificado."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000)
