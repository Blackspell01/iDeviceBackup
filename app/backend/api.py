import json
import os
import time
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from backend.config import BASE_URL, FRONTEND_DIR, LOG_FILE, PAIR_RECORD_DIR, STATE_LOCK, get_status, set_status, log_line, clear_logs, get_client_ip
from backend.backup import write_pair_record_file, read_backup_info, stop_backup, run_backup
from backend.database import get_device, list_devices, set_device, set_pair_record_raw, _db_connect
import threading


app = FastAPI()

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic Models
class StartRequest(BaseModel):
    device: str


class StopRequest(BaseModel):
    pass


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


def _get_base_path():
    if BASE_URL:
        from urllib.parse import urlparse
        base_path = urlparse(BASE_URL).path
        return base_path if base_path else ""
    return ""


# Serve index.html
@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    with open(index_path, "r") as f:
        html = f.read()
    html = html.replace("</head>", f'<script>window.BASE_URL = "{BASE_URL}";</script></head>', 1)
    if BASE_URL:
        html = html.replace('href="styles.css"', f'href="{BASE_URL}/styles.css"')
        html = html.replace('src="app.js"', f'src="{BASE_URL}/app.js"')
    return HTMLResponse(content=html)





@app.get("/api/status")
async def api_status():
    return JSONResponse(content=get_status())


@app.get("/api/progress")
async def api_progress():
    with STATE_LOCK:
        return JSONResponse(content={"progress": get_status()["progress"]})


@app.get("/api/devices")
async def api_devices(device: Optional[str] = None):
    if device:
        dev = get_device(device)
        if not dev:
            raise HTTPException(status_code=404, detail="Device not found")
        return JSONResponse(content={device: {"ip": dev.get("ip"), "pair_record": dev.get("uuid")}})
    return JSONResponse(content=list_devices())


@app.get("/api/my-ip")
async def api_my_ip(request: Request):
    client_ip = get_client_ip(request.headers, request.client)
    return JSONResponse(content={"ip": client_ip})


@app.get("/api/backup-info")
async def api_backup_info(device: Optional[str] = None):
    info = read_backup_info(device) if device else None
    return JSONResponse(content={"info": info})


@app.get("/api/logs")
async def api_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
            logs = "".join(lines[-200:])
        return JSONResponse(content={"logs": logs})
    return JSONResponse(content={"logs": ""})


@app.get("/api/stream")
async def api_stream(request: Request):
    async def event_generator():
        last_size = 0
        last_ping = time.monotonic()
        
        try:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r") as f:
                    lines = f.readlines()
                    logs = "".join(lines[-200:])
                payload = {"logs": logs}
                yield f"data: {json.dumps(payload)}\n\n"
                last_size = os.path.getsize(LOG_FILE)
            
            while get_status()["running"]:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                
                size = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0
                if size > last_size:
                    with open(LOG_FILE, "r") as f:
                        f.seek(last_size)
                        new_content = f.read()
                    payload = {"logs": new_content}
                    yield f"data: {json.dumps(payload)}\n\n"
                    last_size = size
                
                if time.monotonic() - last_ping >= 5:
                    yield ": ping\n\n"
                    last_ping = time.monotonic()
                
                await asyncio.sleep(0.5)
            
            yield "data: {\"done\": true}\n\n"
        
        except Exception:
            pass
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*"
        }
    )


@app.post("/api/start")
async def api_start(request: StartRequest):
    device_name = request.device
    if not device_name:
        raise HTTPException(status_code=400, detail="Invalid device")
    
    dev = get_device(device_name)
    if not dev or not dev.get("ip") or not dev.get("uuid"):
        raise HTTPException(status_code=400, detail="Device ip/uuid missing")
    
    if get_status()["running"]:
        raise HTTPException(status_code=400, detail="Backup already running")
    
    clear_logs()
    set_status(True, device_name, 0)
    threading.Thread(target=run_backup, args=(device_name,), daemon=True).start()
    return JSONResponse(content={"success": True})


@app.post("/api/stop")
async def api_stop():
    stop_backup("⏹️ Backup manuell gestoppt")
    return JSONResponse(content={"success": True})


@app.post("/api/update-pair-record")
async def api_update_pair_record(request: UpdatePairRecordRequest):
    device_name = request.device
    content = request.content
    uuid = request.uuid
    ip_override = request.ip
    
    if not device_name:
        raise HTTPException(status_code=400, detail="Invalid device")
    if not content:
        raise HTTPException(status_code=400, detail="No content provided")
    
    try:
        # Determine uuid for pair-record filename
        if not uuid:
            dev = get_device(device_name)
            uuid = dev.get("uuid") if dev else None
        if not uuid:
            raise HTTPException(status_code=400, detail="uuid missing. Provide in request or set via POST /api/devices")
        
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
        return JSONResponse(content={"success": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices")
async def api_devices_post(request: DeviceRequest):
    device_name = request.device
    new_name = request.newName or request.new_name
    ip = request.ip
    uuid = request.uuid
    
    if not device_name:
        raise HTTPException(status_code=400, detail="Missing device")
    
    try:
        # Rename if requested and source exists
        if new_name and new_name != device_name:
            with _db_connect() as conn:
                # target name must not exist
                exists = conn.execute("SELECT 1 FROM pair_records WHERE name=?", (new_name,)).fetchone()
                if exists:
                    raise HTTPException(status_code=400, detail="Device name already exists")
                
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
        return JSONResponse(content={
            "success": True,
            "device": device_name,
            "ip": dev.get("ip"),
            "uuid": dev.get("uuid")
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/delete")
async def api_devices_delete(request: DeleteDeviceRequest):
    device_name = request.device
    if not device_name:
        raise HTTPException(status_code=400, detail="Missing device")
    
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
        
        return JSONResponse(content={"success": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Static files
@app.get("/{file_path:path}")
async def serve_static(file_path: str):
    if file_path.endswith(('.css', '.js', '.html')):
        file_full_path = os.path.join(FRONTEND_DIR, file_path)
        if not os.path.isfile(file_full_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        import mimetypes
        mimetypes.init()
        content_type, _ = mimetypes.guess_type(file_full_path)
        
        with open(file_full_path, "rb") as f:
            data = f.read()
        
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "X-Content-Type-Options": "nosniff"
            }
        )
    raise HTTPException(status_code=404, detail="Not found")