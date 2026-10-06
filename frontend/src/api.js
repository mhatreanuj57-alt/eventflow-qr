let session = null;
export function setSession(value) { session = value; }
export async function api(path, options = {}) {
  const base = import.meta.env.DEV ? '/api' : import.meta.env.VITE_API_BASE_URL;
  const response = await fetch(`${base}${path}`, {
    ...options,
    signal: options.signal || AbortSignal.timeout(30000),
    headers: { 'Content-Type': 'application/json', ...(session ? { Authorization: `Bearer ${session.token}` } : {}) },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let result;
  try { result = await response.json(); }
  catch { throw new Error('Could not reach EventFlow. Please try again in a moment.'); }
  if (!response.ok) {
    if (response.status === 401 && session) { session = null; window.dispatchEvent(new Event('session-expired')); }
    throw new Error(result.message || 'Request failed. Please try again.');
  }
  return result;
}
