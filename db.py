"""Base de datos SQLite: préstamos de cartas y colecciones.

Los datos están aislados por servidor (guild_id) y por usuario (owner_id),
lo que permite que el bot funcione en varios servidores sin mezclar nada.
"""
import sqlite3

import config

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH)
        _conn.row_factory = sqlite3.Row
        init_db()
    return _conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS loans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER NOT NULL,
            lender_id   INTEGER NOT NULL,
            borrower_id INTEGER NOT NULL,
            card_code   TEXT    NOT NULL,
            card_name   TEXT,
            note        TEXT,
            lent_at     TEXT DEFAULT (datetime('now')),
            returned_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_loans_guild ON loans(guild_id);
        CREATE INDEX IF NOT EXISTS idx_loans_active ON loans(guild_id, returned_at);

        CREATE TABLE IF NOT EXISTS collections (
            guild_id  INTEGER NOT NULL,
            owner_id  INTEGER NOT NULL,
            card_code TEXT    NOT NULL,
            card_name TEXT,
            qty       INTEGER NOT NULL DEFAULT 1,
            added_at  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (guild_id, owner_id, card_code)
        );
        CREATE INDEX IF NOT EXISTS idx_col_guild ON collections(guild_id);
        """
    )
    conn.commit()


# ----------------------------- Préstamos -----------------------------

def add_loan(guild_id: int, lender_id: int, borrower_id: int,
             card_code: str, card_name: str, note: str | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO loans (guild_id, lender_id, borrower_id, card_code, card_name, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (guild_id, lender_id, borrower_id, card_code, card_name, note),
    )
    conn.commit()
    return cur.lastrowid


def return_loan(guild_id: int, loan_id: int, user_id: int) -> bool:
    """Marca como devuelto un préstamo. Solo quien prestó o quien recibió puede devolverlo."""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE loans SET returned_at = datetime('now') "
        "WHERE id = ? AND guild_id = ? AND returned_at IS NULL "
        "AND (lender_id = ? OR borrower_id = ?)",
        (loan_id, guild_id, user_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def return_loans_by_card(guild_id: int, card_code: str, borrower_id: int, user_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE loans SET returned_at = datetime('now') "
        "WHERE guild_id = ? AND card_code = ? AND borrower_id = ? "
        "AND returned_at IS NULL AND (lender_id = ? OR borrower_id = ?)",
        (guild_id, card_code, borrower_id, user_id, user_id),
    )
    conn.commit()
    return cur.rowcount


def list_loans(guild_id: int, *, user_id: int | None = None,
               lender_id: int | None = None, borrower_id: int | None = None,
               active_only: bool = True, limit: int = 50):
    conn = get_conn()
    sql = "SELECT * FROM loans WHERE guild_id = ?"
    args: list = [guild_id]
    if active_only:
        sql += " AND returned_at IS NULL"
    if user_id is not None:
        sql += " AND (lender_id = ? OR borrower_id = ?)"
        args += [user_id, user_id]
    if lender_id is not None:
        sql += " AND lender_id = ?"
        args.append(lender_id)
    if borrower_id is not None:
        sql += " AND borrower_id = ?"
        args.append(borrower_id)
    sql += " ORDER BY lent_at DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


# ----------------------------- Colección -----------------------------

def add_to_collection(guild_id: int, owner_id: int, card_code: str,
                      card_name: str, qty: int = 1) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO collections (guild_id, owner_id, card_code, card_name, qty) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, owner_id, card_code) "
        "DO UPDATE SET qty = qty + excluded.qty, card_name = excluded.card_name",
        (guild_id, owner_id, card_code, card_name, qty),
    )
    conn.commit()


def remove_from_collection(guild_id: int, owner_id: int, card_code: str, qty: int = 1) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT qty FROM collections WHERE guild_id = ? AND owner_id = ? AND card_code = ?",
        (guild_id, owner_id, card_code),
    ).fetchone()
    if row is None:
        return 0
    remaining = row["qty"] - qty
    if remaining <= 0:
        conn.execute(
            "DELETE FROM collections WHERE guild_id = ? AND owner_id = ? AND card_code = ?",
            (guild_id, owner_id, card_code),
        )
        conn.commit()
        return row["qty"]
    conn.execute(
        "UPDATE collections SET qty = ? WHERE guild_id = ? AND owner_id = ? AND card_code = ?",
        (remaining, guild_id, owner_id, card_code),
    )
    conn.commit()
    return qty


def list_collection(guild_id: int, owner_id: int, *, card_code: str | None = None,
                    limit: int = 300):
    conn = get_conn()
    sql = "SELECT * FROM collections WHERE guild_id = ? AND owner_id = ?"
    args: list = [guild_id, owner_id]
    if card_code:
        sql += " AND card_code LIKE ?"
        args.append(f"%{card_code.upper()}%")
    sql += " ORDER BY card_code LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def collection_count(guild_id: int, owner_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(qty), 0) AS total "
        "FROM collections WHERE guild_id = ? AND owner_id = ?",
        (guild_id, owner_id),
    ).fetchone()
    return row["n"], row["total"]


def import_collection(guild_id: int, owner_id: int, items: list[tuple[str, str, int]]) -> int:
    conn = get_conn()
    n = 0
    for code, name, qty in items:
        conn.execute(
            "INSERT INTO collections (guild_id, owner_id, card_code, card_name, qty) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, owner_id, card_code) "
            "DO UPDATE SET qty = qty + excluded.qty, card_name = excluded.card_name",
            (guild_id, owner_id, code.upper(), name or "", qty),
        )
        n += 1
    conn.commit()
    return n
