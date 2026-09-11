"""Cliente de la API oficial de Cardmarket (OAuth 1.0a).

Endpoints usados:
- GET /games                          -> resuelve el idGame de One Piece TCG en runtime.
- GET /products/find?search=...       -> buscar productos (cartas).
- GET /products/{id}/priceGuide       -> precios de referencia por idioma (1 = inglés).
- GET /products/{id}/marketplace      -> ofertas reales con país del vendedor.

Los IDs de idioma de Cardmarket: 1=English, 2=French, 3=German, 4=Spanish, 5=Italian,
6=Simplified Chinese, 7=Japanese, 8=Portuguese, 9=Russian, 10=Korean, 11=Traditional Chinese.
"""
from __future__ import annotations

import requests
from requests_oauthlib import OAuth1

import config

BASE_URL = "https://api.cardmarket.com/ws/v2.0/output.json"


class CardmarketError(Exception):
    pass


class CardmarketClient:
    def __init__(self) -> None:
        if not all([config.CARDMARKET_APP_TOKEN, config.CARDMARKET_APP_SECRET,
                    config.CARDMARKET_ACCESS_TOKEN, config.CARDMARKET_ACCESS_SECRET]):
            raise CardmarketError(
                "Faltan credenciales de Cardmarket en el .env "
                "(CARDMARKET_APP_TOKEN/SECRET y CARDMARKET_ACCESS_TOKEN/SECRET)."
            )
        self._auth = OAuth1(
            config.CARDMARKET_APP_TOKEN, config.CARDMARKET_APP_SECRET,
            config.CARDMARKET_ACCESS_TOKEN, config.CARDMARKET_ACCESS_SECRET,
        )
        self._game_id: int | None = None

    # ----------------------------- utilidades -----------------------------

    def _get(self, path: str, params: dict | None = None):
        try:
            resp = requests.get(f"{BASE_URL}/{path}", params=params,
                                auth=self._auth, timeout=25)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if resp.status_code == 401:
                raise CardmarketError("Credenciales de Cardmarket inválidas (401).") from e
            raise CardmarketError(f"Cardmarket devolvió {resp.status_code}: {e}") from e
        except requests.RequestException as e:
            raise CardmarketError(f"No se pudo contactar con Cardmarket: {e}") from e

    def game_id(self) -> int:
        """ID de juego de One Piece TCG, resuelto dinámicamente y cacheado."""
        if self._game_id is not None:
            return self._game_id
        data = self._get("games")
        for game in data.get("game", []):
            name = (game.get("name") or "").lower()
            if "one piece" in name:
                self._game_id = int(game["idGame"])
                return self._game_id
        raise CardmarketError("No se encontró el juego 'One Piece TCG' en Cardmarket.")

    # ----------------------------- búsqueda -----------------------------

    def find_products(self, search: str, id_language: int = config.LANG_EN,
                      exact: bool = False, max_results: int = 10) -> list[dict]:
        data = self._get("products/find", {
            "search": search,
            "idGame": self.game_id(),
            "idLanguage": id_language,
            "exact": exact,
            "maxResults": max_results,
        })
        return data.get("product", []) or []

    # ----------------------------- precios -----------------------------

    def price_guide(self, product_id: int, id_language: int = config.LANG_EN) -> dict:
        data = self._get(f"products/{product_id}/priceGuide",
                         {"idLanguage": id_language})
        return data.get("priceGuide", {}) or {}

    def marketplace(self, product_id: int, max_results: int = 100) -> list[dict]:
        data = self._get(f"products/{product_id}/marketplace",
                         {"start": 0, "maxResults": max_results})
        return data.get("article", []) or []

    # ----------------------------- helpers de negocio -----------------------------

    @staticmethod
    def best_product(products: list[dict], card_name: str, card_code: str | None = None) -> dict | None:
        """Elige el producto de Cardmarket que mejor coincide con la carta buscada."""
        if not products:
            return None
        card_name_l = (card_name or "").lower()
        # 1) coincidencia exacta de nombre
        for p in products:
            if (p.get("enName") or "").lower() == card_name_l:
                return p
        # 2) el número de la carta aparece en el nombre del producto (p. ej. "OP01-001")
        if card_code:
            code = card_code.upper()
            for p in products:
                if code in (p.get("enName") or "").upper():
                    return p
        # 3) el nombre buscado está contenido en el producto
        for p in products:
            if card_name_l and card_name_l in (p.get("enName") or "").lower():
                return p
        return products[0]

    @staticmethod
    def spanish_offers(articles: list[dict], id_language: int = config.LANG_EN) -> list[dict]:
        """Ofertas de vendedores de España, en el idioma indicado (inglés por defecto)."""
        out = []
        for a in articles:
            lang = (a.get("language") or {}).get("idLanguage")
            seller = (a.get("seller") or {})
            country = (seller.get("country") or "").lower()
            if lang == id_language and country in ("spain", "españa", "es"):
                out.append(a)
        return out

    @staticmethod
    def min_price(articles: list[dict]) -> float | None:
        prices = [float(a["price"]) for a in articles if a.get("price") is not None]
        return min(prices) if prices else None
