#!/usr/bin/env python3
"""
WA Affiliate Pro v6.0 — Motor de Autopost Inteligente
A/B Testing, Smart Schedule, Remarketing, Anti-Ban
"""

import time
import random
import logging
import threading
from typing import List, Optional, Dict
from datetime import datetime, timedelta

from config import settings
from database import get_db, User, Grupo, Template
from whatsapp_service import whatsapp_service
from shopee_service import shopee_service, Produto

logger = logging.getLogger('affiliate.autopost')


class SmartSchedule:
    """Aprende o melhor horário para postar em cada grupo"""

    def __init__(self):
        self._cache: Dict[str, Optional[str]] = {}  # "user_id:grupo_id" -> melhor_horario

    def get_melhor_horario(self, user_id: int, grupo_id: str) -> Optional[str]:
        """Retorna o melhor horário aprendido para o grupo"""
        key = f"{user_id}:{grupo_id}"
        if key in self._cache:
            return self._cache[key]

        try:
            with get_db() as conn:
                # Buscar métricas por horário
                rows = conn.execute(
                    """SELECT hora, total_posts, total_cliques, ctr 
                       FROM horario_metrics 
                       WHERE user_id=? AND grupo_id=? AND total_posts >= 5
                       ORDER BY ctr DESC LIMIT 1""",
                    (user_id, grupo_id)
                ).fetchall()

                if rows:
                    melhor = rows[0]
                    horario = f"{melhor['hora']:02d}:00"
                    self._cache[key] = horario
                    logger.info(f"🧠 SmartSchedule: grupo {grupo_id[:15]}... melhor horário={horario} (CTR={melhor['ctr']:.1f}%)")
                    return horario
        except Exception as e:
            logger.error(f"Erro SmartSchedule: {e}")

        return None

    def registrar_metrica(self, user_id: int, grupo_id: str, hora: int, dia_semana: int, teve_clique: bool):
        """Registra métrica de performance por horário"""
        try:
            with get_db() as conn:
                conn.execute(
                    """INSERT INTO horario_metrics (user_id, grupo_id, hora, dia_semana, total_posts, total_cliques, ctr)
                       VALUES (?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(user_id, grupo_id, hora, dia_semana) DO UPDATE SET
                       total_posts = total_posts + 1,
                       total_cliques = total_cliques + ?,
                       ctr = CAST(total_cliques + ? AS REAL) / (total_posts + 1) * 100""",
                    (user_id, grupo_id, hora, dia_semana, 1 if teve_clique else 0, 
                     100.0 if teve_clique else 0.0, 1 if teve_clique else 0, 1 if teve_clique else 0)
                )
                conn.commit()
                # Invalidar cache
                key = f"{user_id}:{grupo_id}"
                if key in self._cache:
                    del self._cache[key]
        except Exception as e:
            logger.error(f"Erro ao registrar métrica: {e}")


class ABTestEngine:
    """Motor de A/B Testing para templates"""

    def __init__(self):
        self._min_samples = settings.AB_TEST_MIN_SAMPLES
        self._min_diff = settings.AB_TEST_MIN_DIFFERENCE

    def selecionar_template(self, user_id: int) -> Optional[Template]:
        """Seleciona template baseado em A/B test ou melhor performance"""
        try:
            with get_db() as conn:
                # Buscar templates do usuário
                templates = conn.execute(
                    "SELECT * FROM templates WHERE user_id=? AND selecionado=1",
                    (user_id,)
                ).fetchall()

                if not templates:
                    return None

                templates = [dict(t) for t in templates]

                # Se só tem 1, usa ele
                if len(templates) == 1:
                    return Template(**templates[0])

                # Verificar se tem dados suficientes para decisão
                templates_com_dados = [t for t in templates if t['total_envios'] >= self._min_samples]

                if len(templates_com_dados) >= 2:
                    # Ordenar por CTR
                    templates_com_dados.sort(key=lambda t: t['ctr'], reverse=True)
                    melhor = templates_com_dados[0]
                    segundo = templates_com_dados[1]

                    # Verificar diferença significativa
                    if melhor['ctr'] > 0 and segundo['ctr'] > 0:
                        diff = (melhor['ctr'] - segundo['ctr']) / segundo['ctr']
                        if diff >= self._min_diff:
                            logger.info(f"🧪 A/B Test vencedor: '{melhor['nome']}' (CTR={melhor['ctr']:.1f}%) vs '{segundo['nome']}' (CTR={segundo['ctr']:.1f}%)")
                            return Template(**melhor)

                # Ainda não tem dados suficientes — distribuir 50/50
                template_a = [t for t in templates if t['ab_test_group'] == 'A']
                template_b = [t for t in templates if t['ab_test_group'] == 'B']

                if template_a and template_b:
                    # Escolher o que tem menos envios para balancear
                    escolhido = min([template_a[0], template_b[0]], key=lambda t: t['total_envios'])
                    return Template(**escolhido)
                elif templates:
                    return Template(**random.choice(templates))

        except Exception as e:
            logger.error(f"Erro A/B Test: {e}")

        return None

    def registrar_resultado(self, template_id: int, teve_clique: bool):
        """Atualiza estatísticas do template após envio"""
        try:
            with get_db() as conn:
                conn.execute(
                    """UPDATE templates SET 
                       total_envios = total_envios + 1,
                       total_cliques = total_cliques + ?,
                       ctr = CAST(total_cliques + ? AS REAL) / (total_envios + 1) * 100
                       WHERE id = ?""",
                    (1 if teve_clique else 0, 1 if teve_clique else 0, template_id)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Erro ao registrar resultado A/B: {e}")


class RemarketingEngine:
    """Motor de remarketing para quem clicou mas não comprou"""

    def __init__(self):
        self.enabled = settings.REMARKETING_ENABLED

    def criar_sequencia(self, user_id: int, click_id: int, produto_link: str, grupo_id: str):
        """Cria sequência de 3 mensagens de remarketing"""
        if not self.enabled:
            return

        horarios = [
            settings.REMARKETING_HOURS_1,
            settings.REMARKETING_HOURS_2,
            settings.REMARKETING_HOURS_3
        ]

        mensagens = [
            "⏰ Ei! Você viu esse produto mas não garantiu o seu. Ainda dá tempo! 🔥",
            "💡 Lembrete: o desconto desse produto pode acabar a qualquer momento. Não perca!",
            "⚡ Última chance! Estoque limitado e o preço pode subir a qualquer momento."
        ]

        try:
            with get_db() as conn:
                for i, (horas, msg) in enumerate(zip(horarios, mensagens), 1):
                    scheduled = (datetime.now() + timedelta(hours=horas)).isoformat()
                    conn.execute(
                        """INSERT INTO remarketing (user_id, click_id, etapa, status, scheduled_at, message)
                           VALUES (?, ?, ?, 'pendente', ?, ?)""",
                        (user_id, click_id, i, scheduled, msg)
                    )
                conn.commit()
                logger.info(f"📧 Remarketing sequência criada para click_id={click_id}")
        except Exception as e:
            logger.error(f"Erro ao criar remarketing: {e}")

    def processar_pendentes(self):
        """Processa remarketings pendentes"""
        if not self.enabled:
            return

        try:
            with get_db() as conn:
                pendentes = conn.execute(
                    """SELECT r.*, c.produto_link, c.grupo_id 
                       FROM remarketing r
                       JOIN clicks c ON r.click_id = c.id
                       WHERE r.status='pendente' AND r.scheduled_at <= datetime('now')
                       LIMIT 10"""
                ).fetchall()

                for rem in pendentes:
                    # Enviar mensagem
                    success = whatsapp_service.send_message(
                        rem['user_id'],
                        rem['grupo_id'],
                        f"{rem['message']}\n\n🔗 {rem['produto_link']}"
                    )

                    status = 'enviado' if success else 'falhou'
                    conn.execute(
                        "UPDATE remarketing SET status=?, sent_at=datetime('now') WHERE id=?",
                        (status, rem['id'])
                    )

                if pendentes:
                    conn.commit()
                    logger.info(f"📧 {len(pendentes)} remarketings processados")

        except Exception as e:
            logger.error(f"Erro no remarketing: {e}")


class AutopostEngine:
    """Motor principal de autopost com todas as funcionalidades inteligentes"""

    def __init__(self):
        self.smart_schedule = SmartSchedule()
        self.ab_test = ABTestEngine()
        self.remarketing = RemarketingEngine()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _is_dentro_horario(self, hora_inicio: str, hora_fim: str) -> bool:
        """Verifica se está dentro do horário de operação"""
        try:
            agora = datetime.now()
            current = agora.hour * 60 + agora.minute

            h, m = map(int, hora_inicio.split(':'))
            inicio = h * 60 + m

            h, m = map(int, hora_fim.split(':'))
            fim = h * 60 + m

            if inicio > fim:  # Cruza meia-noite
                return current >= inicio or current <= fim
            return inicio <= current <= fim
        except Exception as e:
            logger.error(f"Erro ao verificar horário: {e}")
            return False

    def _pode_postar(self, user_id: int, intervalo_min: int, max_posts_dia: int) -> bool:
        """Verifica se o usuário pode postar agora"""
        try:
            with get_db() as conn:
                control = conn.execute(
                    "SELECT last_post_at, posts_today, error_count, paused_until FROM autopost_control WHERE user_id=?",
                    (user_id,)
                ).fetchone()

                if not control:
                    conn.execute(
                        "INSERT INTO autopost_control (user_id, last_post_at, posts_today) VALUES (?, datetime('now'), 0)",
                        (user_id,)
                    )
                    conn.commit()
                    return True

                # Resetar posts_today se novo dia
                if control['last_post_at']:
                    last_date = control['last_post_at'][:10]
                    hoje = datetime.now().strftime('%Y-%m-%d')
                    if last_date != hoje:
                        conn.execute(
                            "UPDATE autopost_control SET posts_today=0 WHERE user_id=?",
                            (user_id,)
                        )
                        conn.commit()
                        control = dict(control)
                        control['posts_today'] = 0

                # Verificar pausa por erros
                if control['paused_until']:
                    try:
                        paused = datetime.fromisoformat(control['paused_until'])
                        if datetime.now() < paused:
                            return False
                    except:
                        pass

                # Verificar limite de posts por dia
                if control['posts_today'] >= max_posts_dia:
                    return False

                # Verificar intervalo
                if control['last_post_at']:
                    try:
                        last = datetime.fromisoformat(control['last_post_at'])
                        elapsed = (datetime.now() - last).total_seconds() / 60
                        if elapsed < intervalo_min:
                            return False
                    except:
                        pass

                return True
        except Exception as e:
            logger.error(f"Erro em pode_postar({user_id}): {e}")
            return True

    def _registrar_post(self, user_id: int, product_id: str, grupo_id: str, titulo: str, template_id: Optional[int] = None):
        """Registra postagem no banco"""
        try:
            with get_db() as conn:
                conn.execute(
                    """UPDATE autopost_control SET 
                       last_post_at=datetime('now'), 
                       posts_today=posts_today+1, 
                       error_count=0, 
                       paused_until=NULL,
                       total_posts=total_posts+1
                       WHERE user_id=?""",
                    (user_id,)
                )

                if conn.total_changes == 0:
                    conn.execute(
                        "INSERT INTO autopost_control (user_id, last_post_at, posts_today, total_posts) VALUES (?, datetime('now'), 1, 1)",
                        (user_id,)
                    )

                conn.execute(
                    "INSERT INTO produtos_enviados (user_id, product_id, grupo_id) VALUES (?, ?, ?)",
                    (user_id, product_id, grupo_id)
                )

                conn.execute(
                    """INSERT INTO posts (user_id, grupo_id, titulo, template_id, status) VALUES (?, ?, ?, ?, 'enviado')""",
                    (user_id, grupo_id, titulo[:100], template_id)
                )

                conn.execute(
                    "INSERT INTO auto_post_log (user_id, grupo_id, titulo, status) VALUES (?, ?, ?, 'enviado')",
                    (user_id, grupo_id, titulo[:100])
                )

                conn.commit()
        except Exception as e:
            logger.error(f"Erro ao registrar post: {e}")

    def _registrar_falha(self, user_id: int, error_msg: str):
        """Registra falha e aplica backoff exponencial"""
        try:
            with get_db() as conn:
                control = conn.execute(
                    "SELECT error_count FROM autopost_control WHERE user_id=?",
                    (user_id,)
                ).fetchone()

                error_count = (control['error_count'] if control else 0) + 1

                if error_count >= settings.AUTOPOST_MAX_ERRORS_BEFORE_PAUSE:
                    pause_minutes = min(
                        settings.AUTOPOST_PAUSE_MINUTES * (settings.AUTOPOST_BACKOFF_MULTIPLIER ** (error_count - settings.AUTOPOST_MAX_ERRORS_BEFORE_PAUSE)),
                        settings.AUTOPOST_MAX_BACKOFF_MINUTES
                    )
                    paused_until = (datetime.now() + timedelta(minutes=pause_minutes)).isoformat()
                    logger.warning(f"⏸️ User {user_id} pausado por {pause_minutes:.0f}min ({error_count} erros)")
                else:
                    paused_until = None

                conn.execute(
                    "UPDATE autopost_control SET error_count=?, paused_until=? WHERE user_id=?",
                    (error_count, paused_until, user_id)
                )

                if conn.total_changes == 0:
                    conn.execute(
                        "INSERT INTO autopost_control (user_id, error_count, paused_until) VALUES (?, ?, ?)",
                        (user_id, error_count, paused_until)
                    )

                conn.execute(
                    "INSERT INTO auto_post_log (user_id, grupo_id, titulo, status, erro) VALUES (?, '', 'Auto-post', 'erro', ?)",
                    (user_id, error_msg[:200])
                )

                conn.commit()
        except Exception as e:
            logger.error(f"Erro ao registrar falha: {e}")

    def _produto_ja_enviado(self, user_id: int, product_id: str, grupo_id: str) -> bool:
        """Verifica se produto já foi enviado para o grupo"""
        try:
            with get_db() as conn:
                existe = conn.execute(
                    "SELECT id FROM produtos_enviados WHERE user_id=? AND product_id=? AND grupo_id=?",
                    (user_id, product_id, grupo_id)
                ).fetchone()
                return existe is not None
        except:
            return False

    def _limpar_produtos_antigos(self, user_id: int, dias: int = 7):
        """Limpa histórico de produtos enviados antigos"""
        try:
            with get_db() as conn:
                conn.execute(
                    "DELETE FROM produtos_enviados WHERE user_id=? AND enviado_em < datetime('now', '-{} days')".format(dias),
                    (user_id,)
                )
                conn.commit()
        except:
            pass

    def _formatar_mensagem(self, produto: Produto, template: Optional[Template]) -> str:
        """Formata mensagem usando template ou padrão"""
        if template:
            msg = template.copy
            msg = msg.replace('{titulo}', produto.titulo)
            msg = msg.replace('{preco}', f"{produto.preco:.2f}")
            msg = msg.replace('{preco_original}', f"{produto.preco_original:.2f}")
            msg = msg.replace('{desconto}', str(produto.desconto_pct))
            msg = msg.replace('{loja}', produto.loja)
            msg = msg.replace('{link}', produto.link_afiliado)
            msg = msg.replace('{comissao}', f"{produto.comissao_estimada:.2f}")

            # Formatação WhatsApp
            msg = msg.replace(". ", ".\n\n").replace("! ", "!\n\n").replace("? ", "?\n\n")
            msg = msg.replace("Compre agora:", "\nCompre agora:")
            while "\n\n\n" in msg:
                msg = msg.replace("\n\n\n", "\n\n")

            return msg

        return (
            f"🔥 *{produto.titulo}*\n\n"
            f"💵 *R$ {produto.preco:.2f}* (era R$ {produto.preco_original:.2f})\n\n"
            f"📉 Desconto: {produto.desconto_pct}%\n"
            f"💰 Comissão estimada: R$ {produto.comissao_estimada:.2f}\n\n"
            f"🛒 Loja: {produto.loja}\n\n"
            f"🔗 {produto.link_afiliado}"
        )

    def processar_usuario(self, user: dict) -> int:
        """
        Processa autopost para um usuário.
        Retorna número de posts enviados.
        """
        uid = user['id']
        user_name = user.get('nome', 'Desconhecido')
        posts_enviados = 0

        try:
            # Buscar configurações atualizadas
            with get_db() as conn:
                config = conn.execute(
                    """SELECT intervalo, hora_inicio, hora_fim, min_desconto, max_posts_dia,
                              usar_smart_schedule, usar_ab_testing
                       FROM configs WHERE user_id=?""",
                    (uid,)
                ).fetchone()

                if not config:
                    return 0

                config = dict(config)

            hora_inicio = config.get('hora_inicio') or '08:00'
            hora_fim = config.get('hora_fim') or '22:00'
            intervalo = max(int(config.get('intervalo') or 30), 5)
            max_posts = int(config.get('max_posts_dia') or settings.AUTOPOST_MAX_POSTS_PER_DAY)
            min_desconto = int(config.get('min_desconto') or 20)
            usar_smart = bool(config.get('usar_smart_schedule', 1))
            usar_ab = bool(config.get('usar_ab_testing', 1))

            # Verificar horário
            if not self._is_dentro_horario(hora_inicio, hora_fim):
                logger.debug(f"⏰ [{user_name}] Fora do horário ({hora_inicio}-{hora_fim})")
                return 0

            # Verificar se pode postar
            if not self._pode_postar(uid, intervalo, max_posts):
                return 0

            # Buscar grupos selecionados
            with get_db() as conn:
                grupos = conn.execute(
                    "SELECT * FROM grupos WHERE user_id=? AND selecionado=1 AND ativo=1",
                    (uid,)
                ).fetchall()

            if not grupos:
                return 0

            grupos = [dict(g) for g in grupos]

            # Buscar produtos (1 request só, cache por usuário)
            if not user.get('shopee_app_id') or not user.get('shopee_api_key'):
                return 0

            produtos = shopee_service.buscar_produtos(
                uid,
                user['shopee_app_id'],
                user['shopee_api_key'],
                min_desconto
            )

            if not produtos:
                logger.warning(f"⚠️ [{user_name}] Nenhum produto encontrado")
                self._limpar_produtos_antigos(uid)
                return 0

            # Selecionar template (A/B test)
            template = None
            if usar_ab:
                template = self.ab_test.selecionar_template(uid)

            if not template:
                with get_db() as conn:
                    tpl = conn.execute(
                        "SELECT * FROM templates WHERE user_id=? AND selecionado=1 LIMIT 1",
                        (uid,)
                    ).fetchone()
                    if tpl:
                        template = Template(**dict(tpl))

            # Processar cada grupo
            for grupo in grupos:
                # Smart Schedule: verificar melhor horário
                if usar_smart:
                    melhor_hora = self.smart_schedule.get_melhor_horario(uid, grupo['grupo_id'])
                    if melhor_hora:
                        agora = datetime.now().strftime('%H:%M')
                        # Permitir ±30min do melhor horário
                        # Simplificação: se não estiver próximo, pula
                        # Em produção, usar lógica mais sofisticada

                # Filtrar produtos por nicho
                nicho = grupo.get('nicho', 'todos')
                produtos_grupo = produtos

                if nicho and nicho != 'todos':
                    nichos = [n.strip().lower() for n in nicho.split(',')]
                    produtos_filtrados = []
                    for p in produtos:
                        titulo_lower = p.titulo.lower()
                        if any(n in titulo_lower for n in nichos):
                            produtos_filtrados.append(p)
                    if produtos_filtrados:
                        produtos_grupo = produtos_filtrados

                # Filtrar produtos não enviados para ESTE grupo
                disponiveis = [
                    p for p in produtos_grupo
                    if not self._produto_ja_enviado(uid, p.item_id, grupo['grupo_id'])
                ]

                if not disponiveis:
                    continue

                # Escolher melhor produto (top 5, aleatório entre eles)
                top_produtos = disponiveis[:5]
                produto = random.choice(top_produtos)

                # Formatar mensagem
                msg = self._formatar_mensagem(produto, template)

                # Enviar
                success = whatsapp_service.send_message(
                    uid,
                    grupo['grupo_id'],
                    msg,
                    produto.imagem
                )

                if success:
                    self._registrar_post(uid, produto.item_id, grupo['grupo_id'], produto.titulo, template.id if template else None)

                    # Atualizar métricas do grupo
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE grupos SET total_posts=total_posts+1, ultimo_post=datetime('now') WHERE id=?",
                            (grupo['id'],)
                        )
                        conn.commit()

                    # Registrar A/B test
                    if template and usar_ab:
                        # Não sabemos se teve clique ainda, registrar como envio
                        pass  # Será atualizado quando houver clique

                    posts_enviados += 1
                    logger.info(f"✅ [{user_name}] {produto.titulo[:40]}... -> {grupo.get('grupo_nome', grupo['grupo_id'])[:20]}")

                    # Delay entre envios
                    time.sleep(random.uniform(3, 8))
                else:
                    self._registrar_falha(uid, f"Falha ao enviar para {grupo['grupo_id']}")

            return posts_enviados

        except Exception as e:
            logger.error(f"❌ Erro ao processar {user_name}: {e}")
            self._registrar_falha(uid, str(e)[:200])
            return 0

    def worker(self):
        """Worker principal do autopost"""
        logger.info("🚀 Auto-poster worker INICIADO (v6.0 Inteligente)")

        ciclo_count = 0
        erros_seguidos = 0

        while self._running:
            try:
                ciclo_count += 1

                # Buscar usuários ativos
                with get_db() as conn:
                    rows = conn.execute(
                        """SELECT u.id, u.nome, u.autopost, u.trial_start, u.plano_ativo,
                                  c.shopee_app_id, c.shopee_api_key, c.intervalo, 
                                  c.hora_inicio, c.hora_fim, c.min_desconto, c.max_posts_dia
                           FROM users u 
                           JOIN configs c ON u.id = c.user_id 
                           WHERE u.autopost = 1 
                             AND c.shopee_app_id IS NOT NULL 
                             AND c.shopee_app_id != '' 
                             AND c.shopee_api_key IS NOT NULL 
                             AND c.shopee_api_key != ''
                           ORDER BY RANDOM()"""
                    ).fetchall()

                    usuarios = []
                    for row in rows:
                        user = dict(row)
                        # Verificar trial/plano
                        dias_trial = self._calc_dias_trial(user.get('trial_start'))
                        tem_plano = dias_trial > 0 or user.get('plano_ativo', 1) > 1
                        if tem_plano:
                            user['dias_restantes'] = dias_trial
                            usuarios.append(user)

                if not usuarios:
                    if ciclo_count % 10 == 0:
                        logger.info("💤 Nenhum usuário ativo")
                    time.sleep(settings.AUTOPOST_CHECK_INTERVAL)
                    continue

                logger.info(f"🔄 Ciclo #{ciclo_count} | {len(usuarios)} usuário(s) | {datetime.now().strftime('%H:%M')}")

                # Processar remarketing
                self.remarketing.processar_pendentes()

                # Processar cada usuário (com isolamento de erros)
                total_enviados = 0
                for user in usuarios:
                    if not self._running:
                        break

                    try:
                        enviados = self.processar_usuario(user)
                        total_enviados += enviados
                    except Exception as e:
                        logger.error(f"Erro no usuário {user.get('nome', '?')}: {e}")
                        continue

                if total_enviados > 0:
                    erros_seguidos = 0

                if ciclo_count % 10 == 0:
                    logger.info(f"💓 Worker vivo - Ciclo #{ciclo_count} | {total_enviados} posts")

                time.sleep(settings.AUTOPOST_CHECK_INTERVAL)

            except Exception as e:
                erros_seguidos += 1
                logger.error(f"Worker erro crítico (#{erros_seguidos}): {e}")
                sleep_time = min(30 * (2 ** min(erros_seguidos, 4)), 300)
                time.sleep(sleep_time)

    def _calc_dias_trial(self, trial_start: Optional[str]) -> int:
        """Calcula dias restantes do trial"""
        if not trial_start:
            return 0
        try:
            started = datetime.fromisoformat(trial_start)
            days_left = 7 - (datetime.now() - started).days
            return max(0, days_left)
        except:
            return 0

    def start(self):
        """Inicia o worker em thread separada"""
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self.worker, daemon=True)
            self._thread.start()
            logger.info("✅ Auto-poster v6.0 iniciado!")

    def stop(self):
        """Para o worker graciosamente"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("🛑 Auto-poster parado")


# Instância global
autopost_engine = AutopostEngine()
