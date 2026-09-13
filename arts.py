"""Emparejamiento de variantes de la BD con los blueprints de CardTrader por imagen.

La BD local (optcg-api) clasifica las variantes _p1.._pN de una carta como
"Alternate Art" sin distinguir el arte concreto (Secret Rare AA, Manga Panel,
Red Manga Panel, Wanted...). CardTrader tiene un blueprint por arte
(collector OP13-118, OP13-118a/m/r/w...) con su imagen propia.

Este módulo compara el recorte central de la imagen oficial (Bandai) de cada
variante con el de cada blueprint candidato de CardTrader mediante un
difference-hash; el blueprint con menor distancia se guarda como el arte de esa
variante. El resultado se persiste en `variante_art.db` (SQLite) para no repetir
las descargas, y se usa para mostrar el precio correcto de CADA arte
(BerryWallet por etiqueta, CardTrader por blueprint) en el paginador de /buscar.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import threading
import time

import requests
from PIL import Image

import config

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
}

_SESSION = requests.Session()
_SESSION.headers.update(UA)

_DIST_MAX = 16       # distancia máxima aceptada para considerar que hay coincidencia
_MARGEN = 4          # la mejor debe ser al menos así de mejor que la segunda


def _url_imagen_bp(bp: dict) -> str:
    """URL de la imagen COMPLETA del blueprint (el preview se degrada y las
    distancias dHash suben mucho: p. ej. OP13-118 Wanted 26 bits vs 1 con la
    imagen completa)."""
    img = bp.get("image") or {}
    if img.get("url"):
        return "https://cardtrader.com" + img["url"]
    return bp.get("image_url") or ""


def _dhash(data: bytes, size: int = 16, crop: float = 0.62) -> int | None:
    """Difference-hash del recorte central (zona del arte) de una imagen."""
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        cw, ch = int(w * crop), int(h * crop)
        x0, y0 = (w - cw) // 2, (h - ch) // 2
        img = img.crop((x0, y0, x0 + cw, y0 + ch)).convert("L").resize((size + 1, size))
        px = list(img.getdata())
        bits = 0
        for row in range(size):
            base = row * (size + 1)
            for col in range(size):
                bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
        return bits
    except Exception:
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _descargar(url: str, timeout: int = 20) -> bytes | None:
    try:
        r = _SESSION.get(url, timeout=timeout)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        return None
    return None


def _normaliza(codigo: str) -> str:
    return "".join(ch for ch in (codigo or "").upper() if ch.isalnum())


def _medio_url(bp: dict) -> str:
    """Parte del nombre de la imagen entre el nombre de la carta y el código de
    set: 'monkey-d-luffy-secret-rare-alternate-art-op-13-...' -> la parte sin el
    código de set (trabaja solo con el nombre de archivo, no la ruta completa)."""
    url = _url_imagen_bp(bp)
    nombre = url.rsplit("/", 1)[-1]
    if nombre.startswith("preview_"):
        nombre = nombre[len("preview_"):]
    nombre = re.sub(r"\.(?:jpe?g|png|webp)$", "", nombre, flags=re.IGNORECASE)
    m = re.search(r"(.+?)-(?:op|st|eb|pr|don|p|promo)-\d+", nombre)
    return m.group(1) if m else nombre


def _etiqueta_arte(bp: dict, base_bp: dict | None) -> str:
    """Etiqueta legible del arte: tokens del nombre de la imagen que no están en
    el blueprint base (p. ej. 'Manga Panel Alternate Art'). Vacío si es la base."""
    a = _medio_url(bp)
    if not a:
        return ""
    if base_bp is None:
        return ""
    b = _medio_url(base_bp)
    if not b or a == b:
        return ""
    tokens_b = set(re.split(r"-", b))
    diff = [t for t in re.split(r"-", a) if t and t not in tokens_b]
    return " ".join(x.capitalize() for x in diff) if diff else ""


class ArteMatcher:
    """Resuelve y cachea (memoria + SQLite) qué blueprint de CardTrader es cada
    variante de una carta, comparando las imágenes."""

    def __init__(self, ct, cards, db_path: str | None = None) -> None:
        self.ct = ct
        self.cards = cards
        self.db_path = db_path or config.ARTS_DB_PATH
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        try:
            with sqlite3.connect(self.db_path) as con:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS artes ("
                    "card_id TEXT PRIMARY KEY, blueprint_id INTEGER, "
                    "label TEXT, updated TEXT)")
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------

    def mapa_de_carta(self, card_id: str) -> dict[str, dict] | None:
        """Devuelve {variante_id: {"blueprint_id": int, "label": str}} para todas
        las variantes de la carta (la base incluida). None si no se puede mapear."""
        base = re.sub(r"_[pr]\d+$", "", (card_id or "").upper(), flags=re.IGNORECASE)
        try:
            variantes = self.cards.variants_of(base)
        except Exception:
            return None
        if not variantes or len(variantes) <= 1:
            return None
        vids = [v.get("id") or "" for v in variantes]
        vids_upper = [v.upper() for v in vids]

        with self._lock:
            if base in self._cache:
                return self._cache[base]

        mapa = self._persistidas(vids_upper)
        if mapa and all(v.upper() in mapa for v in vids):
            with self._lock:
                self._cache[base] = mapa
            return mapa

        try:
            nuevo = self._emparejar(base, variantes)
        except Exception:
            nuevo = None
        if nuevo:
            self._guardar(nuevo)
            with self._lock:
                self._cache[base] = nuevo
        return nuevo

    # ------------------------------------------------------------------

    def _persistidas(self, ids: list[str]) -> dict:
        out: dict[str, dict] = {}
        if not ids:
            return out
        try:
            with sqlite3.connect(self.db_path) as con:
                con.row_factory = sqlite3.Row
                ph = ",".join("?" * len(ids))
                for r in con.execute(
                        f"SELECT * FROM artes WHERE card_id IN ({ph})", ids):
                    out[r["card_id"]] = {
                        "blueprint_id": r["blueprint_id"], "label": r["label"]}
        except sqlite3.Error:
            pass
        return out

    def _guardar(self, filas: dict[str, dict]) -> None:
        try:
            with sqlite3.connect(self.db_path) as con:
                con.executemany(
                    "INSERT OR REPLACE INTO artes (card_id, blueprint_id, label, updated) "
                    "VALUES (?,?,?,?)",
                    [(cid, a.get("blueprint_id"), a.get("label") or "",
                      time.strftime("%Y-%m-%d %H:%M:%S")) for cid, a in filas.items()])
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------

    def _emparejar(self, base: str, variantes: list[dict]) -> dict | None:
        set_code = base.rsplit("-", 1)[0] if "-" in base else base
        expansion = self.ct._expansion_de(set_code)
        if not expansion:
            return None
        blueprints = self.ct._blueprints_de(expansion["id"])
        if not blueprints:
            return None

        base_norm = _normaliza(base)
        candidatos: list[dict] = []
        for bp in blueprints:
            cn = (bp.get("fixed_properties") or {}).get("collector_number") or ""
            cnn = _normaliza(cn)
            suf = cnn[len(base_norm):] if cnn.startswith(base_norm) else ""
            if cnn == base_norm or (suf.isalpha() and len(suf) == 1):
                candidatos.append(bp)
        if not candidatos:
            return None

        base_bp = next((b for b in candidatos
                        if _normaliza((b.get("fixed_properties") or {})
                                      .get("collector_number") or "") == base_norm),
                       None)

        bp_hash: list[tuple[dict, int]] = []
        for bp in candidatos:
            h = _dhash(_descargar(_url_imagen_bp(bp)) or b"")
            if h is not None:
                bp_hash.append((bp, h))
        if not bp_hash:
            return None

        resultado: dict[str, dict] = {}
        for v in variantes:
            cid = (v.get("id") or "").upper()
            data = _descargar(v.get("image_url") or "")
            h = _dhash(data or b"") if data else None
            if h is None:
                continue
            ordenado = sorted(bp_hash, key=lambda t: _hamming(h, t[1]))
            mejor, d1 = ordenado[0][0], _hamming(h, ordenado[0][1])
            d2 = _hamming(h, ordenado[1][1]) if len(ordenado) > 1 else d1 + _MARGEN + 1
            if d1 <= _DIST_MAX and (d2 - d1) >= _MARGEN:
                resultado[cid] = {
                    "blueprint_id": mejor["id"],
                    "label": _etiqueta_arte(mejor, base_bp),
                }
        return resultado or None
