import { auth, forget } from './auth'
async function handle(r) {
  if (!r.ok) {
    if(r.status===401){forget();window.dispatchEvent(new Event('auth-expired'))}
    let msg = `HTTP ${r.status}`
    try { msg = (await r.json()).detail || msg } catch { /* 非 JSON 响应 */ }
    throw new Error(msg)
  }
  return r.json()
}

export function getJSON(path) {
  return fetch(path).then(handle)
}

export function postJSON(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': auth.csrf },
    body: body === undefined ? undefined : JSON.stringify(body)
  }).then(handle)
}
