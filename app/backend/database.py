import sqlite3
import plistlib
from typing import Optional
from backend.config import DB_FILE


def _db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_main_tables():
    with _db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pair_records (
                name TEXT PRIMARY KEY,
                ip TEXT,
                uuid TEXT,
                key BLOB
            )
            """
        )


def set_device(name: str, ip: Optional[str] = None, uuid: Optional[str] = None):
    _ensure_main_tables()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT name, ip, uuid FROM pair_records WHERE name = ?", (name,)
        ).fetchone()
        new_ip = ip if ip is not None else (row["ip"] if row else None)
        new_uuid = uuid if uuid is not None else (row["uuid"] if row else None)
        conn.execute(
            "INSERT INTO pair_records(name, ip, uuid) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET ip=excluded.ip, uuid=excluded.uuid",
            (name, new_ip, new_uuid),
        )


def rename_device(old: str, new: str):
    _ensure_main_tables()
    with _db_connect() as conn:
        conn.execute("UPDATE pair_records SET name=? WHERE name=?", (new, old))


def delete_device(name: str):
    _ensure_main_tables()
    with _db_connect() as conn:
        conn.execute("DELETE FROM pair_records WHERE name=?", (name,))


def get_device(name: str):
    _ensure_main_tables()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT name, ip, uuid FROM pair_records WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        return {"name": row["name"], "ip": row["ip"], "uuid": row["uuid"]}


def list_devices():
    _ensure_main_tables()
    with _db_connect() as conn:
        rows = conn.execute(
            "SELECT name, ip, uuid FROM pair_records ORDER BY name"
        ).fetchall()
    return {r["name"]: {"ip": r["ip"], "pair_record": r["uuid"]} for r in rows}


def set_pair_record_raw(name: str, raw):
    _ensure_main_tables()
    raw_bytes = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT name FROM pair_records WHERE name=?", (name,)
        ).fetchone()
        if row:
            conn.execute("UPDATE pair_records SET key=? WHERE name=?", (raw_bytes, name))
        else:
            conn.execute(
                "INSERT INTO pair_records(name, key) VALUES(?,?)", (name, raw_bytes)
            )


def get_pair_record(name: str):
    _ensure_main_tables()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT key FROM pair_records WHERE name=?", (name,)
        ).fetchone()
    if not row or not row["key"]:
        return None
    raw = row["key"]
    if not isinstance(raw, (bytes, bytearray)):
        raw = str(raw).encode("utf-8")
    return plistlib.loads(raw)