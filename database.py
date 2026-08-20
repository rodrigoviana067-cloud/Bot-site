#!/usr/bin/env python3
"""Database layer - SQLite"""
import sqlite3
import logging
from contextlib import contextmanager

logger = logging.getLogger('affiliate.db')

@contextmanager
def get_db():
    conn = sqlite3.connect('/app/data/affiliate.db' if __import__('os').path.exists('/app/data') else './affiliate.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            whatsapp TEXT,
            senha TEXT NOT NULL,
            trial_start TEXT,
            plano_ativo INTEGER DEFAULT 1,
            autopost INTEGER DEFAULT 0,
            bridge_id INTEGER DEFAULT 0,
            trial_used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS configs (
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
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS planos (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            max_grupos INTEGER DEFAULT 5,
            max_posts_dia INTEGER DEFAULT 50,
            preco REAL DEFAULT 0.0,
            duracao_dias INTEGER DEFAULT 7
        )""")
        c.execute("SELECT COUNT(*) FROM planos")
        if c.fetchone()[0] == 0:
            planos = [(1,'Trial',5,20,0,7),(2,'Pro',20,50,29.9,30),(3,'Elite',40,100,49.9,30),(4,'Enterprise',100,200,99.9,30)]
            c.executemany("INSERT INTO planos VALUES (?,?,?,?,?,?)", planos)
        c.execute("""CREATE TABLE IF NOT EXISTS auto_post_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, grupo_id TEXT, titulo TEXT, status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS grupos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, grupo_id TEXT, grupo_nome TEXT,
            selecionado INTEGER DEFAULT 0, ativo INTEGER DEFAULT 1,
            nicho TEXT DEFAULT 'todos',
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS agendamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, link TEXT, grupos TEXT,
            data_agendada TEXT, hora_agendada TEXT,
            status TEXT DEFAULT 'pendente', titulo TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS autopost_control (
            user_id INTEGER PRIMARY KEY,
            last_post_at TEXT, posts_today INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0, total_posts INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, nome TEXT, copy TEXT,
            selecionado INTEGER DEFAULT 0, ab_test_group TEXT DEFAULT 'A',
            total_envios INTEGER DEFAULT 0, total_cliques INTEGER DEFAULT 0,
            ctr REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.commit()
    logger.info("✅ SQLite inicializado!")

# Classes para compatibilidade
class Template:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 0)
        self.user_id = kwargs.get('user_id', 0)
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
