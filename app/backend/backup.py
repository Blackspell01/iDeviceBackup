import os
import re
import plistlib
import subprocess
import threading
import time
from backend.config import PAIR_RECORD_DIR, BACKUP_DIR, STATE, STATE_LOCK, STOP, set_status, log_line
from backend.database import get_device, _set_system_config, _get_system_config

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
    dev = get_device(user)
    if not dev or not dev.get("uuid"):
        raise RuntimeError("uuid missing for user")
    target = f"{PAIR_RECORD_DIR}/{dev['uuid']}.plist"

    from backend.database import _db_connect

    with _db_connect() as conn:
        row = conn.execute("SELECT key FROM pair_records WHERE name=?", (user,)).fetchone()
        raw_content = row[0] if row and row[0] else None
    if not raw_content:
        raise RuntimeError("no raw pair_record content in DB")
    if not isinstance(raw_content, (bytes, bytearray)):
        raw_content = str(raw_content).encode("utf-8")
    with open(target, "wb") as f:
        f.write(raw_content)


def read_backup_info(device_name: str):
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


def stop_backup(reason: str = None):
    STOP.set()
    kill_processes()
    set_status(False)
    if reason:
        log_line(reason)


def is_connection_lost(line: str) -> bool:
    patterns = [
        r"Lost connection to device",
        r"Failed to start WIFIDevice",
        r"Failed to add device",
        r"Connection attempt .* failed",
    ]
    return any(re.search(p, line) for p in patterns)


def is_systemconfig_regenerated(line: str) -> bool:
    patterns = [
        r"regenerating SystemConfiguration",
        r"Failed to get SystemBuid",
    ]
    return any(re.search(p, line) for p in patterns)


def stream_output(process, prefix: str = ""):
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
        if prefix.startswith("usbmuxd") and is_systemconfig_regenerated(line):
            try:
                time.sleep(0.5)
                import_system_config_from_fs()
            except Exception as e:
                log_line(f"SystemConfiguration-Import fehlgeschlagen: {e}")


def run_backup(device_name: str):
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