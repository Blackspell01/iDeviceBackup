import os
import socket
import threading
from datetime import datetime

# === CONFIG ===
BASE_URL = os.environ.get("BASE_URL", "")
PORT = int(os.environ.get("PORT", "89"))
LOG_FILE = "./log.txt"
DB_FILE = "./database.sqlite"
FRONTEND_DIR = "./frontend"
BACKUP_DIR = "/iPhone"

os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)


STATE = {
    "running": False,
    "device": None,
    "progress": 0.0,
    "device_info": None,
    "error": None,
}
STATE_LOCK = threading.Lock()


def get_status():
    with STATE_LOCK:
        return dict(STATE)


def start_run(device: str):
    with STATE_LOCK:
        STATE.update(
            running=True, device=device, progress=0.0, device_info=None, error=None
        )


def finish_run():
    with STATE_LOCK:
        STATE["running"] = False


def set_progress(percent: float):
    with STATE_LOCK:
        STATE["progress"] = round(float(percent), 1)


def set_device_info(info: dict):
    with STATE_LOCK:
        STATE["device_info"] = info


def set_error(message: str):
    with STATE_LOCK:
        STATE["error"] = message


def log_line(text):
    timestamp = datetime.now().strftime("%H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {text}\n")


def clear_logs():
    with open(LOG_FILE, "w") as f:
        f.write("")
        f.flush()
        os.fsync(f.fileno())


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
