"""Draw Cubism meshes through the installed game's public Core API and OpenGL."""
import ctypes as C
from pathlib import Path

import moderngl as gl
from PIL import Image

P = C.POINTER
INT, UINT, FLOAT, U8, U16, VOID = C.c_int, C.c_uint, C.c_float, C.c_ubyte, C.c_ushort, C.c_void_p


class Model:
    def __init__(self, core, moc):
        self.dll = C.CDLL(str(Path(core).resolve(strict=True)))
        def bind(name, result, *args):
            f = getattr(self.dll, "csm" + name)
            f.argtypes, f.restype = args, result
            return f
        moc = Path(moc).read_bytes()
        if len(moc) < 64 or moc[:4] != b"MOC3" or len(moc) > 256 * 1024**2:
            raise ValueError("Invalid Cubism model size or signature")
        # Core owns no buffers. The caller must keep both aligned allocations alive.
        self.moc_memory = C.create_string_buffer(len(moc) + 63)
        ptr = (C.addressof(self.moc_memory) + 63) & ~63
        C.memmove(ptr, moc, len(moc))
        if not bind("HasMocConsistency", INT, VOID, UINT)(ptr, len(moc)):
            raise ValueError("Game Cubism Core rejected MOC3 consistency")
        ptr = bind("ReviveMocInPlace", VOID, VOID, UINT)(ptr, len(moc))
        if not ptr:
            raise ValueError("This game Core does not support the MOC3 version")
        size = bind("GetSizeofModel", UINT, VOID)(ptr)
        if not 0 < size <= 512 * 1024**2:
            raise ValueError("Invalid native model allocation")
        self.model_memory = C.create_string_buffer(size + 15)
        self.model = bind("InitializeModelInPlace", VOID, VOID, VOID, UINT)(
            ptr, (C.addressof(self.model_memory) + 15) & ~15, size)
        if not self.model:
            raise ValueError("Cannot initialize Cubism model")
        def field(suffix, result):
            return bind("Get" + suffix, result, VOID)(self.model)
        self.update = bind("UpdateModel", None, VOID)
        self.reset = bind("ResetDrawableDynamicFlags", None, VOID)
        self.count = field("DrawableCount", INT)
        n = field("ParameterCount", INT)
        self.parameters = {v.decode(): i for i, v in enumerate(field("ParameterIds", P(C.c_char_p))[:n])}
        self.values = field("ParameterValues", P(FLOAT))
        self.ranges = list(zip(field("ParameterMinimumValues", P(FLOAT))[:n],
                               field("ParameterMaximumValues", P(FLOAT))[:n]))
        for attr, suffix, result in (
            ("flags", "ConstantFlags", P(U8)), ("dynamic", "DynamicFlags", P(U8)),
            ("textures", "TextureIndices", P(INT)), ("order", "RenderOrders", P(INT)),
            ("opacity", "Opacities", P(FLOAT)), ("vertices", "VertexCounts", P(INT)),
            ("positions", "VertexPositions", P(P(FLOAT))), ("uvs", "VertexUvs", P(P(FLOAT))),
            ("index_counts", "IndexCounts", P(INT)), ("indices", "Indices", P(P(U16))),
            ("multiply", "MultiplyColors", P(FLOAT * 4)), ("screen", "ScreenColors", P(FLOAT * 4)),
        ):
            setattr(self, attr, field("Drawable" + suffix, result))
        counts, indices = field("DrawableMaskCounts", P(INT)), field("DrawableMasks", P(P(INT)))
        self.masks = [tuple(indices[i][:counts[i]]) if counts[i] else () for i in range(self.count)]
        self.update(self.model)

    def visible(self, i):
        return self.dynamic[i] & 1 and self.opacity[i] > 0 and self.index_counts[i] > 0

    def apply(self, values):
        for i, v in values:
            lo, hi = self.ranges[i]
            self.values[i] = min(max(v, lo), hi)
        self.reset(self.model)
        self.update(self.model)


VERTEX = """#version 330
in vec2 p; in vec2 uv; out vec2 texCoord; uniform vec4 framing;
void main() {
    gl_Position = vec4((p - framing.xy) * framing.zw, 0.0, 1.0);
    texCoord = vec2(uv.x, 1.0 - uv.y);
}
"""
FRAGMENT = """#version 330
uniform sampler2D atlas, maskTex;
uniform vec2 viewport; uniform float opacity; uniform int masking;
uniform vec3 multiply, screenColor;
in vec2 texCoord; out vec4 color;
void main() {
    vec4 tex = texture(atlas, texCoord);
    float a = tex.a * opacity;
    if (masking == 1 || masking == 2) {
        float m = texture(maskTex, gl_FragCoord.xy / viewport).a;
        a *= masking == 1 ? m : 1.0-m;
    }
    vec3 rgb = tex.rgb * multiply;
    rgb = rgb + screenColor * (1.0 - rgb);
    color = vec4(rgb * a, a);
}
"""


class Renderer:
    def __init__(self, m, textures, size, scale):
        self.model, self.size = m, size
        self.ctx = ctx = gl.create_standalone_context(require=330)
        self.shader = shader = ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        self.output = ctx.simple_framebuffer(size, components=4)
        self.mask = ctx.texture(size, components=4)
        self.mask_fbo = ctx.framebuffer([self.mask])
        self.white = ctx.texture((1, 1), 4, b"\xff"*4)
        self.textures = []
        for path in textures:
            with Image.open(path) as img:
                image = img.convert("RGBA")
                tex = ctx.texture(image.size, 4, image.tobytes())
                tex.filter = (gl.LINEAR, gl.LINEAR)
                tex.repeat_x = tex.repeat_y = False
                self.textures.append(tex)
        self.buffers, self.meshes = {}, {}
        bounds = []
        for i in range(m.count):
            if not m.vertices[i] or not m.index_counts[i]:
                continue
            n = m.vertices[i]
            position = ctx.buffer(reserve=n*8, dynamic=True)
            uv = ctx.buffer(C.string_at(m.uvs[i], n*8))
            index = ctx.buffer(C.string_at(m.indices[i], m.index_counts[i]*2))
            self.buffers[i] = (position, uv, index)
            self.meshes[i] = ctx.vertex_array(shader, [(position, "2f", "p"), (uv, "2f", "uv")],
                                              index_buffer=index, index_element_size=2)
            if m.visible(i):
                pos = m.positions[i][:2*n]
                bounds.append((min(pos[::2]), min(pos[1::2]), max(pos[::2]), max(pos[1::2])))
        if not bounds:
            raise ValueError("The model has no visible geometry")
        x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
        x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
        pixels = min(size[0] / (x1-x0), size[1] / (y1-y0)) * scale
        shader["framing"].value = (0.5*(x0+x1), 0.5*(y0+y1), 2*pixels/size[0], 2*pixels/size[1])
        shader["viewport"].value = size
        shader["atlas"].value, shader["maskTex"].value = 0, 1
        ctx.enable(gl.BLEND)

    def draw(self, i, mode, mask_source=False):
        m, ctx, s = self.model, self.ctx, self.shader
        if i not in self.meshes:
            return
        tex_id = m.textures[i]
        if not 0 <= tex_id < len(self.textures):
            raise ValueError("Model references a missing texture")
        self.textures[tex_id].use(0)
        s["masking"].value = mode
        s["opacity"].value = 1.0 if mask_source else m.opacity[i]
        s["multiply"].value, s["screenColor"].value = m.multiply[i][:3], m.screen[i][:3]
        blend = 0 if mask_source else m.flags[i] & 3
        ctx.blend_func = {0: (gl.ONE, gl.ONE_MINUS_SRC_ALPHA),
                          1: (gl.ONE, gl.ONE, gl.ZERO, gl.ONE),
                          2: (gl.DST_COLOR, gl.ONE_MINUS_SRC_ALPHA, gl.ZERO, gl.ONE)}[blend]
        self.meshes[i].render()

    def frame(self):
        m = self.model
        for i, (buffer, _, _) in self.buffers.items():
            buffer.write(C.string_at(m.positions[i], m.vertices[i]*8))
        self.output.use()
        self.output.clear()
        self.white.use(1)
        mask_ids = None
        for i in sorted(self.meshes, key=lambda x: m.order[x]):
            if not m.visible(i):
                continue
            mask = m.masks[i]
            if mask and mask != mask_ids:
                self.white.use(1)
                self.mask_fbo.use()
                self.mask_fbo.clear()
                for source in mask:
                    self.draw(source, 0, True)
                self.output.use()
                self.mask.use(1)
                mask_ids = mask
            self.draw(i, (2 if m.flags[i] & 8 else 1) if mask else 0)
        pixels = self.output.read(components=4, alignment=1)
        return Image.frombytes("RGBa", self.size, pixels, "raw", "RGBa", 0, -1).convert("RGBA")
