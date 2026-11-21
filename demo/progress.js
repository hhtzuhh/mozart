"use strict";

const $ = id => document.getElementById(id);
const titles = {
  most_errors: "Most errors first", gemini_windows: "Gemini selects",
  switch_on_stall: "Switch after a stall", gemini_three: "Gemini selects three"
};
const score = n => (n * 100).toFixed(2);
const gain = n => (n > 0 ? "+" : "") + score(n);
let budget, lastRender = "", budgetKey = "";
let chartMode = "trial";
const seriesStyles = {
  most_errors:      {letter: "A", color: "#195ec9", shape: "circle"},
  gemini_windows:   {letter: "B", color: "#be5105", shape: "diamond"},
  switch_on_stall:  {letter: "C", color: "#943fa2", shape: "triangle"},
  gemini_three:     {letter: "D", color: "#007c68", shape: "square"}
};
const hiddenStrategies = new Set();
function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function svgNode(tag, attributes, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function mark(shape, x, y, r, color) {
  const paint = {fill:color, stroke:"white", "stroke-width":1.4};
  if (shape === "circle") return svgNode("circle", {cx:x, cy:y, r, ...paint});
  if (shape === "square") return svgNode("rect", {x:x-r, y:y-r, width:2*r, height:2*r, rx:1, ...paint});
  const corners = shape === "diamond"
    ? [[x,y-r*1.3], [x+r*1.3,y], [x,y+r*1.3], [x-r*1.3,y]]
    : [[x,y-r*1.3], [x+r*1.2,y+r], [x-r*1.2,y+r]];
  return svgNode("polygon", {points:corners.map(p => p.join(",")).join(" "), ...paint});
}

function updateLegend(arms) {
  const legend = $("legend");
  if (legend.dataset.series === arms.map(([name]) => name).join(",")) return;
  legend.dataset.series = arms.map(([name]) => name).join(",");
  legend.replaceChildren();
  for (const [name] of arms) {
    const style = seriesStyles[name];
    const label = element("label");
    const input = element("input");
    input.type = "checkbox";
    input.checked = !hiddenStrategies.has(name);
    label.style.setProperty("--series", style.color);
    const icon = svgNode("svg", {width:15, height:15, "aria-hidden":"true"});
    icon.append(mark(style.shape, 7.5, 7.5, 5, style.color));
    label.append(input, icon, element("span", style.letter + " · " + titles[name]));
    input.onchange = () => {
      if (input.checked) hiddenStrategies.delete(name);
      else hiddenStrategies.add(name);
      plotScores();
    };
    legend.append(label);
  }
}

function plotScores() {
  const state = window.matrixSnapshot;
  if (!state?.groups || !budget) return;
  const arms = Object.entries(state.groups);
  const plot = $("f1Plot");
  updateLegend(arms);
  const visible = arms.filter(([name]) => !hiddenStrategies.has(name));
  const width = Math.max(260, plot.clientWidth);
  const compact = width < 650;
  const left = 35, right = width - (compact ? 77 : 226);
  const top = 20, bottom = 220;
  const x = round => left + round / Number(budget) * (right - left);
  const scores = [state.baseline];
  for (const [, groups] of arms) {
    for (const group of Object.values(groups)) {
      for (const trial of group.history) scores.push(trial.f1);
    }
  }
  const low = Math.max(0, Math.floor((Math.min(...scores) * 100 - 1) / 5) * 5);
  const high = Math.min(100, Math.ceil((Math.max(...scores) * 100 + 1) / 5) * 5);
  const y = f1 => bottom - (f1 * 100 - low) / (high - low) * (bottom - top);
  plot.setAttribute("viewBox", `0 0 ${width} 262`);
  plot.replaceChildren(svgNode("title", {}, `${budget}-round budget, ${chartMode === "trial" ? "actual replay" : "best retained"} F1 on a 100-point scale`));
  for (let tick = low; tick <= high; tick += high - low > 40 ? 10 : 5) {
    plot.append(svgNode("line", {x1:left, x2:right, y1:y(tick/100), y2:y(tick/100), stroke:"#e1e7e5"}),
      svgNode("text", {x:left-8, y:y(tick/100)+4, "text-anchor":"end"}, tick));
  }
  const header = element("tr");
  header.append(element("th", "Strategy"));
  for (let n = 0; n <= Number(budget); n++) {
    plot.append(svgNode("text", {x:x(n), y:bottom+19, "text-anchor":"middle"}, n));
    header.append(element("th", n === 0 ? "Baseline" : "R" + n));
  }
  plot.append(svgNode("text", {x:(left+right)/2, y:258, "text-anchor":"middle"}, "Round"));
  const table = $("scoreTable");
  table.replaceChildren(header);
  if (!visible.length) {
    plot.replaceChildren(svgNode("text", {x:width/2, y:120, "text-anchor":"middle"}, "Select a strategy above to plot its F1."));
    return;
  }
  const ends = [];
  for (const [name, groups] of visible) {
    const title = titles[name], style = seriesStyles[name];
    const values = new Map([[0, state.baseline]]);
    let retained = state.baseline;
    for (const trial of groups[budget].history) {
      if (trial.retained) retained = trial.f1;
      values.set(trial.number, chartMode === "trial" ? trial.f1 : retained);
    }
    const path = Array.from(values, ([n, f1]) => x(n) + "," + y(f1)).join(" ");
    plot.append(svgNode("polyline", {points:path, fill:"none", stroke:style.color, "stroke-width":3.2,
      "stroke-linecap":"round", "stroke-linejoin":"round"}));
    for (const [n, f1] of values) {
      if (n === 0) continue;
      const point = mark(style.shape, x(n), y(f1), 5, style.color);
      point.append(svgNode("title", {}, `${title} · Round ${n} · F1 ${score(f1)}`));
      plot.append(point);
    }
    const latest = groups[budget].history.at(-1);
    if (latest) ends.push({title, style, round:latest.number, f1:values.get(latest.number)});
    const row = element("tr");
    row.append(element("th", title));
    for (let n = 0; n <= Number(budget); n++) row.append(element("td", values.has(n) ? score(values.get(n)) : "—"));
    table.append(row);
  }

  // Spread nearly equal endpoints apart in the label column without moving any data point.
  ends.sort((a, b) => b.f1 - a.f1);
  let nextY = top;
  for (const end of ends) {
    end.labelY = Math.max(y(end.f1), nextY);
    nextY = end.labelY + 24;
  }
  let limit = bottom;
  for (const end of ends.slice().reverse()) {
    end.labelY = Math.min(end.labelY, limit);
    limit = end.labelY - 24;
  }
  for (const end of ends) {
    const tagX = right + 15;
    if (end.round === Number(budget)) {
      plot.append(svgNode("line", {x1:right+5, y1:y(end.f1), x2:tagX-2, y2:end.labelY,
        stroke:end.style.color, opacity:.5}));
    }
    const label = compact ? end.style.letter : end.title;
    const tag = svgNode("text", {x:tagX, y:end.labelY+4, class:"series-tag"}, label);
    tag.style.fill = end.style.color;
    tag.append(svgNode("title", {}, `${end.title} · latest score at round ${end.round}`));
    const value = svgNode("text", {x:width-3, y:end.labelY+4, "text-anchor":"end", class:"series-value"}, score(end.f1));
    plot.append(tag, value);
  }
  const start = svgNode("circle", {cx:x(0), cy:y(state.baseline), r:4, fill:"#233d32"});
  start.append(svgNode("title", {}, "Shared baseline F1 " + score(state.baseline)));
  plot.append(start);
}

for (const [id, mode] of [["trialMode", "trial"], ["bestMode", "best"]]) {
  $(id).onclick = () => {
    chartMode = mode;
    $("trialMode").setAttribute("aria-pressed", String(mode === "trial"));
    $("bestMode").setAttribute("aria-pressed", String(mode === "best"));
    plotScores();
  };
}
new ResizeObserver(plotScores).observe($("f1Plot"));

function render() {
  const state = window.matrixSnapshot;
  if (!state) {
    $("freshness").textContent = "Start the local watcher with python3 submission/demo/watch_matrix.py from the workspace.";
    return;
  }
  const age = Math.max(0, Date.now() / 1000 - state.observed);
  const time = new Date(state.observed * 1000).toLocaleTimeString();
  const freshness = state.finished ? "Experiment complete" : age > 12 ? "Updates paused · last snapshot" : "Watching engine files";
  $("freshness").textContent = freshness + " · " + time;
  $("runName").textContent = state.run + (state.song ? " / " + state.song : "");
  $("matrix").hidden = !!state.waiting;
  if (state.waiting) {
    $("freshness").textContent += " · waiting for the run protocol";
    $("count").textContent = "—";
    return;
  }
  $("count").textContent = state.scored + " / " + state.total;
  const arms = Object.entries(state.groups);
  const budgets = Object.keys(arms[0][1]).sort((a, b) => a - b);
  if (!budgets.includes(budget)) budget = budgets[0];
  if (budgetKey !== budgets.join(",")) {
    budgetKey = budgets.join(",");
    $("budget").replaceChildren(...budgets.map(b => {
      const btn = element("button", b + " rounds");
      btn.type = "button";
      btn.onclick = () => { budget = b; render(); };
      return btn;
    }));
  }
  Array.from($("budget").children).forEach((b, i) => b.setAttribute("aria-pressed", String(budgets[i] === budget)));
  const fingerprint = JSON.stringify([state.run, state.baseline, budget, arms.map(([, groups]) => groups[budget])]);
  if (lastRender === fingerprint) return;
  lastRender = fingerprint;
  plotScores();
  $("arms").replaceChildren(...arms.map(([name, groups]) => {
    const g = groups[budget];
    const card = element("article", undefined, "strategy-arm");
    card.append(element("h2", titles[name] || name), element("p", g.scored + " / " + budget + " rounds scored", "arm-meta"));
    const stage = g.scored < Number(budget) ? "Round " + (g.scored + 1) + " · " : "";
    card.append(element("p", stage + g.phase + (g.retries ? " · retries " + g.retries : ""), "arm-phase"));
    card.append(element("small", "Best full-clip F1 gain"), element("strong", gain(g.best - state.baseline) + " pts"),
      element("p", score(state.baseline) + " → " + score(g.best) + (g.bestRound ? " · Take " + g.bestRound : " · Baseline"), "arm-meta"));
    const list = element("ol");
    for (const trial of g.history) {
      const item = element("li");
      const label = "Take " + trial.number;
      if (trial.video) {
        const link = element("a", label + " ▶");
        link.href = trial.video; link.target = "_blank"; link.rel = "noopener"; item.append(link);
      } else item.append(label);
      item.append(" · " + gain(trial.gain) + " pts · " + (trial.retained ? "Retained" : "Rejected"));
      list.append(item);
    }
    card.append(list);
    return card;
  }));
}

function refresh() {
  const script = document.createElement("script");
  script.src = ".local/matrix-progress.js?t=" + Date.now();
  script.onload = script.onerror = () => {
    script.remove();
    render();
    if (!window.matrixSnapshot?.finished) setTimeout(refresh, 3000);
  };
  document.head.append(script);
}
refresh();
