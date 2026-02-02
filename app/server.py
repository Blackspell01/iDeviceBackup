#!/usr/bin/env python3
"""iPhone Backup Manager - minimal HTTP server"""

import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# === CONFIG ===
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
LOG_FILE = "./logs/backup.log"
PAIR_RECORD_DIR = "/var/lib/lockdown"

# Ensure log directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

DEVICES = {
    "Simon": {"ip": "192.168.188.201", "pair_record": "00008120-0016390A2100201E"},
    "Thomas": {"ip": "192.168.188.67", "pair_record": "00008110-001109460CF1801E"},
    "Jasmin": {"ip": "192.168.188.30", "pair_record": "00008101-00196C3C0206001E"},
    "Petra": {"ip": "192.168.188.69", "pair_record": "00008110-000868AE2212801E"},
}

STATE = {"running": False, "device": None, "progress": 0}
STATE_LOCK = threading.Lock()
STOP = threading.Event()


def clear_logs():
    with open(LOG_FILE, "w") as f:
        f.write("")
        f.flush()
        os.fsync(f.fileno())


def get_status():
    with STATE_LOCK:
        return {"running": STATE["running"], "device": STATE["device"], "progress": STATE["progress"]}


def set_status(running, device=None, progress=None):
    with STATE_LOCK:
        STATE["running"] = running
        STATE["device"] = device
        if progress is not None:
            STATE["progress"] = progress


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


def run_backup(device_name):
    device = DEVICES[device_name]

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
                device["ip"],
                "--pair-record-id",
                device["pair_record"],
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        threading.Thread(target=stream_output, args=(usbmuxd, "usbmuxd: "), daemon=True).start()
        time.sleep(5)

        backup = subprocess.Popen(
            ["stdbuf", "-oL", "idevicebackup2", "backup", "-n", "/iPhone"],
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
        if BASE_URL and path.startswith(BASE_URL):
            path = path[len(BASE_URL) :]
        return path

    def do_GET(self):
        path = self._path()

        if path in ("", "/", "/index.html"):
            with open("./index.html", "r") as f:
                html = f.read()
            if BASE_URL:
                html = html.replace("</head>", f"<script>window.BASE_URL = \"{BASE_URL}\";</script></head>")
            return self._html(html)

        if path == "/api/status":
            return self._json(get_status())

        if path == "/api/progress":
            with STATE_LOCK:
                return self._json({"progress": STATE["progress"]})

        if path == "/api/devices":
            return self._json(DEVICES)

        if path == "/api/my-ip":
            client_ip = get_client_ip(self.headers, self.client_address)
            return self._json({"ip": client_ip})

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
            if not device_name or device_name not in DEVICES:
                return self._json({"error": "Invalid device"}, 400)
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
            if not device_name or device_name not in DEVICES:
                return self._json({"error": "Invalid device"}, 400)
            if not content:
                return self._json({"error": "No content provided"}, 400)
            try:
                record_id = DEVICES[device_name]["pair_record"]
                target = f"{PAIR_RECORD_DIR}/{record_id}.plist"
                with open(target, "w") as f:
                    f.write(content)
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
    port = int(os.environ.get("PORT", "8502"))
    set_status(False)
    clear_logs()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"🚀 iPhone Backup Manager läuft auf http://0.0.0.0:{port}")
    if BASE_URL:
        print(f"📍 Base URL: {BASE_URL}")
    server.serve_forever()


if __name__ == "__main__":
    main()
