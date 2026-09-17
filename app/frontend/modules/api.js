async function request(path, method = 'GET', body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.status === 204 ? null : res.json();
}

const device = (id, suffix = '') => `api/devices/${id}${suffix}`;

export const listDevices = () => request('api/devices');
export const createDevice = (name) => request('api/devices', 'POST', { name });
export const updateDevice = (id, patch) => request(device(id), 'PATCH', patch);
export const deleteDevice = (id) => request(device(id), 'DELETE');
export const setPairRecord = (id, content) => request(device(id, '/pair-record'), 'PUT', { content });
export const archiveInfo = (id) => request(device(id, '/archive'));
export const validatePairRecord = (id) => request(device(id, '/pair-record/validate'));
export const clientIp = () => request('api/client-ip');
export const startBackup = (id) => request('api/start', 'POST', { id });
export const stopBackup = () => request('api/stop', 'POST');
