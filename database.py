#!/usr/bin/env python3
"""Database layer - SQLite (fallback) or PostgreSQL"""
import os
import sqlite3
import logging
from contextlib import contextmanager

logger = logging.getLogger('affiliate.db')

# Verificar se DATABASE_URL do Railway existe
DATABASE_URL = os.environ.get('DATABASE_URL', '')

if DATABASE_URL and DATABASE_URL.startswith('postgres'):
    import psycopg2
    import psycopg2.extras
    
    @contextmanager
    def get_db():
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        try:
            yield conn
        finally:
            conn.close()
    
    def init_database():
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    whatsapp TEXT,
                    senha TEXT NOT NULL,
                    trial_start TEXT,
                    plano_ativo INTEGER DEFAULT 1,
                    autopost INTEGER DEFAULT 0,
                    bridge_id INTEGER DEFAULT 0,
                    trial_used INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS configs (
                    user_id INTEGER PRIMARY KEY,
                    shopee_app_id TEXT DEFAULT '',
                    shopee_api_key TEXT DEFAULT '',
                    intervalo INTEGER DEFAULT 30,
                    min_desconto INTEGER DEFAULT 20,
                    hora_inicio TEXT DEFAULT '08:00',
                    hora_fim TEXT DEFAULT '22:00',
                    max_posts_dia INTEGER DEFAULT 50,
                    usar_smart_schedule INTEGER DEFAULT 1,
                    usar_ab_testing INTEGER DEFAULT 1
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS planos (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    max_grupos INTEGER DEFAULT 5,
                    max_posts_dia INTEGER DEFAULT 50,
                    preco REAL DEFAULT 0.0,
                    duracao_dias INTEGER DEFAULT 7
                )
            """)
            cur.execute("SELECT COUNT(*) FROM planos")
            if cur.fetchone()[0] == 0:
                planos = [
                    (1, 'Trial', 5, 20, 0.0, 7),
                    (2, 'Pro', 20, 50, 29.90, 30),
                    (3, 'Elite', 40, 100, 49.90, 30),
                    (4, 'Enterprise', 100, 200, 99.90, 30)
                ]
                for p in planos:
                    cur.execute("INSERT INTO planos (id, nome, max_grupos, max_posts_dia, preco, duracao_dias) VALUES (%s,%s,%s,%s,%s,%s)", p)
            conn.commit()
            logger.info("✅ PostgreSQL inicializado!")
else:
    @contextmanager
    def get_db():
        conn = sqlite3.connect('./affiliate.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def init_database():
        logger.info("✅ SQLite inicializado!")

# Classes simples para compatibilidade
class Template:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 0)
        self.user_id = kwargs.get('user_id', 0)
        self.nome = kwargs.get('nome', '')
        self.copy = kwargs.get('copy', '')
        self.selecionado = kwargs.get('selecionado', 0)
        self.ab_test_group = kwargs.get('ab_test_group', 'A')
        self.win_rate = kwargs.get('win_rate', 0.0)
        self.total_envios = kwargs.get('total_envios', 0)
        self.total_cliques = kwargs.get('total_cliques', 0)
        self.ctr = kwargs.get('ctr', 0.0)

class Grupo:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 0)
        self.user_id = kwargs.get('user_id', 0)
        self.grupo_id = kwargs.get('grupo_id', '')
        self.grupo_nome = kwargs.get('grupo_nome', '')
        self.selecionado = kwargs.get('selecionado', 0)
        self.nicho = kwargs.get('nicho', 'todos')

class User:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 0)
        self.nome = kwargs.get('nome', '')
        self.email = kwargs.get('email', '')
        self.plano_ativo = kwargs.get('plano_ativo', 1)
        self.autopost = kwargs.get('autopost', 0)
