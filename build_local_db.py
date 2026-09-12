"""Construye el catálogo local `optcg_cards.db` a partir del output del scraper
del proyecto optcg-api (MIT): `data/cards.json` + `data/sets.json`.

El bot usa esta base (CardDataLocal) cuando `OPTCG_API_KEY` está vacía: /buscar
y /listado funcionan con el catálogo completo, sin claves ni red.

Uso:
  python build_local_db.py [ruta_cards.json] [ruta_sets.json] [ruta_db]

Defectos:  ..\\optcg-api\\data\\cards.json   ..\\optcg-api\\data\\sets.json   optcg_cards.db
"""
from __future__ import annotations

import json
import sqlite3
import sys
import os
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def main() -> None:
    cards_path = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI.parent / "optcg-api" / "data" / "cards.json"
    sets_path = Path(sys.argv[2]) if len(sys.argv) > 2 else AQUI.parent / "optcg-api" / "data" / "sets.json"
    db_path = Path(sys.argv[3]) if len(sys.argv) > 3 else AQUI / "optcg_cards.db"

    with open(cards_path, encoding="utf-8") as f:
        cards = json.load(f)
    sets = []
    if sets_path.exists():
        with open(sets_path, encoding="utf-8") as f:
            sets = json.load(f)
    print(f"📦 {len(cards)} cartas · {len(sets)} sets -> {db_path}")

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE cards (
            id TEXT PRIMARY KEY,
            base_id TEXT,
            parallel INTEGER DEFAULT 0,
            variant_type TEXT,
            name TEXT,
            set_id TEXT,
            pack_id TEXT,
            rarity TEXT,
            finish TEXT,
            category TEXT,
            image_url TEXT,
            colors TEXT,
            cost INTEGER,
            power INTEGER,
            counter INTEGER,
            attributes TEXT,
            types TEXT,
            effect TEXT,
            trigger TEXT
        );
        CREATE TABLE sets (
            id TEXT PRIMARY KEY,
            pack_id TEXT,
            label TEXT,
            count INTEGER
        );
        CREATE INDEX idx_cards_set ON cards(set_id);
        CREATE INDEX idx_cards_name ON cards(name);
    """)

    conn.executemany(
        """INSERT INTO sets (id, pack_id, label, count) VALUES (?, ?, ?, ?)""",
        [(s.get("set_id"), s.get("pack_id"), s.get("label"), s.get("count")) for s in sets],
    )

    filas = []
    vistos: set[str] = set()
    duplicados = 0
    for c in cards:
        cid = c.get("id")
        if not cid or cid in vistos:
            duplicados += 1
            continue
        vistos.add(cid)
        filas.append((
            c.get("id"), c.get("base_id"),
            1 if c.get("parallel") else 0,
            c.get("variant_type"), c.get("name"),
            c.get("set_id"), c.get("pack_id"),
            c.get("rarity"), c.get("finish"), c.get("category"),
            c.get("image_url"),
            json.dumps(c.get("colors")) if c.get("colors") else None,
            c.get("cost"), c.get("power"), c.get("counter"),
            json.dumps(c.get("attributes")) if c.get("attributes") else None,
            json.dumps(c.get("types")) if c.get("types") else None,
            c.get("effect"), c.get("trigger"),
        ))
    conn.executemany(
        """INSERT INTO cards (id, base_id, parallel, variant_type, name, set_id, pack_id,
                              rarity, finish, category, image_url, colors, cost, power,
                              counter, attributes, types, effect, trigger)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        filas,
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    s = conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
    conn.close()
    print(f"✅ Base creada: {n} cartas · {s} sets (ignorados {duplicados} duplicados)")
    print(f"   {db_path}")


if __name__ == "__main__":
    main()
