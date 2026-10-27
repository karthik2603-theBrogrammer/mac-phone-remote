"use strict";

const $ = (id) => document.getElementById(id);
let paired = false;
let ready = false;
let selected = null;
let displaySignature = "";
let queue = [];
let draining = false;
let noticeTimer;
let tapTimer;

function notify(message) {
  $("notice").textContent = message;
  $("notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 7000);
}

function connection(online, label) {
  ready = online;
  $("controls").disabled = !online;
  $("status").classList.toggle("online", online);
  $("status-text").textContent = label;
}

function showPair() {
  paired = false;
  queue = [];
  resetGesture();
  connection(false, "Not paired");
  $("pair-screen").hidden = false;
  $("remote-screen").hidden = true;
  $("type-text").value = "";
}

async function api(path, body) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 5000);
  try {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      cache: "no-store",
      signal: abort.signal,
      headers: body === undefined ? {} : { "Content-Type": "application/json", "X-Remote-Request": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401 && path !== "/api/pair") showPair();
      throw new Error(data.error || `Request failed (${response.status}).`);
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("Connection timed out. The last action may have arrived; it was not retried.");
    if (error instanceof TypeError) throw new Error("Mac unreachable. Check Tailscale and keep the Mac remote running.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

// One request at a time preserves the order of movement, clicks, and keystrokes.
function enqueue(action) {
  if (!ready || !selected || document.hidden) return;
  const item = { ...action, display: selected };
  const last = queue[queue.length - 1];
  if (last && last.action === item.action && last.display === item.display &&
      ["move", "scroll"].includes(item.action)) {
    const limit = item.action === "move" ? 2000 : 1000;
    last.dx = Math.max(-limit, Math.min(limit, last.dx + item.dx));
    last.dy = Math.max(-limit, Math.min(limit, last.dy + item.dy));
  } else {
    if (queue.length >= 32) {
      queue = [];
      notify("Connection is too slow. Queued controls were cleared; wait and try again.");
      return;
    }
    queue.push(item);
  }
  void drain();
}

async function drain() {
  if (draining) return;
  draining = true;
  try {
    while (queue.length && ready) {
      const item = queue.shift();
      await api("/api/input", item);
    }
  } catch (error) {
    queue = [];
    resetGesture();
    connection(false, paired ? "Reconnecting" : "Not paired");
    notify(error.message);
  } finally {
    draining = false;
  }
}

function drawDisplays(displays) {
  const signature = JSON.stringify(displays);
  if (signature === displaySignature) return;
  const previous = selected;
  displaySignature = signature;
  if (!displays.some((display) => display.id === selected)) {
    selected = (displays.find((display) => display.primary) || displays[0])?.id || null;
    queue = [];
    if (previous) notify("Monitor setup changed. Check the selected screen before continuing.");
  }
  $("displays").replaceChildren();
  for (const display of displays) {
    const button = document.createElement("button");
    button.className = "display-card";
    button.type = "button";
    button.dataset.display = display.id;
    button.setAttribute("aria-pressed", String(display.id === selected));
    const icon = document.createElement("span");
    icon.className = "display-icon";
    icon.setAttribute("aria-hidden", "true");
    const check = document.createElement("span");
    check.className = "display-check";
    check.textContent = display.id === selected ? "\u2713" : "";
    check.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "display-name";
    name.textContent = display.name;
    const size = document.createElement("span");
    size.className = "display-size";
    size.textContent = `${display.width} \u00d7 ${display.height}${display.primary ? " \u00b7 Main" : ""}`;
    button.append(icon, check, name, size);
    button.addEventListener("click", () => {
      if (!ready) return;
      resetGesture();
      queue = [];
      selected = display.id;
      for (const card of $("displays").children) {
        const active = card.dataset.display === selected;
        card.setAttribute("aria-pressed", String(active));
        card.querySelector(".display-check").textContent = active ? "\u2713" : "";
      }
      enqueue({ action: "focus" });
    });
    $("displays").append(button);
  }
}

let refreshing = false;
async function refresh(silent = false) {
  if (refreshing) return;
  refreshing = true;
  try {
    const state = await api("/api/state");
    paired = true;
    $("pair-screen").hidden = true;
    $("remote-screen").hidden = false;
    $("demo-banner").hidden = !state.demo;
    drawDisplays(state.displays);
    connection(Boolean(selected), state.demo ? "Demo connected" : "Connected");
  } catch (error) {
    queue = [];
    resetGesture();
    connection(false, paired ? "Reconnecting" : "Not paired");
    if (!silent) notify(error.message);
  } finally {
    refreshing = false;
  }
}

$("pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("pair-button");
  button.disabled = true;
  try {
    await api("/api/pair", { code: $("pair-code").value.trim() });
    $("pair-code").value = "";
    await refresh();
  } catch (error) {
    notify(error.message);
  } finally {
    button.disabled = false;
  }
});

$("logout").addEventListener("click", async () => {
  queue = [];
  resetGesture();
  connection(false, "Disconnecting");
  try {
    await api("/api/logout", {});
    showPair();
  } catch (error) {
    notify(error.message);
    connection(false, "Disconnect failed");
  }
});
$("refresh").addEventListener("click", () => { void refresh(); });
for (const tab of ["trackpad", "keyboard"]) {
  $(`${tab}-tab`).addEventListener("click", () => {
    resetGesture();
    for (const name of ["trackpad", "keyboard"]) {
      $(`${name}-tab`).setAttribute("aria-selected", String(tab === name));
      $(`${name}-tab`).tabIndex = tab === name ? 0 : -1;
      $(`${name}-panel`).hidden = tab !== name;
    }
  });
  $(`${tab}-tab`).addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const next = $(`${tab === "trackpad" ? "keyboard" : "trackpad"}-tab`);
      next.click();
      next.focus();
    }
  });
}
document.querySelectorAll("[data-click]").forEach((button) => {
  button.addEventListener("click", () => enqueue({ action: "click", button: button.dataset.click }));
});
document.querySelectorAll("[data-key]").forEach((button) => {
  button.addEventListener("click", () => enqueue({ action: "key", key: button.dataset.key }));
});
document.querySelectorAll("[data-shortcut]").forEach((button) => {
  button.addEventListener("click", () => enqueue({ action: "shortcut", key: button.dataset.shortcut }));
});
document.querySelectorAll("[data-scroll]").forEach((button) => {
  button.addEventListener("click", () => enqueue({ action: "scroll", dx: 0, dy: -Number(button.dataset.scroll) }));
});
$("send-text").addEventListener("click", () => {
  const text = $("type-text").value;
  if (!text) return notify("Type something to send first.");
  if (/[\u0000-\u001f]/u.test(text)) return notify("Send one line at a time. Use Return or Tab separately.");
  enqueue({ action: "text", text });
});

const pad = $("trackpad");
const pointers = new Map();
let centroid = null;
let distance = 0;
let multi = false;
let held = false;
let longPress;

function center() {
  const points = [...pointers.values()];
  return points.length ? {
    x: points.reduce((sum, p) => sum + p.x, 0) / points.length,
    y: points.reduce((sum, p) => sum + p.y, 0) / points.length,
  } : null;
}

function resetGesture() {
  clearTimeout(longPress);
  clearTimeout(tapTimer);
  tapTimer = null;
  for (const id of pointers.keys()) {
    if (pad.hasPointerCapture(id)) pad.releasePointerCapture(id);
  }
  pointers.clear();
  centroid = null;
  distance = 0;
  multi = false;
  held = false;
  pad.classList.remove("touching");
}

pad.addEventListener("pointerdown", (event) => {
  if (!ready || (event.pointerType === "mouse" && event.button !== 0)) return;
  event.preventDefault();
  pad.setPointerCapture(event.pointerId);
  if (!pointers.size) {
    distance = 0;
    multi = false;
    held = false;
  }
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  centroid = center();
  pad.classList.add("touching");
  clearTimeout(longPress);
  if (pointers.size > 1) multi = true;
  else {
    longPress = setTimeout(() => {
      if (pointers.size === 1 && distance < 8 && !multi) {
        clearTimeout(tapTimer);
        tapTimer = null;
        held = true;
        enqueue({ action: "click", button: "right" });
      }
    }, 550);
  }
});

pad.addEventListener("pointermove", (event) => {
  if (!pointers.has(event.pointerId) || !ready) return;
  event.preventDefault();
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  const next = center();
  const dx = next.x - centroid.x;
  const dy = next.y - centroid.y;
  centroid = next;
  distance += Math.hypot(dx, dy);
  if (distance > 8) {
    clearTimeout(longPress);
    clearTimeout(tapTimer);
    tapTimer = null;
  }
  if (held) return;
  if (pointers.size >= 2) enqueue({ action: "scroll", dx, dy });
  else if (!multi) {
    const speed = Number($("sensitivity").value);
    enqueue({ action: "move", dx: dx * speed, dy: dy * speed });
  }
});

pad.addEventListener("pointerup", (event) => {
  if (!pointers.has(event.pointerId)) return;
  event.preventDefault();
  clearTimeout(longPress);
  pointers.delete(event.pointerId);
  centroid = center();
  if (pointers.size) return;
  pad.classList.remove("touching");
  if (!held && distance < 8) {
    if (multi) enqueue({ action: "click", button: "right" });
    else if (tapTimer) {
      clearTimeout(tapTimer);
      tapTimer = null;
      enqueue({ action: "click", button: "left", count: 2 });
    } else {
      // Delay a single tap briefly so a double tap sends one native double-click.
      tapTimer = setTimeout(() => {
        tapTimer = null;
        enqueue({ action: "click", button: "left" });
      }, 260);
    }
  }
});
pad.addEventListener("pointercancel", resetGesture);
pad.addEventListener("lostpointercapture", (event) => {
  if (pointers.has(event.pointerId)) resetGesture();
});
pad.addEventListener("contextmenu", (event) => event.preventDefault());
pad.addEventListener("wheel", (event) => {
  if (!ready) return;
  event.preventDefault();
  const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 300 : 1;
  enqueue({ action: "scroll", dx: Math.max(-1000, Math.min(1000, -event.deltaX * scale)), dy: Math.max(-1000, Math.min(1000, -event.deltaY * scale)) });
}, { passive: false });

document.addEventListener("visibilitychange", () => {
  queue = [];
  resetGesture();
  if (!document.hidden) void refresh(true);
});
window.addEventListener("offline", () => {
  queue = [];
  resetGesture();
  connection(false, "Offline");
});
window.addEventListener("online", () => { void refresh(true); });
setInterval(() => {
  if (paired && !document.hidden) void refresh(true);
}, 5000);
void refresh(true);
