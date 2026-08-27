import plistlib
import sqlite3
from pathlib import Path

DB_FILE = Path("database.sqlite")


def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with connect() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS pair_records ("
            "name TEXT PRIMARY KEY, ip TEXT, uuid TEXT, key BLOB)"
        )


def _device(row):
    return {
        "name": row["name"],
        "ip": row["ip"],
        "uuid": row["uuid"],
        "paired": bool(row["paired"]),
    }


def list_devices():
    with connect() as c:
        rows = c.execute(
            "SELECT name, ip, uuid, key IS NOT NULL AS paired "
            "FROM pair_records ORDER BY name"
        ).fetchall()
    return [_device(r) for r in rows]


def get_device(name):
    with connect() as c:
        row = c.execute(
            "SELECT name, ip, uuid, key IS NOT NULL AS paired "
            "FROM pair_records WHERE name=?", (name,)
        ).fetchone()
    return _device(row) if row else None


def create_device(name):
    with connect() as c:
        c.execute("INSERT INTO pair_records(name) VALUES(?)", (name,))


def update_device(name, new_name=None, ip=None, uuid=None):
    sets, args = [], []
    if new_name:
        sets.append("name=?")
        args.append(new_name)
    if ip is not None:
        sets.append("ip=?")
        args.append(ip)
    if uuid is not None:
        sets.append("uuid=?")
        args.append(uuid)
    if not sets:
        return
    args.append(name)
    with connect() as c:
        c.execute(f"UPDATE pair_records SET {', '.join(sets)} WHERE name=?", args)


def delete_device(name):
    with connect() as c:
        c.execute("DELETE FROM pair_records WHERE name=?", (name,))


def set_pair_record(name, raw):
    with connect() as c:
        c.execute("UPDATE pair_records SET key=? WHERE name=?", (raw, name))


def get_pair_record(name):
    with connect() as c:
        row = c.execute(
            "SELECT key FROM pair_records WHERE name=?", (name,)
        ).fetchone()
    return plistlib.loads(row["key"]) if row and row["key"] else None
