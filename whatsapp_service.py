#!/usr/bin/env python3
"""
WA Affiliate Pro v6.0 — Serviço WhatsApp
Envio de mensagens com retry, circuit breaker, rate limiting e session reuse
"""

import time
import random
import logging
import requests
from typing import Optional, Dict
from datetime import datetime

from config import settings
from circuit_breaker import wa_bridge_breaker, CircuitBreakerOpen

logger = logging.getLogger('affiliate.whatsapp')


class WhatsAppService:
    """Serviço de envio de mensagens WhatsApp com resiliência"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        self._last_send_time: Dict[str, float] = {}  # user_id -> timestamp
        self._daily_count: Dict[str, int] = {}      # user_id -> count
        self._last_count_reset: str = datetime.now().strftime('%Y-%m-%d')

    def _check_rate_limit(self, user_id: str) -> bool:
        """Verifica rate limit por usuário (respeita limites WhatsApp)"""
        now = time.time()
        today = datetime.now().strftime('%Y-%m-%d')

        # Resetar contador diário
        if today != self._last_count_reset:
            self._daily_count.clear()
            self._last_count_reset = today

        # Verificar cooldown entre mensagens
        last = self._last_send_time.get(user_id, 0)
        elapsed = now - last
        if elapsed < settings.WA_COOLDOWN_BETWEEN_MESSAGES:
            wait = settings.WA_COOLDOWN_BETWEEN_MESSAGES - elapsed
            logger.debug(f"⏳ WA rate limit: aguardando {wait:.1f}s para user {user_id}")
            time.sleep(wait)

        # Verificar limite diário
        current_count = self._daily_count.get(user_id, 0)
        if current_count >= settings.WA_MAX_MESSAGES_PER_MINUTE * 60 * 24:
            logger.warning(f"🚫 WA daily limit atingido para user {user_id}")
            return False

        return True

    def _update_rate_limit(self, user_id: str):
        """Atualiza contadores após envio"""
        self._last_send_time[user_id] = time.time()
        self._daily_count[user_id] = self._daily_count.get(user_id, 0) + 1

    def send_message(
        self,
        user_id: int,
        group_id: str,
        message: str,
        image_url: Optional[str] = None,
        retry_count: int = 0
    ) -> bool:
        """
        Envia mensagem via bridge WhatsApp com retry e circuit breaker.

        Args:
            user_id: ID do usuário no sistema
            group_id: ID do grupo WhatsApp
            message: Texto da mensagem
            image_url: URL da imagem (opcional)
            retry_count: Contador interno de retry

        Returns:
            bool: True se enviado com sucesso
        """
        user_id_str = str(user_id)

        # Verificar rate limit
        if not self._check_rate_limit(user_id_str):
            return False

        # Preparar payload
        payload = {
            "userId": user_id_str,
            "numero": group_id,
            "mensagem": message
        }
        if image_url:
            payload["imagem"] = image_url

        # Corrigir links quebrados
        message = message.replace('s.shopee. com.br', 's.shopee.com.br')
        message = message.replace('s.shopee.\ncom.br', 's.shopee.com.br')
        payload["mensagem"] = message

        try:
            # Tentar envio com circuit breaker
            def _do_send():
                resp = self.session.post(
                    f"{settings.WA_BRIDGE_URL}/send",
                    json=payload,
                    timeout=settings.WA_BRIDGE_TIMEOUT
                )
                resp.raise_for_status()
                return resp.json()

            try:
                result = wa_bridge_breaker.call(_do_send)
            except CircuitBreakerOpen:
                logger.warning(f"🔌 Circuit breaker aberto para WA Bridge. Pulando envio.")
                return False

            success = result.get('success', False)

            if success:
                self._update_rate_limit(user_id_str)
                logger.info(f"✅ WA enviado: user={user_id}, grupo={group_id[:20]}...")
                return True
            else:
                error_msg = result.get('error', 'Erro desconhecido')
                logger.warning(f"⚠️ WA falhou: {error_msg}")

                # Retry com backoff
                if retry_count < settings.WA_BRIDGE_MAX_RETRIES:
                    delay = settings.WA_BRIDGE_RETRY_DELAY * (2 ** retry_count) + random.uniform(0, 1)
                    logger.info(f"🔄 Retry {retry_count + 1}/{settings.WA_BRIDGE_MAX_RETRIES} em {delay:.1f}s")
                    time.sleep(delay)
                    return self.send_message(user_id, group_id, message, image_url, retry_count + 1)

                return False

        except requests.Timeout:
            logger.error(f"⏱️ Timeout enviando WA para {group_id}")
            if retry_count < settings.WA_BRIDGE_MAX_RETRIES:
                delay = settings.WA_BRIDGE_RETRY_DELAY * (2 ** retry_count)
                time.sleep(delay)
                return self.send_message(user_id, group_id, message, image_url, retry_count + 1)
            return False

        except requests.ConnectionError as e:
            logger.error(f"🔌 Erro de conexão WA Bridge: {e}")
            return False

        except Exception as e:
            logger.error(f"❌ Erro inesperado no envio WA: {e}")
            return False

    def check_bridge_status(self, user_id: int) -> dict:
        """Verifica status da conexão WhatsApp na bridge"""
        try:
            resp = self.session.get(
                f"{settings.WA_BRIDGE_URL}/status/{user_id}",
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "connected": data.get('connected', False),
                    "groups_count": data.get('groupsCount', 0),
                    "bridge_id": user_id
                }
        except Exception as e:
            logger.error(f"Erro ao verificar status bridge: {e}")

        return {"connected": False, "groups_count": 0, "bridge_id": 0}

    def get_groups(self, user_id: int) -> list:
        """Obtém lista de grupos do WhatsApp via bridge"""
        try:
            resp = self.session.get(
                f"{settings.WA_BRIDGE_URL}/grupos/{user_id}",
                timeout=20
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get('grupos', data.get('chats', []))
        except Exception as e:
            logger.error(f"Erro ao sincronizar grupos: {e}")

        return []


# Instância global do serviço
whatsapp_service = WhatsAppService()
