"use strict";

const find = id => document.getElementById(id);
const session = window.REHEARSAL;
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
const motions = {
  higher_one_key: "Shift up one white key", lower_one_key: "Shift down one white key",
  higher_half_key: "Shift up half a white key", lower_half_key: "Shift down half a white key",
  prepare_earlier: "Prepare 50 ms earlier", prepare_later: "Prepare 50 ms later"
};
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
  const actionName = idea => (idea.hand === "left" ? "Left" : "Right") + " · " + (motions[idea.motion] || idea.motion);

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
    find("takeMidi").href = "recording/take-" + n + ".mid";
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
      showView(index + 1);
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
    if(r) values.push(["Tested passage F1",score(r.parent_window_f1)+" → "+score(r.trial_window_f1)],["Decision",r.adopted?"Retained":"Reverted"]);
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
    const ordered=r.ideas.map((idea,i)=>({idea,i})).sort((a,b)=>a.idea.start_frame-b.idea.start_frame);
    ordered.forEach(({idea,i},row)=>{
      const chosen=i===r.choice;
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
  function showView(i) {
    index=i; binIndex=-1;
    const r=round();
    if(r){
      ideaIndex=r.choice;
      const idea=r.ideas[ideaIndex];
      selectedWindow={start:idea.start_frame,stop:idea.stop_frame};
    }else{
      const bins=windows();
      binIndex=bins.reduce((best,w,i)=>w.after-w.before>bins[best].after-bins[best].before?i:best,0);
      selectedWindow=bins[binIndex];
    }
    const nav=find("rounds"); nav.replaceChildren();
    const labels=[...rounds.map(r=>"Round "+r.round),"Final performance"];
    labels.forEach((text,n)=>nav.append(button(text,"tab",()=>{
      stopAndPause(); showView(n);
    },index===n)));
    renderDecisions(); renderImpact();
    setTake(r?before():after(),r?r.ideas[ideaIndex].evidence_frame*data.step:0);
  }
  for(const n of [0,...rounds.map(r=>r.round)]){
    for (const mode of ["robot", "score"]) {
      const v = el("video");
      v.controls = true; v.preload = "metadata"; v.playsInline = true; v.hidden = true;
      v.setAttribute("aria-label", "Target and " + takeName(n) + " synchronized comparison");
      v.src = "recording/take-" + n + (mode === "score" ? "-score" : "") + ".mp4";
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
  find("subtitle").textContent=duration.toFixed(1)+" s clip  /  "+rounds.length+" rehearsal rounds";
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
  drawErrors(); showView(0); drawRoll();
}
if(session?.rounds?.length) startStudio(session);
else {
  find("notice").textContent="The rehearsal recording could not be loaded. Check recording/session.js.";
  find("notice").hidden=false;
  find("guide").disabled=true;
  find("compare").disabled=true;
}
