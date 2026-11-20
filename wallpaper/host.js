(() => {
  "use strict";
  const pending = {};
  let scene, paused = false, fps = 30, frame = 0, due = 0, previous = null;
  const active = () => !paused && !document.hidden;

  function tick(now) {
    frame = 0;
    if (!scene || !active()) return;
    if (now >= due - .5) {
      const dt = previous === null ? 0 : Math.min((now - previous) / 1000, .25);
      scene.draw(dt);
      previous = now;
      const period = 1000 / fps;
      due = fps ? now + period - (due ? Math.max(0, now - due) % period : 0) : now;
    }
    frame = requestAnimationFrame(tick);
  }

  function sync() {
    const running = !!scene && active();
    if (!running) {
      cancelAnimationFrame(frame);
      frame = due = 0;
      previous = null;
    }
    scene?.setActive?.(running);
    if (running && !frame) frame = requestAnimationFrame(tick);
  }

  // Wallpaper Engine may send properties before the scene's assets have loaded.
  window.wallpaperPropertyListener = {
    applyUserProperties(properties) {
      if (scene) scene.properties(properties);
      else Object.assign(pending, properties);
    },
    applyGeneralProperties(properties) {
      if (properties.fps === undefined) return;
      const value = Number(properties.fps?.value ?? properties.fps);
      if (Number.isFinite(value) && value >= 0) {
        fps = Math.min(240, value);
        due = 0;
      }
    },
    setPaused(value) { paused = value; sync(); },
  };
  document.addEventListener("visibilitychange", sync);
  window.NekoWallpaperHost = {
    active,
    start(next) {
      scene = next;
      scene.properties(pending);
      sync();
    },
    stop() {
      scene = undefined;
      sync();
    },
  };
})();
