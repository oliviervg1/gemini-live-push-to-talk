import { Player } from "/static/player.js";

const SPEECH_END_GRACE_MS = 200;    // keep streaming briefly after key release
const MAX_TURN_MS = 60_000;          // hard cap per press
const RECONNECT_BACKOFF = [250, 500, 1000, 2000, 5000];

const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const overlayEl = document.getElementById("overlay");

function setStatus(text, cls = "") {
  statusEl.textContent = text;
  statusEl.className = cls;
}

// ---- Audio capture ------------------------------------------------------

let audioCtx, micStream, workletNode, player;
let isPressed = false;
let isModelSpeaking = false;
let endGraceTimer = null;
let maxTurnTimer = null;

async function initAudio() {
  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
  } catch (err) {
    console.error("getUserMedia failed", err);
    overlayEl.classList.add("show");
    throw err;
  }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  await audioCtx.audioWorklet.addModule("/static/recorder-worklet.js");
  const src = audioCtx.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(audioCtx, "recorder-processor", {
    processorOptions: { sourceRate: audioCtx.sampleRate },
  });
  src.connect(workletNode);
  // Don't connect worklet to destination — we don't want to hear ourselves.
  workletNode.port.onmessage = (e) => {
    if (!isPressed && endGraceTimer === null) return;  // gate is closed
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(e.data);
    }
  };
  player = new Player(audioCtx);
}

// ---- WebSocket ----------------------------------------------------------

let ws = null;
let backoffIdx = 0;

function connect() {
  setStatus("connecting…");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    backoffIdx = 0;
    setStatus("ready", "ready");
  };

  ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      handleControl(JSON.parse(e.data));
    } else {
      isModelSpeaking = true;
      player.enqueue(e.data);
    }
  };

  ws.onclose = () => {
    setStatus("reconnecting…");
    isPressed = false;
    if (endGraceTimer) { clearTimeout(endGraceTimer); endGraceTimer = null; }
    const delay = RECONNECT_BACKOFF[Math.min(backoffIdx++, RECONNECT_BACKOFF.length - 1)];
    setTimeout(connect, delay);
  };

  ws.onerror = () => setStatus("error", "error");
}

// ---- Transcript rendering ----------------------------------------------

let currentUserTurn = null;
let currentModelTurn = null;

function appendTurn(role) {
  const turnEl = document.createElement("div");
  turnEl.className = `turn ${role}`;
  const labelEl = document.createElement("div");
  labelEl.className = "label";
  labelEl.textContent = role === "user" ? "You" : "Gemini";
  const textEl = document.createElement("div");
  textEl.className = "text";
  turnEl.appendChild(labelEl);
  turnEl.appendChild(textEl);
  transcriptEl.appendChild(turnEl);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return textEl;
}

function handleControl(msg) {
  switch (msg.type) {
    case "input_transcript": {
      if (!currentUserTurn) currentUserTurn = appendTurn("user");
      currentUserTurn.textContent = msg.text;
      if (msg.final) currentUserTurn = null;
      break;
    }
    case "output_transcript": {
      if (!currentModelTurn) currentModelTurn = appendTurn("model");
      currentModelTurn.textContent = (currentModelTurn.textContent || "") + msg.text;
      if (msg.final) currentModelTurn = null;
      break;
    }
    case "turn_complete":
      isModelSpeaking = false;
      currentUserTurn = null;
      currentModelTurn = null;
      setStatus("ready", "ready");
      break;
    case "interrupted":
      // confirmation only — UI already flushed locally
      break;
    case "error":
      setStatus(`error: ${msg.message}`, "error");
      break;
  }
}

// ---- Spacebar handling --------------------------------------------------

function pressStart() {
  if (isPressed) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  isPressed = true;
  if (endGraceTimer) { clearTimeout(endGraceTimer); endGraceTimer = null; }

  if (isModelSpeaking) {
    player.flush();
    ws.send(JSON.stringify({ type: "barge_in" }));
    isModelSpeaking = false;
    currentModelTurn = null;
  }
  ws.send(JSON.stringify({ type: "speech_start" }));
  setStatus("recording…", "recording");

  maxTurnTimer = setTimeout(() => {
    if (isPressed) {
      setStatus("max turn length reached", "error");
      pressEnd();
    }
  }, MAX_TURN_MS);
}

function pressEnd() {
  if (!isPressed) return;
  isPressed = false;
  if (maxTurnTimer) { clearTimeout(maxTurnTimer); maxTurnTimer = null; }

  // Keep the gate open during the grace period so the worklet's last
  // ~200ms of audio still gets sent before we signal speech_end.
  endGraceTimer = setTimeout(() => {
    endGraceTimer = null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "speech_end" }));
    }
    setStatus("ready", "ready");
  }, SPEECH_END_GRACE_MS);
}

window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.repeat && !e.target.matches("input,textarea")) {
    e.preventDefault();
    pressStart();
  }
});
window.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    pressEnd();
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && isPressed) pressEnd();
});

// ---- Boot ---------------------------------------------------------------

initAudio().then(connect).catch((err) => {
  console.error("init failed", err);
  setStatus("init failed", "error");
});
