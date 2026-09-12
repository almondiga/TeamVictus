"""Proveedor de datos de cartas de One Piece TCG.

Fuente principal: optcg-api (comunidad, MIT) -> campos ricos (color, poder, rareza,
categoría, paralelas...) e imagen del sitio oficial de Bandai.
Si la API de cartas no está disponible (sin clave o sin acceso aprobado), se usa un
fallback con la API de Cardmarket (búsqueda por nombre + precio) para que /buscar
siga funcionando; los filtros avanzados de /listado necesitan optcg-api.

URL de imagen oficial (patrón estable): https://en.onepiece-cardgame.com/images/cardlist/card/{CODIGO}.png
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

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


class CardDataBerry:
    """Proveedor de cartas basado en la API de BerryWallet (pokewallet.io/op/search).

    No requiere clave de optcg ni credenciales de Cardmarket: usa BERRYWALLET_API_KEY.
    Ofrece los campos principales (color, poder, rareza, categoría, set), la imagen
    oficial de Bandai (URL pública estable) y permite /buscar y /listado con filtros
    básicos (set, nombre, color, categoría, rareza, poder, número).
    """

    BASE = "https://api.pokewallet.io/op/search"

    def __init__(self, api_key: str | None = None) -> None:
        self.key = api_key or config.BERRYWALLET_API_KEY
        self._session = requests.Session()

    # ----------------------------- bajo nivel -----------------------------

    def _buscar(self, q: str, limit: int = 100) -> list[dict]:
        if not self.key:
            raise PermissionError(
                "Falta BERRYWALLET_API_KEY en el .env para consultar cartas sin optcg."
            )
        resp = self._session.get(self.BASE, params={"q": q, "limit": limit},
                                 headers={"X-API-Key": self.key}, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") or data.get("results") or []

    # ----------------------------- normalización -----------------------------

    def _normalizar(self, item: dict) -> dict:
        code = item.get("card_number") or ""
        name = re.sub(r"\s*\(\d{3,4}\)\s*$", "", item.get("name") or "").strip() \
            or item.get("name")
        cm = item.get("cardmarket") or {}
        prices = cm.get("prices") or {}
        set_id = _set_de_codigo(code)
        return {
            "id": code,
            "name": name,
            "rarity": item.get("rarity"),
            "category": item.get("card_type"),
            "colors": [c for c in [item.get("ext_color")] if c],
            "cost": _int_or_none(item.get("ext_cost")),
            "power": _int_or_none(item.get("ext_power")),
            "counter": None,
            "types": ([t for t in (item.get("ext_subtypes") or "").split(";") if t] or None),
            "effect": None,
            "image_url": official_image_url(code) if code else None,
            "price": _num(prices.get("trend")),
            "sets": [{"id": set_id, "label": set_id}] if set_id else [],
            "_subtype": item.get("sub_type_name"),
            "_link": cm.get("product_url"),
        }

    # ----------------------------- búsqueda -----------------------------

    def resolve_card(self, text: str) -> dict | None:
        code = normalize_code(text)
        q = code or text.strip()
        items = self._buscar(q, limit=20)
        if not items:
            return None
        if code:
            target = code.upper()
            exactos = [i for i in items
                       if (i.get("card_number") or "").upper() == target]
            pool = exactos or items
        else:
            pool = items
        if not pool:
            return None
        # prioriza: variante con precios de cardmarket y, entre ellas, la Normal
        def clave(i):
            con_precios = 1 if (i.get("cardmarket") or {}).get("prices") else 0
            normal = 0 if (i.get("sub_type_name") or "").lower() == "normal" else 1
            return (con_precios, -normal)
        best = max(pool, key=clave)
        return self._normalizar(best)

    # ----------------------------- listado con filtros -----------------------------

    def list_cards(self, *, set_id: str | None = None, name: str | None = None,
                   color: str | None = None, category: str | None = None,
                   rarity: str | None = None, min_power: int | None = None,
                   sort: str = "id", order: str = "asc",
                   page_size: int = 100) -> list[dict]:
        q = name or (set_id.upper().replace("-", "") if set_id else "")
        if not q:
            raise ValueError(
                "Elige al menos un filtro de set, nombre o número: con BerryWallet "
                "(sin OPTCG_API_KEY) no se puede listar todo el catálogo. "
                "Configurando OPTCG_API_KEY el listado completo sí funciona."
            )
        items = self._buscar(q, limit=min(page_size * 3, 500))
        cartas = [self._normalizar(i) for i in items if isinstance(i, dict)]

        if set_id:
            sid = set_id.upper().replace("-", "")
            cartas = [c for c in cartas
                      if (c.get("id") or "").upper().replace("-", "").startswith(sid)]
        if color:
            cartas = [c for c in cartas
                      if color.lower() in [x.lower() for x in (c.get("colors") or [])]]
        if category:
            cartas = [c for c in cartas
                      if (c.get("category") or "").lower() == category.lower()]
        if rarity:
            cartas = [c for c in cartas
                      if (c.get("rarity") or "").lower() == rarity.lower()]
        if min_power is not None:
            cartas = [c for c in cartas if (c.get("power") or 0) >= min_power]
        if name:
            nl = name.lower()
            cartas = [c for c in cartas
                      if nl in (c.get("name") or "").lower()
                      or nl in (c.get("id") or "").lower()]

        # deduplica variantes de la misma carta (conserva Normal con precios si existe)
        mejores: dict[str, dict] = {}
        for c in cartas:
            cid = c.get("id") or ""
            viejo = mejores.get(cid)
            if viejo is None or _mejor_variante(c, viejo):
                mejores[cid] = c
        cartas = list(mejores.values())

        def sk(c):
            v = c.get(sort)
            return (v is not None, v if v is not None else "")
        cartas.sort(key=sk, reverse=(order == "desc"))
        return cartas[:page_size]


class CardDataLocal:
    """Proveedor de cartas desde catálogo local (SQLite `optcg_cards.db`).

    Se genera con `build_local_db.py` a partir de `data/cards.json` del proyecto
    optcg-api (MIT, scraper oficial de Bandai). Catálogo completo, sin claves ni
    red: /buscar y /listado con todos los filtros combinables.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db = db_path or config.LOCAL_DB_PATH
        self._conn = sqlite3.connect(self.db)
        self._conn.row_factory = sqlite3.Row
        self._sets: dict[str, dict] = {}
        try:
            for r in self._conn.execute("SELECT id, label, pack_id FROM sets"):
                self._sets[r["id"]] = dict(r)
        except sqlite3.Error:
            pass

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def _fila_a_carta(self, r) -> dict:
        def jc(v):
            if not v:
                return None
            try:
                return json.loads(v)
            except Exception:
                return None
        set_id = r["set_id"]
        s = self._sets.get(set_id) or {"id": set_id, "label": set_id, "pack_id": None}
        return {
            "id": r["id"],
            "name": r["name"],
            "rarity": r["rarity"],
            "category": r["category"],
            "colors": jc(r["colors"]),
            "cost": r["cost"],
            "power": r["power"],
            "counter": r["counter"],
            "types": jc(r["types"]),
            "effect": r["effect"],
            "image_url": r["image_url"],
            "price": None,  # los precios los aporta el proveedor de precios (BerryWallet)
            "sets": [{"id": s["id"], "label": s["label"], "pack_id": s["pack_id"]}],
        }

    # ----------------------------- búsqueda -----------------------------

    def resolve_card(self, text: str) -> dict | None:
        code = normalize_code(text)
        if code:
            row = self._conn.execute(
                "SELECT * FROM cards WHERE id = ?", (code.upper(),)).fetchone()
            if row:
                return self._fila_a_carta(row)
            # paralelas (OP01-001_p1): devuelve la base si la piden por código
            row = self._conn.execute(
                "SELECT * FROM cards WHERE id LIKE ? ORDER BY parallel ASC, id LIMIT 1",
                (code.upper() + "%",)).fetchone()
            if row:
                return self._fila_a_carta(row)
        name = (text or "").strip()
        if not name:
            return None
        # tolera "Monkey D. Luffy" vs "Monkey.D.Luffy" (como los lista Bandai)
        nl = re.sub(r"[\s.]+", "", name.lower())
        row = self._conn.execute(
            """SELECT * FROM cards
               WHERE name = ? COLLATE NOCASE
                  OR name LIKE ? COLLATE NOCASE
                  OR replace(lower(name), '.', '') = ?
                  OR replace(lower(name), '.', '') LIKE ?
               ORDER BY parallel ASC, id ASC LIMIT 1""",
            (name, f"%{name}%", nl, f"%{nl}%")).fetchone()
        return self._fila_a_carta(row) if row else None

    # ----------------------------- listado con filtros -----------------------------

    def list_cards(self, *, set_id: str | None = None, name: str | None = None,
                   color: str | None = None, category: str | None = None,
                   rarity: str | None = None, min_power: int | None = None,
                   sort: str = "id", order: str = "asc",
                   page_size: int = 100) -> list[dict]:
        cond, params = [], []
        if set_id:
            cond.append("set_id = ?")
            params.append(set_id.upper())
        if name:
            nl = re.sub(r"[\s.]+", "", name.lower())
            cond.append("(name LIKE ? COLLATE NOCASE OR id LIKE ? COLLATE NOCASE"
                        " OR replace(lower(name), '.', '') LIKE ?"
                        " OR replace(lower(id), '.', '') LIKE ?)")
            params += [f"%{name}%", f"%{name}%", f"%{nl}%", f"%{nl}%"]
        if color:
            cond.append("colors LIKE ?")
            params.append(f'%"{color.capitalize()}"%')
        if category:
            cond.append("category = ? COLLATE NOCASE")
            params.append(category)
        if rarity:
            cond.append("rarity = ? COLLATE NOCASE")
            params.append(rarity)
        if min_power is not None:
            cond.append("power >= ?")
            params.append(min_power)
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        order_col = {"id": "id", "name": "name", "power": "power", "cost": "cost"}.get(sort, "id")
        direccion = "DESC" if order == "desc" else "ASC"
        rows = self._conn.execute(
            f"SELECT * FROM cards{where} ORDER BY {order_col} {direccion}, id ASC LIMIT ?",
            params + [page_size]).fetchall()
        return [self._fila_a_carta(r) for r in rows]


def _set_de_codigo(code: str) -> str | None:
    """'OP01-001' -> 'OP-01' · 'ST01-001' -> 'ST-01' · 'P-001' -> 'P'."""
    c = (code or "").upper()
    m = re.fullmatch(r"([A-Z]+)(\d{2})-(\d{3,4})", c)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"([A-Z]+)-(\d{3,4})", c)
    if m:
        return m.group(1)
    return None


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mejor_variante(a: dict, b: dict) -> bool:
    """True si a es mejor variante que b (Normal antes, con precio antes)."""
    def p(c):
        normal = 1 if (c.get("_subtype") or "").lower() == "normal" else 0
        con_precio = 1 if c.get("price") is not None else 0
        return (normal, con_precio)
    return p(a) > p(b)


def build_card_data() -> CardData | CardDataLocal | CardDataBerry | CardDataFallback:
    """Devuelve el mejor proveedor disponible según las claves y datos locales."""
    if config.OPTCG_API_KEY:
        return CardData()
    if os.path.exists(config.LOCAL_DB_PATH):
        return CardDataLocal()
    if config.BERRYWALLET_API_KEY:
        return CardDataBerry()
    try:
        mkm = CardmarketClient()
        return CardDataFallback(mkm)
    except Exception:
        return CardData()  # fallará con un mensaje claro al usarlo
