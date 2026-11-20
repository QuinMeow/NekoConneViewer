const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

test("desktop properties, per-actor voices, gaze and pause share the same scene", async () => {
  const source = file => readFileSync(join(__dirname, "../wallpaper", file), "utf8");
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, Object.assign(new EventTarget(), {
      setAttribute(name, value) { this[name] = value; },
      getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 400 }; }
    }));
    return elements.get(id);
  }
  const document = Object.assign(element("document"), { getElementById: element, hidden: false });
  const actors = ["10102", "10106"].map((id, i) => ({
    id, x: i ? 66 : 34, y: 98, scale: 100, voices: [id + "a.wav", id + "b.wav"]
  }));
  const calls = [], playback = [];
  let finishLoading, nextFrame;
  const window = {
    NekoDuoRenderer: { create: () => new Promise(resolve => { finishLoading = resolve; }) },
    NEKO_SCENE: { actors, backgrounds: ["title", "garden"].map(id => ({ id, file: id + ".jpg" })) }
  };
  class Audio {
    pause() { playback.push("pause"); }
    play() { playback.push(this.src); return Promise.resolve(); }
  }
  const ctx = vm.createContext({
    window, document, Audio, location: { search: "" }, URLSearchParams,
    requestAnimationFrame(fn) { nextFrame = fn; return 1; },
    cancelAnimationFrame() { nextFrame = null; }
  });
  vm.runInContext(source("host.js"), ctx);
  window.wallpaperPropertyListener.applyUserProperties({ p10102x: { value: 18 }, background: { value: "title" } });
  vm.runInContext(source("duo/scene.js"), ctx);
  const host = window.wallpaperPropertyListener;
  host.applyUserProperties({ p10106scale: { value: 140 } });
  const decoderPauses = [];
  finishLoading({ draw(a, p, dt) { calls.push({ a, p, dt }); },
    hitTest(x) { return x < .5 ? "10102" : "10106"; }, setPaused(v) { decoderPauses.push(v); } });
  await new Promise(resolve => setImmediate(resolve));
  nextFrame(16);
  assert.equal(element("background").src, "title.jpg");
  assert.deepEqual(Array.from(calls[0].a, a => [a.x, a.scale]), [[18, 100], [66, 140]]);
  function mouse(type, x) {
    element("actors").dispatchEvent(Object.assign(new Event(type), { clientX: x, clientY: 200, button: 0 }));
  }
  mouse("mousemove", 600);
  nextFrame(60);
  assert.equal(calls.at(-1).p.x, .75);
  assert.equal(calls.at(-1).p.y, .5);
  mouse("click", 200);
  mouse("click", 600);
  assert.deepEqual(playback, ["pause", "10102a.wav", "pause", "10106a.wav"]);
  host.applyUserProperties({ gaze: { value: false }, background: { value: "garden" } });
  nextFrame(110);
  assert.equal(calls.at(-1).p, null);
  assert.equal(element("background").src, "garden.jpg");
  host.applyGeneralProperties({ fps: { value: 15 } });
  nextFrame(200);
  const rendered = calls.length;
  nextFrame(218);
  assert.equal(calls.length, rendered);
  host.setPaused(true);
  mouse("click", 200);
  assert.equal(playback.at(-1), "pause");
  assert.equal(nextFrame, null);
  host.setPaused(false);
  nextFrame(5000);
  assert.equal(calls.at(-1).dt, 0);
  assert.deepEqual(decoderPauses, [false, true, false]);
  mouse("click", 200);
  assert.equal(playback.at(-1), "10102b.wav");
});
