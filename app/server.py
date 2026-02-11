import json
import os
import plistlib
import re
import socket
import sqlite3
import subprocess
import threading
import time
import mimetypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# === CONFIG ===
BASE_URL = os.environ.get("BASE_URL", "")
LOG_FILE = "./log.txt"
DB_FILE = "./database.sqlite"
FRONTEND_DIR = "./frontend"
PAIR_RECORD_DIR = "/var/lib/lockdown"
BACKUP_DIR = "/iPhone"

# Ensure directories exist
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
os.makedirs(PAIR_RECORD_DIR, exist_ok=True)


STATE = {"running": False, "device": None, "progress": 0, "device_info": None}
STATE_LOCK = threading.Lock()
STOP = threading.Event()


# === DATABASE (single-table design) ===
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS "SystemConfig.plist" (
                key BLOB
            )
            """
        )

def set_device(name: str, ip: str | None = None, uuid: str | None = None):
    _ensure_main_tables()
    with _db_connect() as conn:
        row = conn.execute("SELECT name, ip, uuid FROM pair_records WHERE name = ?", (name,)).fetchone()
        cur_ip = row["ip"] if row else None
        cur_uuid = row["uuid"] if row else None
        new_ip = ip if ip is not None else cur_ip
        new_uuid = uuid if uuid is not None else cur_uuid
        conn.execute(
            "INSERT INTO pair_records(name, ip, uuid) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET ip=excluded.ip, uuid=excluded.uuid",
            (name, new_ip, new_uuid),
        )


def get_device(name: str):
    _ensure_main_tables()
    with _db_connect() as conn:
        row = conn.execute("SELECT name, ip, uuid FROM pair_records WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        return {"name": row["name"], "ip": row["ip"], "uuid": row["uuid"]}


def list_devices():
    _ensure_main_tables()
    with _db_connect() as conn:
        rows = conn.execute("SELECT name, ip, uuid FROM pair_records ORDER BY name").fetchall()
    return {r["name"]: {"ip": r["ip"], "pair_record": r["uuid"]} for r in rows}


def set_pair_record_raw(name: str, raw: str | bytes):
    _ensure_main_tables()
    # store as bytes (BLOB); if string provided, encode utf-8
    raw_bytes = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")
    with _db_connect() as conn:
        row = conn.execute("SELECT name FROM pair_records WHERE name=?", (name,)).fetchone()
        if row:
            conn.execute("UPDATE pair_records SET key=? WHERE name=?", (raw_bytes, name))
        else:
            conn.execute("INSERT INTO pair_records(name, key) VALUES(?,?)", (name, raw_bytes))


def _set_system_config(raw: bytes | str):
    _ensure_main_tables()
    raw_bytes = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")
    with _db_connect() as conn:
        conn.execute('DELETE FROM "SystemConfig.plist"')
        conn.execute('INSERT INTO "SystemConfig.plist"(key) VALUES(?)', (raw_bytes,))

def _get_system_config() -> bytes | None:
    _ensure_main_tables()
    with _db_connect() as conn:
        row = conn.execute('SELECT key FROM "SystemConfig.plist" LIMIT 1').fetchone()
        return row[0] if row and row[0] else None

def import_system_config_from_fs():
    path = os.path.join(PAIR_RECORD_DIR, "SystemConfiguration.plist")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = f.read()
            _set_system_config(data)
            log_line("SystemConfiguration.plist aus FS in DB übernommen")
        except Exception as e:
            log_line(f"Konnte SystemConfiguration.plist nicht importieren: {e}")

def export_system_config_to_fs():
    data = _get_system_config()
    if data is None:
        return
    try:
        os.makedirs(PAIR_RECORD_DIR, exist_ok=True)
        with open(os.path.join(PAIR_RECORD_DIR, "SystemConfiguration.plist"), "wb") as f:
            f.write(data)
    except Exception as e:
        log_line(f"SystemConfiguration write failed: {e}")


def write_pair_record_file(user: str):
    """Write current pair_record values from DB to PAIR_RECORD_DIR/<uuid>.plist"""
    dev = get_device(user)
    if not dev or not dev.get("uuid"):
        raise RuntimeError("uuid missing for user")
    target = f"{PAIR_RECORD_DIR}/{dev['uuid']}.plist"

    with _db_connect() as conn:
        row = conn.execute("SELECT key FROM pair_records WHERE name=?", (user,)).fetchone()
        raw_content = row[0] if row and row[0] else None
    if not raw_content:
        raise RuntimeError("no raw pair_record content in DB")
    if not isinstance(raw_content, (bytes, bytearray)):
        raw_content = str(raw_content).encode("utf-8")
    with open(target, "wb") as f:
        f.write(raw_content)


def clear_logs():
    with open(LOG_FILE, "w") as f:
        f.write("")
        f.flush()
        os.fsync(f.fileno())


def get_status():
    with STATE_LOCK:
        return {
            "running": STATE["running"],
            "device": STATE["device"],
            "progress": STATE["progress"],
            "device_info": STATE["device_info"]
        }


def set_status(running, device=None, progress=None, device_info=None):
    with STATE_LOCK:
        STATE["running"] = running
        STATE["device"] = device
        if progress is not None:
            STATE["progress"] = progress
        if device_info is not None:
            STATE["device_info"] = device_info


def log_line(text):
    timestamp = datetime.now().strftime("%H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {text}\n")


def get_local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try:
            sock.close()
        except Exception:
            pass


def get_client_ip(headers, client_address):
    forwarded = headers.get("Forwarded")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(";")]
        for part in parts:
            if part.lower().startswith("for="):
                ip = part[4:].strip().strip('"').strip("'")
                if ip.startswith("[") and ip.endswith("]"):
                    ip = ip[1:-1]
                if ip.count(":") == 1 and ip.rsplit(":", 1)[1].isdigit():
                    ip = ip.rsplit(":", 1)[0]
                if ip in ("127.0.0.1", "::1"):
                    return get_local_ip()
                return ip

    xff = headers.get("X-Forwarded-For")
    if xff:
        ip = xff.split(",")[0].strip()
        if ip in ("127.0.0.1", "::1"):
            return get_local_ip()
        return ip

    xri = headers.get("X-Real-IP")
    if xri:
        if xri in ("127.0.0.1", "::1"):
            return get_local_ip()
        return xri

    ip = client_address[0]
    if ip in ("127.0.0.1", "::1"):
        return get_local_ip()
    return ip


def read_backup_info(device_name):
    dev = get_device(device_name)
    if not dev or not dev.get("uuid"):
        return None
    backup_dir = os.path.join(BACKUP_DIR, dev["uuid"])
    info_path = os.path.join(backup_dir, "Info.plist")
    if not os.path.exists(info_path):
        return None
    try:
        with open(info_path, "rb") as f:
            info = plistlib.load(f)
        
        # Format datetime nicely
        last_backup = info.get("Last Backup Date")
        if last_backup and hasattr(last_backup, 'strftime'):
            last_backup = last_backup.strftime("%d.%m.%Y, %H:%M")
        elif last_backup:
            last_backup = str(last_backup)
        
        return {
            "last_backup_date": last_backup,
            "product_type": info.get("Product Type"),
            "product_version": info.get("Product Version"),
        }
    except Exception:
        return None


def kill_processes():
    subprocess.run(["killall", "-9", "usbmuxd", "idevicebackup2"], capture_output=True)


def stop_backup(reason=None):
    STOP.set()
    kill_processes()
    set_status(False)
    if reason:
        log_line(reason)


def is_connection_lost(line):
    patterns = [
        r"Lost connection to device",
        r"Failed to start WIFIDevice",
        r"Failed to add device",
        r"Connection attempt .* failed",
    ]
    return any(re.search(p, line) for p in patterns)


def is_systemconfig_regenerated(line):
    patterns = [
        r"regenerating SystemConfiguration",
        r"Failed to get SystemBuid",
    ]
    return any(re.search(p, line) for p in patterns)


def stream_output(process, prefix=""):
    for line in iter(process.stdout.readline, ""):
        if STOP.is_set():
            break
        line = line.strip()
        if not line:
            continue
        log_line(f"{prefix}{line}")
        match = re.search(r"(\d+)% Finished", line)
        if match:
            with STATE_LOCK:
                STATE["progress"] = int(match.group(1))
        if prefix.startswith("usbmuxd") and is_connection_lost(line):
            stop_backup("⚠️ Verbindung verloren – Backup automatisch gestoppt")
            break
        # If usbmuxd announces regeneration of SystemConfiguration, import it from FS into DB
        if prefix.startswith("usbmuxd") and is_systemconfig_regenerated(line):
            try:
                # give usbmuxd a moment to write the file
                time.sleep(0.5)
                import_system_config_from_fs()
            except Exception as e:
                log_line(f"SystemConfiguration-Import fehlgeschlagen: {e}")


def run_backup(device_name):
    dev = get_device(device_name)
    if not dev or not dev.get("ip") or not dev.get("uuid"):
        log_line(f"Geräte-Daten fehlen für '{device_name}' (ip/uuid) – Backup abgebrochen")
        return

    STOP.clear()
    try:
        kill_processes()
        time.sleep(1)

        usbmuxd = subprocess.Popen(
            [
                "stdbuf",
                "-oL",
                "usbmuxd",
                "-c",
                dev["ip"],
                "--pair-record-id",
                dev["uuid"],
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        threading.Thread(target=stream_output, args=(usbmuxd, "usbmuxd: "), daemon=True).start()
        time.sleep(5)

        # Get device info
        try:
            result = subprocess.run(
                ["ideviceinfo", "-n"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                info = {}
                for line in lines:
                    if "ProductType:" in line:
                        info["model"] = line.split(":", 1)[1].strip()
                    elif "ProductVersion:" in line and "HumanReadable" not in line:
                        info["version"] = line.split(":", 1)[1].strip()
                if info:
                    set_status(True, device_name, 0, info)
        except Exception as e:
            log_line(f"Device info konnte nicht abgerufen werden: {e}")

        backup = subprocess.Popen(
            ["stdbuf", "-oL", "idevicebackup2", "backup", "-n", BACKUP_DIR],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        stream_output(backup)
        backup.wait()

    except Exception as e:
        log_line(f"Fehler: {e}")

    finally:
        kill_processes()
        set_status(False)
        STOP.clear()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {format % args}")

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(json.dumps(data).encode())
        except BrokenPipeError:
            return

    def _html(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(content.encode())
        except BrokenPipeError:
            return

    def _sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _path(self):
        path = urlparse(self.path).path
        if BASE_URL:
            base_path = urlparse(BASE_URL).path
            if base_path and path.startswith(base_path):
                path = path[len(base_path):]
                if not path.startswith('/'):
                    path = '/' + path
        return path

    def do_GET(self):
        path = self._path()

        # Serve index.html
        if path in ("", "/", "/index.html"):
            index_path = os.path.join(FRONTEND_DIR, "index.html")
            with open(index_path, "r") as f:
                html = f.read()
            # Inject window.BASE_URL for API calls
            html = html.replace("</head>", f'<script>window.BASE_URL = "{BASE_URL}";</script></head>', 1)
            # Fix relative paths for CSS and JS to include BASE_URL
            if BASE_URL:
                html = html.replace('href="styles.css"', f'href="{BASE_URL}/styles.css"')
                html = html.replace('src="app.js"', f'src="{BASE_URL}/app.js"')
            return self._html(html)

        # Serve CSS/JS files directly from frontend root
        if path.endswith(".css") or path.endswith(".js") or path.endswith(".html"):
            file_name = path.lstrip("/")
            file_path = os.path.join(FRONTEND_DIR, file_name)
            
            if not os.path.isfile(file_path):
                return self.send_error(404)
            
            mimetypes.init()
            content_type, _ = mimetypes.guess_type(file_path)
        
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/status":
            return self._json(get_status())

        if path == "/api/progress":
            with STATE_LOCK:
                return self._json({"progress": STATE["progress"]})

        if path == "/api/devices":
            # List devices or fetch single
            query = urlparse(self.path).query
            params = {}
            if query:
                for part in query.split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        params[k] = v
            device_name = params.get("device")
            if device_name:
                dev = get_device(device_name)
                if not dev:
                    return self._json({"error": "Device not found"}, 404)
                return self._json({device_name: {"ip": dev.get("ip"), "pair_record": dev.get("uuid")}})
            return self._json(list_devices())

        if path == "/api/my-ip":
            client_ip = get_client_ip(self.headers, self.client_address)
            return self._json({"ip": client_ip})

        if path == "/api/backup-info":
            query = urlparse(self.path).query
            params = {}
            if query:
                for part in query.split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        params[k] = v
            device_name = params.get("device")
            info = read_backup_info(device_name) if device_name else None
            return self._json({"info": info})

        if path == "/api/logs":
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r") as f:
                    lines = f.readlines()
                    logs = "".join(lines[-200:])
                return self._json({"logs": logs})
            return self._json({"logs": ""})

        if path == "/api/stream":
            self._sse_headers()
            last_size = 0
            last_ping = time.monotonic()
            try:
                if os.path.exists(LOG_FILE):
                    with open(LOG_FILE, "r") as f:
                        lines = f.readlines()
                        logs = "".join(lines[-200:])
                    payload = {"logs": logs}
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                    self.wfile.flush()
                    last_size = os.path.getsize(LOG_FILE)

                while get_status()["running"]:
                    if os.path.exists(LOG_FILE):
                        size = os.path.getsize(LOG_FILE)
                        if size > last_size:
                            with open(LOG_FILE, "r") as f:
                                f.seek(last_size)
                                new_content = f.read()
                            payload = {"logs": new_content}
                            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                            self.wfile.flush()
                            last_size = size

                    if time.monotonic() - last_ping >= 5:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.monotonic()

                    time.sleep(0.5)

                self.wfile.write(b"data: {\"done\": true}\n\n")
                self.wfile.flush()

            except BrokenPipeError:
                pass
            return

        self.send_error(404)

    def do_POST(self):
        path = self._path()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if path == "/api/start":
            device_name = data.get("device")
            if not device_name:
                return self._json({"error": "Invalid device"}, 400)
            dev = get_device(device_name)
            if not dev or not dev.get("ip") or not dev.get("uuid"):
                return self._json({"error": "Device ip/uuid missing"}, 400)
            if get_status()["running"]:
                return self._json({"error": "Backup already running"}, 400)

            clear_logs()
            set_status(True, device_name, 0)
            threading.Thread(target=run_backup, args=(device_name,), daemon=True).start()
            return self._json({"success": True})

        if path == "/api/stop":
            stop_backup("⏹️ Backup manuell gestoppt")
            return self._json({"success": True})

        if path == "/api/update-pair-record":
            device_name = data.get("device")
            content = data.get("content")
            uuid = data.get("uuid")
            ip_override = data.get("ip")
            if not device_name:
                return self._json({"error": "Invalid device"}, 400)
            if not content:
                return self._json({"error": "No content provided"}, 400)
            try:
                # Determine uuid for pair-record filename
                if not uuid:
                    dev = get_device(device_name)
                    uuid = dev.get("uuid") if dev else None
                if not uuid:
                    return self._json({"error": "uuid missing. Provide in request or set via POST /api/devices"}, 400)
                # optionally set/update device record
                if ip_override or uuid:
                    try:
                        set_device(device_name, ip=ip_override, uuid=uuid)
                    except Exception as e:
                        log_line(f"set_device failed: {e}")
                # Store raw content EXACTLY as provided
                set_pair_record_raw(device_name, content)
                # Write plist file from DB
                write_pair_record_file(device_name)
                return self._json({"success": True})
            except Exception as e:
                return self._json({"error": str(e)}, 500)


        if path == "/api/devices":
            # Set device ip/uuid
            device_name = data.get("device")
            new_name = data.get("newName") or data.get("new_name")
            ip = data.get("ip")
            uuid = data.get("uuid")
            if not device_name:
                return self._json({"error": "Missing device"}, 400)
            try:
                # Rename if requested and source exists
                if new_name and new_name != device_name:
                    with _db_connect() as conn:
                        # target name must not exist
                        exists = conn.execute("SELECT 1 FROM pair_records WHERE name=?", (new_name,)).fetchone()
                        if exists:
                            return self._json({"error": "Device name already exists"}, 400)
                        # update source or insert if missing
                        src = conn.execute("SELECT name FROM pair_records WHERE name=?", (device_name,)).fetchone()
                        if src:
                            # update name first (PRIMARY KEY) and attributes atomically
                            conn.execute("UPDATE pair_records SET name=? WHERE name=?", (new_name, device_name))
                            if ip is not None:
                                conn.execute("UPDATE pair_records SET ip=? WHERE name=?", (ip, new_name))
                            if uuid is not None:
                                conn.execute("UPDATE pair_records SET uuid=? WHERE name=?", (uuid, new_name))
                        else:
                            # create new row directly
                            conn.execute("INSERT INTO pair_records(name, ip, uuid) VALUES(?,?,?)", (new_name, ip, uuid))
                    device_name = new_name
                else:
                    set_device(device_name, ip=ip, uuid=uuid)
                dev = get_device(device_name)
                return self._json({"success": True, "device": device_name, "ip": dev.get("ip"), "uuid": dev.get("uuid")})
            except Exception as e:
                return self._json({"error": str(e)}, 500)

        if path == "/api/devices/delete":
            device_name = data.get("device")
            if not device_name:
                return self._json({"error": "Missing device"}, 400)
            try:
                # Read uuid to delete its plist file
                dev = get_device(device_name)
                with _db_connect() as conn:
                    conn.execute("DELETE FROM pair_records WHERE name=?", (device_name,))
                # Remove file if possible
                try:
                    if dev and dev.get("uuid"):
                        path = os.path.join(PAIR_RECORD_DIR, f"{dev['uuid']}.plist")
                        if os.path.exists(path):
                            os.remove(path)
                except Exception as fe:
                    log_line(f"Konnte Pair-Record-Datei nicht löschen: {fe}")
                return self._json({"success": True})
            except Exception as e:
                return self._json({"error": str(e)}, 500)

        self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    port = int(os.environ.get("PORT"))
    set_status(False)
    clear_logs()

    # Ensure DB is available and sync files
    with _db_connect() as _:
        pass
    _ensure_main_tables()

    # write all pair-record files (best-effort)
    for user in list_devices().keys():
        try:
            write_pair_record_file(user)
        except Exception as e:
            log_line(f"Pair record sync skipped for {user}: {e}")
    
    # Sync SystemConfiguration.plist
    # 1) Wenn in DB vorhanden -> in FS schreiben
    # 2) Wenn nicht in DB, aber im FS vorhanden -> in DB importieren
    data = _get_system_config()
    if data is not None:
        try:
            with open(os.path.join(PAIR_RECORD_DIR, "SystemConfiguration.plist"), "wb") as f:
                f.write(data)
        except Exception as e:
            log_line(f"SystemConfiguration write failed: {e}")
    else:
        import_system_config_from_fs()


    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"🚀 iPhone Backup Manager läuft auf http://0.0.0.0:{port}")
    if BASE_URL:
        print(f"📍 Base URL: {BASE_URL}")
    server.serve_forever()


if __name__ == "__main__":
    main()