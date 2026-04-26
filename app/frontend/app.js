// BASE_URL wird vom Server injiziert
const BASE_URL = window.BASE_URL || '';

// State
let devices = {};
let selectedDevice = null;
let isRunning = false;
let eventSource = null;
let currentProgress = 0;

// DOM-Elemente
const progressTitle = document.getElementById('progress-title');
const deviceInfoDisplay = document.getElementById('device-info-display');
const deviceSelector = document.getElementById('device-selector');
const deviceSelect = document.getElementById('device-select');
const deviceInfo = document.getElementById('device-info');
const deviceIP = document.getElementById('device-ip');
const deviceUUID = document.getElementById('device-uuid');
const deviceNameSpan = document.getElementById('device-name');
const btnDeleteDevice = document.getElementById('btn-delete-device');
const lastBackupDate = document.getElementById('last-backup-date');
const productType = document.getElementById('product-type');
const productVersion = document.getElementById('product-version');
const btnStart = document.getElementById('btn-start');
const btnStop = document.getElementById('btn-stop');
const btnClipboard = document.getElementById('btn-clipboard');
const clipboardMessage = document.getElementById('clipboard-message');
const logsEl = document.getElementById('logs');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');

// API-Calls mit BASE_URL
async function api(endpoint, method = 'GET', data = null) {
  const options = { method, headers: { 'Content-Type': 'application/json' } };
  if (data) options.body = JSON.stringify(data);
  const response = await fetch(BASE_URL + endpoint, options);
  return response.json();
}

// Status laden
async function loadStatus() {
  const status = await api('/api/status');
  isRunning = status.running;
  if (isRunning) {
    progressTitle.textContent = `🟢 Backup läuft: ${status.device}`;
    deviceInfo.classList.add('hidden');
    deviceSelector.classList.add('hidden');
    deviceSelect.disabled = true;
    btnStart.disabled = true;
    btnStop.disabled = false;
    if (status.device_info) {
      deviceInfoDisplay.textContent = `${status.device_info.model || ''} • iOS ${status.device_info.version || ''}`;
      deviceInfoDisplay.classList.remove('hidden');
    } else {
      deviceInfoDisplay.classList.add('hidden');
    }
    if (status.progress !== undefined) {
      currentProgress = status.progress;
      progressFill.style.width = status.progress + '%';
      progressText.textContent = status.progress + '%';
    }
    startEventStream();
  } else {
    progressTitle.textContent = '⚪ Bereit';
    deviceInfoDisplay.classList.add('hidden');
    deviceSelect.disabled = false;
    btnStop.disabled = true;
    updateButtons();
    deviceSelector.classList.remove('hidden');
    if (selectedDevice) deviceInfo.classList.remove('hidden');
    loadLogs();
  }
}

// Devices laden
async function loadDevices() {
  devices = await api('/api/devices');
  renderDeviceOptions();
  autoDetectDevice();
}

function renderDeviceOptions() {
  while (deviceSelect.firstChild) deviceSelect.removeChild(deviceSelect.firstChild);
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = '-- Bitte wählen --';
  deviceSelect.appendChild(placeholder);
  for (const [name] of Object.entries(devices)) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    deviceSelect.appendChild(option);
  }
  const addOpt = document.createElement('option');
  addOpt.value = '__add__';
  addOpt.textContent = '＋ Gerät hinzufügen…';
  deviceSelect.appendChild(addOpt);
  if (selectedDevice && devices[selectedDevice]) deviceSelect.value = selectedDevice;
}

// Inline edit helpers
function isValidIPv4(value) {
  const parts = value.split('.');
  if (parts.length !== 4) return false;
  return parts.every(p => {
    if (!/^\d{1,3}$/.test(p)) return false;
    const n = Number(p);
    return n >= 0 && n <= 255 && (p.length === 1 || p[0] !== '0' || n === 0);
  });
}

function inlineEdit(spanEl, field) {
  if (!selectedDevice) return;
  if (spanEl.dataset.editing === '1') return;
  spanEl.dataset.editing = '1';
  const initial = spanEl.textContent || '';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'inline-input';
  input.value = initial;
  input.setAttribute('aria-label', field.toUpperCase() + ' bearbeiten');

  const finish = async (commit) => {
    spanEl.dataset.editing = '';
    const newVal = input.value.trim();
    input.replaceWith(spanEl);
    if (!commit || newVal === initial) return;
    if (field === 'ip' && !isValidIPv4(newVal)) { spanEl.textContent = initial; spanEl.title = 'Ungültige IPv4-Adresse'; return; }
    try {
      if (field === 'name') {
        const res = await api('/api/devices', 'POST', { device: selectedDevice, newName: newVal });
        if (res && !res.error) {
          const current = devices[selectedDevice] || {};
          delete devices[selectedDevice];
          devices[newVal] = current;
          selectedDevice = newVal;
          spanEl.textContent = newVal;
          renderDeviceOptions();
          deviceSelect.value = selectedDevice;
          spanEl.classList.add('blink-ok'); setTimeout(() => spanEl.classList.remove('blink-ok'), 800);
        } else { spanEl.textContent = initial; if (res && res.error) alert('Fehler: ' + res.error); }
      } else {
        const payload = { device: selectedDevice }; payload[field] = newVal;
        const res = await api('/api/devices', 'POST', payload);
        if (res && !res.error) {
          if (!devices[selectedDevice]) devices[selectedDevice] = {};
          if (field === 'ip') { devices[selectedDevice].ip = newVal; spanEl.textContent = newVal; }
          else if (field === 'uuid') { devices[selectedDevice].pair_record = newVal; spanEl.textContent = newVal; }
          spanEl.classList.add('blink-ok'); setTimeout(() => spanEl.classList.remove('blink-ok'), 800);
        } else { spanEl.textContent = initial; if (res && res.error) alert('Fehler: ' + res.error); }
      }
    } catch (e) { spanEl.textContent = initial; alert('Fehler: ' + e.message); }
  };

  spanEl.replaceWith(input);
  input.focus(); input.select();
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
    else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

// Device automatisch erkennen
async function autoDetectDevice() {
  try {
    const response = await fetch(BASE_URL + '/api/my-ip');
    const data = await response.json();
    const myIp = data.ip;
    for (const [name, info] of Object.entries(devices)) {
      if (info.ip === myIp) {
        deviceSelect.value = name; selectedDevice = name;
        deviceNameSpan.textContent = selectedDevice;
        deviceIP.textContent = info.ip || '';
        deviceUUID.textContent = info.pair_record || '';
        deviceInfo.classList.remove('hidden'); btnStart.disabled = false;
        await loadBackupInfo(name); return;
      }
    }
  } catch (error) { console.log('Auto-detect fehlgeschlagen:', error); }
}

// Device-Auswahl
deviceSelect.addEventListener('change', async (e) => {
  selectedDevice = e.target.value;
  if (selectedDevice === '__add__') {
    const base = 'Neues Gerät'; let newName = base; let i = 1;
    while (devices[newName]) { newName = base + ' ' + (++i); }
    try {
      const res = await api('/api/devices', 'POST', { device: newName });
      if (res && res.error) { alert('Fehler: ' + res.error); deviceSelect.value=''; selectedDevice=null; return; }
      devices[newName] = { ip: '', pair_record: '' };
      selectedDevice = newName; renderDeviceOptions(); deviceSelect.value = selectedDevice;
      deviceNameSpan.textContent = selectedDevice; deviceIP.textContent=''; deviceUUID.textContent='';
      deviceInfo.classList.remove('hidden'); btnStart.disabled = false; return;
    } catch (err) { alert('Fehler beim Hinzufügen: ' + err.message); deviceSelect.value=''; selectedDevice=null; return; }
  }
  if (selectedDevice) {
    const device = devices[selectedDevice];
    deviceNameSpan.textContent = selectedDevice;
    deviceIP.textContent = device.ip || '';
    deviceUUID.textContent = device.pair_record || '';
    deviceInfo.classList.remove('hidden'); btnStart.disabled = false; loadBackupInfo(selectedDevice);
  } else { deviceInfo.classList.add('hidden'); btnStart.disabled = true; }
});

// Activate inline edit on click
deviceNameSpan.addEventListener('click', () => inlineEdit(deviceNameSpan, 'name'));
deviceIP.addEventListener('click', () => inlineEdit(deviceIP, 'ip'));
deviceUUID.addEventListener('click', () => inlineEdit(deviceUUID, 'uuid'));

// Delete device (prevent toggling the details)
btnDeleteDevice.addEventListener('click', async (ev) => {
  ev.stopPropagation(); if (!selectedDevice) return;
  const confirmText = `Gerät "${selectedDevice}" wirklich löschen?\nDies entfernt auch die gespeicherte Pair-Record-Datei (falls vorhanden).`;
  if (!confirm(confirmText)) return;
  try {
    const res = await api('/api/devices/delete', 'POST', { device: selectedDevice });
    if (res && !res.error) {
      delete devices[selectedDevice]; selectedDevice = null;
      renderDeviceOptions(); deviceSelect.value=''; deviceInfo.classList.add('hidden'); btnStart.disabled = true;
    } else { alert('Fehler: ' + (res && res.error ? res.error : 'Unbekannt')); }
  } catch (e) { alert('Fehler beim Löschen: ' + e.message); }
});

// Start / Stop Buttons
btnStart.addEventListener('click', async () => {
  if (!selectedDevice) return;
  try {
    btnStart.disabled = true;
    const res = await api('/api/start', 'POST', { device: selectedDevice });
    if (res && res.success) {
      await loadStatus();
    } else {
      alert('Fehler beim Starten des Backups.');
      btnStart.disabled = false;
    }
  } catch (e) {
    alert('Fehler beim Starten: ' + e.message);
    btnStart.disabled = false;
  }
});

btnStop.addEventListener('click', async () => {
  try {
    btnStop.disabled = true;
    const res = await api('/api/stop', 'POST');
    if (res && res.success) {
      await loadStatus();
    } else {
      alert('Fehler beim Stoppen des Backups.');
      btnStop.disabled = false;
    }
  } catch (e) {
    alert('Fehler beim Stoppen: ' + e.message);
    btnStop.disabled = false;
  }
});

async function loadBackupInfo(deviceName) {
  try {
    const response = await fetch(`${BASE_URL}/api/backup-info?device=${encodeURIComponent(deviceName)}`);
    const data = await response.json(); const info = data.info;
    if (info) {
      lastBackupDate.textContent = info.last_backup_date || '–';
      productType.textContent = info.product_type || '–';
      productVersion.textContent = info.product_version || '–';
    } else {
      lastBackupDate.textContent = '–'; productType.textContent = '–'; productVersion.textContent = '–';
    }
  } catch { lastBackupDate.textContent = '–'; productType.textContent = '–'; productVersion.textContent = '–'; }
}

// Logs laden
async function loadLogs() {
  try {
    const data = await api('/api/logs');
    logsEl.textContent = data.logs || 'Keine Logs vorhanden.';
    updateProgress(data.progress);
    logsEl.scrollTop = logsEl.scrollHeight;
  } catch (error) { console.error('Fehler beim Laden der Logs:', error); }
}

// Server-Sent Events für Live-Updates
function startEventStream() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(BASE_URL + '/api/stream');
  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.done) { eventSource.close(); eventSource = null; loadStatus(); return; }
    if (data.logs) { logsEl.textContent += data.logs; logsEl.scrollTop = logsEl.scrollHeight; }
  };
  eventSource.onerror = () => { eventSource.close(); eventSource = null; loadStatus(); };
}

function updateProgress(percent) {
  if (percent > currentProgress) {
    currentProgress = percent;
    progressFill.style.width = percent + '%';
    progressText.textContent = percent + '%';
  }
}

function updateButtons() {
  if (isRunning) { btnStart.disabled = true; btnStop.disabled = false; }
  else { btnStart.disabled = !selectedDevice; btnStop.disabled = true; }
}

// Polling
setInterval(() => { if (!isRunning && selectedDevice) loadLogs(); }, 2000);
setInterval(loadStatus, 3000);
setInterval(async () => { if (isRunning) { const status = await api('/api/status'); if (status.progress !== undefined) updateProgress(status.progress); } }, 1000);

// Init
loadDevices();
loadStatus();
