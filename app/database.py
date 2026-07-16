import sqlite3
from datetime import datetime

from config import DATABASE_PATH
from security import decrypt_text, encrypt_text


def utcnow():
    return datetime.utcnow()


def iso(dt):
    return dt.isoformat(timespec="seconds")


def get_connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS check_history_local (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_at TEXT NOT NULL,
                total INTEGER NOT NULL,
                ok_count INTEGER NOT NULL,
                problem_count INTEGER NOT NULL,
                max_discount REAL NOT NULL,
                mode TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )


def _set_setting(key, value):
    now = iso(utcnow())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, now),
        )


def _get_setting(key):
    with get_connection() as conn:
        row = conn.execute("SELECT value, updated_at FROM app_settings WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def save_app_setting(key, value):
    _set_setting(key, str(value))


def get_app_setting(key, default=None):
    row = _get_setting(key)
    return row["value"] if row else default


def save_wb_token(token):
    save_marketplace_token("wildberries", token)


def get_wb_token():
    return get_marketplace_token("wildberries")


def get_wb_token_hint():
    return get_marketplace_token_hint("wildberries")


def save_marketplace_token(marketplace_id, token):
    token_hint = token[:6] + "..." + token[-4:] if len(token) > 12 else "заполнен"
    _set_setting(f"{marketplace_id}_token_encrypted", encrypt_text(token))
    _set_setting(f"{marketplace_id}_token_hint", token_hint)


def get_marketplace_token(marketplace_id):
    row = _get_setting(f"{marketplace_id}_token_encrypted")
    if not row:
        return None
    return decrypt_text(row["value"])


def get_marketplace_token_hint(marketplace_id):
    row = _get_setting(f"{marketplace_id}_token_hint")
    return {"token_hint": row["value"], "updated_at": row["updated_at"]} if row else None


def add_history(stats, mode, message):
    now = iso(utcnow())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO check_history_local (checked_at, total, ok_count, problem_count, max_discount, mode, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (now, stats["total"], stats["ok"], stats["problem"], stats["max_discount"], mode, message),
        )


def list_history(limit=50):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM check_history_local ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_history_dates(limit=90):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT substr(checked_at, 1, 10) AS date, COUNT(*) AS count
            FROM check_history_local
            GROUP BY substr(checked_at, 1, 10)
            ORDER BY date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_history_by_date(date, limit=200):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM check_history_local
            WHERE substr(checked_at, 1, 10) = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (date, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_empty_checks():
    with get_connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM check_history_local
            WHERE message = ?
            """,
            ("Все скидки в норме. Исправлять нечего.",),
        )
        return cursor.rowcount
