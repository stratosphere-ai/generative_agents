// Shared helpers for the insurance platform frontend.

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let body = null;
  try { body = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const msg = (body && body.detail) || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return body;
}

function money(n) {
  if (n === Infinity || n === null || n === undefined) return "∞";
  return "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function pct(p) { return (Number(p) * 100).toFixed(1) + "%"; }
function fmtDate(d) {
  if (!d) return "—";
  try { return new Date(d).toLocaleDateString(); } catch (_) { return d; }
}

let _toastTimer = null;
function toast(msg, kind = "") {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = "show " + kind;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => (el.className = kind), 3500);
}

async function renderPoolBar(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  try {
    const p = await api("/api/pool");
    const ratio = p.solvency_ratio;
    const ratioTxt = ratio === null || ratio === Infinity || ratio > 1e6
      ? "∞" : Number(ratio).toFixed(2);
    const ratioClass = ratio === Infinity || ratio >= 1 ? "good" : "bad";
    el.innerHTML = `
      <div class="stat"><div class="label">Cash Balance</div><div class="value good">${money(p.cash_balance)}</div></div>
      <div class="stat"><div class="label">Reserved Liabilities</div><div class="value">${money(p.reserved_liabilities)}</div></div>
      <div class="stat"><div class="label">Hedge Assets (底层资产)</div><div class="value">${money(p.hedge_asset_value)}</div></div>
      <div class="stat"><div class="label">Premiums Collected</div><div class="value">${money(p.collected_premiums)}</div></div>
      <div class="stat"><div class="label">Solvency Ratio</div><div class="value ${ratioClass}">${ratioTxt}</div></div>
      <div class="stat"><div class="label">Active Policies</div><div class="value">${p.active_policies}</div></div>`;
  } catch (e) {
    el.innerHTML = `<div class="stat"><div class="label">Pool</div><div class="value bad">unavailable</div></div>`;
  }
}
