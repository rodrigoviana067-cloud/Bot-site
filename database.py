#!/usr/bin/env python3
"""Database - PostgreSQL"""
import os
import logging
from contextlib import contextmanager

logger = logging.getLogger('affiliate.db')

DATABASE_URL = os.environ.get('DATABASE_URL', '') or os.environ.get('DATABASE_PRIVATE_URL', '')

if DATABASE_URL and 'postgres' in DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    
    class DB:
        def __init__(self, conn):
            self._conn = conn
        def cursor(self):
            return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        def execute(self, query, params=None):
            cur = self.cursor()
            if params:
                cur.execute(query, params)
            else:
                cur.execute(query)
            self._conn.commit()
            return cur
        def commit(self):
            self._conn.commit()
        def close(self):
            self._conn.close()
    
    @contextmanager
    def get_db():
        conn = psycopg2.connect(DATABASE_URL)
        db = DB(conn)
        try:
            yield db
        finally:
            conn.close()
    
    def init_database():
        with get_db() as db:
            cur = db.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, nome TEXT NOT NULL, email TEXT UNIQUE NOT NULL, whatsapp TEXT, senha TEXT NOT NULL, trial_start TEXT, plano_ativo INTEGER DEFAULT 1, autopost INTEGER DEFAULT 0, bridge_id INTEGER DEFAULT 0, trial_used INTEGER DEFAULT 0, created_at TEXT DEFAULT NOW())")
            cur.execute("CREATE TABLE IF NOT EXISTS configs (user_id INTEGER PRIMARY KEY, shopee_app_id TEXT DEFAULT '', shopee_api_key TEXT DEFAULT '', intervalo INTEGER DEFAULT 30, min_desconto INTEGER DEFAULT 20, hora_inicio TEXT DEFAULT '08:00', hora_fim TEXT DEFAULT '22:00', max_posts_dia INTEGER DEFAULT 50, usar_smart_schedule INTEGER DEFAULT 1, usar_ab_testing INTEGER DEFAULT 1)")
            cur.execute("CREATE TABLE IF NOT EXISTS planos (id SERIAL PRIMARY KEY, nome TEXT NOT NULL, max_grupos INTEGER DEFAULT 5, max_posts_dia INTEGER DEFAULT 50, preco REAL DEFAULT 0.0, duracao_dias INTEGER DEFAULT 7)")
            cur.execute("SELECT COUNT(*) as c FROM planos")
            if cur.fetchone()['c'] == 0:
                for p in [(1,'Trial',5,20,0,7),(2,'Pro',20,50,29.9,30),(3,'Elite',40,100,49.9,30),(4,'Enterprise',100,200,99.9,30)]:
                    cur.execute("INSERT INTO planos (id,nome,max_grupos,max_posts_dia,preco,duracao_dias) VALUES (%s,%s,%s,%s,%s,%s)", p)
            cur.execute("CREATE TABLE IF NOT EXISTS auto_post_log (id SERIAL PRIMARY KEY, user_id INTEGER, grupo_id TEXT, titulo TEXT, status TEXT, created_at TEXT DEFAULT NOW())")
            cur.execute("CREATE TABLE IF NOT EXISTS grupos (id SERIAL PRIMARY KEY, user_id INTEGER, grupo_id TEXT, grupo_nome TEXT, selecionado INTEGER DEFAULT 0, ativo INTEGER DEFAULT 1, nicho TEXT DEFAULT 'todos', total_cliques INTEGER DEFAULT 0, total_posts INTEGER DEFAULT 0, ctr REAL DEFAULT 0.0, ultimo_post TEXT, melhor_horario TEXT, created_at TEXT DEFAULT NOW())")
            cur.execute("CREATE TABLE IF NOT EXISTS agendamentos (id SERIAL PRIMARY KEY, user_id INTEGER, link TEXT, grupos TEXT, data_agendada TEXT, hora_agendada TEXT, status TEXT DEFAULT 'pendente', titulo TEXT, created_at TEXT DEFAULT NOW())")
            cur.execute("CREATE TABLE IF NOT EXISTS autopost_control (user_id INTEGER PRIMARY KEY, last_post_at TEXT, posts_today INTEGER DEFAULT 0, error_count INTEGER DEFAULT 0, total_posts INTEGER DEFAULT 0)")
            cur.execute("CREATE TABLE IF NOT EXISTS templates (id SERIAL PRIMARY KEY, user_id INTEGER, nome TEXT, copy TEXT, selecionado INTEGER DEFAULT 0, ab_test_group TEXT DEFAULT 'A', total_envios INTEGER DEFAULT 0, total_cliques INTEGER DEFAULT 0, ctr REAL DEFAULT 0.0, created_at TEXT DEFAULT NOW())")
            cur.execute("CREATE TABLE IF NOT EXISTS posts (id SERIAL PRIMARY KEY, user_id INTEGER, grupo_id TEXT, titulo TEXT, link TEXT, link_afiliado TEXT, plataforma TEXT DEFAULT 'wh', template_id INTEGER, ab_test_group TEXT, status TEXT DEFAULT 'enviado', cliques INTEGER DEFAULT 0, created_at TEXT DEFAULT NOW())")
            cur.execute("CREATE TABLE IF NOT EXISTS clicks (id SERIAL PRIMARY KEY, user_id INTEGER, post_id INTEGER, short_code TEXT UNIQUE NOT NULL, produto_link TEXT NOT NULL, grupo_id TEXT, template_id INTEGER, clicked_at TEXT, converted INTEGER DEFAULT 0, conversion_value REAL DEFAULT 0.0)")
            cur.execute("CREATE TABLE IF NOT EXISTS horario_metrics (id SERIAL PRIMARY KEY, user_id INTEGER, grupo_id TEXT, hora INTEGER, dia_semana INTEGER, total_posts INTEGER DEFAULT 0, total_cliques INTEGER DEFAULT 0, ctr REAL DEFAULT 0.0)")
            cur.execute("CREATE TABLE IF NOT EXISTS produtos_enviados (id SERIAL PRIMARY KEY, user_id INTEGER, product_id TEXT, grupo_id TEXT, enviado_em TEXT DEFAULT NOW())")
            cur.execute("CREATE TABLE IF NOT EXISTS ab_tests (id SERIAL PRIMARY KEY, user_id INTEGER, nome TEXT, template_a_id INTEGER, template_b_id INTEGER, created_at TEXT DEFAULT NOW())")
            cur.execute("CREATE TABLE IF NOT EXISTS pagamentos (id SERIAL PRIMARY KEY, user_id INTEGER, order_id TEXT UNIQUE, payment_id TEXT, status TEXT DEFAULT 'pending', valor REAL, plano TEXT, metodo TEXT, criado_em TEXT, processado INTEGER DEFAULT 0)")
            db.commit()
        logger.info("✅ PostgreSQL inicializado!")

class Template:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 0)
        self.nome = kwargs.get('nome', '')
        self.copy = kwargs.get('copy', '')
        self.selecionado = kwargs.get('selecionado', 0)
        self.ab_test_group = kwargs.get('ab_test_group', 'A')
        self.total_envios = kwargs.get('total_envios', 0)
        self.total_cliques = kwargs.get('total_cliques', 0)
        self.ctr = kwargs.get('ctr', 0.0)

class Grupo:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 0)
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
