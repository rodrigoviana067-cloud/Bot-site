#!/usr/bin/env python3
"""
WA Affiliate Pro v6.0 — Worker de Agendamentos
Executa agendamentos pendentes em background
"""

import time
import random
import logging
import threading
from datetime import datetime

from config import settings
from database import get_db
from whatsapp_service import whatsapp_service

logger = logging.getLogger('affiliate.agendador')


class AgendadorWorker:
    """Worker que executa agendamentos pendentes"""

    def __init__(self):
        self._running = False
        self._thread: threading.Thread = None

    def _processar_agendamentos(self):
        """Processa agendamentos pendentes"""
        try:
            now = datetime.now()
            current_date = now.strftime('%Y-%m-%d')
            current_time = now.strftime('%H:%M')

            with get_db() as conn:
                pendentes = conn.execute(
                    """SELECT * FROM agendamentos 
                       WHERE status='pendente' 
                         AND data_agendada <= ? 
                         AND hora_agendada <= ?
                       ORDER BY data_agendada ASC, hora_agendada ASC
                       LIMIT 50""",
                    (current_date, current_time)
                ).fetchall()

            if not pendentes:
                return

            logger.info(f"⏰ {len(pendentes)} agendamento(s) a executar")

            for ag in pendentes:
                uid = ag['user_id']
                aid = ag['id']
                mensagem = ag['link']
                grupos = [g.strip() for g in ag['grupos'].split(',') if g.strip()]
                titulo = ag['titulo'] or 'Agendamento'

                enviados = 0
                erros = 0

                for gid in grupos:
                    try:
                        success = whatsapp_service.send_message(uid, gid, mensagem)

                        if success:
                            enviados += 1
                            with get_db() as conn:
                                conn.execute(
                                    "INSERT INTO auto_post_log (user_id, grupo_id, titulo, status) VALUES (?, ?, ?, ?)",
                                    (uid, gid, titulo[:100], 'enviado')
                                )
                                conn.commit()
                        else:
                            erros += 1

                    except Exception as e:
                        erros += 1
                        logger.error(f"Erro ao enviar agendamento para grupo {gid}: {e}")

                    time.sleep(random.randint(2, 8))

                # Atualiza status
                with get_db() as conn:
                    conn.execute(
                        "UPDATE agendamentos SET status='concluido' WHERE id=?",
                        (aid,)
                    )
                    conn.commit()

                logger.info(f"✅ Agendamento #{aid}: {enviados}/{len(grupos)} enviados")

        except Exception as e:
            logger.error(f"Erro no agendador: {e}")

    def worker(self):
        """Loop principal do worker"""
        logger.info("⏰ Agendador worker INICIADO")

        while self._running:
            try:
                self._processar_agendamentos()
                time.sleep(30)
            except Exception as e:
                logger.error(f"Erro crítico no agendador: {e}")
                time.sleep(60)

    def start(self):
        """Inicia o worker em thread separada"""
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self.worker, daemon=True)
            self._thread.start()
            logger.info("✅ Agendador iniciado!")

    def stop(self):
        """Para o worker graciosamente"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("🛑 Agendador parado")


# Instância global
agendador_worker = AgendadorWorker()
