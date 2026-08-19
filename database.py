#!/usr/bin/env python3
"""
WA Affiliate Pro v6.0 — Camada de Banco de Dados
SQLite com connection pool, índices otimizados e thread safety
"""

import sqlite3
import threading
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List, Dict, Any

from config import settings

logger = logging.getLogger('affiliate.db')


class ConnectionPool:
    """Pool de conexões SQLite thread-safe"""

    def __init__(self, db_path: str, max_size: int = 5, timeout: int = 30):
        self.db_path = db_path
        self.max_size = max_size
        self.timeout = timeout
        self._pool = []
        self._lock = threading.Lock()
        self._local = threading.local()

        # Criar conexões iniciais
        for _ in range(max_size):
            conn = self._create_connection()
            self._pool.append(conn)

    def _create_connection(self) -> sqlite3.Connection:
        """Cria uma nova conexão SQLite otimizada"""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=self.timeout,
            isolation_level=None
        )
        conn.row_factory = sqlite3.Row

        # Otimizações SQLite
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=30000000000")

        return conn

    @contextmanager
    def acquire(self):
        """Adquire uma conexão do pool"""
        conn = None
        with self._lock:
            if self._pool:
                conn = self._pool.pop()

        if conn is None:
            conn = self._create_connection()

        try:
            yield conn
        finally:
            with self._lock:
                if len(self._pool) < self.max_size:
                    self._pool.append(conn)
                else:
                    conn.close()


# Pool global
_pool: Optional[ConnectionPool] = None


def init_pool() -> ConnectionPool:
    """Inicializa o pool de conexões"""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.DB_PATH,
            max_size=settings.DB_POOL_SIZE,
            timeout=settings.DB_TIMEOUT
        )
    return _pool


@contextmanager
def get_db():
    """Context manager para obter conexão do pool"""
    pool = init_pool()
    with pool.acquire() as conn:
        yield conn


def init_database():
    """Inicializa o banco com todas as tabelas e índices"""
    with get_db() as conn:
        c = conn.cursor()

        # Tabela de usuários
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                whatsapp TEXT,
                senha TEXT NOT NULL,
                trial_start TEXT,
                plano_ativo INTEGER DEFAULT 1,
                autopost INTEGER DEFAULT 0,
                bridge_id INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Tabela de configurações
        c.execute("""
            CREATE TABLE IF NOT EXISTS configs (
                user_id INTEGER PRIMARY KEY,
                shopee_app_id TEXT,
                shopee_api_key TEXT,
                intervalo INTEGER DEFAULT 30,
                min_desconto INTEGER DEFAULT 20,
                estilo TEXT DEFAULT 'padrao',
                hora_inicio TEXT DEFAULT '08:00',
                hora_fim TEXT DEFAULT '22:00',
                max_posts_dia INTEGER DEFAULT 50,
                usar_smart_schedule INTEGER DEFAULT 1,
                usar_ab_testing INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Tabela de templates
        c.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                nome TEXT NOT NULL,
                copy TEXT NOT NULL,
                selecionado INTEGER DEFAULT 0,
                ab_test_group TEXT DEFAULT 'A',
                win_rate REAL DEFAULT 0.0,
                total_envios INTEGER DEFAULT 0,
                total_cliques INTEGER DEFAULT 0,
                ctr REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Tabela de grupos
        c.execute("""
            CREATE TABLE IF NOT EXISTS grupos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                grupo_id TEXT NOT NULL,
                grupo_nome TEXT,
                plataforma TEXT DEFAULT 'wh',
                fonte TEXT DEFAULT 'ambos',
                selecionado INTEGER DEFAULT 0,
                nicho TEXT DEFAULT 'todos',
                ativo INTEGER DEFAULT 1,
                total_cliques INTEGER DEFAULT 0,
                total_posts INTEGER DEFAULT 0,
                ctr REAL DEFAULT 0.0,
                ultimo_post TEXT,
                melhor_horario TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Tabela de posts
        c.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                grupo_id TEXT NOT NULL,
                titulo TEXT,
                link TEXT,
                link_afiliado TEXT,
                plataforma TEXT DEFAULT 'wh',
                template_id INTEGER,
                ab_test_group TEXT,
                status TEXT DEFAULT 'enviado',
                cliques INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (template_id) REFERENCES templates(id)
            )
        """)

        # Tabela de cliques (rastreamento)
        c.execute("""
            CREATE TABLE IF NOT EXISTS clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                post_id INTEGER,
                short_code TEXT UNIQUE NOT NULL,
                produto_link TEXT NOT NULL,
                grupo_id TEXT,
                template_id INTEGER,
                clicked_at TEXT,
                converted INTEGER DEFAULT 0,
                conversion_value REAL DEFAULT 0.0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (post_id) REFERENCES posts(id)
            )
        """)

        # Tabela de remarketing
        c.execute("""
            CREATE TABLE IF NOT EXISTS remarketing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                click_id INTEGER NOT NULL,
                etapa INTEGER DEFAULT 1,
                status TEXT DEFAULT 'pendente',
                scheduled_at TEXT NOT NULL,
                sent_at TEXT,
                message TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (click_id) REFERENCES clicks(id)
            )
        """)

        # Tabela de planos
        c.execute("""
            CREATE TABLE IF NOT EXISTS planos (
                id INTEGER PRIMARY KEY,
                nome TEXT NOT NULL,
                max_grupos INTEGER DEFAULT 5,
                max_posts_dia INTEGER DEFAULT 50,
                preco REAL DEFAULT 0.0,
                duracao_dias INTEGER DEFAULT 7
            )
        """)

        # Tabela de pagamentos
        c.execute("""
            CREATE TABLE IF NOT EXISTS pagamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plano_id INTEGER,
                payment_id TEXT,
                status TEXT DEFAULT 'pendente',
                payment_link TEXT,
                valor REAL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (plano_id) REFERENCES planos(id)
            )
        """)

        # Tabela de log de autopost
        c.execute("""
            CREATE TABLE IF NOT EXISTS auto_post_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                grupo_id TEXT,
                titulo TEXT,
                status TEXT,
                erro TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Tabela de agendamentos
        c.execute("""
            CREATE TABLE IF NOT EXISTS agendamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                link TEXT NOT NULL,
                grupos TEXT NOT NULL,
                data_agendada TEXT NOT NULL,
                hora_agendada TEXT NOT NULL,
                status TEXT DEFAULT 'pendente',
                titulo TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Tabela de controle de autopost
        c.execute("""
            CREATE TABLE IF NOT EXISTS autopost_control (
                user_id INTEGER PRIMARY KEY,
                last_post_at TEXT,
                posts_today INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                paused_until TEXT,
                total_posts INTEGER DEFAULT 0,
                total_cliques INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Tabela de produtos enviados
        c.execute("""
            CREATE TABLE IF NOT EXISTS produtos_enviados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id TEXT NOT NULL,
                grupo_id TEXT NOT NULL,
                enviado_em TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, product_id, grupo_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Tabela de métricas por horário (smart schedule)
        c.execute("""
            CREATE TABLE IF NOT EXISTS horario_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                grupo_id TEXT NOT NULL,
                hora INTEGER NOT NULL,
                dia_semana INTEGER NOT NULL,
                total_posts INTEGER DEFAULT 0,
                total_cliques INTEGER DEFAULT 0,
                ctr REAL DEFAULT 0.0,
                UNIQUE(user_id, grupo_id, hora, dia_semana),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # ÍNDICES CRÍTICOS PARA PERFORMANCE
        c.execute("CREATE INDEX IF NOT EXISTS idx_posts_user_date ON posts(user_id, created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_posts_grupo ON posts(grupo_id, created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clicks_user ON clicks(user_id, clicked_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clicks_short ON clicks(short_code)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_log_user ON auto_post_log(user_id, created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_agendamentos_status ON agendamentos(status, data_agendada, hora_agendada)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_produtos_enviados ON produtos_enviados(user_id, product_id, grupo_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_grupos_user ON grupos(user_id, selecionado, ativo)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_templates_user ON templates(user_id, selecionado)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_horario_metrics ON horario_metrics(user_id, grupo_id, ctr)")

        # Inserir planos padrão
        c.execute("SELECT COUNT(*) FROM planos")
        if c.fetchone()[0] == 0:
            planos = [
                (1, 'Trial', 5, 20, 0.0, 7),
                (2, 'Pro', 20, 50, 29.90, 30),
                (3, 'Elite', 40, 100, 49.90, 30),
                (4, 'Enterprise', 100, 200, 99.90, 30)
            ]
            c.executemany(
                "INSERT INTO planos (id, nome, max_grupos, max_posts_dia, preco, duracao_dias) VALUES (?, ?, ?, ?, ?, ?)",
                planos
            )
            logger.info("✅ Planos padrão inseridos")

        conn.commit()

    logger.info("✅ Banco de dados inicializado com índices otimizados!")


# Modelos Pydantic para tipagem
from pydantic import BaseModel
from datetime import datetime as dt

class User(BaseModel):
    id: int
    nome: str
    email: str
    whatsapp: str = ""
    trial_start: str = ""
    plano_ativo: int = 1
    autopost: int = 0
    bridge_id: int = 0
    created_at: str = ""

class Config(BaseModel):
    user_id: int
    shopee_app_id: str = ""
    shopee_api_key: str = ""
    intervalo: int = 30
    min_desconto: int = 20
    estilo: str = 'padrao'
    hora_inicio: str = '08:00'
    hora_fim: str = '22:00'
    max_posts_dia: int = 50
    usar_smart_schedule: int = 1
    usar_ab_testing: int = 1

class Grupo(BaseModel):
    id: int
    user_id: int
    grupo_id: str
    grupo_nome: str = ""
    plataforma: str = 'wh'
    selecionado: int = 0
    nicho: str = 'todos'
    ativo: int = 1
    total_cliques: int = 0
    total_posts: int = 0
    ctr: float = 0.0
    melhor_horario: str = ""

class Template(BaseModel):
    id: int
    user_id: int
    nome: str
    copy: str
    selecionado: int = 0
    ab_test_group: str = 'A'
    win_rate: float = 0.0
    total_envios: int = 0
    total_cliques: int = 0
    ctr: float = 0.0
