(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const status = byId("status"), stage = byId("scene"), canvas = byId("actors");
  function report(message) {
    status.textContent = message;
    status.hidden = !message;
  }
  const data = window.NEKO_SCENE;
  if (!data || !window.NekoDuoRenderer) {
    stage.setAttribute("aria-busy", "false");
    report("壁纸文件不完整。请从完整的项目目录打开 index.html。");
    return;
  }
  const actors = data.actors.map(actor => ({ ...actor }));
  const voiceIndex = new Map();
  const voice = new Audio();
  voice.preload = "none";
  voice.volume = 0.7;
  const host = window.NekoWallpaperHost;
  let renderer, pointer = null, gaze = true, audioRequest = 0;
  let selectedBackground, backgroundError = false;
  function number(value, min, max, current) {
    const n = Number(value);
    return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : current;
  }
  function chooseBackground(id) {
    const bg = data.backgrounds.find(item => item.id === id);
    if (!bg || selectedBackground === id) return;
    selectedBackground = id;
    byId("background").src = bg.file;
  }
  byId("background").addEventListener("error", () => {
    backgroundError = true;
    report("背景图片未能载入。请检查导出目录中的背景文件。");
  });
  byId("background").addEventListener("load", () => {
    if (backgroundError && renderer) report("");
    backgroundError = false;
  });

  function stopVoice() {
    ++audioRequest;
    voice.pause();
    if (voice.readyState) voice.currentTime = 0;
  }
  function speak(actor) {
    if (!host.active() || !voice.volume || !actor.voices.length) return;
    stopVoice();
    const request = audioRequest;
    const index = voiceIndex.get(actor.id) || 0;
    voiceIndex.set(actor.id, (index + 1) % actor.voices.length);
    voice.src = actor.voices[index];
    voice.play().then(() => {
      if (request === audioRequest && !backgroundError) report("");
    }).catch(error => {
      if (request === audioRequest && error.name !== "AbortError") {
        report("这段角色语音未能播放，请检查音频文件与播放权限。");
      }
    });
  }
  function position(event) {
    const bounds = canvas.getBoundingClientRect();
    return { x: (event.clientX - bounds.left) / bounds.width, y: (event.clientY - bounds.top) / bounds.height };
  }
  canvas.addEventListener("mousemove", event => { if (host.active()) pointer = position(event); });
  canvas.addEventListener("mouseleave", () => { pointer = null; });
  canvas.addEventListener("click", event => {
    if (!renderer || !host.active() || event.button !== 0) return;
    const p = position(event);
    pointer = p;
    const id = renderer.hitTest(p.x, p.y);
    const actor = actors.find(item => item.id === id);
    if (actor) speak(actor);
  });

  const controls = byId("controls"), actorChoice = byId("actor-choice");
  const showControls = new URLSearchParams(location.search).has("controls");
  const selected = () => actors.find(actor => actor.id === actorChoice.value);
  function reflectControls() {
    if (!showControls) return;
    byId("background-choice").value = selectedBackground;
    byId("volume").value = Math.round(voice.volume * 100);
    byId("gaze").checked = gaze;
    const actor = selected();
    if (actor) for (const key of ["x", "y", "scale"]) byId(key).value = actor[key];
  }
  function applyUserProperties(properties) {
    const value = key => properties[key]?.value;
    if (value("background") !== undefined) chooseBackground(value("background"));
    if (value("gaze") !== undefined) gaze = value("gaze") === true;
    if (value("volume") !== undefined) {
      voice.volume = number(value("volume"), 0, 100, voice.volume * 100) / 100;
      if (!voice.volume) stopVoice();
    }
    for (const actor of actors) {
      for (const key of ["x", "y", "scale"]) {
        const v = value("p" + actor.id + key);
        if (v !== undefined) actor[key] = key === "scale" ? number(v, 25, 180, actor[key]) : number(v, 0, 100, actor[key]);
      }
    }
    reflectControls();
  }
  if (showControls) {
    for (const bg of data.backgrounds) byId("background-choice").add(new Option(bg.name, bg.id));
    for (const actor of actors) actorChoice.add(new Option(actor.name, actor.id));
    controls.hidden = false;
    actorChoice.addEventListener("change", reflectControls);
    controls.addEventListener("input", event => {
      const { id, value, checked } = event.target;
      if (id === "actor-choice") return;
      let key = id;
      if (id === "background-choice") key = "background";
      if (["x", "y", "scale"].includes(id)) key = "p" + actorChoice.value + id;
      applyUserProperties({ [key]: { value: id === "gaze" ? checked : value } });
    });
  }
  chooseBackground("garden");
  reflectControls();
  function renderError(error) {
    renderer?.setPaused(true);
    renderer = null;
    host.stop();
    stopVoice();
    report("角色动画无法播放：" + error.message);
    stage.setAttribute("aria-busy", "false");
  }
  window.NekoDuoRenderer.create(canvas, actors, renderError).then(result => {
    renderer = result;
    if (!backgroundError) report("");
    stage.setAttribute("aria-busy", "false");
    host.start({
      properties: applyUserProperties,
      draw(dt) { renderer.draw(actors, gaze ? pointer : null, dt); },
      setActive(active) {
        renderer.setPaused(!active);
        if (!active) {
          pointer = null;
          stopVoice();
        }
      },
    });
  }).catch(renderError);
})();
