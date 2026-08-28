const $ = (id) => document.getElementById(id);

const logs = $('logs');
const progressTitle = $('progress-title');
const progressFill = $('progress-fill');
const progressText = $('progress-text');
const deviceInfoDisplay = $('device-info-display');
const deviceSelector = $('device-selector');
const deviceInfo = $('device-info');
const lastBackupDate = $('last-backup-date');
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

export function renderLog(lines) {
  logs.textContent = lines.join('\n');
  logs.scrollTop = logs.scrollHeight;
}

export function renderStatus(status, selected) {
  progressTitle.textContent = status.running
    ? `🟢 Backup läuft: ${status.device}`
    : status.error ? `🔴 Fehler: ${status.error}` : '⚪ Bereit';

  const info = status.device_info;
  deviceInfoDisplay.textContent = info ? `${info.model} • iOS ${info.version}` : '';
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
  for (const device of devices) deviceSelect.add(new Option(device.name, device.name));
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
