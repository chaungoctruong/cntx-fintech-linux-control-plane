"""Frontend HTML cho khách end-user — self-contained 1 file, không cần build.

Khách mở URL → dán JWT → link MT5 account 1 lần → bật/tắt bot.

Module này tách riêng để dễ scale: sau này nâng cấp lên React/Vue / Mini App
chỉ cần thay nội dung trả về của endpoint `/partner-user/ui`.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(prefix="/partner-user", tags=["partner-user-ui"])


HTML = """<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Spider AI — Cổng khách</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0b1220; color: #e6edf3; margin: 0; padding: 24px;
      min-height: 100vh;
    }
    .card {
      max-width: 480px; margin: 24px auto; background: #111827;
      border: 1px solid #1f2937; border-radius: 12px; padding: 24px;
      box-shadow: 0 1px 3px rgba(0,0,0,.3);
    }
    h1 { margin: 0 0 8px; font-size: 20px; }
    .muted { color: #9ca3af; font-size: 13px; margin-bottom: 18px; }
    label { display: block; margin: 14px 0 6px; font-size: 13px; color: #cbd5e1; }
    input, textarea {
      width: 100%; padding: 10px 12px; border-radius: 8px;
      border: 1px solid #374151; background: #0f172a; color: #e6edf3;
      font-size: 14px; font-family: inherit;
    }
    textarea { font-family: monospace; min-height: 100px; resize: vertical; }
    button {
      width: 100%; padding: 12px; margin-top: 14px; border: 0; border-radius: 8px;
      background: #3b82f6; color: #fff; font-size: 14px; font-weight: 600;
      cursor: pointer;
    }
    button:hover { background: #2563eb; }
    button:disabled { background: #4b5563; cursor: not-allowed; }
    button.danger { background: #ef4444; }
    button.danger:hover { background: #dc2626; }
    button.secondary { background: #374151; }
    button.secondary:hover { background: #4b5563; }
    .row { display: flex; gap: 8px; }
    .row > * { flex: 1; }
    .badge {
      display: inline-block; padding: 3px 10px; border-radius: 6px;
      font-size: 12px; font-weight: 600;
    }
    .badge.ok { background: #064e3b; color: #6ee7b7; }
    .badge.warn { background: #78350f; color: #fcd34d; }
    .badge.err { background: #7f1d1d; color: #fca5a5; }
    .badge.info { background: #1e3a8a; color: #93c5fd; }
    .kv { font-size: 13px; line-height: 1.7; }
    .kv b { color: #f3f4f6; }
    .alert {
      padding: 10px 12px; border-radius: 8px; font-size: 13px; margin: 12px 0;
    }
    .alert.error { background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; }
    .alert.success { background: #052e1a; color: #6ee7b7; border: 1px solid #065f46; }
    code { background: #0f172a; padding: 1px 6px; border-radius: 4px; font-size: 12px; }
    .footer { text-align: center; color: #6b7280; font-size: 11px; margin-top: 16px; }
  </style>
</head>
<body>

<div class="card" id="login-card">
  <h1>🎫 Đăng nhập bằng token</h1>
  <p class="muted">Dán JWT mà đối tác cấp cho bạn vào ô bên dưới.</p>
  <label for="token">Token</label>
  <textarea id="token" placeholder="eyJhbGciOi..."></textarea>
  <button id="btn-login">Đăng nhập</button>
  <div id="login-msg"></div>
</div>

<div class="card" id="home-card" style="display:none">
  <h1>Xin chào, <span id="who">khách</span></h1>
  <p class="muted">Bot: <code id="bot-name">-</code> · Hết hạn sau <span id="remaining">-</span></p>

  <div id="link-section" style="display:none">
    <div class="alert error">Bạn cần link MT5 account 1 lần để dùng bot.</div>
    <label for="account-id">MT5 account_id</label>
    <input id="account-id" type="number" placeholder="vd 433573479" inputmode="numeric" />
    <button id="btn-link">Link account</button>
    <div id="link-msg"></div>
  </div>

  <div id="control-section" style="display:none">
    <div class="kv">
      <div>Account MT5: <b id="acc">-</b></div>
      <div>Trạng thái bot: <span id="status-badge" class="badge info">-</span></div>
      <div>Deployment: <code id="deployment-id">-</code></div>
    </div>
    <div class="row">
      <button id="btn-start">▶ Bật bot</button>
      <button id="btn-stop" class="danger">⏸ Tắt bot</button>
    </div>
    <button id="btn-refresh" class="secondary">↻ Cập nhật trạng thái</button>
    <div id="action-msg"></div>
  </div>

  <button id="btn-logout" class="secondary">Đăng xuất</button>
</div>

<div class="footer">Spider AI — Partner User Portal</div>

<script>
'use strict';
const API = '/api/v2/partner-user';
const STORAGE_KEY = 'spider_pu_token';

const $ = (id) => document.getElementById(id);

function setHidden(id, hidden) { $(id).style.display = hidden ? 'none' : ''; }
function setMsg(id, text, kind) {
  const el = $(id);
  if (!text) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="alert ${kind || 'error'}">${text}</div>`;
}

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  const token = localStorage.getItem(STORAGE_KEY);
  if (token) headers['Authorization'] = 'Bearer ' + token;
  const r = await fetch(API + path, Object.assign({ headers }, opts));
  let body;
  try { body = await r.json(); } catch { body = null; }
  if (!r.ok) {
    const code = (body && body.detail && body.detail.public_code) || ('http_' + r.status);
    const msg = (body && body.detail && body.detail.message) || ('Lỗi ' + r.status);
    const err = new Error(msg); err.code = code; err.status = r.status; err.body = body;
    throw err;
  }
  return body;
}

function fmtRemaining(sec) {
  if (sec <= 0) return 'đã hết';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function statusBadge(status) {
  const el = $('status-badge');
  el.textContent = status || 'unknown';
  el.className = 'badge ' +
    (status === 'running' ? 'ok' :
     status === 'stopped' || status === 'unlinked' ? 'warn' :
     status === 'failed' ? 'err' : 'info');
}

async function loadMe() {
  const me = await api('/me');
  $('who').textContent = me.end_user_label || 'khách';
  $('bot-name').textContent = me.bot_id;
  $('remaining').textContent = fmtRemaining(me.remaining_seconds);
  if (me.account_id === null) {
    setHidden('link-section', false);
    setHidden('control-section', true);
  } else {
    setHidden('link-section', true);
    setHidden('control-section', false);
    $('acc').textContent = me.account_id;
    await refreshBot();
  }
}

async function refreshBot() {
  setMsg('action-msg', '', null);
  try {
    const info = await api('/bot');
    statusBadge(info.bot.status);
    $('deployment-id').textContent = info.bot.deployment_id || '-';
  } catch (e) {
    setMsg('action-msg', 'Lỗi cập nhật: ' + e.message);
  }
}

$('btn-login').onclick = async () => {
  const token = $('token').value.trim();
  if (!token) { setMsg('login-msg', 'Bạn chưa dán token.'); return; }
  setMsg('login-msg', '', null);
  $('btn-login').disabled = true;
  try {
    localStorage.setItem(STORAGE_KEY, token);
    await loadMe();
    setHidden('login-card', true);
    setHidden('home-card', false);
  } catch (e) {
    localStorage.removeItem(STORAGE_KEY);
    setMsg('login-msg', `${e.code}: ${e.message}`);
  } finally {
    $('btn-login').disabled = false;
  }
};

$('btn-link').onclick = async () => {
  const v = $('account-id').value.trim();
  const account_id = Number(v);
  if (!v || !Number.isInteger(account_id) || account_id <= 0) {
    setMsg('link-msg', 'account_id phải là số nguyên dương.');
    return;
  }
  $('btn-link').disabled = true;
  setMsg('link-msg', '', null);
  try {
    await api('/link-account', { method: 'POST', body: JSON.stringify({ account_id }) });
    setMsg('link-msg', 'Đã link thành công.', 'success');
    await loadMe();
  } catch (e) {
    setMsg('link-msg', `${e.code}: ${e.message}`);
  } finally {
    $('btn-link').disabled = false;
  }
};

$('btn-start').onclick = async () => {
  $('btn-start').disabled = true; $('btn-stop').disabled = true;
  setMsg('action-msg', '', null);
  try {
    const r = await api('/bot/start', { method: 'POST', body: '{}' });
    setMsg('action-msg', `${r.action}: ${r.note || 'Đã gửi lệnh bật bot.'}`, 'success');
    statusBadge(r.bot.status);
    setTimeout(refreshBot, 3000);
  } catch (e) {
    setMsg('action-msg', `${e.code}: ${e.message}`);
  } finally {
    $('btn-start').disabled = false; $('btn-stop').disabled = false;
  }
};

$('btn-stop').onclick = async () => {
  $('btn-start').disabled = true; $('btn-stop').disabled = true;
  setMsg('action-msg', '', null);
  try {
    const r = await api('/bot/stop', { method: 'POST', body: '{}' });
    setMsg('action-msg', `${r.action}: ${r.note || 'Đã gửi lệnh tắt bot.'}`, 'success');
    statusBadge(r.bot.status);
    setTimeout(refreshBot, 3000);
  } catch (e) {
    setMsg('action-msg', `${e.code}: ${e.message}`);
  } finally {
    $('btn-start').disabled = false; $('btn-stop').disabled = false;
  }
};

$('btn-refresh').onclick = refreshBot;

$('btn-logout').onclick = () => {
  localStorage.removeItem(STORAGE_KEY);
  setHidden('home-card', true);
  setHidden('login-card', false);
  $('token').value = '';
  setMsg('login-msg', 'Đã đăng xuất.', 'success');
};

// auto-login nếu đã có token trong localStorage
(async () => {
  const token = localStorage.getItem(STORAGE_KEY);
  if (!token) return;
  try {
    await loadMe();
    setHidden('login-card', true);
    setHidden('home-card', false);
  } catch (e) {
    localStorage.removeItem(STORAGE_KEY);
  }
})();
</script>
</body>
</html>
"""


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def partner_user_ui() -> str:
    """Trang web đơn cho khách end-user. Tự load JWT từ localStorage nếu đã login."""
    return HTML
