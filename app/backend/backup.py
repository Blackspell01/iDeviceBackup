import asyncio
import os
import plistlib
import threading
from pymobiledevice3.lockdown import create_using_tcp
from pymobiledevice3.services.heartbeat import HeartbeatService
from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
from backend.config import BACKUP_DIR, log_line, set_progress, set_device_info, set_error, finish_run
from backend.database import get_device, get_pair_record

_INFO_KEYS = {"model": "ProductType", "version": "ProductVersion"}

_ACTIVE = {"loop": None, "task": None}
_LOCK = threading.Lock()


def read_backup_info(device_name: str):
    dev = get_device(device_name)
    if not dev or not dev.get("uuid"):
        return None
    info_path = os.path.join(BACKUP_DIR, dev["uuid"], "Info.plist")
    if not os.path.exists(info_path):
        return None
    try:
        with open(info_path, "rb") as f:
            info = plistlib.load(f)
        last_backup = info.get("Last Backup Date")
        if hasattr(last_backup, "strftime"):
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


def stop_backup(reason: str = None):
    with _LOCK:
        loop, task = _ACTIVE.get("loop"), _ACTIVE.get("task")
    if loop is not None and task is not None and not task.done():
        loop.call_soon_threadsafe(task.cancel)
    else:
        finish_run()
    if reason:
        log_line(reason)


async def _heartbeat(lockdown):
    try:
        await HeartbeatService(lockdown).start()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_line(f"Heartbeat beendet: {type(e).__name__}: {e}")


async def _read_device_info(lockdown):
    info = {}
    for label, key in _INFO_KEYS.items():
        try:
            info[label] = await lockdown.get_value(key=key)
        except Exception:
            pass
    return info


async def _run(device_name: str):
    with _LOCK:
        _ACTIVE["loop"] = asyncio.get_running_loop()
        _ACTIVE["task"] = asyncio.current_task()

    dev = get_device(device_name)
    record = get_pair_record(device_name)
    if not dev or not dev.get("ip") or not dev.get("uuid"):
        log_line(f"❌ Geräte-Daten fehlen für '{device_name}' (IP oder UDID)")
        set_error("Geräte-Daten unvollständig")
        return
    if not record:
        log_line(f"❌ Kein Pair-Record für '{device_name}' hinterlegt")
        set_error("Pair-Record fehlt")
        return

    lockdown = None
    heartbeat = None
    try:
        log_line(f"Verbinde mit {dev['ip']} …")
        lockdown = await create_using_tcp(
            hostname=dev["ip"],
            identifier=dev["uuid"],
            pair_record=record,
            autopair=False,
            keep_alive=True,
        )

        info = await _read_device_info(lockdown)
        if info:
            set_device_info(info)
            log_line(f"Verbunden: {info.get('model', '?')} · iOS {info.get('version', '?')}")

        heartbeat = asyncio.create_task(_heartbeat(lockdown))
        await asyncio.sleep(3)

        async with Mobilebackup2Service(lockdown) as client:
            log_line("Backup gestartet")
            await client.backup(
                full=False,
                backup_directory=BACKUP_DIR,
                progress_callback=set_progress,
            )

        set_progress(100)
        log_line("✅ Backup abgeschlossen")

    except asyncio.CancelledError:
        log_line("⏹️ Abgebrochen")
        raise
    except Exception as e:
        message = f"{type(e).__name__}: {e}"
        log_line(f"❌ {message}")
        set_error(message)
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
        if lockdown is not None:
            try:
                await lockdown.close()
            except Exception:
                pass


def run_backup(device_name: str):
    try:
        asyncio.run(_run(device_name))
    except asyncio.CancelledError:
        pass
    finally:
        with _LOCK:
            _ACTIVE["loop"] = None
            _ACTIVE["task"] = None
        finish_run()