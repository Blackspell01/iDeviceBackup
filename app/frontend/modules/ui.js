const $ = (id) => document.getElementById(id);

const statusLine = $('status-line');
const chart = $('chart');
const progressTitle = $('progress-title');
const progressFill = $('progress-fill');
const progressText = $('progress-text');
const deviceInfoDisplay = $('device-info-display');
const deviceSelector = $('device-selector');
const deviceInfo = $('device-info');
const lastBackupDate = $('last-backup-date');
const archiveSize = $('archive-size');
const productType = $('product-type');
const productVersion = $('product-version');
const pairMessage = $('pair-message');

export const deviceSelect = $('device-select');
export const deviceName = $('device-name');
export const deviceIP = $('device-ip');
export const deviceUUID = $('device-uuid');
export const btnStart = $('btn-start');
export const btnStop = $('btn-stop');
export const btnDelete = $('btn-delete-device');
export const btnValidate = $('btn-validate');
export const btnCreate = $('btn-create');

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name);

const CHART = {
  chart: { type: 'area', height: 40, sparkline: { enabled: true }, animations: { enabled: false } },
  stroke: { curve: 'smooth', width: 0 },
  fill: { type: 'gradient', gradient: { opacityFrom: 0.45, opacityTo: 0 } },
  colors: [css('--accent'), css('--accent-alt')],
  tooltip: { enabled: false },
};

let apex;

function renderTransfer({ running, history = [] }) {
  chart.classList.toggle('hidden', !running || history.length < 2);
  if (history.length < 2) return;

  const series = [
    { name: 'vom iPhone', data: history.map((rate) => rate[0]) },
    { name: 'zum iPhone', data: history.map((rate) => rate[1]) },
  ];
  if (apex) return apex.updateSeries(series);
  apex = new ApexCharts(chart, { ...CHART, series });
  apex.render();
}

function renderMessage(message) {
  const text = message ?? '';
  if (statusLine.textContent === text) return;
  statusLine.textContent = text;
  // Animation neu starten, damit jede Meldung kurz aufblendet.
  statusLine.classList.remove('flash');
  void statusLine.offsetWidth;
  if (text) statusLine.classList.add('flash');
}

export function renderStatus(status, selected) {
  progressTitle.textContent = status.running
    ? `🟢 Backup läuft: ${status.device}`
    : status.failed ? '🔴 Fehler' : '⚪ Bereit';

  renderMessage(status.message);
  renderTransfer(status);

  const info = status.device_info;
  const [rx = 0, tx = 0] = status.rate ?? [];
  const rates = status.running ? ` · ↓ ${rx.toFixed(1)} MB/s · ↑ ${tx.toFixed(1)} MB/s` : '';
  deviceInfoDisplay.textContent = info ? `${info.model} • iOS ${info.version}${rates}` : '';
  deviceInfoDisplay.classList.toggle('hidden', !info);

  deviceSelector.classList.toggle('hidden', status.running);
  deviceInfo.classList.toggle('hidden', status.running || !selected);
  deviceSelect.disabled = status.running;
  btnStart.disabled = status.running || !selected;
  btnStop.disabled = !status.running;

  const percent = Math.max(0, Math.min(100, status.progress || 0));
  progressFill.style.width = `${percent}%`;
  progressText.textContent = `${percent}%`;
}

export function renderOptions(devices, selected) {
  deviceSelect.replaceChildren();
  deviceSelect.add(new Option('-- Bitte wählen --', ''));
  for (const device of devices) deviceSelect.add(new Option(device.name, device.id));
  deviceSelect.add(new Option('＋ Gerät hinzufügen…', '__add__'));
  deviceSelect.value = selected ?? '';
}

export function renderDevice(device) {
  deviceName.textContent = device.name;
  deviceIP.textContent = device.ip ?? '';
  deviceUUID.textContent = device.uuid ?? '';
  deviceInfo.classList.remove('hidden');
  btnStart.disabled = false;
}

export function renderArchive(info) {
  lastBackupDate.textContent = info?.last_backup
    ? new Date(info.last_backup).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
    : '–';
  archiveSize.textContent = info?.size ? `${(info.size / 1e9).toFixed(1)} GB` : '–';
  productType.textContent = info?.product_type ?? '–';
  productVersion.textContent = info?.product_version ?? '–';
}

export function clearDevice() {
  deviceInfo.classList.add('hidden');
  btnStart.disabled = true;
}

export function setMessage(text) {
  pairMessage.textContent = text;
}

export function inlineEdit(span, commit) {
  if (span.dataset.editing) return;
  span.dataset.editing = '1';
  const before = span.textContent;
  const input = Object.assign(document.createElement('input'), {
    type: 'text',
    className: 'inline-input',
    value: before,
  });

  const finish = async (save) => {
    delete span.dataset.editing;
    const value = input.value.trim();
    input.replaceWith(span);
    if (!save || value === before) return;
    try {
      await commit(value);
    } catch (error) {
      span.textContent = before;
      alert(`Fehler: ${error.message}`);
    }
  };

  span.replaceWith(input);
  input.focus();
  input.select();
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') finish(true);
    if (event.key === 'Escape') finish(false);
  });
  input.addEventListener('blur', () => finish(true));
}
