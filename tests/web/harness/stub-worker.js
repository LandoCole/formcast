// A stand-in detection worker for the time-limit tests, chosen by ?mode=:
//   ok          answers load (provider "wasm") and detect (no boxes)
//   trickle     answers load with two progress messages 200 ms apart, then goes silent
const mode = new URL(self.location.href).searchParams.get("mode");

self.onmessage = (event) => {
  const { id, type } = event.data;
  if (mode === "ok") {
    postMessage(type === "load" ? { id, ok: true, provider: "wasm" } : { id, ok: true, provider: "wasm", detections: [], ms: 1 });
  } else if (mode === "trickle" && type === "load") {
    postMessage({ id, type: "progress", loaded: 1, total: 3 });
    setTimeout(() => postMessage({ id, type: "progress", loaded: 2, total: 3 }), 200);
  }
};
