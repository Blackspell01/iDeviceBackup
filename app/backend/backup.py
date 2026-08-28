import asyncio
import logging
import plistlib
from collections import deque
from pathlib import Path
from pymobiledevice3.lockdown import create_using_tcp
from pymobiledevice3.services.heartbeat import HeartbeatService
from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
from backend import db

BACKUP_DIR = Path("/iPhone")


def archive_info(name, uuid):
    path = BACKUP_DIR / name / uuid / "Info.plist"
    if not path.exists():
        return None
    info = plistlib.loads(path.read_bytes())
    return {
        "last_backup": info.get("Last Backup Date"),
        "product_type": info.get("Product Type"),
        "product_version": info.get("Product Version"),
    }


async def validate_pairing(dev, record):
    """Prüft, ob das Gerät den gespeicherten Pairing Record noch akzeptiert."""
    lockdown = None
    try:
        async with asyncio.timeout(15):
            lockdown = await create_using_tcp(
                hostname=dev["ip"], identifier=dev["uuid"], pair_record=record, autopair=False,
            )
            valid = lockdown.paired
    except Exception as e:
        return {"error": str(e) or "Gerät nicht erreichbar"}
    finally:
        if lockdown:
            await lockdown.close()
    return {"error": None if valid else "Record wird vom Gerät abgelehnt"}


class Backup:
    def __init__(self):
        self.task = None
        self.device = None
        self.progress = 0.0
        self.info = None
        self.error = None
        self.messages = deque(maxlen=500)
        self.subscribers: set[asyncio.Queue] = set()

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def status(self):
        return {
            "running": self.running,
            "device": self.device,
            "progress": self.progress,
            "device_info": self.info,
            "error": self.error,
        }

    def payload(self, *log):
        return {"status": self.status(), "log": list(log)}

    def publish(self, *log):
        for queue in self.subscribers:
            queue.put_nowait(self.payload(*log))

    def log(self, message):
        self.messages.append(message)
        self.publish(message)

    def start(self, name):
        self.device, self.progress, self.info, self.error = name, 0.0, None, None
        self.messages.clear()
        self.task = asyncio.create_task(self._run(db.get_device(name), db.get_pair_record(name)))
        self.task.add_done_callback(lambda _: self.publish())
        self.publish()

    def cancel(self):
        if self.running:
            self.task.cancel()

    async def _heartbeat(self, lockdown):
        await HeartbeatService(lockdown).start()

    def _progress(self, percent):
        value = round(float(percent), 1)
        if value != self.progress:
            self.progress = value
            self.publish()

    async def _run(self, dev, record):
        lockdown = heartbeat = None
        try:
            logging.info("Verbinde mit %s", dev["ip"])
            lockdown = await create_using_tcp(
                hostname=dev["ip"], identifier=dev["uuid"], pair_record=record,
                autopair=False, keep_alive=True,
            )
            self.info = {
                "model": await lockdown.get_value(key="ProductType"),
                "version": await lockdown.get_value(key="ProductVersion"),
            }
            self.publish()
            logging.info("Verbunden: %s · iOS %s", self.info["model"], self.info["version"])

            heartbeat = asyncio.create_task(self._heartbeat(lockdown))
            await asyncio.sleep(3)

            async with Mobilebackup2Service(lockdown) as client:
                logging.info("Backup gestartet")
                await client.backup(full=False, backup_directory=BACKUP_DIR / dev["name"],
                                    progress_callback=self._progress)
            self.progress = 100.0
            logging.info("Backup abgeschlossen")
        except Exception as e:
            logging.exception("Backup fehlgeschlagen")
            self.error = str(e) or type(e).__name__
        finally:
            if heartbeat:
                heartbeat.cancel()
            if lockdown:
                await lockdown.close()


backup = Backup()
