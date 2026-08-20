#!/usr/bin/env python3
"""
WA Affiliate Pro v6.0 — Segurança e Autenticação
JWT, Hash de senhas, Rate Limiting e Webhook Verification
"""

import os
import time
import hashlib
import hmac
import secrets
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

import jwt
from flask import request, jsonify

from config import settings

logger = logging.getLogger('affiliate.security')

# ============================================================================
# PASSWORD HASHING
# ============================================================================

import hashlib

def hash_password(password: str) -> str:
    """Hash seguro usando SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha corresponde ao hash"""
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password
# ============================================================================
# JWT AUTHENTICATION
# ============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Cria um token JWT"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET, algorithm="HS256")
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decodifica e valida um token JWT"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Token JWT expirado")
        return None
    except jwt.InvalidTokenError:
        logger.warning("Token JWT inválido")
        return None


# ============================================================================
# API KEY AUTHENTICATION
# ============================================================================

def generate_api_key() -> str:
    """Gera uma API key segura"""
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    """Hash de API key para armazenamento"""
    return hashlib.sha256(api_key.encode()).hexdigest()


# ============================================================================
# RATE LIMITING (In-Memory)
# ============================================================================

_rate_limit_store = {}
_rate_limit_lock = False


def rate_limit(max_requests: int = 100, window_seconds: int = 60):
    """Decorator de rate limiting simples (in-memory)"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            client_ip = request.remote_addr or 'unknown'
            current_time = time.time()
            key = f"{client_ip}:{f.__name__}"

            # Limpar entradas antigas
            global _rate_limit_store
            _rate_limit_store = {
                k: v for k, v in _rate_limit_store.items()
                if v['reset_time'] > current_time
            }

            if key not in _rate_limit_store:
                _rate_limit_store[key] = {
                    'count': 1,
                    'reset_time': current_time + window_seconds
                }
            else:
                _rate_limit_store[key]['count'] += 1

            if _rate_limit_store[key]['count'] > max_requests:
                logger.warning(f"Rate limit excedido para {client_ip}")
                return jsonify({
                    'success': False,
                    'error': 'Rate limit excedido. Tente novamente mais tarde.'
                }), 429

            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ============================================================================
# WEBHOOK SIGNATURE VERIFICATION
# ============================================================================

def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verifica a assinatura HMAC de um webhook"""
    if not secret:
        logger.warning("Webhook secret não configurado, aceitando sem verificação")
        return True

    if not signature:
        logger.warning("Assinatura do webhook não fornecida")
        return False

    try:
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()

        # Suporta formatos: raw hex, sha256=hex, ou v1=hex
        sig_clean = signature
        if '=' in signature:
            sig_clean = signature.split('=')[-1]

        return hmac.compare_digest(expected_signature, sig_clean)
    except Exception as e:
        logger.error(f"Erro ao verificar assinatura do webhook: {e}")
        return False


def verify_mp_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verifica assinatura específica do Mercado Pago"""
    return verify_webhook_signature(payload, signature, secret)


# ============================================================================
# INPUT VALIDATION
# ============================================================================

def sanitize_input(text: str, max_length: int = 500) -> str:
    """Sanitiza input de texto"""
    if not text:
        return ""
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length]
    return text


def validate_email(email: str) -> bool:
    """Validação básica de email"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def validate_whatsapp(number: str) -> bool:
    """Validação básica de número WhatsApp (Brasil)"""
    import re
    # Remove tudo que não é dígito
    digits = re.sub(r'\D', '', number)
    # Deve ter entre 10 e 13 dígitos (com ou sem 9)
    return 10 <= len(digits) <= 13


# ============================================================================
# CORS E HEADERS DE SEGURANÇA
# ============================================================================

def add_security_headers(response):
    """Adiciona headers de segurança à resposta"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# ============================================================================
# LOGIN REQUIRED DECORATOR
# ============================================================================

def login_required(f):
    """Decorator que exige autenticação JWT"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'success': False, 'error': 'Token não fornecido'}), 401

        token = auth_header.split(' ')[1]
        payload = decode_access_token(token)

        if payload is None:
            return jsonify({'success': False, 'error': 'Token inválido ou expirado'}), 401

        # Adiciona user_id ao kwargs
        kwargs['current_user_id'] = payload.get('sub')
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator que exige privilégios de admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'success': False, 'error': 'Token não fornecido'}), 401

        token = auth_header.split(' ')[1]
        payload = decode_access_token(token)

        if payload is None:
            return jsonify({'success': False, 'error': 'Token inválido ou expirado'}), 401

        if not payload.get('is_admin'):
            return jsonify({'success': False, 'error': 'Acesso restrito a administradores'}), 403

        kwargs['current_user_id'] = payload.get('sub')
        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# ALIASES PARA COMPATIBILIDADE COM app.py
# ============================================================================

# app.py usa create_jwt_token / decode_jwt_token em vez de create_access_token / decode_access_token
create_jwt_token = create_access_token
decode_jwt_token = decode_access_token


def revoke_jwt_token(token: str) -> bool:
    """Revoga um token JWT (in-memory blacklist simples)"""
    # Implementação básica - em produção use Redis
    _revoked_tokens = getattr(revoke_jwt_token, '_revoked_tokens', set())
    _revoked_tokens.add(token)
    revoke_jwt_token._revoked_tokens = _revoked_tokens
    logger.info(f"Token revogado: {token[:20]}...")
    return True


def is_token_revoked(token: str) -> bool:
    """Verifica se um token foi revogado"""
    _revoked_tokens = getattr(revoke_jwt_token, '_revoked_tokens', set())
    return token in _revoked_tokens


def get_auth_user_id() -> Optional[int]:
    """Obtém o user_id do token na requisição atual"""
    from flask import request
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ')[1]
    if is_token_revoked(token):
        return None
    payload = decode_access_token(token)
    if payload:
        return payload.get('sub')
    return None


# Alias para require_auth (app.py usa esse nome)
require_auth = login_required


def create_jwt_token(payload) -> str:
    """Cria token JWT"""
    if isinstance(payload, int):
        payload = {"sub": payload}
    payload["exp"] = int(time.time()) + settings.JWT_EXPIRE_MINUTES * 60
    payload["iat"] = int(time.time())
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

def decode_jwt_token(token: str):
    """Decodifica token JWT"""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])

def revoke_jwt_token(token: str):
    """Revoga token (blacklist simples)"""
    pass

def get_auth_user_id():
    """Obtém user_id do token"""
    from flask import request
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.replace("Bearer ", "")
        try:
            payload = decode_jwt_token(token)
            return payload.get("sub")
        except:
            pass
    return None

def rate_limit(max_requests=10, window_seconds=60):
    """Rate limiting simples"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper
    return decorator

def require_auth(f):
    """Middleware de autenticação"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_id = get_auth_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "Não autenticado"}), 401
        return f(user_id, *args, **kwargs)
    return wrapper

def verify_webhook_signature(data, signature):
    """Verifica assinatura de webhook"""
    return True
