import asyncio
import logging
import plistlib
from collections import deque
from pathlib import Path
from pymobiledevice3.pair_records import PAIRING_RECORD_EXT, create_pairing_records_cache_folder, get_remote_pairing_record_filename
from pymobiledevice3.remote.common import TunnelProtocol
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.remote.tunnel_service import create_core_device_tunnel_service_using_remotepairing, start_tunnel_over_remotepairing
from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
from backend import db

BACKUP_DIR = Path("/iPhone")

class UiLog(logging.Handler):
    def emit(self, record):
        backup.log(self.format(record))

    @classmethod
    def setup(cls):
        logging.basicConfig(level=logging.INFO)
        logging.getLogger().addHandler(cls())


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


async def connect(dev, record):
    """Öffnet den RemotePairing Kanal. Der Record wird abgelegt, wo pymobiledevice3 ihn sucht."""
    path = create_pairing_records_cache_folder() / (
        f"{get_remote_pairing_record_filename(dev['uuid'])}.{PAIRING_RECORD_EXT}"
    )
    path.write_bytes(plistlib.dumps(record))
    return await create_core_device_tunnel_service_using_remotepairing(
        remote_identifier=dev["uuid"], hostname=dev["ip"], port=49152, autopair=False,
    )


async def validate_pairing(dev, record):
    """Prüft, ob das Gerät den gespeicherten Pairing Record noch akzeptiert."""
    service = None
    try:
        async with asyncio.timeout(15):
            service = await connect(dev, record)
    finally:
        if service:
            await service.close()
    return {"error": None}


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

    def _progress(self, percent):
        value = round(float(percent), 1)
        if value != self.progress:
            self.progress = value
            self.publish()

    async def _run(self, dev, record):
        try:
            logging.info("Verbinde mit %s", dev["ip"])
            service = await connect(dev, record)
            async with start_tunnel_over_remotepairing(service, protocol=TunnelProtocol.TCP) as tunnel:
                async with RemoteServiceDiscoveryService((tunnel.address, tunnel.port)) as rsd:
                    self.info = {"model": rsd.product_type, "version": rsd.product_version}
                    self.publish()
                    logging.info("Verbunden: %s · iOS %s", self.info["model"], self.info["version"])

                    async with Mobilebackup2Service(rsd) as client:
                        logging.info("Backup gestartet")
                        await client.backup(full=False, backup_directory=BACKUP_DIR / dev["name"],
                                            progress_callback=self._progress)
            self.progress = 100.0
            logging.info("Backup abgeschlossen")
        except Exception as e:
            logging.exception("Backup fehlgeschlagen")
            self.error = str(e) or type(e).__name__


backup = Backup()
