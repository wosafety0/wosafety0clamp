#!/usr/bin/env node
"use strict";

/**
 * Read-only Poi/KanColle coordinate collector.
 * Maps pointer coordinates from child frames into the game WebView viewport.
 * No mouse input is dispatched and no game state is changed.
 * Implementation: click-name annotation is implemented.
 * Verification: Node syntax and injected-script compilation only; live Poi interaction is not yet verified.
 */
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");

const args = process.argv.slice(2);
function arg(name, fallback) {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}
const port = Number(arg("--port", "9222"));
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outputPath = path.resolve(arg(
  "--output",
  path.join(__dirname, "..", "diagnostics", "captures",
    "poi-coordinate-capture-" + stamp + ".json")
));
const MAX_EVENTS = 20000;
const pending = new Map();
let nextId = 1;
let socket;
let stopping = false;
let savePromise;
const events = [];
const contexts = new Set();
const pendingLabelEvents = [];
let labelInterface;
let labelPromptActive = false;
let clickSequence = 0;
const metadata = {
  tool: "poi-coordinate-collector",
  schema_version: 1,
  started_at: new Date().toISOString(),
  coordinate_space: "webview-main-frame-css-pixels",
  input_dispatched: false,
  game_state_modified: false,
  target: null,
  viewport: null,
  frames: [],
  events_dropped: 0
};

function send(method, params) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return Promise.reject(new Error("CDP WebSocket is not open"));
  }
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("CDP timeout: " + method));
    }, 5000);
    pending.set(id, { resolve, reject, timer, method });
    socket.send(JSON.stringify({ id, method, params: params || {} }));
  });
}

async function selectTarget() {
  const response = await fetch("http://127.0.0.1:" + port + "/json/list", {
    signal: AbortSignal.timeout(4000)
  });
  if (!response.ok) throw new Error("CDP target list returned HTTP " + response.status);
  const targets = await response.json();
  const matches = targets.filter(item =>
    item && item.type === "webview" &&
    /(?:games\.dmm\.com\/game\/kancolle|kancolle-server\.com)/i.test(item.url || "")
  );
  if (matches.length !== 1) {
    const summary = targets
      .filter(item => item.type === "webview" || item.type === "page")
      .map(item => item.type + ": " + (item.url || item.title || "(no URL)"))
      .join("\n");
    throw new Error("Expected exactly one KanColle game WebView; found " +
      matches.length + ".\n" + summary);
  }
  return matches[0];
}

const injectedCollector = [
  "(() => {",
  "  if (window.__poiCoordinateCollectorInstalled) return 'already-installed';",
  "  window.__poiCoordinateCollectorInstalled = true;",
  "  const TAG = '__POI_COORDINATE_CAPTURE_V1__';",
  "  let lastMoveAt = 0;",
  "  function sendUp(record) {",
  "    if (window === window.top) {",
  "      if (typeof window.__poiCoordinateReport === 'function') window.__poiCoordinateReport(JSON.stringify(record));",
  "      return;",
  "    }",
  "    window.parent.postMessage({ __poiCoordinateTag: TAG, record }, '*');",
  "  }",
  "  window.addEventListener('message', event => {",
  "    const message = event.data;",
  "    if (!message || message.__poiCoordinateTag !== TAG || !message.record) return;",
  "    const child = Array.from(document.querySelectorAll('iframe')).find(frame => frame.contentWindow === event.source);",
  "    if (!child) return;",
  "    const rect = child.getBoundingClientRect();",
  "    const data = message.record;",
  "    const childWidth = Number(data.viewportWidth);",
  "    const childHeight = Number(data.viewportHeight);",
  "    if (!(childWidth > 0 && childHeight > 0)) return;",
  "    const mapped = { ...data,",
  "      x: rect.left + child.clientLeft + data.x * (child.clientWidth / childWidth),",
  "      y: rect.top + child.clientTop + data.y * (child.clientHeight / childHeight),",
  "      framePath: [String(child.id || child.name || child.title || 'iframe'), ...(data.framePath || [])],",
  "      coordinateMappedToParent: true, viewportWidth: window.innerWidth, viewportHeight: window.innerHeight",
  "    };",
  "    sendUp(mapped);",
  "  }, true);",
  "  function describeTarget(target) {",
  "    if (!target || !target.tagName) return null;",
  "    const rect = target.getBoundingClientRect();",
  "    const canvas = target.tagName.toLowerCase() === 'canvas' ? target : target.closest?.('canvas');",
  "    const canvasRect = canvas ? canvas.getBoundingClientRect() : null;",
  "    return { tag: target.tagName.toLowerCase(), id: target.id || null,",
  "      className: typeof target.className === 'string' ? target.className.slice(0, 160) : null,",
  "      targetRect: { x: +rect.x.toFixed(2), y: +rect.y.toFixed(2), width: +rect.width.toFixed(2), height: +rect.height.toFixed(2) },",
  "      canvas: canvas ? { width: canvas.width, height: canvas.height,",
  "        cssRect: { x: +canvasRect.x.toFixed(2), y: +canvasRect.y.toFixed(2), width: +canvasRect.width.toFixed(2), height: +canvasRect.height.toFixed(2) } } : null",
  "    };",
  "  }",
  "  function capture(type, event) {",
  "    const now = performance.now();",
  "    if (type === 'move' && now - lastMoveAt < 50) return;",
  "    if (type === 'move') lastMoveAt = now;",
  "    const canvas = event.target?.tagName?.toLowerCase() === 'canvas' ? event.target : event.target?.closest?.('canvas');",
  "    const canvasRect = canvas ? canvas.getBoundingClientRect() : null;",
  "    const canvasPoint = canvas ? { cssX: +(event.clientX - canvasRect.left).toFixed(2), cssY: +(event.clientY - canvasRect.top).toFixed(2),",
  "      pixelX: +((event.clientX - canvasRect.left) * canvas.width / Math.max(1, canvasRect.width)).toFixed(2),",
  "      pixelY: +((event.clientY - canvasRect.top) * canvas.height / Math.max(1, canvasRect.height)).toFixed(2) } : null;",
  "    sendUp({ type, timestamp: new Date().toISOString(),",
  "      x: +event.clientX.toFixed(2), y: +event.clientY.toFixed(2),",
  "      screenX: event.screenX, screenY: event.screenY,",
  "      viewportWidth: window.innerWidth, viewportHeight: window.innerHeight,",
  "      devicePixelRatio: window.devicePixelRatio, frameUrl: location.href, framePath: [],",
  "      target: describeTarget(event.target), canvasPoint, coordinateMappedToParent: false",
  "    });",
  "  }",
  "  window.addEventListener('pointermove', event => capture('move', event), true);",
  "  window.addEventListener('click', event => capture('click', event), true);",
  "  window.addEventListener('pointerdown', event => capture('pointerdown', event), true);",
  "  return 'installed';",
  "})()"
].join("\n");

if (args.includes("--self-test")) {
  try {
    new Function(injectedCollector);
    process.stdout.write("PASS: injected frame collector JavaScript compiles.\n");
    process.exit(0);
  } catch (error) {
    process.stderr.write("FAIL: injected frame collector JavaScript: " + error.message + "\n");
    process.exit(1);
  }
}

function promptForNextLabel() {
  if (!labelInterface || labelPromptActive || pendingLabelEvents.length === 0 || stopping) return;
  labelPromptActive = true;
  const item = pendingLabelEvents[0];
  process.stdout.write("\nLabel click #" + item.clickSequence +
    " (enter button name; blank to skip): ");
}

function handleLabelLine(value) {
  if (!labelPromptActive) return;
  const item = pendingLabelEvents.shift();
  if (!item) {
    labelPromptActive = false;
    return;
  }
  const label = String(value || "").trim();
  item.label = label || null;
  item.annotation_status = label ? "labeled" : "skipped";
  process.stdout.write(label
    ? "Recorded label: " + label + "\n"
    : "Skipped label for click #" + item.clickSequence + "\n");
  labelPromptActive = false;
  promptForNextLabel();
}

function addEvent(record) {
  if (!record || typeof record !== "object") return;
  if (events.length >= MAX_EVENTS) {
    metadata.events_dropped += 1;
    return;
  }
  const clean = {
    ...record,
    x: Number(record.x),
    y: Number(record.y),
    rootFrameX: Number(record.x),
    rootFrameY: Number(record.y),
    framePath: Array.isArray(record.framePath) ? record.framePath : []
  };
  events.push(clean);
  if (clean.type === "click") {
    clean.clickSequence = ++clickSequence;
    clean.label = null;
    clean.annotation_status = "pending";
    pendingLabelEvents.push(clean);
  }
  if (clean.type === "click" || clean.type === "pointerdown") {
    process.stdout.write("\n[" + clean.type + "] WebView=(" +
      clean.x.toFixed(1) + ", " + clean.y.toFixed(1) + ") frame=" +
      (clean.framePath.join("/") || "main") + " target=" +
      (clean.target?.tag || "unknown") + "#" + (clean.target?.id || "") + "\n");
  } else if (clean.type === "move") {
    process.stdout.write("\\rMouse WebView X=" + clean.x.toFixed(1) +
      " Y=" + clean.y.toFixed(1) + " frame=" +
      (clean.framePath.join("/") || "main") + "       ");
  }
  if (clean.type === "click") promptForNextLabel();
}

async function installInContext(contextId) {
  if (contexts.has(contextId)) return;
  contexts.add(contextId);
  try {
    await send("Runtime.evaluate", {
      expression: injectedCollector,
      contextId,
      returnByValue: true,
      awaitPromise: false
    });
  } catch (error) {
    process.stderr.write("\n[WARN] Frame instrumentation skipped: " + error.message + "\n");
  }
}

async function saveAndClose(reason) {
  if (savePromise) return savePromise;
  stopping = true;
  if (labelInterface) labelInterface.close();
  savePromise = (async () => {
    metadata.stopped_at = new Date().toISOString();
    metadata.click_count = clickSequence;
    metadata.labeled_click_count = events.filter(item => item.type === "click" && item.annotation_status === "labeled").length;
    metadata.stop_reason = reason;
    metadata.event_count = events.length;
    await fs.promises.mkdir(path.dirname(outputPath), { recursive: true });
    const document = { metadata, events };
    await fs.promises.writeFile(outputPath, JSON.stringify(document, null, 2) + "\n", "utf8");
    process.stdout.write("\n\nSaved " + events.length + " events to:\n" + outputPath + "\n");
    if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  })();
  return savePromise;
}

async function main() {
  if (typeof WebSocket !== "function") {
    throw new Error("Node.js with built-in WebSocket support is required (Node 22+).");
  }
  const target = await selectTarget();
  metadata.target = {
    id: target.id, type: target.type, title: target.title || null, url: target.url || null
  };
  socket = new WebSocket(target.webSocketDebuggerUrl);
  socket.addEventListener("message", async messageEvent => {
    let message;
    try { message = JSON.parse(messageEvent.data); } catch { return; }
    if (message.id && pending.has(message.id)) {
      const item = pending.get(message.id);
      pending.delete(message.id);
      clearTimeout(item.timer);
      if (message.error) item.reject(new Error(item.method + ": " + (message.error.message || "CDP error")));
      else item.resolve(message.result || {});
      return;
    }
    const event = message.method;
    const params = message.params || {};
    if (event === "Runtime.executionContextCreated") {
      const context = params.context;
      if (context && context.auxData?.isDefault && Number.isInteger(context.id)) {
        void installInContext(context.id);
      }
    } else if (event === "Runtime.executionContextDestroyed") {
      contexts.delete(params.executionContextId);
    } else if (event === "Runtime.executionContextsCleared") {
      contexts.clear();
    } else if (event === "Runtime.bindingCalled" && params.name === "__poiCoordinateReport") {
      try { addEvent(JSON.parse(params.payload)); } catch { /* ignore malformed page data */ }
    } else if (event === "Page.frameNavigated") {
      const frame = params.frame;
      if (frame) {
        metadata.frames = metadata.frames.filter(item => item.id !== frame.id);
        metadata.frames.push({ id: frame.id, parentId: frame.parentId || null, url: frame.url || null });
      }
    }
  });
  socket.addEventListener("error", () => process.stderr.write("\n[ERROR] CDP WebSocket error.\n"));
  socket.addEventListener("close", () => {
    if (!stopping) void saveAndClose("websocket-closed");
  });

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("CDP WebSocket connection timed out")), 5000);
    socket.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
    socket.addEventListener("error", () => {
      clearTimeout(timer);
      reject(new Error("CDP WebSocket connection failed"));
    }, { once: true });
  });

  await send("Runtime.addBinding", { name: "__poiCoordinateReport" });
  await send("Page.enable");
  await send("Runtime.enable");
  const results = await Promise.all([send("Page.getFrameTree"), send("Page.getLayoutMetrics")]);
  const viewport = results[1].cssVisualViewport || results[1].visualViewport || {};
  metadata.viewport = {
    width: viewport.clientWidth || null,
    height: viewport.clientHeight || null,
    pageX: viewport.pageX ?? null,
    pageY: viewport.pageY ?? null
  };
  function flatten(node) {
    if (!node || !node.frame) return;
    const frame = node.frame;
    metadata.frames.push({ id: frame.id, parentId: frame.parentId || null, url: frame.url || null });
    for (const child of node.childFrames || []) flatten(child);
  }
  flatten(results[0].frameTree);

  labelInterface = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  labelInterface.on("line", handleLabelLine);
  labelInterface.on("SIGINT", () => { void saveAndClose("ctrl-c"); });
  labelInterface.on("close", () => {
    if (!stopping) void saveAndClose("stdin-closed");
  });

  process.stdout.write("Poi coordinate collector active (read-only).\n");
  process.stdout.write("Target: " + target.url + "\n");
  process.stdout.write("WebView viewport: " + metadata.viewport.width + "x" +
    metadata.viewport.height + " CSS px\n");
  process.stdout.write("Move the mouse over the game; after each click, enter its button name in this terminal.\n");
  process.stdout.write("Press Enter to skip a label; Ctrl+C saves JSON.\n");
  process.stdout.write("No clicks are generated by this tool.\n");
  process.on("SIGINT", () => { void saveAndClose("ctrl-c"); });
  process.on("SIGTERM", () => { void saveAndClose("sigterm"); });
}

main().catch(error => {
  process.stderr.write("[ERROR] " + (error.stack || error.message) + "\n");
  process.exitCode = 1;
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
});
