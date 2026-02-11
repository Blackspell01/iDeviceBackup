import json
import os
import mimetypes
import time
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse
from backend.config import BASE_URL, FRONTEND_DIR, LOG_FILE, PAIR_RECORD_DIR, STATE_LOCK, get_status, set_status, log_line, clear_logs, get_client_ip
from backend.backup import write_pair_record_file, read_backup_info, stop_backup, run_backup
from backend.database import get_device, list_devices, set_device, set_pair_record_raw, _db_connect


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
            html = html.replace("</head>", f'<script>window.BASE_URL = "{BASE_URL}";</script></head>', 1)
            if BASE_URL:
                html = html.replace('href="styles.css"', f'href="{BASE_URL}/styles.css"')
                html = html.replace('src="app.js"', f'src="{BASE_URL}/app.js"')
            return self._html(html)

        # Static files
        if path.endswith(('.css', '.js', '.html')):
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
                return self._json({"progress": get_status()["progress"]})

        if path == "/api/devices":
            query = urlparse(self.path).query
            params = dict(part.split('=') for part in query.split('&') if '=' in part)
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
            params = dict(part.split('=') for part in query.split('&') if '=' in part)
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
                    size = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0
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
