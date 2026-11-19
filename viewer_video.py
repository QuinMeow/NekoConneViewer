"""Bounded FFmpeg playback on a Tk canvas. Only the Tk thread touches widgets."""
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Full, Queue
import re
import subprocess
import sys
import threading
import time

from viewer_runtime import FROZEN, tool_home
from viewer_pcm import play_stream

FPS = 24


def _ffmpeg():
    folder = tool_home() / "video"
    direct = folder / "ffmpeg.exe"
    if direct.is_file():
        return direct
    candidate = next(folder.rglob("ffmpeg*.exe"), None)
    if candidate is not None:
        return candidate
    if not FROZEN:
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            return get_ffmpeg_exe()
        except (RuntimeError, ImportError):
            pass
    raise FileNotFoundError("缺少随包 FFmpeg，请检查程序目录中的 .tools/video。")


class _Session:
    def __init__(self, path, ffmpeg):
        self.path, self.ffmpeg = path, ffmpeg
        self.frames = Queue(maxsize=4)
        self.cancelled, self.playing = threading.Event(), threading.Event()
        self.audio_ready, self.audio_done, self.video_done = (threading.Event() for _ in range(3))
        self.audio_seconds, self.warning, self.error = None, "", ""
        self.children, self.lock = set(), threading.Lock()

    @contextmanager
    def child(self, *args, probe=False):
        proc = subprocess.Popen([self.ffmpeg, "-nostdin", "-hide_banner", *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE if probe else subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with self.lock:
            self.children.add(proc)
            if self.cancelled.is_set():
                proc.kill()
        try:
            yield proc
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            proc.wait()
            with self.lock:
                self.children.discard(proc)

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            for child in self.children:
                if child.poll() is None:
                    child.kill()  # reaped on its own worker; no wait on the UI thread

    def decode_audio(self):
        try:
            with self.child("-loglevel", "error", "-i", self.path, "-map", "0:a:0", "-vn", "-ac", "2",
                            "-ar", "48000", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1") as proc:
                play_stream(proc.stdout, self)
        except Exception as exc:
            if not self.cancelled.is_set():
                self.warning = f"静音预览：{exc}"
        finally:
            self.audio_done.set()
            self.audio_ready.set()

    def decode(self):
        try:
            with self.child("-i", self.path, probe=True) as probe:
                _, stderr = probe.communicate()
            info = stderr.decode("utf8", "replace")
            line = next((line for line in info.splitlines() if "Stream #" in line and "Video:" in line), "")
            match = re.search(r",\s*(\d{1,6})x(\d{1,6})(?=[\s,])", line)
            if not match:
                raise ValueError("无法读取视频尺寸或没有视频轨道。")
            w, h = map(int, match.groups())
            scale = min(1., 960/max(w, h))
            self.size = tuple(max(1, round(n*scale)) for n in (w, h))
            if not self.cancelled.is_set() and sys.platform == "win32" and re.search(r"Stream #.*Audio:", info):
                threading.Thread(target=self.decode_audio, daemon=True).start()
            else:
                self.audio_ready.set()
                self.audio_done.set()
            width, height = self.size
            # The native VP9 decoder discards the auxiliary WebM alpha track.
            alpha = re.search(r"alpha_mode\s*:\s*1", info, re.I)
            codec = ["-c:v", "libvpx-vp9"] if "Video: vp9" in line and alpha else []
            with self.child("-loglevel", "error", "-threads", "2", *codec, "-i", self.path, "-map", "0:v:0",
                            "-an", "-vf", f"fps={FPS},scale={width}:{height}", "-threads", "2",
                            "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1") as proc:
                frame_size = width*height*4
                while not self.cancelled.is_set():
                    data = proc.stdout.read(frame_size)
                    if not data:
                        if proc.wait():
                            raise ValueError("FFmpeg 视频解码失败。")
                        break
                    if len(data) != frame_size:
                        raise ValueError("视频画面数据不完整。")
                    while not self.cancelled.is_set():
                        try:
                            self.frames.put(data, timeout=.05)
                            break
                        except Full:
                            continue
        except Exception as exc:
            if not self.cancelled.is_set():
                self.error = str(exc)
        finally:
            self.video_done.set()


class VideoPreview:
    tag = "inline_video"

    def __init__(self, canvas, on_state=None):
        self.canvas, self.on_state = canvas, on_state
        self.path = self.session = self.timer = self.frame = self.photo = None
        self.state, self.detail = "stopped", ""
        self.index, self.want_play = -1, False
        self.offset, self.started = 0., None
        canvas.bind("<Destroy>", self._destroy, add="+")

    def _state(self, state, detail=""):
        changed = (self.state, self.detail) != (state, detail)
        self.state, self.detail = state, detail
        if changed and self.on_state:
            self.on_state(state, detail)

    def show(self, path, *, autoplay=False):
        path = Path(path)
        if path == self.path and self.state not in ("ended", "error", "stopped"):
            if self.frame is not None and not self.canvas.find_withtag(self.tag):
                self.resize()
            if autoplay:
                self.play()
            return
        self._clear()
        self.canvas.delete(self.tag)
        self.path, self.want_play, self.index = path, autoplay, -1
        self.started, self.offset = None, 0.
        try:
            from PIL import ImageTk  # fail visibly before scheduling a Tk callback
            if not path.is_file():
                raise FileNotFoundError("视频文件不存在。")
            self.session = _Session(path.resolve(), _ffmpeg())
        except ImportError:
            self._state("error", "内嵌视频预览需要 Pillow / ImageTk。")
            return
        except OSError as exc:
            self._state("error", str(exc))
            return
        self._state("loading")
        threading.Thread(target=self.session.decode, daemon=True).start()
        self.timer = self.canvas.after(10, self._tick)

    def play(self):
        if self.path is None:
            return
        if self.state in ("ended", "error"):
            self.show(self.path, autoplay=True)
            return
        self.want_play = True

    def _seconds(self):
        s = self.session
        if s and s.audio_seconds is not None and not s.audio_done.is_set():
            return s.audio_seconds
        return self.offset + (time.monotonic()-self.started if self.started is not None else 0.)

    def pause(self):
        self.want_play = False
        if self.session:
            self.session.playing.clear()
        if self.state == "playing":
            self.offset, self.started = self._seconds(), None
            self._state("paused", self.session.warning)

    def _tick(self):
        self.timer = None
        s = self.session
        if not s:
            return
        if s.error:
            s.cancel()
            self._state("error", s.error)
            return
        if self.index >= 0 and self.want_play and self.state != "playing" and s.audio_ready.is_set():
            self.started = time.monotonic()
            s.playing.set()
            self._state("playing", s.warning)
        target = int(self._seconds()*FPS) if self.state == "playing" else max(0, self.index)
        data = None
        while self.index < target:
            try:
                data = s.frames.get_nowait()
            except Empty:
                break
            self.index += 1
        if data is not None:
            from PIL import Image
            self.frame = Image.frombytes("RGBA", s.size, data)
            self.resize()
            if self.state == "loading":
                self._state("ready")
        if s.video_done.is_set() and s.frames.empty():
            if self.index < 0:
                s.cancel()
                self._state("error", s.error or "视频没有可预览的画面。")
                return
            if self.state == "playing" and s.audio_done.is_set() and self._seconds()*FPS >= self.index+1:
                self._state("ended", s.warning)
                return
        if self.state == "playing" and s.warning != self.detail:
            self._state("playing", s.warning)
        self.timer = self.canvas.after(16 if self.want_play else 60, self._tick)

    def resize(self):
        if self.frame is None:
            return
        from PIL import Image, ImageTk
        cv = self.canvas
        w, h = max(1, cv.winfo_width()), max(1, cv.winfo_height())
        factor = min(w/self.frame.width, h/self.frame.height)
        size = tuple(max(1, round(d*factor)) for d in self.frame.size)
        self.photo = ImageTk.PhotoImage(self.frame.resize(size, Image.Resampling.BILINEAR), master=cv)
        cv.delete(self.tag)
        cv.create_rectangle(0, 0, w, h, fill="#23282d", outline="", tags=self.tag)
        cv.create_image(w//2, h//2, image=self.photo, tags=self.tag)

    def _clear(self):
        if self.timer is not None:
            self.canvas.after_cancel(self.timer)
            self.timer = None
        if self.session is not None:
            self.session.cancel()
            self.session = None
        self.frame = self.photo = self.path = None

    def stop(self):
        self._clear()
        self.canvas.delete(self.tag)
        self._state("stopped")

    def _destroy(self, event):
        if event.widget is self.canvas:
            self._clear()
