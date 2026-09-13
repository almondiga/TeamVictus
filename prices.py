"""Proveedores de precios de Cardmarket.

La API oficial de Cardmarket no admite altas nuevas (estado: cerrada, sin fecha de
reapertura), por lo que los precios se obtienen de proveedores alternativos que
agregan sus datos:

- BerryWallet (api.pokewallet.io): precios Cardmarket en EUR (trend / avg / low).
  Gratis (100 req/h, 1.000/día). NO ofrece desglose por país.
- CardTrader (api.cardtrader.com): comparativa España — ofertas de vendedores con
  país ES e idioma EN en su propio mercado (no es Cardmarket). Cuenta gratuita en
  cardtrader.com (sin tarjeta; la tarjeta solo se pide para COMPRAR por API).
- RapidAPI «CardMarket API TCG»: añade `lowest_near_mint_ES` (mínimo near-mint de
  vendedores de España) cuando el dato existe. Plan free: 100 req/día.
- API oficial de Cardmarket (OAuth1): filtro exacto por idioma inglés y ofertas por
  país. Se mantiene por si el acceso vuelve a abrirse algún día.

Configuración (config.py / .env):
PRICE_PROVIDER=auto|berrywallet|rapidapi|cardtrader|cardmarket
Prioridad en "auto": berrywallet > rapidapi > cardtrader > cardmarket.
Con BERRYWALLET_API_KEY + CARDTRADER_TOKEN se usa un proveedor híbrido:
EUR de Cardmarket vía BerryWallet + comparativa España vía CardTrader.
"""
from __future__ import annotations

import re
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


def _norm_set(codigo: str) -> str:
    """'OP-01' / 'OP01' / 'op 01' -> 'OP01' (para emparejar códigos de set)."""
    return "".join(ch for ch in (codigo or "").upper() if ch.isalnum())


def _base_codigo(codigo: str | None) -> str:
    """Quita el sufijo de variante: 'OP01-001_p1' -> 'OP01-001'."""
    return re.sub(r"_[pr]\d+$", "", codigo or "", flags=re.IGNORECASE)


def _sufijo_variante(codigo: str | None) -> str:
    """Sufijo de variante: 'OP01-001_p1' -> 'p' · 'OP01-001_r1' -> 'r' · '' si base."""
    m = re.search(r"_([pr])\d+$", codigo or "", re.IGNORECASE)
    return m.group(1).lower() if m else ""


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


def _elegir_paralela(items: list[dict], card_code: str | None, card_name: str) -> dict | None:
    """Para una variante paralela / Alternate Art: prefiere el item con precios cuyo
    nombre o sub_tipo indique paralela (p. ej. 'Roronoa Zoro (001) (Parallel)'),
    porque BerryWallet lista la paralela como otra variante con precios propios.
    Si no hay datos de paralela, cae a la selección normal."""
    if not items:
        return None

    def es_paralela(it: dict) -> bool:
        nombre = (it.get("name") or "").lower()
        sub = (it.get("sub_type_name") or "").lower()
        return ("(parallel)" in nombre or "(alternate art)" in nombre
                or sub in ("foil", "parallel"))

    con_precios = [it for it in items
                   if es_paralela(it) and (it.get("cardmarket") or {}).get("prices")]
    if con_precios:
        # prefiere la marcada como (Parallel)
        con_precios.sort(key=lambda x: 0 if "(parallel)" in (x.get("name") or "").lower() else 1)
        return con_precios[0]
    return _elegir_con_precios(items, card_code, card_name)


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
        base = _base_codigo(card_code)
        sufijo = _sufijo_variante(card_code)
        q = base or card_name
        cache_key = f"bw:{q}:{sufijo}"
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
        if sufijo == "p":
            # variante paralela / Alternate Art: busca su propio precio
            best = _elegir_paralela(items, base, card_name)
        else:
            best = _elegir_con_precios(items, base, card_name)
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
        q = _base_codigo(card_code) or card_name
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
        best = _elegir_mejor(items, q, card_name)
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
# CardTrader (api.cardtrader.com) — gratis, sin tarjeta, sin captchas.
# Comparativa España: ofertas de vendedores con country_code ES e idioma EN,
# en su propio mercado (no es Cardmarket). Cuenta gratuita -> token en Settings.
# ---------------------------------------------------------------------------

def _es_foil(oferta: dict) -> bool:
    """Detecta si una oferta de CardTrader es foil (clave de propiedad con 'foil')."""
    for k, v in (oferta.get("properties_hash") or {}).items():
        if "foil" in k.lower():
            return bool(v)
    return False


class CardTraderProvider(PriceProvider):
    name = "cardtrader"

    BASE = "https://api.cardtrader.com/api/v2"

    def __init__(self, token: str, session: requests.Session | None = None) -> None:
        self.token = token
        self.session = session or requests.Session()
        self._juegos: dict | None = None
        self._expansiones = _TTLCache(86400)   # 24 h
        self._blueprints = _TTLCache(86400)    # 24 h
        self._ofertas_cache = _TTLCache(21600)  # 6 h

    # -- helpers ----------------------------------------------------------

    def _juego_op(self) -> int | None:
        if self._juegos is None:
            r = self.session.get(f"{self.BASE}/games",
                                 headers={"Authorization": f"Bearer {self.token}"},
                                 timeout=20)
            r.raise_for_status()
            data = r.json()
            self._juegos = data.get("array") if isinstance(data, dict) else (data or [])
        for g in self._juegos or []:
            texto = f"{(g or {}).get('name') or ''} {(g or {}).get('display_name') or ''}".lower()
            if "one piece" in texto:
                return (g or {}).get("id")
        return None

    def _expansion_de(self, set_code: str) -> dict | None:
        norm = _norm_set(set_code)
        cache_key = f"exp:{norm}"
        hit = self._expansiones.get(cache_key)
        if hit is not None:
            return hit or None
        juego = self._juego_op()
        if juego is None:
            self._expansiones.set(cache_key, None)
            return None
        r = self.session.get(f"{self.BASE}/expansions",
                             headers={"Authorization": f"Bearer {self.token}"},
                             timeout=30)
        r.raise_for_status()
        encontrado = None
        for e in (r.json() or []):
            if e.get("game_id") == juego and _norm_set(e.get("code") or "") == norm:
                encontrado = e
                break
        self._expansiones.set(cache_key, encontrado)
        return encontrado

    def _blueprint_de(self, expansion_id: int, card_name: str, card_code: str) -> dict | None:
        cache_key = f"bp:{expansion_id}"
        hit = self._blueprints.get(cache_key)
        if hit is None:
            r = self.session.get(f"{self.BASE}/blueprints/export",
                                 params={"expansion_id": expansion_id},
                                 headers={"Authorization": f"Bearer {self.token}"},
                                 timeout=60)
            r.raise_for_status()
            lista = r.json() or []
            self._blueprints.set(cache_key, lista)
            hit = lista
        nombre = (card_name or "").strip().upper()
        codigo = (card_code or "").strip().upper()
        codigo_norm = _norm_set(codigo)
        # 1) coincidencia exacta por collector_number (evita alt-art/paralelas)
        for bp in hit:
            fp = bp.get("fixed_properties") or {}
            if _norm_set(fp.get("collector_number") or "") == codigo_norm:
                return bp
        # 2) nombre exacto
        for bp in hit:
            n = (bp.get("name") or "").strip().upper()
            if n == nombre:
                return bp
        # 3) el código aparece en el nombre
        for bp in hit:
            if codigo_norm and codigo_norm in _norm_set(bp.get("name") or ""):
                return bp
        # 4) el nombre aparece en el nombre del blueprint
        for bp in hit:
            if nombre and nombre in (bp.get("name") or "").upper():
                return bp
        return None

    def _ofertas(self, blueprint_id: int) -> list[dict]:
        cache_key = f"pr:{blueprint_id}"
        hit = self._ofertas_cache.get(cache_key)
        if hit is not None:
            return hit
        r = self.session.get(f"{self.BASE}/marketplace/products",
                             params={"blueprint_id": blueprint_id, "language": "en"},
                             headers={"Authorization": f"Bearer {self.token}"},
                             timeout=30)
        r.raise_for_status()
        data = r.json()
        ofertas: list[dict] = []
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    ofertas.extend(v)
        elif isinstance(data, list):
            ofertas = data
        self._ofertas_cache.set(cache_key, ofertas)
        return ofertas

    # -- interfaz ----------------------------------------------------------

    def get_prices(self, card_name: str, card_code: str | None) -> PriceResult | None:
        if not card_code:
            return PriceResult(provider=self.name,
                               nota="CardTrader necesita el código de carta (p. ej. OP01-001)")
        try:
            base = _base_codigo(card_code)               # OP01-001_p1 -> OP01-001
            sufijo = _sufijo_variante(card_code)          # 'p' / 'r' / ''
            set_code = base.rsplit("-", 1)[0]             # OP01-001 -> OP01
            expansion = self._expansion_de(set_code)
            if not expansion:
                return PriceResult(provider=self.name,
                                   nota=f"Set «{set_code}» no encontrado en CardTrader")
            # las Alternate Art tienen blueprint propio con collector base+'A'
            bp = None
            if sufijo == "p":
                bp = self._blueprint_de(expansion.get("id"), card_name, base + "A")
            if bp is None:
                bp = self._blueprint_de(expansion.get("id"), card_name, base)
            if not bp:
                return PriceResult(provider=self.name,
                                   nota=f"«{card_name} ({card_code})» no encontrado en CardTrader")
            ofertas = self._ofertas(bp.get("id"))
        except Exception as exc:
            return PriceResult(provider=self.name, nota=f"Error consultando CardTrader: {exc}")

        es = [o for o in ofertas if (o.get("user") or {}).get("country_code") == "ES"]
        if not es:
            return PriceResult(provider=self.name,
                               nota="Sin vendedores de España en CardTrader para esta carta (idioma EN)")

        no_foil = [o for o in es if not _es_foil(o)]
        candidatos = no_foil or es  # prefiere no-foil; si solo hay foil, usa esas
        nm = [o for o in candidatos
              if (o.get("properties_hash") or {}).get("condition") in ("Near Mint", "Mint")]
        pool = nm or candidatos   # prefiere Near Mint
        precios = [_num((o.get("price") or {}).get("cents")) for o in pool]
        precios = [p / 100.0 for p in precios if p is not None]
        if not precios:
            return PriceResult(provider=self.name, nota="CardTrader sin precios en EUR")
        es_count = sum(int(o.get("quantity") or 1) for o in pool)
        link = f"https://www.cardtrader.com/en/cards/{bp.get('id')}"
        nota = ("CardTrader (mercado propio, no Cardmarket) · mín. vendedores de España, "
                "idioma EN")
        if not nm:
            nota += " (sin ofertas Near Mint)"
        return PriceResult(
            provider=self.name,
            es_price=min(precios),
            es_count=es_count,
            link=link,
            nota=nota,
        )


# ---------------------------------------------------------------------------
# Híbrido: BerryWallet (EUR Cardmarket) + CardTrader (comparativa España)
# ---------------------------------------------------------------------------

class HybridProvider(PriceProvider):
    name = "berrywallet+cardtrader"

    def __init__(self, bw: PriceProvider, ct: PriceProvider) -> None:
        self.bw = bw
        self.ct = ct

    def get_prices(self, card_name: str, card_code: str | None) -> PriceResult | None:
        bw = self.bw.get_prices(card_name, card_code)
        ct = self.ct.get_prices(card_name, card_code)
        if bw and (bw.trend is not None or bw.avg is not None or bw.low is not None):
            nota = "EUR: Cardmarket vía BerryWallet"
            if ct and ct.es_disponible:
                nota += " · " + ct.nota
            elif ct and ct.nota:
                nota += " · " + ct.nota
            return PriceResult(
                provider=self.name,
                trend=bw.trend, avg=bw.avg, low=bw.low, link=bw.link,
                es_price=ct.es_price if ct else None,
                es_count=ct.es_count if ct else None,
                nota=nota,
            )
        return ct or bw


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
    """Devuelve (proveedor, error).

    Modo "auto": berrywallet > rapidapi > cardtrader > oficial; si hay
    BERRYWALLET_API_KEY y CARDTRADER_TOKEN devuelve el híbrido (EUR + España).
    """
    mode = (config.PRICE_PROVIDER or "auto").lower()
    candidatos: list[tuple[str, PriceProvider]] = []

    def add_berry():
        if config.BERRYWALLET_API_KEY:
            candidatos.append(("berrywallet", BerryWalletProvider(config.BERRYWALLET_API_KEY)))

    def add_rapi():
        if config.RAPIDAPI_KEY:
            candidatos.append(("rapidapi", RapidApiCMProvider(config.RAPIDAPI_KEY)))

    def add_ct():
        if config.CARDTRADER_TOKEN:
            candidatos.append(("cardtrader", CardTraderProvider(config.CARDTRADER_TOKEN)))

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
        add_ct()
        add_oficial()
    elif mode == "berrywallet":
        add_berry()
    elif mode == "rapidapi":
        add_rapi()
    elif mode == "cardtrader":
        add_ct()
    elif mode == "cardmarket":
        add_oficial()
    else:
        return None, f"PRICE_PROVIDER desconocido: {mode}"

    if not candidatos:
        return None, (
            "No hay proveedor de precios configurado (la API oficial de Cardmarket está "
            "cerrada a nuevas altas). Configura en el .env:\n"
            "• BERRYWALLET_API_KEY → precios Cardmarket EUR (gratis)\n"
            "• CARDTRADER_TOKEN → comparativa España (cuenta gratis en cardtrader.com, sin tarjeta)"
        )

    nombres = [n for n, _ in candidatos]
    if "berrywallet" in nombres and "cardtrader" in nombres:
        bw = next(p for n, p in candidatos if n == "berrywallet")
        ct = next(p for n, p in candidatos if n == "cardtrader")
        return HybridProvider(bw, ct), None
    return candidatos[0][1], None
