#!/usr/bin/env python3
"""
WA Affiliate Pro v6.0 — Serviço Shopee Affiliate
Busca de produtos com cache TTL, scoring inteligente e circuit breaker
"""

import time
import json
import hashlib
import logging
import requests
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from config import settings
from circuit_breaker import shopee_api_breaker, CircuitBreakerOpen

logger = logging.getLogger('affiliate.shopee')


@dataclass
class Produto:
    """Modelo de produto Shopee"""
    item_id: str
    shop_id: str
    titulo: str
    preco: float
    preco_original: float
    desconto_pct: int
    loja: str
    imagem: str
    link_afiliado: str
    comissao_estimada: float = 0.0
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "titulo": self.titulo,
            "preco": f"{self.preco:.2f}",
            "preco_original": f"{self.preco_original:.2f}",
            "desconto_pct": self.desconto_pct,
            "loja": self.loja,
            "imagem": self.imagem,
            "link_afiliado": self.link_afiliado,
            "comissao_estimada": f"{self.comissao_estimada:.2f}",
            "score": round(self.score, 2)
        }


class ShopeeCache:
    """Cache de produtos por usuário com TTL"""

    def __init__(self, ttl_seconds: int = 180):
        self.ttl = ttl_seconds
        self._cache: Dict[int, Tuple[float, List[Produto]]] = {}  # user_id -> (timestamp, produtos)
        self._last_request: Dict[int, float] = {}  # user_id -> timestamp do último request

    def get(self, user_id: int) -> Optional[List[Produto]]:
        """Obtém produtos do cache se ainda válido"""
        if user_id not in self._cache:
            return None

        timestamp, produtos = self._cache[user_id]
        if time.time() - timestamp > self.ttl:
            del self._cache[user_id]
            return None

        return produtos

    def set(self, user_id: int, produtos: List[Produto]):
        """Armazena produtos no cache"""
        self._cache[user_id] = (time.time(), produtos)

    def can_request(self, user_id: int) -> bool:
        """Verifica se pode fazer request (cooldown)"""
        last = self._last_request.get(user_id, 0)
        elapsed = time.time() - last
        return elapsed >= settings.SHOPEE_REQUEST_COOLDOWN

    def mark_requested(self, user_id: int):
        """Marca que um request foi feito"""
        self._last_request[user_id] = time.time()


class ShopeeService:
    """Serviço de integração com API Shopee Affiliate"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.cache = ShopeeCache(settings.SHOPEE_CACHE_TTL_SECONDS)

    def _build_signature(self, app_id: str, api_key: str, payload: str) -> str:
        """Gera assinatura SHA256 para API Shopee"""
        ts = int(time.time())
        raw = f"{app_id}{ts}{payload}{api_key}"
        signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return ts, signature

    def _fetch_products(
        self,
        app_id: str,
        api_key: str,
        limit: int = 50
    ) -> List[Dict]:
        """Faz request à API Shopee com circuit breaker"""
        query = f'{{ productOfferV2(limit: {limit}, sortType: 2) {{ nodes {{ itemId shopId productName priceMin priceMax priceDiscountRate imageUrl offerLink productLink shopName commissionRate }} }} }}'
        payload = json.dumps({"query": query, "variables": {}}, separators=(",", ":"))

        ts, signature = self._build_signature(app_id, api_key, payload)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={signature}"
        }

        def _do_request():
            resp = self.session.post(
                settings.SHOPEE_API_URL,
                headers=headers,
                data=payload,
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()

        try:
            data = shopee_api_breaker.call(_do_request)
        except CircuitBreakerOpen:
            logger.warning("🔌 Circuit breaker Shopee OPEN — usando cache ou retornando vazio")
            return []

        if "errors" in data:
            logger.error(f"Erro API Shopee: {data['errors']}")
            return []

        nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", [])
        return nodes

    def _parse_product(self, node: Dict) -> Optional[Produto]:
        """Converte node da API em objeto Produto"""
        try:
            item_id = str(node.get("itemId", ""))
            if not item_id:
                return None

            preco = float(node.get("priceMin", 0))
            if preco > 10000:
                preco /= 100000

            preco_original = float(node.get("priceMax", preco))
            if preco_original > 10000:
                preco_original /= 100000

            desconto = int(node.get("priceDiscountRate", 0))
            comissao_pct = float(node.get("commissionRate", 0.05))
            comissao_valor = preco * comissao_pct

            # Score inteligente: comissão (peso 3) + desconto (peso 1) - preço/100 (penalidade)
            score = (comissao_valor * 3) + (desconto * 0.5) - (preco / 200)

            return Produto(
                item_id=item_id,
                shop_id=str(node.get("shopId", "")),
                titulo=node.get("productName", "Produto"),
                preco=preco,
                preco_original=preco_original,
                desconto_pct=desconto,
                loja=node.get("shopName", "Shopee"),
                imagem=node.get("imageUrl", ""),
                link_afiliado=node.get("offerLink") or node.get("productLink") or "",
                comissao_estimada=comissao_valor,
                score=max(0, score)
            )
        except Exception as e:
            logger.debug(f"Erro ao parsear produto: {e}")
            return None

    def buscar_produtos(
        self,
        user_id: int,
        app_id: str,
        api_key: str,
        min_desconto: int = 20,
        forcar_refresh: bool = False
    ) -> List[Produto]:
        """
        Busca produtos da Shopee com cache e scoring.

        Args:
            user_id: ID do usuário (para cache)
            app_id: App ID da API Shopee
            api_key: API Key da Shopee
            min_desconto: Desconto mínimo (%)
            forcar_refresh: Ignora cache

        Returns:
            Lista de produtos ordenados por score
        """
        # Verificar cache
        if not forcar_refresh:
            cached = self.cache.get(user_id)
            if cached is not None:
                logger.debug(f"📦 Cache hit para user {user_id}")
                return cached

        # Verificar cooldown
        if not self.cache.can_request(user_id):
            logger.debug(f"⏳ Cooldown Shopee para user {user_id}")
            # Retornar cache mesmo expirado se existir
            cached = self.cache.get(user_id)
            if cached:
                return cached
            return []

        # Fazer request
        try:
            nodes = self._fetch_products(app_id, api_key, settings.SHOPEE_MAX_PRODUCTS_PER_REQUEST)
            self.cache.mark_requested(user_id)

            if not nodes:
                return []

            # Parsear e filtrar
            produtos = []
            for node in nodes:
                produto = self._parse_product(node)
                if produto and produto.desconto_pct >= min_desconto and produto.preco > 0:
                    produtos.append(produto)

            # Ordenar por score (melhores primeiro)
            produtos.sort(key=lambda p: p.score, reverse=True)

            # Cachear
            self.cache.set(user_id, produtos)

            logger.info(f"🛒 {len(produtos)} produtos encontrados para user {user_id}")
            return produtos

        except Exception as e:
            logger.error(f"Erro ao buscar produtos Shopee: {e}")
            # Tentar retornar cache expirado
            cached = self.cache.get(user_id)
            return cached if cached else []

    def buscar_produto_por_link(
        self,
        app_id: str,
        api_key: str,
        link: str
    ) -> Optional[Produto]:
        """Busca produto específico por link"""
        import re

        # Resolver link encurtado
        try:
            resp = self.session.get(link, allow_redirects=True, timeout=20, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            link = resp.url
        except:
            pass

        if "shopee.com.br" not in link:
            return None

        # Extrair IDs
        item_id = None
        shop_id = None

        match = re.search(r"shopee\.com\.br/(?:product|item)/(\d+)/(\d+)", link)
        if match:
            shop_id = int(match.group(1))
            item_id = int(match.group(2))
        else:
            match = re.search(r"shopee\.com\.br/(?:product|item)/(\d+)", link)
            if match:
                item_id = int(match.group(1))

        if not item_id:
            return None

        # Buscar na API
        query = f'{{ productOfferV2(itemId: {item_id}, shopId: {shop_id or 0}, limit: 1) {{ nodes {{ itemId shopId productName priceMin priceMax priceDiscountRate imageUrl productLink offerLink shopName commissionRate }} }} }}'
        payload = json.dumps({"query": query, "variables": {}}, separators=(",", ":"))

        ts, signature = self._build_signature(app_id, api_key, payload)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={signature}"
        }

        try:
            resp = self.session.post(settings.SHOPEE_API_URL, headers=headers, data=payload, timeout=15)
            data = resp.json()
            nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", [])
            if nodes:
                return self._parse_product(nodes[0])
        except Exception as e:
            logger.error(f"Erro ao buscar produto por link: {e}")

        return None


# Instância global
shopee_service = ShopeeService()
