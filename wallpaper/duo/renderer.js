(() => {
  'use strict';
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  function load(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('无法读取角色图片: ' + src));
      img.src = src;
    });
  }
  function loadVideo(src) {
    return new Promise((resolve, reject) => {
      const video = document.createElement('video');
      if (!video.requestVideoFrameCallback) {
        reject(new Error('当前浏览器不支持逐帧视频回调，请更新 Wallpaper Engine 或使用新版 Chromium。'));
        return;
      }
      video.muted = true; video.loop = true; video.playsInline = true; video.preload = 'auto';
      video.onloadeddata = () => resolve(video);
      video.onerror = () => reject(new Error('无法载入角色待机视频: ' + src));
      video.src = src;
    });
  }
  function surface(width, height) {
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('无法创建角色画布');
    return ctx;
  }
  async function sprite(actor) {
    const m = actor.model, [, , w, h] = m.patch;
    const [poster, video, atlases] = await Promise.all([
      load(m.portrait), loadVideo(m.video), Promise.all(m.atlases.map(load))
    ]);
    const feather = surface(w, h);
    // The baked patch has a neutral rim: fade across that rim only to avoid a
    // rectangular RGB/WebM colour seam without ghosting the moving pupils.
    const rings = Math.min(6, Math.ceil(Math.min(w, h) / 2));
    for (let i = 0; i < rings; i++) {
      feather.globalAlpha = 1 / (rings - i);
      feather.fillRect(i, i, w - i*2, h - i*2);
    }
    const s = {model: m, video, atlases, feather, layer: surface(m.width, m.height),
      original: surface(w, h), edge: surface(w, h), eyes: surface(w, h), bits: atob(m.mask.bits),
      gx: 0, gy: 0, frame: 0, handle: null, last: ''};
    capture(s, poster, 0);
    return s;
  }
  function capture(s, source, frame) {
    const {width, height} = s.model;
    s.frame = frame;
    s.newFrame = true;
    s.last = '';
    s.layer.clearRect(0, 0, width, height);
    s.layer.drawImage(source, 0, 0, width, height);
  }
  function saveOriginal(s) {
    const [l,t,w,h] = s.model.patch;
    s.original.clearRect(0, 0, w, h);
    s.original.drawImage(s.layer.canvas, l,t,w,h, 0,0,w,h);
    s.edge.clearRect(0, 0, w, h);
    s.edge.drawImage(s.original.canvas, 0, 0);
    s.edge.save();
    s.edge.globalCompositeOperation = 'destination-out';
    s.edge.drawImage(s.feather.canvas, 0, 0);
    s.edge.restore();
  }
  function paintEyes(s, tx, ty, dt) {
    const follow = 1 - Math.exp(-14 * dt);
    const approach = (v, t) => Math.abs(v - t) < .001 ? t : v + (t - v) * follow;
    s.gx = approach(s.gx, tx); s.gy = approach(s.gy, ty);
    const {grid, framesPerAtlas, patch: [l,t,w,h]} = s.model;
    const tile = v => Math.round((v + 1) * .5 * (grid - 1) * 128) / 128;
    const gx = tile(s.gx), gy = tile(s.gy);
    const newFrame = s.newFrame;
    s.newFrame = false;
    if (gx === (grid-1)/2 && gy === (grid-1)/2) {
      if (!s.last) return newFrame;
      s.layer.clearRect(l,t,w,h);
      s.layer.drawImage(s.original.canvas, l, t);
      s.last = '';
      return true;
    }
    const key = gx + ':' + gy;
    if (key === s.last) return false;
    if (!s.last) saveOriginal(s);
    s.last = key;
    const ix = Math.floor(gx), iy = Math.floor(gy), fx = gx - ix, fy = gy - iy;
    const atlas = s.atlases[Math.floor(s.frame / framesPerAtlas)];
    const baseRow = (s.frame % framesPerAtlas) * grid;
    const eyes = s.eyes;
    eyes.clearRect(0, 0, w, h);
    eyes.save();
    eyes.globalCompositeOperation = 'lighter';
    for (const [x, wx] of [[ix, 1-fx], [ix+1, fx]]) {
      for (const [y, wy] of [[iy, 1-fy], [iy+1, fy]]) {
        const weight = wx * wy;
        if (!weight) continue;
        eyes.globalAlpha = weight;
        eyes.drawImage(atlas, x*w, (baseRow+y)*h, w,h, 0,0,w,h);
      }
    }
    eyes.globalAlpha = 1;
    eyes.globalCompositeOperation = 'destination-in';
    eyes.drawImage(s.feather.canvas, 0, 0);
    eyes.restore();
    s.layer.clearRect(l, t, w, h);
    s.layer.drawImage(s.edge.canvas, l, t);
    s.layer.save();
    s.layer.globalCompositeOperation = 'lighter';
    s.layer.drawImage(eyes.canvas, l, t);
    s.layer.restore();
    return true;
  }
  async function create(canvas, actors, onError) {
    const sprites = new Map(await Promise.all(actors.map(async actor => [actor.id, await sprite(actor)])));
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('无法创建角色画布');
    let placements = [], paused = true;
    function schedule(s) {
      s.handle = s.video.requestVideoFrameCallback((_, metadata) => {
        if (paused) return;
        // Capture immediately: video.currentTime is the playback clock, not
        // the timestamp of the pixels actually delivered by the decoder.
        const frame = clamp(Math.round(metadata.mediaTime * s.model.fps), 0, s.model.frames - 1);
        capture(s, s.video, frame);
        schedule(s);
      });
    }
    function setPaused(value) {
      if (value === paused) return;
      paused = value;
      for (const s of sprites.values()) {
        if (paused) {
          s.video.pause();
          if (s.handle !== null) s.video.cancelVideoFrameCallback(s.handle);
          s.handle = null;
        } else {
          schedule(s);
          s.video.play().catch(error => {
            if (!paused && error.name !== 'AbortError') onError(error);
          });
        }
      }
    }
    for (const s of sprites.values()) {
      s.video.onerror = () => onError(new Error('角色待机播放失败: ' + s.model.video));
    }
    function draw(sceneActors, pointer, elapsed) {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.round(canvas.clientWidth*dpr));
      const h = Math.max(1, Math.round(canvas.clientHeight*dpr));
      let dirty = canvas.width !== w || canvas.height !== h || placements.length !== sceneActors.length;
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
      const dt = clamp(elapsed || 0, 0, 1);
      placements = sceneActors.map((actor, index) => {
        const s = sprites.get(actor.id), m = s.model;
        const sh = h * .85 * actor.scale / 100, sw = sh * m.width / m.height;
        const x = actor.x * w / 100 - sw / 2, y = actor.y * h / 100 - sh;
        const [pl, pt, pw, ph] = m.patch;
        const eyeX = x + (pl + pw / 2) * sw / m.width;
        const eyeY = y + (pt + ph / 2) * sh / m.height;
        const tx = pointer ? clamp((pointer.x*w-eyeX) / (.50*sh), -1, 1) : 0;
        const ty = pointer ? clamp((pointer.y*h-eyeY) / (.42*sh), -1, 1) : 0;
        const old = placements[index];
        if (paintEyes(s, tx, ty, dt) || !old || old.id !== actor.id ||
            old.x !== x || old.y !== y || old.sw !== sw || old.sh !== sh) dirty = true;
        return {id: actor.id, s, frame: s.frame, x, y, sw, sh};
      });
      if (!dirty) return;
      ctx.clearRect(0, 0, w, h);
      for (const {s,x,y,sw,sh} of placements) ctx.drawImage(s.layer.canvas, x,y,sw,sh);
    }
    function hitTest(x, y) {
      for (let i = placements.length - 1; i >= 0; i--) {
        const {id,s,frame,x: left,y: top,sw,sh} = placements[i];
        const u = (x * canvas.width-left)/sw, v = (y * canvas.height-top)/sh;
        if (!(u >= 0 && v >= 0 && u < 1 && v < 1)) continue;
        const {width,height,stride} = s.model.mask;
        const col = Math.floor(u*width), row = Math.floor(frame/stride)*height + Math.floor(v*height);
        const byte = s.bits.charCodeAt(row*Math.ceil(width/8)+(col>>3));
        if (byte & (128 >> (col & 7))) return id;
      }
      return null;
    }
    return {draw, hitTest, setPaused};
  }
  window.NekoDuoRenderer = {create};
})();
