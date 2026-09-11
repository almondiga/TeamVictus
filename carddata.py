"""Proveedor de datos de cartas de One Piece TCG.

Fuente principal: optcg-api (comunidad, MIT) -> campos ricos (color, poder, rareza,
categoría, paralelas...) e imagen del sitio oficial de Bandai.
Si la API de cartas no está disponible (sin clave o sin acceso aprobado), se usa un
fallback con la API de Cardmarket (búsqueda por nombre + precio) para que /buscar
siga funcionando; los filtros avanzados de /listado necesitan optcg-api.

URL de imagen oficial (patrón estable): https://en.onepiece-cardgame.com/images/cardlist/card/{CODIGO}.png
"""
from __future__ import annotations

import re

import requests

import config
from cardmarket import CardmarketClient

# Campos de una carta "normalizada" para las embeds y la cuadrícula.
CARD_KEYS = ["id", "name", "rarity", "category", "colors", "cost", "power",
             "counter", "types", "effect", "image_url", "price", "sets"]

RARITIES = ["Common", "Uncommon", "Rare", "SuperRare", "SecretRare", "Leader"]
COLORS = ["Red", "Green", "Blue", "Purple", "Black", "Yellow"]
CATEGORIES = ["Leader", "Character", "Event", "Stage", "Don"]


def normalize_code(text: str) -> str | None:
    """Normaliza un código de carta escrito a mano a formato estándar.

    "op1-1" -> "OP01-001", "eb01-001" -> "EB01-001", "P-1" -> "P-001", "DON-1" -> "DON-001".
    Devuelve None si no parece un código.
    """
    t = re.sub(r"\s+", "", text).upper()
    m = re.fullmatch(r"(DON)[- ]?(\d{1,3})", t)
    if m:
        return f"DON-{int(m.group(2)):03d}"
    m = re.fullmatch(r"(P|PRB|SP|U)[- ]?(\d{1,4})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):03d}"
    m = re.fullmatch(r"([A-Z]{1,4})[- ]?(\d{1,2})[- ]?(\d{1,4})", t)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}-{int(m.group(3)):03d}"
    return None


def official_image_url(code: str) -> str:
    return f"https://en.onepiece-cardgame.com/images/cardlist/card/{code}.png"


def _clean(card: dict) -> dict:
    return {k: card.get(k) for k in CARD_KEYS}


class CardData:
    """Cliente de optcg-api con normalización de resultados."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base = (base_url or config.OPTCG_API_URL).rstrip("/")
        self.key = api_key or config.OPTCG_API_KEY
        self._session = requests.Session()
        if self.key:
            self._session.headers["X-API-Key"] = self.key

    # ----------------------------- bajo nivel -----------------------------

    def _get(self, path: str, params: dict | None = None):
        resp = self._session.get(f"{self.base}/{path}", params=params, timeout=25)
        if resp.status_code in (401, 403):
            raise PermissionError(
                "La API de cartas denegó el acceso (OPTCG_API_KEY no válida o sin acceso aprobado)."
            )
        resp.raise_for_status()
        return resp.json()

    # ----------------------------- búsqueda -----------------------------

    def get_by_code(self, code: str) -> dict | None:
        code = normalize_code(code) or code.upper()
        try:
            data = self._get(f"cards/{code}")
            if isinstance(data, list):
                data = data[0] if data else None
            return _clean(data) if data else None
        except requests.HTTPError:
            return None

    def search_by_name(self, name: str, limit: int = 10) -> list[dict]:
        data = self._get("cards", {"name": name, "page_size": limit})
        return [_clean(c) for c in data if isinstance(c, dict)]

    def resolve_card(self, text: str) -> dict | None:
        """Acepta código o nombre. Devuelve la carta normalizada (o None)."""
        code = normalize_code(text)
        if code:
            card = self.get_by_code(code)
            if card:
                return card
        matches = self.search_by_name(text.strip())
        return matches[0] if matches else None

    # ----------------------------- listado con filtros -----------------------------

    def list_cards(self, *, set_id: str | None = None, name: str | None = None,
                   color: str | None = None, category: str | None = None,
                   rarity: str | None = None, min_power: int | None = None,
                   sort: str = "id", order: str = "asc",
                   page_size: int = 100) -> list[dict]:
        params: dict = {"sort": sort, "order": order, "page_size": page_size}
        if set_id:
            params["set_id"] = set_id
        if name:
            params["name"] = name
        if color:
            params["color"] = color
        if category:
            params["category"] = category
        if rarity:
            params["rarity"] = rarity
        if min_power is not None:
            params["min_power"] = min_power
        data = self._get("cards", params)
        return [_clean(c) for c in data if isinstance(c, dict)]


class CardDataFallback:
    """Fallback cuando optcg-api no está disponible: busca en Cardmarket por nombre.

    Ofrece menos campos (sin color/poder/rareza avanzada) pero permite /buscar.
    """

    def __init__(self, mkm: CardmarketClient) -> None:
        self.mkm = mkm

    def resolve_card(self, text: str) -> dict | None:
        code = normalize_code(text)
        products = self.mkm.find_products(text.strip(), exact=False, max_results=10)
        if not products:
            return None
        best = self.mkm.best_product(products, text.strip(), code)
        card = {
            "id": code or (best.get("number") or best.get("enName") or ""),
            "name": best.get("enName"),
            "rarity": best.get("rarity"),
            "category": None,
            "colors": None,
            "cost": None,
            "power": None,
            "counter": None,
            "types": None,
            "effect": None,
            "image_url": best.get("image") or (official_image_url(code) if code else None),
            "price": None,
            "sets": [{"id": best.get("expansionName"), "label": best.get("expansionName")}],
            "_mkm_product": best,
        }
        return card


def build_card_data() -> CardData | CardDataFallback:
    """Devuelve el proveedor principal si hay credenciales/clave; si no, el fallback."""
    if config.OPTCG_API_KEY:
        return CardData()
    try:
        mkm = CardmarketClient()
        return CardDataFallback(mkm)
    except Exception:
        return CardData()  # fallará con un mensaje claro al usarlo
