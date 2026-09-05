#!/usr/bin/env python3
"""
WA Affiliate Pro v6.0 — Aplicação Principal
Flask app com todas as rotas, validação Pydantic, métricas Prometheus
"""

import os
import json
import time
import logging
import signal
import sys
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_cors import CORS
from pydantic import BaseModel, Field

from config import settings
from database import init_database, get_db
from security import (
    hash_password, verify_password, create_jwt_token, decode_jwt_token,
    revoke_jwt_token, rate_limit, require_auth, get_auth_user_id,
    verify_webhook_signature
)
from circuit_breaker import wa_bridge_breaker, shopee_api_breaker
from whatsapp_service import whatsapp_service
from shopee_service import shopee_service
from autopost_engine import autopost_engine
from agendador import agendador_worker

# ============================================================================
# INICIALIZAÇÃO
# ============================================================================

app = Flask(__name__)
CORS(app, resources={r'/*': {'origins': '*'}})
app.config['SECRET_KEY'] = settings.SECRET_KEY
app.config['JSON_SORT_KEYS'] = False

@app.before_request
def handle_preflight():
    if request.method == 'OPTIONS':
        resp = make_response('', 200)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return resp

# Logging estruturado
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, 'extra'):
            log_obj.update(record.extra)
        return json.dumps(log_obj, ensure_ascii=False)

if settings.LOG_FORMAT == "json":
    formatter = JSONFormatter()
else:
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

logger = logging.getLogger('affiliate')
logger.setLevel(getattr(logging, settings.LOG_LEVEL))

from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    'logs/affiliate.log',
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT
)
file_handler.setFormatter(formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Inicializar banco
init_database()

# ============================================================================
# MODELOS PYDANTIC (Validação de Requests) — Corrigidos para Pydantic v1
# ============================================================================

# Modelos Pydantic (Pydantic V2)
class RegisterRequest(BaseModel):
    nome: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    senha: str = Field(..., min_length=6, max_length=128)
    whatsapp: str = Field(default="", max_length=50)

class LoginRequest(BaseModel):
    email: str = Field(..., pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    senha: str = Field(..., min_length=1)

class ConfigUpdateRequest(BaseModel):
    shopee_app_id: str = Field(default="", max_length=100)
    shopee_api_key: str = Field(default="", max_length=200)
    intervalo: int = 30
    min_desconto: int = 20
    hora_inicio: str = Field(default="08:00", pattern=r'^\d{2}:\d{2}$')
    hora_fim: str = Field(default="22:00", pattern=r'^\d{2}:\d{2}$')
    max_posts_dia: int = 50
    usar_smart_schedule: int = 1
    usar_ab_testing: int = 1

class GrupoCreateRequest(BaseModel):
    grupo_id: str = Field(..., min_length=1, max_length=100)
    grupo_nome: str = Field(..., min_length=1, max_length=200)
    plataforma: str = Field(default="wh", max_length=10)
    fonte: str = Field(default="ambos", max_length=20)
    nicho: str = Field(default="todos", max_length=100)

class TemplateCreateRequest(BaseModel):
    nome: str = Field(..., min_length=1, max_length=100)
    copy: str = Field(..., min_length=10, max_length=2000)
    ab_test_group: str = Field(default="A", pattern=r'^[ABC]$')

class AgendamentoCreateRequest(BaseModel):
    link: str = Field(..., min_length=10)
    grupos: list = []
    data_agendada: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    hora_agendada: str = Field(..., pattern=r'^\d{2}:\d{2}$')
    titulo: str = Field(default="", max_length=200)

class MensagemRequest(BaseModel):
    grupo_id: str = Field(..., min_length=1)
    mensagem: str = Field(..., min_length=1, max_length=4000)
    imagem: str = Field(default="", max_length=500)

# ============================================================================
# RESPOSTAS PADRONIZADAS
# ============================================================================

def success_response(data=None, message="OK", code=200):
    resp = {"success": True, "message": message}
    if data is not None:
        resp["data"] = data
    return jsonify(resp), code

def error_response(message="Erro", code=400):
    return jsonify({"success": False, "error": message}), code

# ============================================================================
# ROTAS DE AUTENTICAÇÃO
# ============================================================================

@app.route('/api/auth/register', methods=['POST', 'OPTIONS'])
@rate_limit(max_requests=5, window_seconds=60)
def register():
    """Registra novo usuário"""
    try:
        data = RegisterRequest(**request.get_json(silent=True) or {})
    except Exception as e:
        return error_response(f"Dados inválidos: {e}", 400)

    try:
        with get_db() as conn:
            senha_hash = hash_password(data.senha)
            cursor = conn.execute(
                "INSERT INTO users (nome, email, senha, whatsapp, trial_start) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (data.nome, data.email.lower(), senha_hash, data.whatsapp, datetime.now().isoformat())
            )
            row = cursor.fetchone()
            user_id = row['id'] if hasattr(row, 'get') else row[0]

            # Criar configurações padrão
            conn.execute("INSERT INTO configs (user_id) VALUES (%s)", (user_id,))
            conn.commit()

        token = create_jwt_token({"sub": str(user_id)})
        logger.info(f"✅ Usuário registrado: {data.email}", extra={"user_id": user_id, "email": data.email})

        return success_response({
            "token": token,
            "user_id": user_id,
            "nome": data.nome,
            "email": data.email
        }, "Usuário registrado com sucesso!")

    except Exception as e:
        if "UNIQUE constraint failed" in str(e) or "IntegrityError" in str(type(e).__name__):
            return error_response("Email já cadastrado", 400)
        logger.error(f"Erro no registro: {e}")
        return error_response(f"Erro ao registrar: {str(e)}", 500)


@app.route('/api/auth/login', methods=['POST', 'OPTIONS'])
@rate_limit(max_requests=10, window_seconds=60)
def login():
    """Login de usuário"""
    try:
        data = LoginRequest(**request.get_json(silent=True) or {})
    except Exception as e:
        return error_response(f"Dados inválidos: {e}", 400)

    try:
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email=%s", (data.email.lower(),)
            ).fetchone()

        if not user or not verify_password(data.senha, user['senha']):
            return error_response("Email ou senha inválidos", 401)

        token = create_jwt_token({"sub": str(user["id"])})

        # Calcular dias trial
        dias = 0
        if user['trial_start']:
            try:
                started = datetime.fromisoformat(user['trial_start'].replace(' ', 'T').split('+')[0])
                duracao = 7 if user['plano_ativo'] == 1 else 30
                dias = max(0, duracao - (datetime.now() - started).days)
            except:
                pass

        logger.info(f"🔑 Login: {data.email}", extra={"user_id": user["id"]})

        return success_response({
            "token": token,
            "user_id": user["id"],
            "nome": user["nome"],
            "email": user["email"],
            "whatsapp": user["whatsapp"],
            "dias_restantes": dias,
            "autopost": bool(user["autopost"]),
            "plano_ativo": user["plano_ativo"]
        }, "Login realizado com sucesso!")

    except Exception as e:
        logger.error(f"Erro no login: {e}")
        return error_response(f"Erro ao fazer login: {str(e)}", 500)


@app.route('/api/auth/logout', methods=['POST', 'OPTIONS'])
@require_auth
def logout(user_id: int):
    """Logout — revoga token JWT"""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        revoke_jwt_token(token)
    return success_response(message="Logout realizado")


# ============================================================================
# ROTAS DE DASHBOARD
# ============================================================================

@app.route('/api/dashboard', methods=['GET', 'OPTIONS'])
@require_auth
def dashboard(user_id: int):
    """Dashboard com estatísticas completas"""
    try:
        with get_db() as conn:
            # Grupos
            grupos = conn.execute(
                "SELECT COUNT(*) as c FROM grupos WHERE user_id=%s AND ativo=1", (user_id,)
            ).fetchone()['c']

            grupos_ativos = conn.execute(
                "SELECT COUNT(*) as c FROM grupos WHERE user_id=%s AND selecionado=1 AND ativo=1", (user_id,)
            ).fetchone()['c']

            # Posts hoje
            posts_hoje = conn.execute(
                "SELECT COUNT(*) as c FROM posts WHERE user_id=%s AND date(created_at)=date('now')", (user_id,)
            ).fetchone()['c']

            # Total de cliques
            total_cliques = conn.execute(
                "SELECT COUNT(*) as c FROM clicks WHERE user_id=%s", (user_id,)
            ).fetchone()['c']

            # CTR médio
            ctr_data = conn.execute(
                "SELECT AVG(ctr) as avg_ctr FROM grupos WHERE user_id=%s AND total_posts > 0", (user_id,)
            ).fetchone()
            ctr_medio = round(ctr_data['avg_ctr'] or 0, 2)

            # Últimos envios
            logs = conn.execute(
                "SELECT * FROM auto_post_log WHERE user_id=%s ORDER BY created_at DESC LIMIT 10",
                (user_id,)
            ).fetchall()

            # Status autopost
            user = conn.execute("SELECT autopost FROM users WHERE id=%s", (user_id,)).fetchone()

            # Status circuit breakers
            cb_stats = {
                "wa_bridge": wa_bridge_breaker.stats,
                "shopee_api": shopee_api_breaker.stats
            }

        return success_response({
            "grupos": {"total": grupos, "ativos": grupos_ativos},
            "posts_hoje": posts_hoje,
            "total_cliques": total_cliques,
            "ctr_medio": ctr_medio,
            "autopost_ativo": bool(user['autopost']),
            "ultimos_envios": [dict(l) for l in logs],
            "circuit_breakers": cb_stats
        })

    except Exception as e:
        logger.error(f"Erro no dashboard: {e}")
        return error_response(f"Erro dashboard: {str(e)}", 500)


# ============================================================================
# ROTAS DE GRUPOS
# ============================================================================

@app.route('/api/grupos', methods=['GET', 'OPTIONS'])
@require_auth
def listar_grupos(user_id: int):
    """Lista grupos do usuário"""
    try:
        with get_db() as conn:
            grupos = conn.execute(
                "SELECT * FROM grupos WHERE user_id=%s AND ativo=1 ORDER BY created_at DESC",
                (user_id,)
            ).fetchall()
        return success_response([dict(g) for g in grupos])
    except Exception as e:
        logger.error(f"Erro ao listar grupos: {e}")
        return error_response("Erro ao listar grupos", 500)


@app.route('/api/grupos', methods=['POST', 'OPTIONS'])
@require_auth
def criar_grupo(user_id: int):
    """Cria novo grupo"""
    try:
        data = GrupoCreateRequest(**request.get_json(silent=True) or {})
    except Exception as e:
        return error_response(f"Dados inválidos: {e}", 400)

    try:
        with get_db() as conn:
            cursor = conn.execute(
                """INSERT INTO grupos (user_id, grupo_id, grupo_nome, plataforma, fonte, nicho)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (user_id, data.grupo_id, data.grupo_nome, data.plataforma, data.fonte, data.nicho)
            )
            conn.commit()

        logger.info(f"✅ Grupo criado: {data.grupo_nome}", extra={"user_id": user_id})
        return success_response({"id": cursor.lastrowid}, "Grupo criado com sucesso!")
    except Exception as e:
        logger.error(f"Erro ao criar grupo: {e}")
        return error_response("Erro ao criar grupo", 500)


@app.route('/api/grupos/<int:grupo_id>', methods=['DELETE', 'OPTIONS'])
@require_auth
def deletar_grupo(user_id: int, grupo_id: int):
    """Remove grupo (soft delete)"""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE grupos SET ativo=0 WHERE id=%s AND user_id=%s",
                (grupo_id, user_id)
            )
            conn.commit()
        return success_response(message="Grupo removido!")
    except Exception as e:
        logger.error(f"Erro ao deletar grupo: {e}")
        return error_response("Erro ao remover grupo", 500)


@app.route('/api/grupos/<int:grupo_id>/toggle', methods=['PATCH', 'OPTIONS'])
@require_auth
def toggle_grupo(user_id: int, grupo_id: int):
    """Ativa/desativa grupo para autopost"""
    try:
        with get_db() as conn:
            grupo = conn.execute(
                "SELECT selecionado FROM grupos WHERE id=%s AND user_id=%s AND ativo=1",
                (grupo_id, user_id)
            ).fetchone()

            if not grupo:
                return error_response("Grupo não encontrado", 404)

            novo = 1 - grupo['selecionado']
            conn.execute("UPDATE grupos SET selecionado=%s WHERE id=%s", (novo, grupo_id))
            conn.commit()

        return success_response({"selecionado": novo})
    except Exception as e:
        logger.error(f"Erro ao alternar grupo: {e}")
        return error_response("Erro ao alternar grupo", 500)


@app.route('/api/grupos/sincronizar', methods=['POST', 'OPTIONS'])
@require_auth
def sincronizar_grupos(user_id: int):
    """Sincroniza grupos do WhatsApp"""
    try:
        grupos_whatsapp = whatsapp_service.get_groups(user_id)

        sincronizados = 0
        with get_db() as conn:
            for g in grupos_whatsapp:
                grupo_id = g.get('id') or g.get('jid', '')
                grupo_nome = g.get('nome') or g.get('name') or g.get('subject', '')

                if not grupo_id:
                    continue

                existente = conn.execute(
                    "SELECT id FROM grupos WHERE user_id=%s AND grupo_id=%s AND ativo=1",
                    (user_id, grupo_id)
                ).fetchone()

                if not existente:
                    conn.execute(
                        "INSERT INTO grupos (user_id, grupo_id, grupo_nome, plataforma) VALUES (%s, %s, %s, %s)",
                        (user_id, grupo_id, grupo_nome, 'whatsapp')
                    )
                    sincronizados += 1

            conn.commit()

        logger.info(f"🔄 {sincronizados} grupos sincronizados", extra={"user_id": user_id})
        return success_response({"sincronizados": sincronizados, "total": len(grupos_whatsapp)})

    except Exception as e:
        logger.error(f"Erro ao sincronizar grupos: {e}")
        return error_response(str(e), 500)


# ============================================================================
# ROTAS DE TEMPLATES
# ============================================================================

@app.route('/api/templates', methods=['GET', 'OPTIONS'])
@require_auth
def listar_templates(user_id: int):
    """Lista templates do usuário"""
    try:
        with get_db() as conn:
            templates = conn.execute(
                "SELECT * FROM templates WHERE user_id=%s ORDER BY created_at DESC",
                (user_id,)
            ).fetchall()
        return success_response([dict(t) for t in templates])
    except Exception as e:
        logger.error(f"Erro ao listar templates: {e}")
        return error_response("Erro ao listar templates", 500)


@app.route('/api/templates', methods=['POST', 'OPTIONS'])
@require_auth
def criar_template(user_id: int):
    """Cria novo template"""
    try:
        data = TemplateCreateRequest(**request.get_json(silent=True) or {})
    except Exception as e:
        return error_response(f"Dados inválidos: {e}", 400)

    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO templates (user_id, nome, copy, ab_test_group) VALUES (%s, %s, %s, %s)",
                (user_id, data.nome, data.copy, data.ab_test_group)
            )
            conn.commit()
        return success_response({"id": cursor.lastrowid}, "Template criado!")
    except Exception as e:
        logger.error(f"Erro ao criar template: {e}")
        return error_response("Erro ao criar template", 500)


@app.route('/api/templates/<int:template_id>/select', methods=['PATCH', 'OPTIONS'])
@require_auth
def select_template(user_id: int, template_id: int):
    """Seleciona template como ativo"""
    try:
        with get_db() as conn:
            conn.execute("UPDATE templates SET selecionado=0 WHERE user_id=%s", (user_id,))
            conn.execute(
                "UPDATE templates SET selecionado=1 WHERE id=%s AND user_id=%s",
                (template_id, user_id)
            )
            conn.commit()
        return success_response(message="Template selecionado!")
    except Exception as e:
        logger.error(f"Erro ao selecionar template: {e}")
        return error_response("Erro ao selecionar template", 500)


# ============================================================================
# ROTAS DE CONFIGURAÇÃO
# ============================================================================

def _ensure_config_exists(conn, user_id: int):
    """Garante que existe uma linha na tabela configs para o usuário."""
    existing = conn.execute(
        "SELECT 1 FROM configs WHERE user_id=%s", (user_id,)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO configs (user_id) VALUES (%s)",
            (user_id,)
        )
        conn.commit()
        logger.info(f"📝 Linha de config criada para user {user_id}")
    return existing is not None


@app.route('/api/config', methods=['GET', 'OPTIONS'])
@require_auth
def get_config(user_id: int):
    """Obtém configurações do usuário"""
    try:
        with get_db() as conn:
            _ensure_config_exists(conn, user_id)
            config = conn.execute("SELECT * FROM configs WHERE user_id=%s", (user_id,)).fetchone()
        return success_response(dict(config) if config else {})
    except Exception as e:
        logger.error(f"Erro ao obter config: {e}")
        return error_response("Erro ao obter configuração", 500)


@app.route('/api/config', methods=['PUT', 'OPTIONS'])
@require_auth
def update_config(user_id: int):
    """Atualiza configurações do usuário"""
    try:
        data = ConfigUpdateRequest(**request.get_json(silent=True) or {})
    except Exception as e:
        return error_response(f"Dados inválidos: {e}", 400)

    try:
        updates = []
        values = []

        for field, value in data.dict(exclude_unset=True).items():
            if value is not None:
                updates.append(f"{field}=%s")
                values.append(value)

        if updates:
            with get_db() as conn:
                _ensure_config_exists(conn, user_id)
                values.append(user_id)
                query = f"UPDATE configs SET {', '.join(updates)} WHERE user_id=%s"
                conn.execute(query, values)
                conn.commit()

        return success_response(message="Configurações atualizadas!")
    except Exception as e:
        logger.error(f"Erro ao atualizar config: {e}")
        return error_response("Erro ao atualizar configuração", 500)


@app.route('/api/config/shopee', methods=['POST', 'OPTIONS'])
@require_auth
def config_shopee(user_id: int):
    """Salva credenciais da API Shopee"""
    d = request.get_json(silent=True) or {}
    app_id = d.get('app_id', '').strip()
    api_key = d.get('api_key', '').strip()

    if not app_id or not api_key:
        return error_response("app_id e api_key são obrigatórios", 400)

    try:
        with get_db() as conn:
            _ensure_config_exists(conn, user_id)
            conn.execute(
                "UPDATE configs SET shopee_app_id=%s, shopee_api_key=%s WHERE user_id=%s",
                (app_id, api_key, user_id)
            )
            conn.commit()

        logger.info(f"✅ Credenciais Shopee salvas para user {user_id}")
        return success_response(message="Credenciais salvas!")
    except Exception as e:
        logger.error(f"Erro ao salvar credenciais Shopee: {e}", extra={"user_id": user_id})
        return error_response(str(e), 500)

# ============================================================================
# ROTAS DE AUTOPOST
# ============================================================================

@app.route('/api/autopost/toggle', methods=['PATCH', 'POST', 'OPTIONS'])
@require_auth
def toggle_autopost(user_id: int):
    """Ativa/desativa autopost"""
    try:
        with get_db() as conn:
            user = conn.execute("SELECT autopost FROM users WHERE id=%s", (user_id,)).fetchone()
            novo = 1 - user['autopost']
            conn.execute("UPDATE users SET autopost=%s WHERE id=%s", (novo, user_id))
            conn.commit()

        status = "ativado" if novo else "desativado"
        logger.info(f"✅ Autopost {status} para user {user_id}")
        return success_response({"autopost": bool(novo)})
    except Exception as e:
        logger.error(f"Erro ao alternar autopost: {e}")
        return error_response("Erro ao alternar autopost", 500)


@app.route('/api/autopost/stats', methods=['GET', 'OPTIONS'])
@require_auth
def autopost_stats(user_id: int):
    """Estatísticas do autopost"""
    try:
        with get_db() as conn:
            control = conn.execute(
                "SELECT * FROM autopost_control WHERE user_id=%s", (user_id,)
            ).fetchone()

            posts = conn.execute(
                """SELECT p.*, g.grupo_nome 
                   FROM posts p 
                   LEFT JOIN grupos g ON p.grupo_id=g.grupo_id AND p.user_id=g.user_id
                   WHERE p.user_id=%s ORDER BY p.created_at DESC LIMIT 10""",
                (user_id,)
            ).fetchall()

            prod_unicos = conn.execute(
                "SELECT COUNT(DISTINCT product_id) as c FROM produtos_enviados WHERE user_id=%s",
                (user_id,)
            ).fetchone()['c']

        return success_response({
            "status": {
                "ativo": bool(control['posts_today'] < 50 if control else True),
                "ultimo_post": control['last_post_at'] if control else None,
                "posts_hoje": control['posts_today'] if control else 0,
                "erros": control['error_count'] if control else 0,
                "pausado_ate": control['paused_until'] if control else None,
                "total_posts": control['total_posts'] if control else 0,
                "produtos_unicos": prod_unicos
            },
            "ultimos_posts": [dict(p) for p in posts]
        })
    except Exception as e:
        logger.error(f"Erro nas stats: {e}")
        return error_response(str(e), 500)


@app.route('/api/autopost/reset-errors', methods=['POST', 'OPTIONS'])
@require_auth
def reset_autopost_errors(user_id: int):
    """Reseta erros do autopost"""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE autopost_control SET error_count=0, paused_until=NULL WHERE user_id=%s",
                (user_id,)
            )
            conn.commit()
        return success_response(message="Erros resetados!")
    except Exception as e:
        return error_response(str(e), 500)


# ============================================================================
# ROTAS DE AGENDAMENTO
# ============================================================================

@app.route('/api/agendamentos', methods=['GET', 'OPTIONS'])
@require_auth
def listar_agendamentos(user_id: int):
    """Lista agendamentos do usuário"""
    try:
        with get_db() as conn:
            ags = conn.execute(
                "SELECT * FROM agendamentos WHERE user_id=%s ORDER BY data_agendada DESC, hora_agendada DESC",
                (user_id,)
            ).fetchall()
        return success_response([dict(a) for a in ags])
    except Exception as e:
        return error_response(str(e), 500)


@app.route('/api/agendamentos', methods=['POST', 'OPTIONS'])
@require_auth
def criar_agendamento(user_id: int):
    """Cria novo agendamento"""
    try:
        data = AgendamentoCreateRequest(**request.get_json(silent=True) or {})
    except Exception as e:
        return error_response(f"Dados inválidos: {e}", 400)

    try:
        grupos_str = ','.join(data.grupos) if isinstance(data.grupos, list) else str(data.grupos)

        with get_db() as conn:
            cursor = conn.execute(
                """INSERT INTO agendamentos (user_id, link, grupos, data_agendada, hora_agendada, titulo)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (user_id, data.link, grupos_str, data.data_agendada, data.hora_agendada, data.titulo or '')
            )
            conn.commit()

        return success_response({"id": cursor.lastrowid}, "Agendamento criado!")
    except Exception as e:
        logger.error(f"Erro ao criar agendamento: {e}")
        return error_response(str(e), 500)


@app.route('/api/agendamentos/<int:agendamento_id>', methods=['DELETE', 'OPTIONS'])
@require_auth
def deletar_agendamento(user_id: int, agendamento_id: int):
    """Deleta agendamento"""
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM agendamentos WHERE id=%s AND user_id=%s",
                (agendamento_id, user_id)
            )
            conn.commit()
        return success_response(message="Agendamento deletado!")
    except Exception as e:
        return error_response(str(e), 500)


# ============================================================================
# ROTAS DE ENVIO MANUAL
# ============================================================================

@app.route('/api/enviar-mensagem', methods=['POST', 'OPTIONS'])
@require_auth
def enviar_mensagem(user_id: int):
    """Envia mensagem manualmente"""
    try:
        data = MensagemRequest(**request.get_json(silent=True) or {})
    except Exception as e:
        return error_response(f"Dados inválidos: {e}", 400)

    try:
        success = whatsapp_service.send_message(
            user_id,
            data.grupo_id,
            data.mensagem,
            data.imagem
        )

        if success:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO auto_post_log (user_id, grupo_id, titulo, status) VALUES (%s, %s, %s, %s)",
                    (user_id, data.grupo_id, data.mensagem[:100], 'enviado')
                )
                conn.commit()
            return success_response(message="Mensagem enviada!")

        return error_response("Falha ao enviar mensagem", 500)
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem: {e}")
        return error_response(str(e), 500)


# ============================================================================
# ROTAS DE PRODUTOS
# ============================================================================

@app.route('/api/buscar-produto', methods=['POST', 'OPTIONS'])
@require_auth
def buscar_produto(user_id: int):
    """Busca produto por link"""
    d = request.get_json(silent=True) or {}
    link = d.get('link', '')

    if not link:
        return error_response("Link obrigatório", 400)

    try:
        with get_db() as conn:
            config = conn.execute(
                "SELECT shopee_app_id, shopee_api_key FROM configs WHERE user_id=%s",
                (user_id,)
            ).fetchone()

        if not config or not config['shopee_app_id']:
            return error_response("Configure sua API Shopee", 400)

        produto = shopee_service.buscar_produto_por_link(
            config['shopee_app_id'],
            config['shopee_api_key'],
            link
        )

        if not produto:
            return error_response("Produto não encontrado", 404)

        return success_response({"produto": produto.to_dict()})
    except Exception as e:
        logger.error(f"Erro ao buscar produto: {e}")
        return error_response(str(e), 500)


# ============================================================================
# ROTAS DE ANALYTICS
# ============================================================================

@app.route('/api/analytics', methods=['GET', 'OPTIONS'])
@require_auth
def analytics(user_id: int):
    """Analytics completo"""
    try:
        with get_db() as conn:
            dias_semana = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab']
            posts_por_dia = {}
            total = 0

            for i in range(6, -1, -1):
                data = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
                count = conn.execute(
                    "SELECT COUNT(*) as c FROM auto_post_log WHERE user_id=%s AND date(created_at)=%s",
                    (user_id, data)
                ).fetchone()['c']
                dia_nome = dias_semana[(datetime.now() - timedelta(days=i)).weekday()]
                posts_por_dia[dia_nome] = count
                total += count

            # Top grupos
            top_grupos = conn.execute(
                """SELECT g.grupo_nome, COUNT(*) as c, AVG(g.ctr) as ctr
                   FROM auto_post_log al 
                   JOIN grupos g ON al.grupo_id=g.grupo_id AND al.user_id=g.user_id
                   WHERE al.user_id=%s AND date(al.created_at) >= date('now', '-7 days')
                   GROUP BY al.grupo_id ORDER BY c DESC LIMIT 5""",
                (user_id,)
            ).fetchall()

            # Templates performance
            templates_perf = conn.execute(
                "SELECT nome, total_envios, total_cliques, ctr FROM templates WHERE user_id=%s AND total_envios > 0",
                (user_id,)
            ).fetchall()

            # Horários com melhor CTR
            melhores_horarios = conn.execute(
                """SELECT hora, AVG(ctr) as avg_ctr, SUM(total_posts) as total
                   FROM horario_metrics WHERE user_id=%s 
                   GROUP BY hora HAVING total >= 3 ORDER BY avg_ctr DESC LIMIT 5""",
                (user_id,)
            ).fetchall()

        return success_response({
            "posts_por_dia": posts_por_dia,
            "total_semana": total,
            "top_grupos": [{"nome": g['grupo_nome'] or 'Grupo', "posts": g['c'], "ctr": round(g['ctr'] or 0, 2)} for g in top_grupos],
            "templates_performance": [{"nome": t['nome'], "envios": t['total_envios'], "cliques": t['total_cliques'], "ctr": round(t['ctr'], 2)} for t in templates_perf],
            "melhores_horarios": [{"hora": f"{h['hora']:02d}:00", "ctr": round(h['avg_ctr'], 2), "posts": h['total']} for h in melhores_horarios]
        })
    except Exception as e:
        logger.error(f"Erro no analytics: {e}")
        return error_response(str(e), 500)


# ============================================================================
# ROTAS DE CLIQUES
# ============================================================================

@app.route('/r/<short_code>')
def redirect_click(short_code: str):
    """Redireciona clique e registra métrica"""
    try:
        with get_db() as conn:
            click = conn.execute(
                "SELECT * FROM clicks WHERE short_code=%s", (short_code,)
            ).fetchone()

            if click:
                conn.execute(
                    "UPDATE clicks SET clicked_at=datetime('now') WHERE short_code=%s",
                    (short_code,)
                )

                # Atualizar métricas do post
                if click['post_id']:
                    conn.execute(
                        "UPDATE posts SET cliques=cliques+1 WHERE id=%s",
                        (click['post_id'],)
                    )

                # Atualizar CTR do grupo
                if click['grupo_id']:
                    conn.execute(
                        """UPDATE grupos SET total_cliques=total_cliques+1, 
                           ctr=CAST(total_cliques+1 AS REAL)/(total_posts+1)*100 
                           WHERE grupo_id=%s AND user_id=%s""",
                        (click['grupo_id'], click['user_id'])
                    )

                # Atualizar A/B test
                if click['template_id']:
                    conn.execute(
                        """UPDATE templates SET total_cliques=total_cliques+1,
                           ctr=CAST(total_cliques+1 AS REAL)/(total_envios+1)*100
                           WHERE id=%s""",
                        (click['template_id'],)
                    )

                conn.commit()

                from flask import redirect
                return redirect(click['produto_link'], code=302)
    except Exception as e:
        logger.error(f"Erro no redirect: {e}")

    return error_response("Link não encontrado", 404)


@app.route('/api/clicks/stats', methods=['GET', 'OPTIONS'])
@require_auth
def clicks_stats(user_id: int):
    """Estatísticas de cliques"""
    try:
        with get_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM clicks WHERE user_id=%s", (user_id,)
            ).fetchone()['c']

            hoje = conn.execute(
                "SELECT COUNT(*) as c FROM clicks WHERE user_id=%s AND date(clicked_at)=date('now')",
                (user_id,)
            ).fetchone()['c']

            por_grupo = conn.execute(
                """SELECT grupo_id, COUNT(*) as c FROM clicks 
                   WHERE user_id=%s GROUP BY grupo_id ORDER BY c DESC LIMIT 10""",
                (user_id,)
            ).fetchall()

        return success_response({
            "total_clicks": total,
            "clicks_hoje": hoje,
            "por_grupo": [{"grupo": g['grupo_id'][:30], "cliques": g['c']} for g in por_grupo]
        })
    except Exception as e:
        return error_response(str(e), 500)


# ============================================================================
# ROTAS DE PLANO
# ============================================================================

@app.route('/api/plano/limites', methods=['GET', 'OPTIONS'])
@require_auth
def verificar_limites(user_id: int):
    """Verifica limites do plano"""
    try:
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
            plano = conn.execute("SELECT * FROM planos WHERE id=%s", (user['plano_ativo'] or 1,)).fetchone()
            grupos_count = conn.execute(
                "SELECT COUNT(*) as c FROM grupos WHERE user_id=%s AND selecionado=1 AND ativo=1", (user_id,)
            ).fetchone()

            dias = 0
            if user['trial_start']:
                try:
                    started = datetime.fromisoformat(user['trial_start'].replace(' ', 'T').split('+')[0])
                    duracao = 7 if user['plano_ativo'] == 1 else 30
                    dias = max(0, duracao - (datetime.now() - started).days)
                except:
                    pass

        return success_response({
            "plano": {
                "id": plano['id'],
                "nome": plano['nome'],
                "max_grupos": plano['max_grupos'],
                "max_posts_dia": plano['max_posts_dia'],
                "preco": plano['preco'],
                "duracao_dias": plano['duracao_dias']
            },
            "dias_restantes": dias,
            "grupos_selecionados": grupos_count['c']
        })
    except Exception as e:
        return error_response(str(e), 500)


@app.route('/api/plano/disponiveis', methods=['GET', 'OPTIONS'])
@require_auth
def planos_disponiveis(user_id: int):
    """Lista planos disponíveis"""
    try:
        with get_db() as conn:
            user = conn.execute("SELECT plano_ativo FROM users WHERE id=%s", (user_id,)).fetchone()
            planos = conn.execute("SELECT * FROM planos ORDER BY id").fetchall()

        return success_response({
            "planos": [dict(p) for p in planos],
            "plano_atual_id": user['plano_ativo'] if user else 1
        })
    except Exception as e:
        return error_response(str(e), 500)


# ============================================================================
# ROTAS ADICIONAIS (piloto, relatorio, pagamentos, etc)
# ============================================================================

@app.route('/api/piloto/status', methods=['GET', 'OPTIONS'])
@require_auth
def piloto_status(user_id: int):
    try:
        with get_db() as conn:
            user = conn.execute("SELECT autopost FROM users WHERE id=%s", (user_id,)).fetchone()
            control = conn.execute("SELECT posts_today, error_count, last_post_at FROM autopost_control WHERE user_id=%s", (user_id,)).fetchone()
            config = conn.execute("SELECT intervalo, hora_inicio, hora_fim FROM configs WHERE user_id=%s", (user_id,)).fetchone()
            
            ativo = bool(user['autopost']) if user else False
            intervalo = int(config['intervalo']) if config and config['intervalo'] else 30
            
            # Calcular próximo post (BRASIL UTC-3)
            proximo = None
            agora = datetime.now() - timedelta(hours=3)
            if ativo and control and control['last_post_at']:
                try:
                    last = datetime.fromisoformat(control['last_post_at'].replace(' ', 'T').split('+')[0])
                    candidato = last + timedelta(minutes=intervalo)
                    # Se o candidato já passou, o próximo é AGORA
                    if candidato < agora:
                        proximo = agora.isoformat()
                    else:
                        proximo = candidato.isoformat()
                except:
                    proximo = agora.isoformat()
            elif ativo:
                proximo = agora.isoformat()
            
            return success_response({
                "ativo": ativo,
                "posts_hoje": control['posts_today'] if control else 0,
                "posts_restantes": 200 - (control['posts_today'] if control else 0),
                "proximo_post": proximo,
                "erros": control['error_count'] if control else 0
            })
    except Exception as e:
        return error_response(str(e), 500)

@app.route('/api/piloto/toggle', methods=['POST', 'OPTIONS'])
@require_auth
def piloto_toggle(user_id: int):
    try:
        with get_db() as conn:
            user = conn.execute("SELECT autopost FROM users WHERE id=%s", (user_id,)).fetchone()
            novo = 0 if user['autopost'] else 1
            conn.execute("UPDATE users SET autopost=%s WHERE id=%s", (novo, user_id))
            conn.commit()
        return success_response({"ativo": bool(novo)})
    except Exception as e:
        return error_response(str(e), 500)

@app.route('/api/piloto/config', methods=['PUT', 'OPTIONS'])
@require_auth
def piloto_config(user_id: int):
    d = request.get_json(silent=True) or {}
    try:
        with get_db() as conn:
            conn.execute("UPDATE configs SET intervalo=%s, hora_inicio=%s, hora_fim=%s WHERE user_id=%s",
                (d.get('intervalo_minimo', 30), d.get('horario_inicio', '08:00'), d.get('horario_fim', '22:00'), user_id))
            conn.commit()
        return success_response(message="Configurações salvas!")
    except Exception as e:
        return error_response(str(e), 500)

@app.route('/api/relatorio', methods=['GET', 'OPTIONS'])
@require_auth
def relatorio(user_id: int):
    return success_response({
        "total_posts": 0,
        "total_cliques": 0,
        "taxa_clique": 0,
        "posts_por_dia": {},
        "posts": []
    })

@app.route('/api/shopee/conversoes', methods=['GET', 'OPTIONS'])
@require_auth
def shopee_conversoes(user_id: int):
    return success_response({"conversoes": [], "total_vendas": 0, "total_comissao": 0})

@app.route('/api/ab-tests', methods=['GET', 'OPTIONS'])
@require_auth
def ab_tests(user_id: int):
    return success_response({"testes": []})

@app.route('/api/ab-tests', methods=['POST', 'OPTIONS'])
@require_auth
def criar_ab_test(user_id: int):
    return success_response({"id": 1}, "Teste A/B criado!")

@app.route('/api/ab-tests/<int:test_id>', methods=['DELETE', 'OPTIONS'])
@require_auth
def deletar_ab_test(user_id: int, test_id: int):
    return success_response(message="Teste removido!")

@app.route('/api/whatsapp/save-creds', methods=['POST'])
def save_wa_creds():
    try:
        import psycopg2, os
        data = request.get_json(silent=True) or {}
        uid = str(data.get('userId', ''))
        creds = data.get('creds', '')
        connected = 1 if data.get('connected', False) else 0
        
        conn = psycopg2.connect('postgresql://postgres:wAPmhEQuFdJowHjWyveTUTkdotElMtOQ@kodama.proxy.rlwy.net:21141/railway')
        cur = conn.cursor()
        cur.execute("INSERT INTO wa_sessions (user_id, creds_json, connected, updated_at) VALUES (%s, %s, %s, NOW()) ON CONFLICT (user_id) DO UPDATE SET creds_json=%s, connected=%s, updated_at=NOW()", (uid, creds, connected, creds, connected))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/whatsapp/get-creds', methods=['GET'])
def get_wa_creds():
    try:
        import psycopg2, os
        uid = request.args.get('userId', '')
        conn = psycopg2.connect('postgresql://postgres:wAPmhEQuFdJowHjWyveTUTkdotElMtOQ@kodama.proxy.rlwy.net:21141/railway')
        cur = conn.cursor()
        cur.execute("SELECT creds_json, connected FROM wa_sessions WHERE user_id=%s", (uid,))
        row = cur.fetchone()
        conn.close()
        if row:
            return jsonify({"success": True, "creds": row[0], "connected": bool(row[1])})
        return jsonify({"success": False, "error": "Sem sessão"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/whatsapp/status', methods=['GET'])
@require_auth
def whatsapp_status(user_id: int):
    import requests as req
    try:
        resp = req.get('http://127.0.0.1:3000/status/' + str(user_id), timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return success_response({
                "connected": data.get('connected', False),
                "pareado": True
            })
        return success_response({"connected": False, "pareado": False})
    except:
        return success_response({"connected": False, "pareado": False})

@app.route('/api/whatsapp/pairing', methods=['POST', 'OPTIONS'])
@require_auth
def whatsapp_pairing(user_id: int):
    import requests as req
    data = request.get_json(silent=True) or {}
    phone = data.get('phone', '')
    if not phone:
        return error_response("Número obrigatório", 400)
    try:
        resp = req.post('http://127.0.0.1:3000/pairing-code', json={"userId": str(user_id), "phoneNumber": phone}, timeout=60)
        if resp.status_code == 200:
            result = resp.json()
            if result.get('success'):
                return success_response({"code": result.get('pairingCode'), "phone": phone}, "Código gerado!")
            return error_response(result.get('error', 'Erro'), 500)
        return error_response(f"Bridge erro {resp.status_code}", 503)
    except Exception as e:
        return error_response(f"Bridge offline: {str(e)}", 503)

@app.route('/api/ia/gerar-template', methods=['POST', 'OPTIONS'])
@require_auth
def ia_gerar_template(user_id: int):
    return success_response({"template": "🔥 {titulo} por R$ {preco}!"}, "Template gerado!")

@app.route('/api/clonar-post', methods=['POST', 'OPTIONS'])
@require_auth
def clonar_post(user_id: int):
    """Clona um post a partir de um link de produto"""
    d = request.get_json(silent=True) or {}
    link = d.get('link', '')
    
    if not link:
        return error_response("Link obrigatório", 400)
    
    try:
        with get_db() as conn:
            config = conn.execute(
                "SELECT shopee_app_id, shopee_api_key FROM configs WHERE user_id=%s",
                (user_id,)
            ).fetchone()
        
        if not config or not config['shopee_app_id']:
            return error_response("Configure sua API Shopee", 400)
        
        # Buscar produto na Shopee
        produto = shopee_service.buscar_produto_por_link(
            config['shopee_app_id'],
            config['shopee_api_key'],
            link
        )
        
        if not produto:
            return error_response("Produto não encontrado", 404)
        
        # Gerar texto clonado
        texto = f"🔥 {produto.titulo}\n\n"
        texto += f"💰 De R$ {produto.preco_original:.2f} por R$ {produto.preco:.2f}\n"
        texto += f"📉 Desconto de {produto.desconto_pct}%\n\n"
        texto += f"👉 {produto.link_afiliado}\n\n"
        texto += f"🏪 Loja: {produto.loja}\n"
        
        return success_response({
            "texto_clonado": texto,
            "produto": produto.to_dict()
        }, "Post clonado com sucesso!")
        
    except Exception as e:
        logger.error(f"Erro ao clonar post: {e}")
        return error_response(f"Erro ao clonar: {str(e)}", 500)

@app.route('/api/grupos/selecionar', methods=['POST', 'OPTIONS'])
@require_auth
def selecionar_grupos(user_id: int):
    d = request.get_json(silent=True) or {}
    grupo_ids = d.get('grupo_ids', [])
    try:
        with get_db() as conn:
            conn.execute("UPDATE grupos SET selecionado=0 WHERE user_id=%s", (user_id,))
            for gid in grupo_ids:
                conn.execute("UPDATE grupos SET selecionado=1 WHERE id=%s AND user_id=%s", (gid, user_id))
            conn.commit()
        return success_response({"selecionados": len(grupo_ids)})
    except Exception as e:
        return error_response(str(e), 500)

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check profundo"""
    status = {"status": "online", "timestamp": datetime.now().isoformat()}

    # Verificar banco
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        status["database"] = "ok"
    except Exception as e:
        status["database"] = f"error: {str(e)}"
        status["status"] = "degraded"

    # Verificar bridge (não falha o health check se bridge offline)
    try:
        resp = whatsapp_service.session.get(f"{settings.WA_BRIDGE_URL}/status", timeout=3)
        status["bridge"] = "ok" if resp.status_code == 200 else "unreachable"
    except:
        status["bridge"] = "unreachable"

    # Circuit breakers
    status["circuit_breakers"] = {
        "wa_bridge": wa_bridge_breaker.stats,
        "shopee_api": shopee_api_breaker.stats
    }

    # Sempre retorna 200 para não matar o container no Railway
    return jsonify(status), 200


@app.route('/api/version', methods=['GET'])
def api_version():
    return jsonify({"version": "6.0", "build": "2026-07-27", "status": "online"})


# ============================================================================
# SERVIR FRONTEND
# ============================================================================

@app.route('/')
def serve_frontend():
    try:
        frontend_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
        if os.path.exists(frontend_path):
            return send_from_directory(os.path.dirname(frontend_path), 'index.html')
        return "<h1>WA Affiliate Pro v6.0</h1><p>API Online. Frontend não encontrado.</p>", 200
    except Exception as e:
        return f"<h1>WA Affiliate Pro v6.0</h1><p>Erro: {e}</p>", 500


# ============================================================================
# GRACEFUL SHUTDOWN
# ============================================================================

def signal_handler(sig, frame):
    """Handler para shutdown gracioso"""
    logger.info("🛑 Sinal de shutdown recebido. Parando workers...")
    autopost_engine.stop()
    agendador_worker.stop()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ============================================================================
# INICIALIZAÇÃO
# ============================================================================

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🚀 WA Affiliate Pro v6.0 — Iniciando")
    logger.info(f"📊 Banco: {settings.DB_PATH}")
    logger.info(f"🔐 JWT: {settings.JWT_SECRET[:20]}...")
    logger.info("=" * 60)

    # Iniciar workers
    # Workers desativados temporariamente
    autopost_engine.start()
    agendador_worker.start()

    app.run(
        host='0.0.0.0',
        port=int(os.environ.get("PORT", 5002)),
        debug=False,
        use_reloader=False,
        threaded=True
    )

# Rota para deletar template
@app.route('/api/templates/<int:template_id>', methods=['DELETE', 'OPTIONS'])
@require_auth
def deletar_template(user_id: int, template_id: int):
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM templates WHERE id=%s AND user_id=%s",
                (template_id, user_id)
            )
            conn.commit()
        return success_response(message="Template deletado com sucesso!")
    except Exception as e:
        return error_response(str(e), 500)
