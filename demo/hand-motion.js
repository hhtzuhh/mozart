"use strict";

function describeHandMotion(hand, motion) {
  const side = hand === "left" ? "Left" : "Right";
  if (motion === "prepare_earlier" || motion === "prepare_later") {
    return side + " hand prepares 50 ms " + motion.split("_")[1];
  }
  const tip = /^tip_([1-5])_(higher|lower)$/.exec(motion);
  if (tip) return side + " fingertip " + tip[1] + " moves 11.75 mm toward the " + tip[2] + " keys";
  const shift = /^(higher|lower)_(one|half)_key$/.exec(motion);
  if (shift) {
    const distance = shift[2] === "one" ? "23.5" : "11.75";
    return side + " hand moves " + distance + " mm toward the " + shift[1] + " keys";
  }
  return side + " hand · " + motion.replaceAll("_", " ");
}

// Saved replays use the engine's field names. Live checkpoints use camel case.
function describeEdit(edit, step = 0.05) {
  const start = edit.start_frame ?? edit.startFrame;
  const stop = edit.stop_frame ?? edit.stopFrame;
  return (start * step).toFixed(2) + "–" + (stop * step).toFixed(2) + " s · " + describeHandMotion(edit.hand, edit.motion);
}
