"use strict";

function showRecordedDecision(session) {
  const get = id => document.getElementById(id);
  const select = get("trace-round");
  for (const round of session.rounds) select.add(new Option(round.round, round.round));
  get("trace-song").textContent = session.title;
  function render() {
    const round = session.rounds[select.selectedIndex];
    const motion = round.motion.replaceAll("_", " ");
    get("trace-edit").textContent = round.hand + " hand · " + motion;
    const seconds = frame => (frame * session.step).toFixed(2);
    get("trace-passage").textContent = "Edited passage · " + seconds(round.start_frame) + "–" + seconds(round.stop_frame) + " s";
    get("trace-parent").textContent = (round.parent_f1 * 100).toFixed(2);
    get("trace-trial").textContent = (round.trial_f1 * 100).toFixed(2);
    get("trace-decision").textContent = round.adopted ? "Retained" : "Rejected";
  }
  select.addEventListener("change", render);
  render();
  get("recorded-trace").hidden = false;
}

if (window.REHEARSAL?.rounds?.length) showRecordedDecision(window.REHEARSAL);
