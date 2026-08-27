import json
import os
import asyncio
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import (
    BASE_URL, FRONTEND_DIR, LOG_FILE, get_status,
    start_run, clear_logs, get_client_ip
)
from backend.backup import read_backup_info, stop_backup, run_backup
from backend.database import (
    get_device, list_devices, set_device, set_pair_record_raw, delete_device
)

app = FastAPI(root_path=BASE_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MODELS ---
class StartRequest(BaseModel):
    device: str

class UpdatePairRecordRequest(BaseModel):
    device: str
    content: str
    uuid: Optional[str] = None
    ip: Optional[str] = None

class DeviceRequest(BaseModel):
    device: str
    newName: Optional[str] = None
    new_name: Optional[str] = None
    ip: Optional[str] = None
    uuid: Optional[str] = None

class DeleteDeviceRequest(BaseModel):
    device: str

# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def serve_index():
    with open(os.path.join(FRONTEND_DIR, "index.html"), "r") as f:
        html = f.read()
    html = html.replace("</head>", f'<script>window.BASE_URL = "{BASE_URL}";</script></head>', 1)
    if BASE_URL:
        html = html.replace('href="styles.css"', f'href="{BASE_URL}/styles.css"')
        html = html.replace('src="app.js"', f'src="{BASE_URL}/app.js"')
    return HTMLResponse(content=html)

@app.get("/api/status")
def api_status():
    return get_status()


@app.get("/api/my-ip")
def api_my_ip(request: Request):
    return {"ip": get_client_ip(request.headers, request.client)}

@app.get("/api/backup-info")
def api_backup_info(device: Optional[str] = None):
    return {"info": read_backup_info(device) if device else None}

@app.get("/api/devices")
def api_devices(device: Optional[str] = None):
    if device:
        dev = get_device(device)
        if not dev: raise HTTPException(status_code=404)
        return {device: {"ip": dev.get("ip"), "pair_record": dev.get("uuid")}}
    return list_devices()

@app.get("/api/logs")
def api_logs():
    with open(LOG_FILE, "r") as f:
        return {"logs": "".join(f.readlines()[-200:])}

@app.get("/api/stream")
async def api_stream(request: Request):
    async def event_generator():
        with open(LOG_FILE, "r") as f:
            yield f"data: {json.dumps({'logs': ''.join(f.readlines()[-200:])})}\n\n"
        last_size = os.path.getsize(LOG_FILE)
        try:
            while True:
                if await request.is_disconnected():
                    break
                curr_size = os.path.getsize(LOG_FILE)
                if curr_size > last_size:
                    with open(LOG_FILE, "r") as f:
                        f.seek(last_size)
                        content = f.read()
                    if content:
                        yield f"data: {json.dumps({'logs': content})}\n\n"
                    last_size = curr_size
                if not get_status()["running"]:
                    yield 'data: {"done": true}\n\n'
                    break
                await asyncio.sleep(0.5)
        except Exception:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@app.post("/api/start")
def api_start(request: StartRequest):
    clear_logs()
    start_run(request.device)
    threading.Thread(target=run_backup, args=(request.device,), daemon=True).start()
    return {"success": True}

@app.post("/api/stop")
def api_stop():
    stop_backup("⏹️ Manuell gestoppt")
    return {"success": True}

@app.post("/api/update-pair-record")
def api_update_pair_record(req: UpdatePairRecordRequest):
    uuid = req.uuid or (get_device(req.device) or {}).get("uuid")
    if not uuid: raise HTTPException(status_code=400)
    set_device(req.device, ip=req.ip, uuid=uuid)
    set_pair_record_raw(req.device, req.content)
    return {"success": True}

@app.post("/api/devices")
def api_devices_post(req: DeviceRequest):
    name = req.newName or req.new_name or req.device
    set_device(name, ip=req.ip, uuid=req.uuid)
    return {"success": True, "device": name}

@app.post("/api/devices/delete")
def api_devices_delete(req: DeleteDeviceRequest):
    delete_device(req.device)
    return {"success": True}