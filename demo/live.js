"use strict";

const byId = id => document.getElementById(id);
const buttons = Array.from(byId("launch").querySelectorAll("button[name=strategy]"));
const strategies = new Map(buttons.map(b => [b.value, b.textContent]));
const local = ["localhost", "127.0.0.1"].includes(location.hostname) && location.protocol === "http:";
const replayId = new URLSearchParams(location.search).get("replay");
if (replayId !== null) {
  const destination = new URL("index.html", location.href);
  destination.search = location.search;
  location.replace(destination.href);
}
const pendingKey = "mozart.pendingLaunches";
const pending = new Map(local ? JSON.parse(sessionStorage.getItem(pendingKey) || "[]") : []);
const sending = new Set();
let runs = [], selected = location.hash.slice(1), connected = false, refreshNumber = 0;
if (local && !selected) selected = sessionStorage.getItem("mozart.liveRun") || "";

function requestKey(strategy) {
  return [byId("song").value, strategy, byId("budget").value].join("/");
}

function renderButtons() {
  for (const button of buttons) {
    const request = pending.get(requestKey(button.value));
    const busy = request && sending.has(request.id);
    button.disabled = !connected || !!busy;
    button.textContent = (busy ? "Starting " : request ? "Retry " : "") + strategies.get(button.value);
  }
}

function modalLink(url, label) {
  const link = document.createElement("a");
  link.href = url; link.textContent = label; link.target = "_blank"; link.rel = "noopener noreferrer";
  return link;
}

function callLink(id, label) {
  return modalLink("https://modal.com/id/" + encodeURIComponent(id), label);
}

function phase(run) {
  return (run.scored < run.rounds && !run.failed ? "Round " + (run.scored + 1) + " · " : "") + run.phase;
}

function showActivity() {
  const body = byId("activityRows");
  body.replaceChildren();
  const seen = new Set();
  for (const run of runs) {
    // Keep every active run and the newest run of each strategy once it finishes.
    if (seen.has(run.strategy) && (run.failed || run.scored === run.rounds)) continue;
    seen.add(run.strategy);
    const row = body.insertRow();
    const link = document.createElement("a");
    link.href = "#" + run.id; link.textContent = strategies.get(run.strategy);
    row.insertCell().append(link);
    row.insertCell().textContent = run.scored + " / " + run.rounds;
    row.insertCell().textContent = phase(run);
    const calls = row.insertCell();
    if (run.modalAppUrl) calls.append(modalLink(run.modalAppUrl, "App ↗"));
    for (const trial of run.history) {
      if (trial.callId) calls.append(callLink(trial.callId, "R" + trial.number + " GPU ↗"));
    }
    if (run.waitingCall) calls.append(callLink(run.waitingCall.id, "R" + (run.scored + 1) + " GPU ↗"));
    if (!calls.childElementCount) calls.textContent = "Awaiting first call";
  }
  for (const request of pending.values()) {
    if (runs.some(r => r.id === request.id)) continue;
    const row = body.insertRow();
    row.insertCell().textContent = strategies.get(request.strategy);
    row.insertCell().textContent = "0 / " + request.rounds;
    row.insertCell().textContent = sending.has(request.id) ? "Submitting to the local engine" :
      (request.error || "Launch unconfirmed") + " · retry this strategy";
    row.insertCell().textContent = "—";
  }
  byId("activity").hidden = !body.rows.length;
}

const drawRunChart = rehearsalChart(byId("replayChart"));

function showRun() {
  const selectedIsPending = Array.from(pending.values()).some(r => r.id === selected);
  const current = runs.find(run => run.id === selected) || (!selectedIsPending && runs[0]);
  byId("history").hidden = !current;
  if (!current) return;
  selected = current.id;
  sessionStorage.setItem("mozart.liveRun", selected);
  byId("runs").value = selected;
  byId("name").textContent = strategies.get(current.strategy);
  byId("phase").textContent = phase(current);
  byId("bar").max = current.rounds;
  byId("bar").value = current.scored;
  const first = current.history[0];
  const baseline = first ? first.parentF1 ?? first.f1 - first.gain : current.best;
  drawRunChart(current.history.map(t => ({trial_f1:t.f1})), current.rounds, baseline);
  const f1 = n => (n * 100).toFixed(2);
  byId("counts").textContent = current.scored + " / " + current.rounds + " scored" +
    (current.best == null ? "" : " · Best full-clip F1 " + f1(current.best));
  byId("tactic").textContent = current.currentTactic || "";
  byId("edits").textContent = (current.currentEdits || []).map(e => describeEdit(e)).join(" / ");
  byId("call").hidden = !current.waitingCall;
  if (current.waitingCall) byId("call").href = "https://modal.com/id/" + encodeURIComponent(current.waitingCall.id);
  byId("trials").replaceChildren(...current.history.map(t => {
    const row = document.createElement("li");
    row.append("F1 " + f1(t.f1) + " · " + (t.retained ? "Retained" : "Rejected"));
    if (t.video) {
      const link = document.createElement("a");
      link.href = "index.html?" + new URLSearchParams({replay: current.id, round: t.number}); link.textContent = "Watch round " + t.number; link.target = "_blank"; link.rel = "noopener";
      row.append(" · ", link);
    }
    if (t.callId) row.append(" · ", callLink(t.callId, "Modal GPU call ↗"));
    for (const edit of t.edits || []) {
      const action = document.createElement("div");
      action.textContent = describeEdit(edit);
      row.append(action);
    }
    return row;
  }));
  const link = byId("personal");
  link.hidden = false;
  link.href = "index.html?" + new URLSearchParams({replay: current.id});
  link.textContent = "Replay my rehearsal · " + strategies.get(current.strategy) + " · " + current.rounds + (current.rounds === 1 ? " round" : " rounds");
}

async function refresh() {
  const number = ++refreshNumber;
  try {
    const response = await fetch("/api/runs", {cache:"no-store"});
    if (response.status === 404 && local && number === refreshNumber && await openLocalEngine()) return;
    if (!response.ok) throw Error("Local engine unavailable");
    const updated = await response.json();
    if (number !== refreshNumber) return;
    connected = true;
    runs = updated;
    for (const [key, request] of pending) {
      if (!sending.has(request.id) && runs.some(r => r.id === request.id)) pending.delete(key);
    }
    sessionStorage.setItem(pendingKey, JSON.stringify(Array.from(pending)));
    const choices = runs.map(r => new Option(new Date(r.created * 1000).toLocaleString() +
      " · " + strategies.get(r.strategy) + " · " + r.rounds + " rounds · " + r.id.slice(0,6), r.id));
    byId("runs").replaceChildren(...choices);
    showRun(); showActivity();
    byId("setup").hidden = true;
    byId("connection").textContent = "Each button starts its own run. Gemini selects the edits before each GPU replay.";
  } catch {
    if (number !== refreshNumber) return;
    connected = false;
    byId("setup").hidden = false;
    byId("connection").textContent = "The local rehearsal server is unavailable.";
  }
  renderButtons();
}

async function openLocalEngine() {
  // A static preview can serve this HTML while the live engine occupies the other local port.
  for (const port of ["8794", "8795"]) {
    if (location.port === port) continue;
    const page = new URL("/live.html", location.href);
    page.port = port; page.search = location.search; page.hash = location.hash;
    try {
      const reply = await fetch(new URL("/api/health", page), {cache:"no-store", signal:AbortSignal.timeout(1500)});
      if (reply.ok && (await reply.json()).service === "mozart-live") {
        location.replace(page.href);
        return true;
      }
    } catch { /* This port may have no live engine. */ }
  }
  return false;
}

byId("runs").onchange = () => { selected = byId("runs").value; location.hash = selected; showRun(); };
window.addEventListener("hashchange", () => { selected = location.hash.slice(1); showRun(); });
byId("launch").onchange = renderButtons;
for (const button of buttons) {
  button.addEventListener("click", e => { if (e.detail > 1) e.preventDefault(); });
}
byId("launch").onsubmit = async e => {
  e.preventDefault();
  const strategy = e.submitter?.value;
  if (!connected || !strategies.has(strategy)) return;
  const key = requestKey(strategy);
  const request = pending.get(key) || {id: crypto.randomUUID().replaceAll("-",""),
    song:byId("song").value, strategy, rounds:Number(byId("budget").value)};
  if (sending.has(request.id)) return;
  sending.add(request.id); pending.set(key, request);
  sessionStorage.setItem(pendingKey, JSON.stringify(Array.from(pending)));
  selected = request.id; location.hash = selected;
  renderButtons(); showRun(); showActivity();
  try {
    const {id, song, rounds} = request;
    const response = await fetch("/api/runs", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id, song, strategy, rounds})});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || "Submission failed");
    if (result.id !== id) throw Error("Launch not confirmed");
    pending.delete(key);
  } catch (error) {
    request.error = error.message;
  } finally {
    sending.delete(request.id);
    sessionStorage.setItem(pendingKey, JSON.stringify(Array.from(pending)));
    await refresh();
    renderButtons(); showActivity();
  }
};
if (local) {
  (async function poll() { await refresh(); setTimeout(poll, 3000); })();
} else {
  byId("connection").textContent = "Live runs use the local rehearsal server.";
  byId("setup").hidden = false;
}
