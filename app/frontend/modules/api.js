async function request(path, method = 'GET', body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.status === 204 ? null : res.json();
}

const device = (name, suffix = '') => `api/devices/${encodeURIComponent(name)}${suffix}`;

export const listDevices = () => request('api/devices');
export const createDevice = (name) => request('api/devices', 'POST', { name });
export const updateDevice = (name, patch) => request(device(name), 'PATCH', patch);
export const deleteDevice = (name) => request(device(name), 'DELETE');
export const setPairRecord = (name, content) => request(device(name, '/pair-record'), 'PUT', { content });
export const archiveInfo = (name) => request(device(name, '/archive'));
export const clientIp = () => request('api/client-ip');
export const startBackup = (name) => request('api/start', 'POST', { name });
export const stopBackup = () => request('api/stop', 'POST');
