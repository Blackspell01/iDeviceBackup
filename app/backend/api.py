import asyncio
import json
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import db
from backend.backup import archive_info, backup, validate_pairing, UiLog

BASE_URL = os.environ.get("BASE_URL", "")
FRONTEND = Path("frontend")


@asynccontextmanager
async def lifespan(app):
    UiLog.setup()
    db.init()
    yield
    backup.cancel()


app = FastAPI(title="iOS Backup", root_path=BASE_URL, lifespan=lifespan)


class Name(BaseModel):
    name: str


class Id(BaseModel):
    id: int


class Patch(BaseModel):
    name: str | None = None
    ip: str | None = None
    uuid: str | None = None


class PairRecord(BaseModel):
    content: str


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    html = (FRONTEND / "index.html").read_text()
    return html.replace("<head>", f'<head><base href="{BASE_URL}/">', 1)


@app.get("/api/devices")
async def devices():
    return db.list_devices()


@app.post("/api/devices", status_code=201)
async def create(body: Name):
    return db.get_device(db.create_device(body.name))


@app.patch("/api/devices/{device_id}")
async def patch(device_id: int, body: Patch):
    db.update_device(device_id, body.name, body.ip, body.uuid)
    return db.get_device(device_id)


@app.delete("/api/devices/{device_id}", status_code=204)
async def remove(device_id: int):
    db.delete_device(device_id)


@app.put("/api/devices/{device_id}/pair-record", status_code=204)
async def pair_record(device_id: int, body: PairRecord):
    db.set_pair_record(device_id, body.content.encode())


@app.get("/api/devices/{device_id}/archive")
async def archive(device_id: int):
    dev = db.get_device(device_id)
    return archive_info(dev["name"], dev["uuid"]) if dev and dev["uuid"] else None


@app.get("/api/devices/{device_id}/pair-record/validate")
async def check_pair_record(device_id: int):
    return await validate_pairing(db.get_device(device_id), db.get_pair_record(device_id))


@app.get("/api/status")
async def status():
    return backup.status()


@app.post("/api/start")
async def start(body: Id):
    if not backup.running:
        backup.start(body.id)
    return backup.status()


@app.post("/api/stop")
async def stop():
    backup.cancel()
    return backup.status()


@app.get("/api/client-ip")
async def client_ip(request: Request):
    forwarded = request.headers.get("X-Forwarded-For", "")
    return {"ip": forwarded.split(",")[0].strip() or request.client.host}


@app.get("/api/events")
async def events():
    queue = asyncio.Queue()
    backup.subscribers.add(queue)

    async def stream():
        try:
            yield f"data: {json.dumps(backup.status())}\n\n"
            while True:
                yield f"data: {json.dumps(await queue.get())}\n\n"
        finally:
            backup.subscribers.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


mimetypes.add_type("application/octet-stream", ".shortcut")
app.mount("/", StaticFiles(directory=FRONTEND), name="static")
