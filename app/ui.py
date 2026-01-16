import streamlit as st
import subprocess
import threading
import time
import os
import json
import re
import streamlit.components.v1 as components
from pathlib import Path

CLIPBOARD_DIR = "./clipboard"
STATUS_FILE = "./logs/backup_status"
LOG_FILE = "./logs/backup.log"


_paste_component = components.declare_component("paste_button", path=str(CLIPBOARD_DIR))

st.set_page_config(page_title="iPhone Backup Manager", page_icon="📱", layout="wide")

DEVICES = {
    "Simon": {"ip": "192.168.188.201", "pair_record": "00008120-0016390A2100201E"},
    "Thomas": {"ip": "192.168.188.67", "pair_record": "00008110-001109460CF1801E"},
    "Jasmin": {"ip": "192.168.188.30", "pair_record": "00008101-00196C3C0206001E"},
    "Petra": {"ip": "192.168.188.69", "pair_record": "00008110-000868AE2212801E"}
}

if os.path.exists(STATUS_FILE):
    with open(STATUS_FILE) as f:
        state = json.load(f)
    if 'backup_running' not in st.session_state:
        st.session_state.backup_running = state.get("running", False)
        st.session_state.backup_device = state.get("device", None)
else:
    if 'backup_running' not in st.session_state:
        st.session_state.backup_running = False
        st.session_state.backup_device = None

def save_status(running, device=None):
    with open(STATUS_FILE, 'w') as f:
        json.dump({"running": running, "device": device}, f)

def kill_processes():
    subprocess.run(["killall", "-9", "usbmuxd", "idevicebackup2"], capture_output=True)

def stream_output(process, prefix=""):
    from datetime import datetime
    for line in iter(process.stdout.readline, ''):
        if line.strip():
            timestamp = datetime.now().strftime('%H:%M:%S')
            with open(LOG_FILE, 'a') as f:
                f.write(f"[{timestamp}] {prefix}{line.strip()}\n")

def run_backup(device_name):
    device = DEVICES[device_name]
    with open(LOG_FILE, 'w') as f:
        f.write("")
    try:
        kill_processes()
        time.sleep(1)
        usbmuxd = subprocess.Popen(
            ['stdbuf', '-oL', 'usbmuxd', '-c', device["ip"], '--pair-record-id', device["pair_record"]],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        threading.Thread(target=stream_output, args=(usbmuxd, "usbmuxd: "), daemon=True).start()
        time.sleep(5)
        backup = subprocess.Popen(
            ['stdbuf', '-oL', 'idevicebackup2', 'backup', '-n', '/iPhone'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        stream_output(backup)
        backup.wait()
    except Exception as e:
        with open(LOG_FILE, 'a') as f:
            f.write(f"Fehler: {e}\n")
    finally:
        kill_processes()
        st.session_state.backup_running = False
        st.session_state.backup_device = None
        save_status(False)

# UI
st.title("📱 iPhone Backup Manager")

if st.session_state.backup_running:
    st.success(f"🟢 Backup läuft: {st.session_state.backup_device}")
else:
    st.info("⚪ Bereit")

selected = st.selectbox("iPhone:", list(DEVICES.keys()), disabled=st.session_state.backup_running)

if selected:
    st.write(f"**IP:** {DEVICES[selected]['ip']}")
    col_info1, col_info2 = st.columns(2)    
    with col_info1:
        st.write(f"**UUID:** {DEVICES[selected]['pair_record']}")
    with col_info2:
        with st.expander("🔑 Pairing Record aktualisieren"):
            clipboard_payload = _paste_component(label="Inhalt aus Clipboard einfügen", key="paster_widget")
            
            if clipboard_payload:
                if str(clipboard_payload).startswith("error"):
                    st.error("Clipboard-Zugriff verweigert (HTTPS nötig)")
                else:
                    target = f"/var/lib/lockdown/{DEVICES[selected]['pair_record']}.plist"
                    try:
                        with open(target, "w") as f:
                            f.write(clipboard_payload)
                        st.success("Aktualisiert")
                    except Exception as e:
                        st.error(f"Schreibfehler: {e}")

col1, col2 = st.columns(2)

with col1:
    if not st.session_state.backup_running:
        if st.button("▶️ Backup starten", type="primary"):
            st.session_state.backup_running = True
            st.session_state.backup_device = selected
            save_status(True, selected)
            threading.Thread(target=run_backup, args=(selected,), daemon=True).start()
            st.rerun()

with col2:
    if st.session_state.backup_running:
        if st.button("⏹️ Stoppen"):
            kill_processes()
            st.session_state.backup_running = False
            st.session_state.backup_device = None
            save_status(False)
            st.rerun()

st.subheader("📋 Live-Logs")

@st.fragment(run_every="1s" if st.session_state.backup_running else None)
def live_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            # Tail: Nur die letzten 200 Zeilen lesen
            lines = f.readlines()
            logs = "".join(lines[-200:])
        
        # Fortschritt aus den letzten Zeilen extrahieren
        matches = re.findall(r"(\d+)% Finished", logs)
        progress = int(matches[-1]) if matches else 0
        st.progress(progress / 100)
        
        # Komponente für Auto-Scroll und sauberes Design
        components.html(f"""
        <div id="log-container" style="
            height: 400px; 
            overflow-y: auto; 
            background-color: #f0f2f6; 
            color: #31333f; 
            padding: 10px; 
            border-radius: 5px; 
            border: 1px solid #dcdde1;
            font-family: monospace; 
            font-size: 12px; 
            white-space: pre-wrap;
        ">{logs}</div>
        <script>
            var elem = document.getElementById('log-container');
            elem.scrollTop = elem.scrollHeight;
        </script>
        """, height=420)
    else:
        st.info("Keine Logs vorhanden.")

live_logs()