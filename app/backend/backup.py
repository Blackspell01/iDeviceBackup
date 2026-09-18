import asyncio
import logging
import plistlib
import time
from collections import deque
from datetime import timezone
from pathlib import Path
from pymobiledevice3.pair_records import PAIRING_RECORD_EXT, create_pairing_records_cache_folder, get_remote_pairing_record_filename
from pymobiledevice3.remote.common import TunnelProtocol
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.remote.tunnel_service import create_core_device_tunnel_service_using_remotepairing, start_tunnel_over_remotepairing
from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
from backend import db

BACKUP_DIR = Path("/iPhone")

def counters(iface):
    stats = Path(f"/sys/class/net/{iface}/statistics")
    return int((stats / "rx_bytes").read_text()), int((stats / "tx_bytes").read_text())

class UiLog(logging.Handler):
    def emit(self, record):
        backup.log(record.getMessage().strip())

    @classmethod
    def setup(cls):
        logging.basicConfig(level=logging.INFO)
        logging.getLogger().addHandler(cls())


def archive_info(name, uuid):
    archive = BACKUP_DIR / name / uuid
    path = archive / "Info.plist"
    status = archive / "Status.plist"
    if not path.exists():
        return None
    info = plistlib.loads(path.read_bytes())
    date = plistlib.loads(status.read_bytes())["Date"]
    return {
        "last_backup": date.replace(tzinfo=timezone.utc),
        "product_type": info.get("Product Type"),
        "product_version": info.get("Product Version"),
        "size": sum(f.stat().st_size for f in archive.rglob("*") if f.is_file()),
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
    except Exception as e:
        return {"error": str(e)}
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
        self.failed = False
        self.message = ""
        self.rate = [0.0, 0.0]
        self.history = deque(maxlen=60)
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
            "failed": self.failed,
            "message": self.message,
            "rate": self.rate,
            "history": list(self.history),
        }

    def publish(self):
        for queue in self.subscribers:
            queue.put_nowait(self.status())

    def log(self, message):
        """Ersetzt die aktuell angezeigte Zeile im UI."""
        self.message = message
        self.publish()

    def start(self, device_id):
        dev = db.get_device(device_id)
        self.device, self.progress, self.info = dev["name"], 0.0, None
        self.failed, self.message = False, ""
        self.rate = [0.0, 0.0]
        self.history.clear()
        self.task = asyncio.create_task(self._run(dev, db.get_pair_record(device_id)))
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

    async def _sample(self, iface):
        last, clock = counters(iface), time.monotonic()
        while True:
            await asyncio.sleep(1)
            now, tick = counters(iface), time.monotonic()
            self.rate = [(n - l) / (tick - clock) / 1e6 for n, l in zip(now, last)]   # [rx, tx] in MB/s
            last, clock = now, tick
            self.history.append(self.rate)
            self.publish()

    async def _run(self, dev, record):
        sampler = None
        try:
            logging.info("Verbinde mit %s", dev["ip"])
            service = await connect(dev, record)
            async with start_tunnel_over_remotepairing(service, protocol=TunnelProtocol.TCP) as tunnel:
                sampler = asyncio.create_task(self._sample(tunnel.interface))
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
            logging.exception("Backup fehlgeschlagen: %s", str(e) or type(e).__name__)
            self.failed = True
        finally:
            if sampler:
                sampler.cancel()
            self.rate = [0.0, 0.0]


backup = Backup()
