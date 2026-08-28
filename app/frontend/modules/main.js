import * as api from './api.js';
import * as ui from './ui.js';

let devices = [];
let selected = null;

const find = (name) => devices.find((device) => device.name === name);

async function reload() {
  devices = await api.listDevices();
  ui.renderOptions(devices, selected);
}

async function select(name) {
  selected = name;
  ui.deviceSelect.value = name;
  ui.renderDevice(find(name));
  ui.renderArchive(await api.archiveInfo(name));
}

let lines = [];

function connect() {
  const source = new EventSource('api/events');

  source.addEventListener('init', (event) => {
    const { status, log } = JSON.parse(event.data);
    lines = log;
    ui.renderLog(lines);
    ui.renderStatus(status, selected);
  });

  source.addEventListener('update', (event) => {
    const { status, log } = JSON.parse(event.data);
    lines.push(...log);
    ui.renderLog(lines);
    ui.renderStatus(status, selected);
  });

  source.onerror = () => {
    source.close();
    setTimeout(connect, 2000);
  };
}

ui.deviceSelect.addEventListener('change', async (event) => {
  const value = event.target.value;
  if (value === '__add__') {
    let name = 'Neues Gerät';
    for (let i = 2; find(name); i++) name = `Neues Gerät ${i}`;
    const created = await api.createDevice(name);
    await reload();
    await select(created.name);
    return;
  }
  if (!value) {
    selected = null;
    ui.clearDevice();
    return;
  }
  await select(value);
});

const edit = (span, field) => ui.inlineEdit(span, async (value) => {
  await api.updateDevice(selected, { [field]: value });
  if (field === 'name') selected = value;
  await reload();
  await select(selected);
});

ui.deviceName.addEventListener('click', () => edit(ui.deviceName, 'name'));
ui.deviceIP.addEventListener('click', () => edit(ui.deviceIP, 'ip'));
ui.deviceUUID.addEventListener('click', () => edit(ui.deviceUUID, 'uuid'));

ui.btnDelete.addEventListener('click', async (event) => {
  event.stopPropagation();
  if (!selected || !confirm(`Gerät "${selected}" wirklich löschen?`)) return;
  await api.deleteDevice(selected);
  selected = null;
  await reload();
  ui.clearDevice();
});

ui.btnClipboard.addEventListener('click', async () => {
  if (!selected) return ui.setClipboardMessage('Bitte zuerst ein Gerät auswählen.');
  if (!navigator.clipboard?.readText) return ui.setClipboardMessage('Clipboard-Zugriff nur über HTTPS.');
  try {
    const content = await navigator.clipboard.readText();
    if (!content.trim()) throw new Error('Clipboard ist leer');
    await api.setPairRecord(selected, content);
    ui.setClipboardMessage('✅ Pairing Record aktualisiert.');
    await reload();
  } catch (error) {
    ui.setClipboardMessage(`Fehler: ${error.message}`);
  }
});

ui.btnStart.addEventListener('click', async () => {
  ui.btnStart.disabled = true;
  ui.renderStatus(await api.startBackup(selected), selected);
});

ui.btnStop.addEventListener('click', async () => {
  ui.btnStop.disabled = true;
  ui.renderStatus(await api.stopBackup(), selected);
});

await reload();
const { ip } = await api.clientIp();
const detected = devices.find((device) => device.ip === ip);
if (detected) await select(detected.name);
connect();
