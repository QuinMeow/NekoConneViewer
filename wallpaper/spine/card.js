(() => {
  "use strict";
  const canvas = document.getElementById("card");
  const status = document.getElementById("status");
  const options = new URLSearchParams(location.search);
  let hideBg = options.has("transparent"), whole = options.has("whole");

  function image(url) {
    return new Promise((resolve, reject) => {
      const texture = new Image();
      texture.onload = () => resolve(texture);
      texture.onerror = () => reject(new Error("Texture could not be loaded"));
      texture.src = url;
    });
  }

  function cardBounds(skeleton, bg) {
    const mask = skeleton.findSlot("ALL_Clipping");
    const clip = mask?.getAttachment();
    const backdrop = bg?.getAttachment();
    let vertices;
    if (clip instanceof spine.ClippingAttachment) {
      vertices = new Float32Array(clip.worldVerticesLength);
      clip.computeWorldVertices(mask, 0, vertices.length, vertices, 0, 2);
    } else if (backdrop instanceof spine.RegionAttachment) {
      vertices = new Float32Array(8);
      backdrop.computeWorldVertices(bg, vertices, 0, 2);
    } else if (backdrop instanceof spine.MeshAttachment) {
      vertices = new Float32Array(backdrop.worldVerticesLength);
      backdrop.computeWorldVertices(bg, 0, vertices.length, vertices, 0, 2);
    }
    if (!vertices?.length) return skeleton.getBoundsRect();
    const xs = vertices.filter((_, i) => !(i % 2)), ys = vertices.filter((_, i) => i % 2);
    const x = Math.min(...xs), y = Math.min(...ys);
    return {x, y, width: Math.max(...xs) - x, height: Math.max(...ys) - y};
  }

  async function start() {
    const data = window.NEKO_SPINE;
    const context = new spine.ManagedWebGLRenderingContext(canvas, {alpha: true, premultipliedAlpha: true});
    const gl = context.gl;
    const atlas = new spine.TextureAtlas(data.atlas);
    const pma = atlas.pages[0].pma;
    if (atlas.pages.some(page => page.pma !== pma)) throw new Error("Mixed atlas alpha modes");
    await Promise.all(atlas.pages.map(async page => {
      const source = data.textures[page.name];
      if (!source) throw new Error("Missing atlas page: " + page.name);
      const bitmap = await image(source);
      const ratio = bitmap.naturalWidth / page.width;
      if (ratio !== bitmap.naturalHeight / page.height) {
        throw new Error("Atlas page aspect ratio does not match: " + page.name);
      }
      // Game atlases describe the original pixels, but decoded pages may be uniformly downscaled.
      // Mesh UVs in Spine 4.1 use trim metadata in the actual texture's pixel units.
      for (const region of page.regions) {
        for (const field of ["x", "y", "width", "height", "originalWidth", "originalHeight", "offsetX", "offsetY"]) {
          region[field] *= ratio;
        }
      }
      page.setTexture(new spine.GLTexture(context, bitmap));
    }));
    const loader = new spine.SkeletonBinary(new spine.AtlasAttachmentLoader(atlas));
    const skeletonData = loader.readSkeletonData(Uint8Array.from(atob(data.skeleton), c => c.charCodeAt(0)));
    if (skeletonData.skins.length !== 1 || skeletonData.skins[0].name !== "default" ||
        skeletonData.animations.length !== 1 || skeletonData.animations[0].name !== "animation") {
      throw new Error("Expected a single default / animation card");
    }
    const skeleton = new spine.Skeleton(skeletonData);
    const state = new spine.AnimationState(new spine.AnimationStateData(skeletonData));
    const bg = skeleton.findSlot("BG");
    state.setAnimation(0, "animation", true);
    state.apply(skeleton);
    skeleton.updateWorldTransform();
    const bounds = cardBounds(skeleton, bg);
    if (!(bounds.width > 0 && bounds.height > 0)) throw new Error("Card has no visible bounds");

    const renderer = new spine.SceneRenderer(canvas, context);
    renderer.camera.position.set(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2, 0);
    function draw() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
      const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
      const fit = whole ? Math.min : Math.max;
      const scale = fit(w / bounds.width, h / bounds.height);
      renderer.camera.setViewport(w / scale, h / scale);
      gl.viewport(0, 0, w, h);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      const saved = bg?.color.a;
      if (bg && hideBg) bg.color.a = 0;
      renderer.begin();
      renderer.drawSkeleton(skeleton, pma);
      renderer.end();
      if (bg) bg.color.a = saved;
    }
    addEventListener("resize", draw);
    window.NekoWallpaperHost.start({
      properties(p) {
        if (p.hidebg) hideBg = p.hidebg.value === true;
        if (p.wholecard) whole = p.wholecard.value === true;
        draw();
      },
      draw(dt) {
        state.update(dt);
        state.apply(skeleton);
        skeleton.updateWorldTransform();
        draw();
      },
    });
    status.hidden = true;
    canvas.setAttribute("aria-busy", "false");
  }

  start().catch(error => {
    status.textContent = "Spine 卡面加载失败：" + error.message;
    canvas.setAttribute("aria-busy", "false");
    console.error(error);
  });
})();
