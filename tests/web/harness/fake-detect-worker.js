// A stand-in detection worker for the showcase page tests: it downloads nothing and answers every
// detect request with the same canned boxes (normalized to the page render, as detect-worker.js
// answers). The page uses it through ?worker=, which it honours only on localhost. ?mode=:
//   (none)   load reports half the download, waits, reports all of it, then succeeds
//   fail     load fails with a plain message, as an offline download does
const mode = new URL(self.location.href).searchParams.get("mode");
const MB = 1024 * 1024;
const TOTAL = 38 * MB;

/** Five boxes: three text lines, a checkbox and a small box near the right edge. */
const BOXES = [
  { kind: "text", box: [0.1, 0.15, 0.6, 0.17], conf: 0.91 },
  { kind: "text", box: [0.1, 0.2, 0.6, 0.22], conf: 0.88 },
  { kind: "text", box: [0.1, 0.25, 0.9, 0.27], conf: 0.84 },
  { kind: "choice", box: [0.12, 0.3, 0.133, 0.31], conf: 0.8 },
  { kind: "text", box: [0.89, 0.35, 0.94, 0.365], conf: 0.7 },
];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

self.onmessage = async (event) => {
  const { id, type } = event.data;
  if (type === "load") {
    if (mode === "fail") {
      await wait(100);
      postMessage({ id, ok: false, message: "The detection model could not be downloaded. Check the connection and try again." });
      return;
    }
    postMessage({ id, type: "progress", loaded: TOTAL / 2, total: TOTAL });
    await wait(500);
    postMessage({ id, type: "progress", loaded: TOTAL, total: TOTAL });
    postMessage({ id, ok: true, provider: "wasm" });
  } else if (type === "detect") {
    await wait(50);
    postMessage({ id, ok: true, provider: "wasm", detections: BOXES, ms: 50 });
  }
};
