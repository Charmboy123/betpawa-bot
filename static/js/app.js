const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const views = ["dashboard", "opportunities", "proposals", "bankroll", "performance"];
$$("nav a[data-view]").forEach(a => a.addEventListener("click", e => {
  e.preventDefault();
  const v = a.dataset.view;
  views.forEach(x => $("#"+x).classList.toggle("hidden", x !== v));
  if (v === "dashboard") loadDashboard();
  if (v === "opportunities") loadOpportunities();
  if (v === "proposals") loadProposals("");
  if (v === "bankroll") loadBankroll();
  if (v === "performance") loadPerformance();
}));

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fmtPct(x) { return x == null ? "-" : (x*100).toFixed(1) + "%"; }
function fmtNum(x, d=2) { return x == null ? "-" : Number(x).toFixed(d); }

async function loadDashboard() {
  const [status, bankroll, props] = await Promise.all([
    api("/api/system/status"), api("/api/bankroll"), api("/api/proposals"),
  ]);
  const pending = props.filter(p => p.status === "AWAITING_APPROVAL").length;
  const approved = props.filter(p => p.status === "APPROVED").length;
  $("#dash-cards").innerHTML = `
    <div class="card"><div class="label">Balance</div><div class="value">${fmtNum(bankroll.balance)}</div></div>
    <div class="card"><div class="label">Pending</div><div class="value">${pending}</div></div>
    <div class="card"><div class="label">Approved</div><div class="value">${approved}</div></div>
    <div class="card"><div class="label">Mode</div><div class="value">${status.paper_mode ? "PAPER" : "LIVE"}</div></div>
  `;
  $("#system-status").innerHTML = `
    <div class="label">System</div>
    <div>BetPawa configured: <b>${status.betpawa_configured}</b></div>
    <div>BetPawa health: <b>${JSON.stringify(status.betpawa_health)}</b></div>
  `;
}

async function loadOpportunities() {
  const events = await api("/api/events");
  const tbody = $("#opp-table tbody"); tbody.innerHTML = "";
  for (const ev of events.slice(0, 30)) {
    const analysis = await api(`/api/analysis/${ev.id}`);
    if (!analysis.analysis) continue;
    for (const mp of analysis.market_predictions || []) {
      const fair = mp.fair_odds ? mp.fair_odds.toFixed(2) : "-";
      const edge = (mp.edge*100).toFixed(1) + "%";
      const evv = (mp.expected_value*100).toFixed(1) + "%";
      const decision = mp.expected_value > 0.02 && mp.agreement !== "LOW" ? "BET_CANDIDATE" : "WATCH";
      tbody.insertAdjacentHTML("beforeend", `
        <tr>
          <td>${ev.home_team} vs ${ev.away_team}</td>
          <td>${ev.league}</td>
          <td>${mp.market_type}</td>
          <td>${fmtPct(mp.model_probability)}</td>
          <td>${fair}</td>
          <td>${edge}</td>
          <td>${evv}</td>
          <td><span class="badge ${mp.agreement === 'HIGH' ? 'high' : mp.agreement === 'MODERATE' ? 'mod' : 'low'}">${mp.agreement}</span></td>
          <td>${decision}</td>
        </tr>
      `);
    }
  }
}

async function loadProposals(status) {
  const url = status ? `/api/proposals?status=${encodeURIComponent(status)}` : "/api/proposals";
  const props = await api(url);
  const tbody = $("#prop-table tbody"); tbody.innerHTML = "";
  for (const p of props) {
    tbody.insertAdjacentHTML("beforeend", `
      <tr>
        <td>${p.id}</td>
        <td>${p.market_type}</td>
        <td>${p.selection_name}</td>
        <td>${fmtNum(p.odds)}</td>
        <td>${fmtPct(p.model_probability)}</td>
        <td>${fmtPct(p.edge)}</td>
        <td>${fmtPct(p.expected_value)}</td>
        <td>${fmtNum(p.recommended_stake || p.approved_stake)}</td>
        <td><span class="badge">${p.status}</span></td>
        <td>
          ${p.status === "AWAITING_APPROVAL" ? `
            <button class="good" onclick="approve(${p.id})">Approve</button>
            <button class="bad" onclick="reject(${p.id})">Reject</button>
          ` : ""}
        </td>
      </tr>
    `);
  }
}

async function approve(id) {
  try {
    await api(`/api/proposals/${id}/approve`, { method: "POST", headers: {"Content-Type":"application/json"}, body: "{}" });
    loadProposals("");
  } catch (e) { alert("Approval failed: " + e.message); }
}
async function reject(id) {
  try {
    await api(`/api/proposals/${id}/reject`, { method: "POST" });
    loadProposals("");
  } catch (e) { alert("Reject failed: " + e.message); }
}

$$(".tabs button").forEach(b => b.addEventListener("click", () => loadProposals(b.dataset.status)));

async function loadBankroll() {
  const data = await api("/api/bankroll");
  $("#bankroll-summary").innerHTML = `
    <div class="card"><div class="label">Mode</div><div class="value">${data.mode.toUpperCase()}</div></div>
    <div class="card"><div class="label">Balance</div><div class="value">${fmtNum(data.balance)}</div></div>
  `;
  const tbody = $("#bankroll-table tbody"); tbody.innerHTML = "";
  for (const t of data.history) {
    tbody.insertAdjacentHTML("beforeend", `
      <tr><td>${t.created_at}</td><td>${t.kind}</td><td>${fmtNum(t.amount)}</td><td>${fmtNum(t.balance_after)}</td></tr>
    `);
  }
}

async function loadPerformance() {
  const bets = await api("/api/bets");
  const settled = bets.filter(b => b.status === "won" || b.status === "lost");
  const wins = settled.filter(b => b.status === "won").length;
  const totalStake = settled.reduce((s,b)=>s+b.stake,0);
  const totalPnl = settled.reduce((s,b)=>s+(b.pnl||0),0);
  $("#perf-summary").innerHTML = `
    <div class="card"><div class="label">Bets</div><div class="value">${bets.length}</div></div>
    <div class="card"><div class="label">Win Rate</div><div class="value">${settled.length ? fmtPct(wins/settled.length) : "-"}</div></div>
    <div class="card"><div class="label">Staked</div><div class="value">${fmtNum(totalStake)}</div></div>
    <div class="card"><div class="label">P/L</div><div class="value">${fmtNum(totalPnl)}</div></div>
  `;
}

loadDashboard();
