"use strict";

const find = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
function openStudio(personal) {
const catalog = window.STRATEGY_SESSIONS || {};
const picker = find("strategy");
const groupPicker = find("trainingGroup");
const requested = personal?.strategy || params.get("strategy") || "most_errors";
const selectedStrategy = Object.hasOwn(catalog, requested) ? requested
  : requested === "recorded" || !Object.keys(catalog).length ? "recorded" : "most_errors";
picker.value = selectedStrategy;
for (const option of picker.options) option.disabled = option.value !== "recorded" && !Object.hasOwn(catalog, option.value);
function groupsFor(strategy) {
  const entry = catalog[strategy];
  if (!entry) return {};
  // Older offline packages contain only the six-round branch.
  return Array.isArray(entry.rounds) ? {6: entry} : entry;
}
function availableBudgets(strategy) {
  if (strategy === "recorded") return [String(window.REHEARSAL.rounds.length)];
  return Object.keys(groupsFor(strategy)).sort((a, b) => Number(a) - Number(b));
}
const budgets = personal ? [String(personal.budget)] : availableBudgets(selectedStrategy);
const budget = budgets.includes(params.get("budget")) ? params.get("budget") : budgets.at(-1);
for (const value of budgets) groupPicker.add(new Option(value + "-round run", value));
groupPicker.value = budget;
groupPicker.disabled = budgets.length === 1;
function openRun(strategy, value) {
  const url = new URL(location.href);
  url.searchParams.set("strategy", strategy);
  url.searchParams.set("budget", value);
  url.searchParams.set("round", "1");
  location.href = url.href;
}
picker.onchange = () => {
  const choices = availableBudgets(picker.value);
  openRun(picker.value, choices.includes(budget) ? budget : choices.at(-1));
};
groupPicker.onchange = () => openRun(selectedStrategy, groupPicker.value);
if (personal) {
  picker.disabled = true;
  groupPicker.selectedOptions[0].textContent += " · " + new Date(personal.created * 1000).toLocaleString();
  const history = document.createElement("a");
  history.href = "live.html#" + personal.runId;
  history.textContent = "My rehearsal history";
  groupPicker.parentElement.append(history);
  find("roundStatus").querySelector("a").href = history.href;
  find("roundStatus").querySelector("a").textContent = history.textContent;
}
const matrix = personal || groupsFor(selectedStrategy)[budget];
const session = matrix ? {...window.REHEARSAL, ...matrix, strategies:[]} : window.REHEARSAL;
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
function button(text, cls, action, pressed) {
  const node = el("button", text, cls);
  node.type = "button";
  node.addEventListener("click", action);
  if (pressed !== undefined) node.setAttribute("aria-pressed", String(pressed));
  return node;
}
function explain(parent, label, paragraphs) {
  const details = el("details");
  details.append(el("summary", label));
  for (const paragraph of paragraphs) details.append(el("p", paragraph));
  parent.append(details);
}
const score = value => (value * 100).toFixed(2);
function delta(before, after) {
  const n = Number(score(after - before));
  return (n > 0 ? "+" : "") + n.toFixed(2);
}
function notes(pitches) {
  const names = ["C","C♯","D","D♯","E","F","F♯","G","G♯","A","A♯","B"];
  return pitches.map(p => names[p % 12] + (Math.floor(p / 12) - 1)).join(" · ") || "Rest";
}
function startStudio(data) {
  const rounds = data.rounds;
  const updateChart = rehearsalChart(find("replayChart"));
  updateChart(rounds, Number(budget), data.baseline);
  let personalState, publishedRounds = rounds.length;
  if (personal && rounds.length < Number(budget)) {
    async function pollPersonal() {
      try {
        const response = await fetch("/api/runs", {cache:"no-store"});
        if (!response.ok) throw Error();
        personalState = (await response.json()).find(run => run.id === personal.runId);
        if (!personalState) throw Error();
        const current = find("rounds").dataset.selected;
        if (Number(current) > rounds.length) {
          find("roundUpdate").textContent = personalState.failed || Number(current) === personalState.scored + 1
            ? personalState.phase : "Waiting for this round's replay.";
        }
        if (personalState.scored > rounds.length) {
          const next = await loadPersonal(personal.runId);
          if (next.rounds.length > rounds.length) {
            publishedRounds = next.rounds.length;
            updateChart(next.rounds, Number(budget), data.baseline);
            const url = new URL(location.href);
            if (Number(current) > rounds.length && Number(current) <= next.rounds.length) {
              url.searchParams.set("round",current); location.replace(url.href); return;
            }
            url.searchParams.set("round",String(rounds.length+1));
            const link = find("newRounds");
            link.href = url.href; link.textContent = "Open newly scored round " + (rounds.length+1); link.hidden = false;
            if (next.rounds.length === personal.budget) return;
          }
        }
        if (personalState.failed) return;
      } catch { /* A worker may still be publishing the latest replay. */ }
      setTimeout(pollPersonal, 3000);
    }
    setTimeout(pollPersonal, 0);
  }
  const roundLimit = Number(budget);
  const takePrefix = n => data.media?.[n] || "recording/take-" + n;
  const duration = data.frames * data.step;
  const seconds = frame => (frame * data.step).toFixed(2);
  const span = (a, b) => seconds(a) + "–" + seconds(b) + " s";
  const videos = new Map();
  let index = 0, ideaIndex = 0, active, selectedWindow, binIndex = -1;
  let comparing = null, guided = false, sequence = 0;
  let listenWindow, previewEnd = null;
  const requestedSeeks = new WeakMap();
  const round = () => rounds[index];
  const before = () => round() ? round().parent_round : 0;
  const after = () => round() ? round().round : data.bestRound;
  const windows = () => round() ? round().windows : data.windows;
  const takeName = n => n === 0 ? "Robot baseline" : "Take " + n;
  const actionName = idea => describeHandMotion(idea.hand, idea.motion);

  let activeTake = 0, sound = "robot", displayedFrame = -1, animation;
  const frameStates = spans => {
    const states = Array.from({length:data.frames}, () => []);
    for (const [start, stop, pitch] of spans) {
      for (let f = start; f < stop; f++) states[f].push(pitch);
    }
    return states;
  };
  const targetFrames = frameStates(data.notes);
  const playedFrames = new Map(Object.entries(data.playedNotes).map(([n, spans]) => [Number(n), frameStates(spans)]));
  const frameNumber = () => Math.min(data.frames - 1, Math.max(0, Math.floor((active?.currentTime || 0) / data.step + .000001)));
  function differences(frame) {
    const target = targetFrames[frame], played = playedFrames.get(activeTake)[frame];
    return {missed:target.filter(n => !played.includes(n)), extra:played.filter(n => !target.includes(n))};
  }
  function drawErrors() {
    const canvas = find("errorCanvas");
    canvas.width = data.frames;
    canvas.height = 2;
    const ctx = canvas.getContext("2d");
    let hasErrors = false;
    for (let f = 0; f < data.frames; f++) {
      const {missed, extra} = differences(f);
      hasErrors ||= !!(missed.length || extra.length);
      ctx.fillStyle = missed.length ? "#ab482b" : "#a6d7b1";
      ctx.fillRect(f, 0, 1, 1);
      ctx.fillStyle = extra.length ? "#6452b4" : "#a6d7b1";
      ctx.fillRect(f, 1, 1, 1);
    }
    find("nextError").disabled = !hasErrors;
  }
  function inspectFrame(f) {
    if (f === displayedFrame) return;
    displayedFrame = f;
    const {missed, extra} = differences(f);
    find("frameTime").textContent = seconds(f) + " s";
    find("targetNotes").textContent = notes(targetFrames[f]);
    find("playedNotes").textContent = notes(playedFrames.get(activeTake)[f]);
    const errors = find("errorNotes");
    errors.replaceChildren();
    if (missed.length) errors.append(el("span", "Missed · " + notes(missed), "missed"));
    if (extra.length) errors.append(el("span", "Extra · " + notes(extra), "extra"));
    if (!missed.length && !extra.length) errors.append(el("span", "All keys match", "chip"));
    find("scrub").value = f;
    find("scrub").setAttribute("aria-valuetext", seconds(f) + " seconds");
    find("errorCursor").style.left = (100 * f / data.frames) + "%";
  }
  function followPlayback() {
    cancelAnimationFrame(animation);
    if (previewEnd !== null) checkBoundary();
    else updatePlayhead();
    if (active && !active.paused && !active.ended) animation = requestAnimationFrame(followPlayback);
  }
  function selectListenWindow(start, stop) {
    listenWindow = {start, stop};
    find("listenRange").textContent = span(start, stop);
  }
  function listenFromFrame(f) {
    const inPassage = f >= selectedWindow.start && f < selectedWindow.stop;
    const stop = inPassage ? selectedWindow.stop : Math.min(data.frames, f + Math.round(2 / data.step));
    selectListenWindow(f, stop);
  }
  function goToFrame(f) {
    stopAndPause();
    listenFromFrame(f);
    setTake(activeTake, f * data.step);
  }
  find("scrub").max = data.frames - 1;
  find("scrub").oninput = event => goToFrame(Number(event.target.value));
  find("nextError").onclick = () => {
    const current = frameNumber();
    const fingerprint = f => {
      const {missed, extra} = differences(f);
      return missed.length || extra.length ? missed.join(",") + "/" + extra.join(",") : "";
    };
    let previous = fingerprint(current);
    for (let offset = 1; offset <= data.frames; offset++) {
      const f = (current + offset) % data.frames;
      const error = fingerprint(f);
      if (error && (f === 0 || error !== previous)) { goToFrame(f); return; }
      previous = error;
    }
  };

  function notice(message) {
    find("notice").textContent = message;
    find("notice").hidden = !message;
  }
  function stopSequence() {
    guided = false;
    comparing = null;
    previewEnd = null;
    sequence++;
    find("guide").textContent = "▶  Replay rehearsal";
    find("compare").textContent = "▶  Hear before & after";
  }
  function stopAndPause() {
    stopSequence();
    if (active) active.pause();
  }
  function setTake(n, time, play = false) {
    active?.pause();
    if (active) active.hidden = true;
    const video = videos.get(n + "-" + sound);
    if (activeTake !== n) {
      activeTake = n;
      drawErrors();
    }
    displayedFrame = -1;
    active = video;
    video.hidden = false;
    if (video.preload === "none") { video.preload = "metadata"; video.load(); }
    const ticket = ++sequence;
    function seek() {
      if (ticket !== sequence || active !== video) return;
      const end = Number.isFinite(video.duration) ? video.duration : duration;
      const position = Math.max(0, Math.min(time, end));
      requestedSeeks.set(video, position);
      video.currentTime = position;
      if (play) video.play().catch(error => {
        if (ticket !== sequence || error.name === "AbortError") return;
        stopSequence();
        notice("Playback paused. Use the player’s play button to hear this take.");
      });
    }
    if (video.readyState >= 1) seek();
    else video.addEventListener("loadedmetadata", seek, {once:true});
    const takeF1 = n === before() ? (round()?.parent_f1 ?? data.baseline) : (round()?.trial_f1 ?? data.best);
    find("takeLabel").textContent = takeName(n) + " · F1 " + score(takeF1);
    find("takeMidi").href = takePrefix(n) + ".mid";
    const bar = find("versions");
    bar.replaceChildren();
    const versions = [[before(), round() ? "Before" : "Baseline"], [after(), round() ? "Trial" : "Best"]];

    for (const [id, label] of versions) {
      const control = button(label, "", () => {
        const at = active.currentTime, playing = !active.paused;
        stopSequence();
        setTake(id, at, playing);
      }, id === n);
      bar.append(control);
    }
    const audioBar = find("sound");
    audioBar.replaceChildren();
    for (const [mode, label] of [["score", "Target piano"], ["robot", "Robot"]]) {
      audioBar.append(button(label, "", () => {
        stopAndPause();
        sound = mode;
        previewEnd = listenWindow.stop * data.step;
        setTake(n, listenWindow.start * data.step, true);
      }, sound === mode));
    }
    updatePlayhead();
  }
  function updatePlayhead() {
    inspectFrame(frameNumber());
    const time = active?.currentTime || 0;
    find("playhead").style.left = Math.min(99.8, 100 * time / duration) + "%";
  }
  function beginComparison() {
    sound = "robot";
    comparing = {phase:"before", start:selectedWindow.start * data.step, end:selectedWindow.stop * data.step};
    find("compare").textContent = "■  Stop comparison";
    setTake(before(), comparing.start, true);
  }
  function checkBoundary() {
    updatePlayhead();
    if (previewEnd !== null && active && !active.seeking && (active.ended || active.currentTime >= previewEnd)) {
      const end = previewEnd;
      stopAndPause();
      requestedSeeks.set(active, end);
      active.currentTime = end;
      return;
    }
    if (!comparing || !active || (!active.ended && active.paused)) return;
    if (!active.ended && active.currentTime < comparing.end) return;
    if (comparing.phase === "before") {
      comparing.phase = "after";
      setTake(after(), comparing.start, true);
    } else if (guided && round()) {
      showView(index + 1 < rounds.length ? index + 1 : roundLimit);
      if (round()) beginComparison();
      else {
        stopSequence();
        setTake(data.bestRound, 0, true);
      }
    } else {
      stopAndPause();
    }
  }
  function drawRoll() {
    const c = find("roll"), rect = c.getBoundingClientRect();
    if (!rect.width) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(rect.width*dpr); c.height = Math.round(rect.height*dpr);
    const ctx = c.getContext("2d");
    ctx.scale(dpr,dpr);
    const w = rect.width, h = rect.height;
    ctx.fillStyle = "#cbd6cd";
    for (let t=0; t<duration; t+=2) ctx.fillRect(t / duration*w,0,1,h);
    const pitches = data.notes.map(n=>n[2]);
    const bottom = Math.min(...new Set(pitches)) - 2, top = Math.max(...new Set(pitches)) + 2;
    ctx.fillStyle = "#81a18b";
    for (const [a,b,p] of data.notes) {
      ctx.fillRect(a/data.frames*w,(top-p)/(top-bottom)*h,Math.max(2,(b-a)/data.frames*w),2);
    }
  }
  function pickBin(i) {
    stopAndPause();
    binIndex=i;
    selectedWindow=windows()[i];
    setTake(round() ? before() : after(), selectedWindow.start*data.step);
    renderImpact();
  }
  function renderImpact() {
    const r=round(), base=r ? r.parent_f1 : data.baseline, result=r ? r.trial_f1 : data.best;
    const metrics=find("metrics");
    metrics.replaceChildren();
    const values=[["Full-clip F1",score(base)+" → "+score(result)],["Full-clip gain",delta(base,result)+" pts"]];
    if(r) {
      if(r.edits) values.push(["Tested passages",String(r.edits.length)]);
      else values.push(["Tested passage F1",score(r.parent_window_f1)+" → "+score(r.trial_window_f1)]);
      values.push(["Decision",r.adopted?"Retained":"Reverted"]);
    }
    else values.push(["Best performance",takeName(data.bestRound)]);
    for(const [label,value] of values){
      const m=el("div",undefined,"metric"); m.append(el("small",label),el("b",value)); metrics.append(m);
    }
    const bins=find("windows"); bins.replaceChildren();
    windows().forEach((w,i)=>{
      const d=delta(w.before,w.after), desc=span(w.start,w.stop)+" · "+d+" F1 points";
      const b=button(d,"bin "+(Number(d)>0?"positive":Number(d)<0?"negative":""),()=>pickBin(i),i===binIndex);
      b.prepend(el("small", Number(seconds(w.start))+"s"));
      b.title=desc; b.setAttribute("aria-label",desc); bins.append(b);
    });
    find("selection").textContent="Listen · "+span(selectedWindow.start,selectedWindow.stop);
    selectListenWindow(selectedWindow.start, selectedWindow.stop);
    find("impactLabel").textContent=r ? takeName(before())+" vs "+takeName(after()) : "Robot baseline vs best";
  }
  function pickIdea(i) {
    stopAndPause(); ideaIndex=i; binIndex=-1;
    const idea=round().ideas[i];
    selectedWindow={start:idea.start_frame,stop:idea.stop_frame};
    renderDecisions();
    renderImpact();
    setTake(before(),idea.evidence_frame*data.step);
  }
  function renderDecisions() {
    const r=round(), ideas=find("ideas"), detail=find("detail"), markers=find("markers");
    ideas.replaceChildren(); detail.replaceChildren(); markers.replaceChildren();
    find("panelKicker").textContent=r?"Gemini 4 Neon + Hydrogen":"Rehearsal result";
    find("panelTitle").textContent=r?"Practice decisions":"Best performance";
    find("mapKey").textContent=r?"Solid = AI choice":"Target notes";
    const initial = r ? r.parent_f1 : data.baseline;
    const measured = r ? r.trial_f1 : data.best;
    const headline = el("div", undefined, "final-score");
    headline.dataset.direction = measured > initial ? "up" : measured < initial ? "down" : "flat";
    headline.append(
      el("span", r ? "This round · Full-clip F1 improvement" : "Overall · Full-clip F1 improvement"),
      el("strong", delta(initial, measured) + " pts"),
      el("b", takeName(before()) + " (" + score(initial) + ") → " + takeName(after()) + " (" + score(measured) + ")")
    );
    ideas.append(headline);
    if(!r){
      ideas.append(el("p", "Select a time window below to hear the robot baseline alongside the best retained performance.", "scope"));
      return;
    }
    find("map").style.minHeight = (r.ideas.length * 22 + 6) + "px";
    const ordered=r.ideas.map((idea,i)=>({idea,i})).sort((a,b)=>a.idea.start_frame-b.idea.start_frame);
    ordered.forEach(({idea,i},row)=>{
      const chosen=(r.choices || [r.choice]).includes(i);
      const stamp=span(idea.start_frame,idea.stop_frame);
      const card=button("","idea",()=>pickIdea(i),i===ideaIndex);
      const label=el("span",stamp,"stamp");
      if(chosen) label.append(el("span","✓ AI choice","chip"));
      card.append(label,el("strong",actionName(idea))); ideas.append(card);
      const marker=button((idea.hand==="left"?"L":"R")+(chosen?" ✓":""),"mark"+(chosen?" chosen":"")+(i===ideaIndex?" focused":""),()=>pickIdea(i),i===ideaIndex);
      marker.title=stamp+" · "+actionName(idea);
      marker.setAttribute("aria-label",marker.title);
      marker.style.left=100*idea.start_frame/data.frames+"%";
      marker.style.width=100*(idea.stop_frame-idea.start_frame)/data.frames+"%";
      marker.style.top=(5+row*22)+"px"; markers.append(marker);
    });
    const idea=r.ideas[ideaIndex];
    detail.append(el("strong","Key evidence at "+seconds(idea.evidence_frame)+" s"));
    const pitches=el("dl",undefined,"notes");
    for(const [label,keys] of [["Target",idea.target],["Before",idea.before],["Trial",idea.after]]){
      pitches.append(el("dt",label),el("dd",notes(keys)));
    }
    detail.append(pitches);
    explain(detail,"What Neon observed",[idea.observation,idea.hypothesis]);
    explain(detail,"Why Hydrogen chose the tested edit",[r.rationale]);
  }
  function showEdits(number, edits, outcome = "", scored = true) {
    find("roundEdits").hidden = !edits.length;
    find("editHeading").textContent = (scored ? "Applied" : "Selected") +
      (edits.length > 1 ? " adjustments" : " adjustment") + " · Round " + number;
    find("appliedEdits").replaceChildren(...edits.map(edit => el("li", describeEdit(edit, data.step))));
    find("editTactic").textContent = outcome;
    find("editTactic").hidden = !outcome;
  }
  function outcomeText(tactic, f1, retained) {
    return [tactic, "Full-clip F1 " + score(f1), retained ? "Retained" : "Rejected"].filter(Boolean).join(" · ");
  }
  function statusPanel(visible) {
    for (const node of document.querySelectorAll(".console,.impact,.foot")) node.hidden = visible;
    find("songReference").hidden = visible || !data.scoreAudio;
    find("roundStatus").hidden = !visible;
    find("guide").disabled = visible || !rounds.length;
  }
  let pendingTimer, allScored = rounds.length === roundLimit;
  function refreshPending(i) {
    if (personal || index !== i || selectedStrategy !== "gemini_adaptive") return;
    const script = el("script");
    script.src = ".local/adaptive-progress.js?t=" + Date.now();
    script.onload = () => {
      script.remove();
      if (index !== i) return;
      const snapshot = window.matrixSnapshot;
      if (!snapshot || snapshot.song !== data.task || snapshot.baseline !== data.baseline) return;
      const group = snapshot.groups?.gemini_adaptive?.[budget];
      if (!group) return;
      const bestView = i === roundLimit;
      const number = bestView ? group.bestRound : i + 1;
      const trial = group.history.find(t => t.number === number);
      if (group.scored === roundLimit) {
        allScored = true;
        find("rounds").lastElementChild.textContent = "Final performance";
      }
      if (trial && (!bestView || number > rounds.length)) {
        if (bestView && find("roundStatus").hidden) { stopAndPause(); statusPanel(true); }
        find("statusTitle").textContent = picker.selectedOptions[0].text +
          (bestView ? " · Best retained · Round " : " · Round ") + number;
        const edits = trial.edits || (trial.hand ? [trial] : []);
        showEdits(number, edits);
        find("roundUpdate").textContent = outcomeText(trial.tactic, trial.f1, trial.retained);
        const recent = find("recentTake");
        if (trial.video && recent.getAttribute("src") !== trial.video) {
          recent.src = trial.video;
          recent.hidden = false;
        }
      } else if (!bestView) {
        const current = i === group.scored;
        find("roundUpdate").textContent = current ? group.phase : "Waiting for earlier rounds to finish.";
        showEdits(number, current ? group.currentEdits || [] : [], "", false);
      }
      if (!allScored && (bestView || !trial)) pendingTimer = setTimeout(() => refreshPending(i), 3000);
    };
    script.onerror = () => { script.remove(); };
    document.head.append(script);
  }
  function showView(i) {
    index=i; binIndex=-1;
    clearTimeout(pendingTimer);
    const pending = i < roundLimit && !round();
    const nav=find("rounds"); nav.replaceChildren();
    nav.dataset.selected = i === roundLimit ? "final" : String(i + 1);
    const labels=Array.from({length:roundLimit}, (_, n) => "Round " + (n+1));
    labels.push(allScored ? "Final performance" : "Best so far");
    labels.forEach((text,n)=>nav.append(button(text,"tab",()=>{
      stopAndPause(); showView(n);
    },index===n)));
    statusPanel(pending);
    const recent = find("recentTake");
    recent.pause();
    recent.hidden = true;
    if (recent.hasAttribute("src")) { recent.removeAttribute("src"); recent.load(); }
    if (pending) {
      showEdits(i + 1, []);
      find("statusTitle").textContent = picker.selectedOptions[0].text + " · Round " + (i + 1);
      if (personal && i < publishedRounds) {
        const url = new URL(location.href);
        url.searchParams.set("round", String(i+1)); location.replace(url.href); return;
      }
      find("roundUpdate").textContent = personal ? "Waiting for this round’s replay." : "This round has no packaged replay yet.";
      refreshPending(i);
      return;
    }
    const r=round();
    const take = r || rounds.find(t => t.round === data.bestRound);
    const applied = take ? take.edits || (take.choices || [take.choice]).map(id => take.ideas[id]) : [];
    showEdits(take?.round, applied, take ? outcomeText(take.tactic, take.trial_f1, take.adopted) : "");
    if (!r && rounds.length < roundLimit) refreshPending(i);
    if(r){
      ideaIndex=r.choice;
      const idea=r.ideas[ideaIndex];
      selectedWindow={start:idea.start_frame,stop:idea.stop_frame};
    }else{
      const bins=windows();
      binIndex=bins.reduce((best,w,i)=>w.after-w.before>bins[best].after-bins[best].before?i:best,0);
      selectedWindow=bins[binIndex];
    }
    renderDecisions(); renderImpact();
    setTake(r && !matrix ? before() : after(), r ? r.ideas[ideaIndex].evidence_frame * data.step : 0);
  }
  for(const n of [0,...rounds.map(r=>r.round)]){
    for (const mode of ["robot", "score"]) {
      const v = el("video");
      v.controls = true; v.preload = "none"; v.playsInline = true; v.hidden = true;
      v.setAttribute("aria-label", "Target and " + takeName(n) + " synchronized comparison");
      v.src = takePrefix(n) + (mode === "score" ? "-score" : "") + ".mp4";
      v.addEventListener("error", () => notice("Could not load the synchronized recording for " + takeName(n) + "."));
      v.addEventListener("playing", () => { if (v === active) followPlayback(); });
      v.addEventListener("seeking", () => {
        if (v !== active) return;
        const requested = requestedSeeks.get(v);
        if (requested !== undefined && Math.abs(v.currentTime - requested) < data.step / 2) return;
        stopSequence();
        listenFromFrame(frameNumber());
      });
      v.addEventListener("seeked", () => {
        requestedSeeks.delete(v);
        if (v === active) updatePlayhead();
      });
      for (const event of ["ended", "timeupdate"]) v.addEventListener(event, () => { if (v === active) checkBoundary(); });
      videos.set(n + "-" + mode, v);
      find("stage").append(v);
    }
  }
  if (data.scoreAudio) {
    find("songReference").hidden = false;
    find("playScore").onclick = () => {
      stopAndPause();
      sound = "score";
      setTake(activeTake, 0, true);
    };
    if (/^SomewhereOnlyWeKnow_\d+$/.test(data.task)) {
      const official = find("officialSong");
      official.href = "https://www.youtube.com/watch?v=Oextk-If8HQ";
      official.hidden = false;
      official.addEventListener("click", stopAndPause);
    }
  }
  find("title").textContent=data.title;
  find("subtitle").textContent=duration.toFixed(1)+" s clip  /  "+roundLimit+" rehearsal rounds";
  const ruler=find("ruler");
  for(let i=0;i<=4;i++) ruler.append(el("span",(duration*i/4).toFixed(1)+"s"));
  const strategies=find("strategies");
  for(const arm of data.strategies){
    const name=arm.strategy==="gemini_windows"?"Gemini-selected passages":arm.strategy==="most_errors"?"Most errors first":arm.strategy;
    strategies.append(el("p",name+" · "+arm.count+" trials · Best F1 "+score(arm.best)));
  }
  find("benchmark").hidden=data.strategies.length<2;
  find("guide").onclick=()=>{
    if(guided){stopAndPause();return;}
    stopAndPause(); guided=true; showView(0);
    find("guide").textContent="■  Stop replay";
    beginComparison();
  };
  find("compare").onclick=()=>{
    if(comparing){stopAndPause();return;}
    stopAndPause(); beginComparison();
  };
  new ResizeObserver(drawRoll).observe(find("map"));
  const requestedRound = params.get("round");
  const first = Number(requestedRound) - 1;
  drawErrors(); showView(requestedRound === "final" ? roundLimit
    : Number.isInteger(first) && first >= 0 && first < roundLimit ? first : 0); drawRoll();
}
if(session?.frames && session?.playedNotes) startStudio(session);
else {
  find("notice").textContent="The rehearsal recording could not be loaded. Check recording/session.js.";
  find("notice").hidden=false;
  find("guide").disabled=true;
  find("compare").disabled=true;
}

}

async function loadPersonal(id) {
  if (!/^[0-9a-f]{32}$/.test(id)) throw Error("Invalid rehearsal ID.");
  const response = await fetch(".local/replays/" + id + "/session.json", {cache:"no-store"});
  if (!response.ok) throw Error("The personal replay is being prepared.");
  const replay = await response.json();
  if (replay.runId !== id) throw Error("Rehearsal ID mismatch.");
  return replay;
}

async function openPersonal(id) {
  document.body.classList.add("loading-replay");
  try {
    const replay = await loadPersonal(id);
    document.body.classList.remove("loading-replay");
    find("notice").hidden = true;
    openStudio(replay);
  } catch (error) {
    let message = error.message;
    let retry = false;
    try {
      const response = await fetch("/api/runs", {cache:"no-store"});
      if (!response.ok) throw Error();
      const run = (await response.json()).find(run => run.id === id);
      message = run ? (run.failed ? run.phase : "Preparing your rehearsal · " + run.phase) : "This rehearsal could not be found.";
      retry = !!run && !run.failed;
    } catch { message = "Open your rehearsal from the local Live studio."; }
    find("notice").textContent = message; find("notice").hidden = false;
    if (retry) setTimeout(() => openPersonal(id), 3000);
  }
}
if (params.has("replay")) openPersonal(params.get("replay"));
else openStudio(null);
