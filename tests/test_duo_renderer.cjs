const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  const canvases = [], videos = [];
  function canvas() {
    const calls = [];
    const ctx = {drawImage(...args) { calls.push(args); }, clearRect() {}, fillRect() {}, save() {}, restore() {}};
    const obj = {clientWidth: 100, clientHeight: 100, width: 1, height: 1, getContext: () => ctx, calls};
    ctx.canvas = obj;
    canvases.push(obj);
    return obj;
  }
  const window = {devicePixelRatio: 1};
  class Image { set src(value) { this.url = value; this.onload(); } }
  function createElement(tag) {
    if (tag === 'canvas') return canvas();
    assert.equal(tag, 'video');
    const v = {set src(url) { this.url = url; this.onloadeddata(); }, active: false,
      play() { this.active = true; return Promise.resolve(); }, pause() { this.active = false; },
      requestVideoFrameCallback(cb) { this.cb = cb; return 1; },
      cancelVideoFrameCallback() { this.cb = null; },
      present(frame) { this.cb(0, {mediaTime: frame / 24}); }};
    videos.push(v);
    return v;
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../wallpaper/duo/renderer.js'), 'utf8'),
    {window, document: {createElement}, Image, atob});
  function actor(id, mask) {
    return {id, x: 50, y: 90, scale: 100, model: {width: 100, height: 100, portrait: 'body', video: id + '.webm',
      atlases: [id + '/eyes-0', id + '/eyes-1'], framesPerAtlas: 2, fps: 24, frames: 4,
      patch: [40,10,20,10], grid: 5,
      mask: {width: 3, height: 1, stride: 2, bits: Buffer.from([mask, 0]).toString('base64')}}};
  }
  return {create: window.NekoDuoRenderer.create, stage: canvas(), canvases, videos,
    actors: [actor('10102', 0xe0), actor('10106', 0x40)]};
}

test('topmost opaque actor wins; scaled or moved layers leave no stale hit region', async () => {
  const {create, stage, actors} = fixture();
  const r = await create(stage, actors);
  r.draw(actors, null, 0);
  assert.equal(r.hitTest(.5,.3), '10106');
  assert.equal(r.hitTest(.8,.3), '10102');
  assert.equal(r.hitTest(.05,.3), null);
  actors[1].x = 20; actors[1].y = 40; actors[1].scale = 30;
  r.draw(actors, null, 0);
  assert.equal(r.hitTest(.5,.3), '10102');
  assert.equal(r.hitTest(.2,.3), '10106');
  assert.equal(r.hitTest(.2,.6), '10102');
});

test('the same pointer drives different gaze tiles on either side of the cursor', async () => {
  const {create, stage, canvases, actors} = fixture();
  actors[0].x = 15; actors[1].x = 85;
  const r = await create(stage, actors);
  for (let i = 0; i < 12; i++) r.draw(actors, {x:.5, y:.5}, .08);
  const gazeX = id => canvases.flatMap(c => c.calls).filter(c => c[0].url === id + '/eyes-0').at(-1)[1] / 20;
  assert.ok(gazeX('10102') > 2);
  assert.ok(gazeX('10106') < 2);
  for (let i = 0; i < 12; i++) r.draw(actors, null, .08);
  const settledDraws = stage.calls.length;
  r.draw(actors, null, .08);
  assert.equal(stage.calls.length, settledDraws);
});

test('each decoder frame selects the matching atlas page, body and hit mask; pause stops decoders', async () => {
  const {create, stage, canvases, actors, videos} = fixture();
  const r = await create(stage, actors);
  r.setPaused(false);
  assert.ok(videos.every(v => v.active));
  r.draw(actors, {x:.5, y:.3}, .1);
  videos[1].present(3);
  assert.equal(r.hitTest(.5,.3), '10106', 'last displayed frame remains clickable before the next draw');
  r.draw(actors, {x:.5, y:.3}, .05);
  assert.equal(r.hitTest(.5,.3), '10102');
  const calls = canvases.flatMap(c => c.calls);
  assert.ok(calls.some(args => args[0] === videos[1]), 'captured body from the native video frame');
  assert.ok(calls.some(args => args[0].url === '10106/eyes-1' && args[2] >= 50 && args[2] < 100),
    'frame 3 = page 1, row 1*grid + gaze y');
  r.setPaused(true);
  assert.ok(videos.every(v => !v.active && !v.cb));
  r.setPaused(false);
  videos[1].present(0);
  r.draw(actors, null, 0);
  assert.equal(r.hitTest(.5,.3), '10106', 'looping resets the mask to the new frame');
});
