"""Proveedores de precios de Cardmarket.

La API oficial de Cardmarket no admite altas nuevas (estado: cerrada, sin fecha de
reapertura), por lo que los precios se obtienen de proveedores alternativos que
agregan sus datos:

- BerryWallet (api.pokewallet.io): precios Cardmarket en EUR (trend / avg / low).
  Gratis (100 req/h, 1.000/día). NO ofrece desglose por país.
- RapidAPI «CardMarket API TCG»: añade `lowest_near_mint_ES` (mínimo near-mint de
  vendedores de España) cuando el dato existe. Plan free: 100 req/día.
- API oficial de Cardmarket (OAuth1): filtro exacto por idioma inglés y ofertas por
  país. Se mantiene por si el acceso vuelve a abrirse algún día.

Configuración (config.py / .env): PRICE_PROVIDER=auto|berrywallet|rapidapi|cardmarket
Prioridad en "auto": berrywallet > rapidapi > cardmarket.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests

import config

URL_MKM_SEARCH = "https://www.cardmarket.com/es/OnePiece/Products/Search?searchString={}"


@dataclass
class PriceResult:
    """Resultado de consulta de precios, independiente del proveedor."""

    provider: str                 # berrywallet | rapidapi | cardmarket
    trend: float | None = None    # precio de tendencia (EUR)
    avg: float | None = None      # precio medio (EUR)
    low: float | None = None      # precio mínimo (EUR)
    es_price: float | None = None  # mín. near-mint de vendedores de España (si existe)
    es_count: int | None = None    # nº de ofertas/artículos en España (si se conoce)
    link: str | None = None        # url del producto en Cardmarket
    nota: str | None = None        # aclaración sobre el proveedor / limitaciones

    @property
    def es_disponible(self) -> bool:
        return self.es_price is not None

    @property
    def en_language(self) -> bool:
        """Solo la API oficial filtra por idioma inglés exacto."""
        return self.provider == "cardmarket"


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _elegir_mejor(items: list[dict], card_code: str | None, card_name: str) -> dict | None:
    """Elige el item que mejor coincide por código (card_number) y luego por nombre."""
    if not items:
        return None
    if card_code:
        code = card_code.upper().replace("-", "").replace(" ", "")
        for it in items:
            n = (it.get("card_number") or it.get("card_code_number") or "").upper()
            if n.replace("-", "").replace(" ", "") == code:
                return it
    if card_name:
        name = (card_name or "").lower()
        for it in items:
            if (it.get("name") or "").lower() == name:
                return it
        for it in items:
            if name and name in (it.get("name") or "").lower():
                return it
    return items[0]


def _elegir_con_precios(items: list[dict], card_code: str | None, card_name: str) -> dict | None:
    """Como _elegir_mejor, pero prefiere resultados que tengan precios de Cardmarket.

    Muchos resultados (variantes alt-art, foil, promos) traen cardmarket: null; con
    este helper el bot salta a la variante Normal del mismo código que sí tiene datos.
    """
    best = _elegir_mejor(items, card_code, card_name)
    if best and (best.get("cardmarket") or {}).get("prices"):
        return best
    candidatos: list[dict] = []
    if card_code:
        code = card_code.upper().replace("-", "").replace(" ", "")
        candidatos = [it for it in items
                      if (it.get("card_number") or "").upper().replace("-", "").replace(" ", "") == code]
    else:
        candidatos = items
    # prioriza la variante Normal, luego cualquier otra con precios
    for it in sorted(candidatos,
                     key=lambda x: 0 if (x.get("sub_type_name") or "").lower() == "normal" else 1):
        if (it.get("cardmarket") or {}).get("prices"):
            return it
    return best


class _TTLCache:
    def __init__(self, ttl: int) -> None:
        self.ttl = ttl
        self._d: dict = {}

    def get(self, key: str):
        v = self._d.get(key)
        if v and v[0] > time.time():
            return v[1]
        self._d.pop(key, None)
        return None

    def set(self, key: str, val) -> None:
        self._d[key] = (time.time() + self.ttl, val)


class PriceProvider:
    name = "base"

    def get_prices(self, card_name: str, card_code: str | None) -> PriceResult | None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# BerryWallet / PokéWallet (gratis, 100 req/h)
# ---------------------------------------------------------------------------

class BerryWalletProvider(PriceProvider):
    name = "berrywallet"

    def __init__(self, api_key: str, session: requests.Session | None = None) -> None:
        self.key = api_key
        self.session = session or requests.Session()
        self._cache = _TTLCache(1800)  # 30 min

    def get_prices(self, card_name: str, card_code: str | None) -> PriceResult | None:
        q = card_code or card_name
        cache_key = f"bw:{q}"
        hit = self._cache.get(cache_key)
        if hit is not None:
            return hit
        resp = self.session.get(
            "https://api.pokewallet.io/op/search",
            params={"q": q, "limit": 10},
            headers={"X-API-Key": self.key},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") or data.get("results") or []
        best = _elegir_con_precios(items, card_code, card_name)
        result: PriceResult | None = None
        if best:
            cm = best.get("cardmarket") or {}
            prices = cm.get("prices") or {}
            if prices:
                result = PriceResult(
                    provider=self.name,
                    trend=_num(prices.get("trend")),
                    avg=_num(prices.get("avg")),
                    low=_num(prices.get("low")),
                    link=cm.get("product_url"),
                    nota="Cardmarket (EUR) vía BerryWallet · sin desglose por país/idioma",
                )
        self._cache.set(cache_key, result)
        return result


# ---------------------------------------------------------------------------
# RapidAPI «CardMarket API TCG» (free 100 req/día; incluye precio por país ES)
# ---------------------------------------------------------------------------

class RapidApiCMProvider(PriceProvider):
    name = "rapidapi"

    def __init__(self, api_key: str, session: requests.Session | None = None) -> None:
        self.key = api_key
        self.session = session or requests.Session()
        self._cache = _TTLCache(3600)  # 1 h

    def get_prices(self, card_name: str, card_code: str | None) -> PriceResult | None:
        q = card_code or card_name
        cache_key = f"rapi:{q}"
        hit = self._cache.get(cache_key)
        if hit is not None:
            return hit
        resp = self.session.get(
            "https://cardmarket-api-tcg.p.rapidapi.com/onepiece/cards",
            params={"search": q, "rapidapi-key": self.key},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data if isinstance(data, list) else (data.get("data") or data.get("results") or [])
        best = _elegir_mejor(items, card_code, card_name)
        result: PriceResult | None = None
        if best:
            cm = (best.get("prices") or {}).get("cardmarket") or {}
            es = _num(cm.get("lowest_near_mint_ES") or cm.get("lowest_near_mint_Es")
                      or cm.get("lowest_near_mint_es"))
            if cm:
                result = PriceResult(
                    provider=self.name,
                    trend=_num(cm.get("7d_average") or cm.get("trend")),
                    avg=_num(cm.get("30d_average") or cm.get("avg")),
                    low=_num(cm.get("lowest_near_mint") or cm.get("low")),
                    es_price=es,
                    es_count=_num(cm.get("count_ES") or cm.get("count_es")),
                    link=(best.get("url") or best.get("product_url")
                          or URL_MKM_SEARCH.format(quote_plus(card_name))),
                    nota=("Cardmarket (EUR) vía RapidAPI · ES = mín. near-mint de "
                          "vendedores de España (sin filtro por idioma)"),
                )
        self._cache.set(cache_key, result)
        return result


# ---------------------------------------------------------------------------
# API oficial de Cardmarket (OAuth1) — solo si el acceso está disponible
# ---------------------------------------------------------------------------

class CardmarketOfficialProvider(PriceProvider):
    name = "cardmarket"

    def __init__(self) -> None:
        from cardmarket import CardmarketClient
        self.mkm = CardmarketClient()

    def get_prices(self, card_name: str, card_code: str | None) -> PriceResult | None:
        products = self.mkm.find_products(card_name)
        best = self.mkm.best_product(products, card_name, card_code)
        if not best:
            return None
        pg = self.mkm.price_guide(int(best["idProduct"]))
        arts = self.mkm.marketplace(int(best["idProduct"]))
        es = self.mkm.spanish_offers(arts)
        es_min = self.mkm.min_price(es)
        return PriceResult(
            provider=self.name,
            trend=_num(pg.get("TREND")),
            avg=_num(pg.get("AVG")),
            low=_num(pg.get("LOW")),
            es_price=es_min,
            es_count=sum(int(a.get("count", 1)) for a in es),
            link=URL_MKM_SEARCH.format(quote_plus(card_name)),
            nota="API oficial de Cardmarket · idioma inglés + vendedores de España",
        )


# ---------------------------------------------------------------------------
# Fábrica
# ---------------------------------------------------------------------------

def build_price_provider() -> tuple[PriceProvider | None, str | None]:
    """Devuelve (proveedor, error). En modo auto prioriza berrywallet > rapidapi > oficial."""
    mode = (config.PRICE_PROVIDER or "auto").lower()
    candidatos: list[tuple[str, PriceProvider]] = []

    def add_berry():
        if config.BERRYWALLET_API_KEY:
            candidatos.append(("berrywallet", BerryWalletProvider(config.BERRYWALLET_API_KEY)))

    def add_rapi():
        if config.RAPIDAPI_KEY:
            candidatos.append(("rapidapi", RapidApiCMProvider(config.RAPIDAPI_KEY)))

    def add_oficial():
        if all([config.CARDMARKET_APP_TOKEN, config.CARDMARKET_APP_SECRET,
                config.CARDMARKET_ACCESS_TOKEN, config.CARDMARKET_ACCESS_SECRET]):
            try:
                candidatos.append(("cardmarket", CardmarketOfficialProvider()))
            except Exception:
                pass  # credenciales presentes pero inválidas/incompletas

    if mode == "auto":
        add_berry()
        add_rapi()
        add_oficial()
    elif mode == "berrywallet":
        add_berry()
    elif mode == "rapidapi":
        add_rapi()
    elif mode == "cardmarket":
        add_oficial()
    else:
        return None, f"PRICE_PROVIDER desconocido: {mode}"

    if not candidatos:
        return None, (
            "No hay proveedor de precios configurado (la API oficial de Cardmarket está "
            "cerrada a nuevas altas). Configura en el .env:\n"
            "• BERRYWALLET_API_KEY → precios Cardmarket EUR (gratis)\n"
            "• RAPIDAPI_KEY → añade comparativa por país España (plan free RapidAPI)"
        )
    return candidatos[0][1], None
