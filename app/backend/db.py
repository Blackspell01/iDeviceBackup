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
            "CREATE TABLE IF NOT EXISTS devices ("
            "id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, ip TEXT, uuid TEXT, key BLOB)"
        )


def _device(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "ip": row["ip"],
        "uuid": row["uuid"],
        "paired": bool(row["paired"]),
    }


def list_devices():
    with connect() as c:
        rows = c.execute(
            "SELECT id, name, ip, uuid, key IS NOT NULL AS paired "
            "FROM devices ORDER BY name"
        ).fetchall()
    return [_device(r) for r in rows]


def get_device(device_id):
    with connect() as c:
        row = c.execute(
            "SELECT id, name, ip, uuid, key IS NOT NULL AS paired "
            "FROM devices WHERE id=?", (device_id,)
        ).fetchone()
    return _device(row) if row else None


def create_device(name):
    with connect() as c:
        return c.execute("INSERT INTO devices(name) VALUES(?)", (name,)).lastrowid


def update_device(device_id, name=None, ip=None, uuid=None):
    sets, args = [], []
    if name:
        sets.append("name=?")
        args.append(name)
    if ip is not None:
        sets.append("ip=?")
        args.append(ip)
    if uuid is not None:
        sets.append("uuid=?")
        args.append(uuid)
    if not sets:
        return
    args.append(device_id)
    with connect() as c:
        c.execute(f"UPDATE devices SET {', '.join(sets)} WHERE id=?", args)


def delete_device(device_id):
    with connect() as c:
        c.execute("DELETE FROM devices WHERE id=?", (device_id,))


def set_pair_record(device_id, raw):
    with connect() as c:
        c.execute("UPDATE devices SET key=? WHERE id=?", (raw, device_id))


def get_pair_record(device_id):
    with connect() as c:
        row = c.execute(
            "SELECT key FROM devices WHERE id=?", (device_id,)
        ).fetchone()
    return plistlib.loads(row["key"]) if row and row["key"] else None
