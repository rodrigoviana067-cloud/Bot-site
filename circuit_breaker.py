#!/usr/bin/env python3
"""
WA Affiliate Pro v6.0 — Circuit Breaker
Protege contra falhas em cascata em serviços externos
"""

import time
import threading
import logging
from enum import Enum
from typing import Callable, Optional
from functools import wraps

logger = logging.getLogger('affiliate.circuit')


class CircuitState(Enum):
    CLOSED = "closed"       # Normal - permite requests
    OPEN = "open"          # Falhou muito - bloqueia requests
    HALF_OPEN = "half_open"  # Testando se recuperou


class CircuitBreaker:
    """
    Circuit Breaker para proteger chamadas a serviços externos.

    Estados:
    - CLOSED: Tudo normal, requests passam
    - OPEN: Muitas falhas, requests são bloqueados imediatamente
    - HALF_OPEN: Após timeout, permite 1 request para testar
    """

    def __init__(
        self,
        name: str,
        fail_max: int = 5,
        timeout_seconds: float = 30.0,
        half_open_max: int = 3,
        expected_exception: type = Exception
    ):
        self.name = name
        self.fail_max = fail_max
        self.timeout_seconds = timeout_seconds
        self.half_open_max = half_open_max
        self.expected_exception = expected_exception

        self._state = CircuitState.CLOSED
        self._fail_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.RLock()

        logger.info(f"🔌 Circuit Breaker '{name}' inicializado (fail_max={fail_max}, timeout={timeout_seconds}s)")

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "fail_count": self._fail_count,
                "success_count": self._success_count,
                "last_failure": self._last_failure_time
            }

    def _can_attempt(self) -> bool:
        """Verifica se pode tentar uma chamada"""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Verificar se já passou o timeout
                if self._last_failure_time and                    (time.time() - self._last_failure_time) >= self.timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info(f"🔌 Circuit '{self.name}' -> HALF_OPEN (testando recuperação)")
                    return True
                return False

            if self._state == CircuitState.HALF_OPEN:
                return self._success_count < self.half_open_max

            return True

    def _on_success(self):
        """Registra sucesso"""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_max:
                    self._state = CircuitState.CLOSED
                    self._fail_count = 0
                    logger.info(f"🔌 Circuit '{self.name}' -> CLOSED (recuperado)")
            else:
                self._fail_count = max(0, self._fail_count - 1)

    def _on_failure(self):
        """Registra falha"""
        with self._lock:
            self._fail_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning(f"🔌 Circuit '{self.name}' -> OPEN (falha em HALF_OPEN)")
            elif self._fail_count >= self.fail_max:
                self._state = CircuitState.OPEN
                logger.warning(
                    f"🔌 Circuit '{self.name}' -> OPEN ({self._fail_count} falhas consecutivas)"
                )

    def call(self, func: Callable, *args, **kwargs):
        """Executa função com proteção do circuit breaker"""
        if not self._can_attempt():
            raise CircuitBreakerOpen(f"Circuit '{self.name}' está OPEN")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise

    def __call__(self, func: Callable) -> Callable:
        """Decorator"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.call(func, *args, **kwargs)
        return wrapper


class CircuitBreakerOpen(Exception):
    """Exceção lançada quando o circuit breaker está aberto"""
    pass


# Circuit breakers globais
wa_bridge_breaker = CircuitBreaker(
    name="wa_bridge",
    fail_max=5,
    timeout_seconds=60.0,
    expected_exception=Exception
)

shopee_api_breaker = CircuitBreaker(
    name="shopee_api",
    fail_max=3,
    timeout_seconds=120.0,
    expected_exception=Exception
)
