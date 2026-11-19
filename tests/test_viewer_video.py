"""Exercise inline playback using synthetic VP9/Opus media, never game files."""
import gc
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

try:
    import tkinter as tk
except ImportError:
    tk = None

from viewer_video import FPS, VideoPreview, _ffmpeg


@unittest.skipIf(tk is None, "Tkinter unavailable")
class VideoPreviewTests(unittest.TestCase):
    def setUp(self):
        try:
            from PIL import ImageTk
        except ImportError:
            self.skipTest("Pillow with Tk support is required")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(gc.collect)
        self.addCleanup(self.root.destroy)
        self.root.geometry("300x180+3000+3000")
        self.canvas = tk.Canvas(self.root)
        self.canvas.pack(fill="both", expand=True)
        self.root.update()
        self.states = []
        self.preview = VideoPreview(self.canvas, lambda state, detail: self.states.append((state, detail)))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        def close():
            session = self.preview.session
            self.preview.stop()
            if session:
                self.until(lambda: session.video_done.is_set() and not session.children)
        self.addCleanup(close)

    def until(self, condition, timeout=6):
        end = time.monotonic()+timeout
        while not condition():
            self.root.update()
            self.assertLess(time.monotonic(), end, self.states)
            time.sleep(.008)

    def sample(self):
        try:
            ffmpeg = _ffmpeg()
        except FileNotFoundError as exc:
            self.skipTest(str(exc))
        clip, sound = self.tmp/"fixture.webm", self.tmp/"silent.wav"
        with wave.open(str(sound), "wb") as wav:
            wav.setparams((2, 2, 48000, 0, "NONE", ""))
            wav.writeframes(bytes(48000*4))
        pixels = b"".join((bytes([i*10, 30, 190, 255])*16 + bytes(64))*24 for i in range(FPS))
        subprocess.run([ffmpeg, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "32x24",
                        "-r", str(FPS), "-i", "pipe:0", "-i", sound, "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                        "-auto-alt-ref", "0", "-threads", "1", "-c:a", "libopus", clip], input=pixels,
                       check=True, timeout=15, capture_output=True)
        return clip

    def test_play_pause_resize_end_and_release_decoders(self):
        self.preview.show(self.sample())
        self.until(lambda: self.preview.state == "ready")
        self.assertEqual(self.preview.frame.getchannel("A").getextrema(), (0, 255))
        self.preview.play()
        self.until(lambda: self.preview.index >= 3)
        self.preview.pause()
        i = self.preview.index
        now = time.monotonic()
        self.until(lambda: time.monotonic()-now > .17)
        self.assertEqual(self.preview.index, i)
        self.root.geometry("180x300+3000+3000")
        self.root.update()
        self.preview.resize()
        self.assertEqual((self.preview.photo.width(), self.preview.photo.height()), (180, 135))
        self.preview.play()
        self.until(lambda: self.preview.state == "ended")
        session = self.preview.session
        self.assertEqual(self.preview.index, FPS-1)
        self.assertTrue(session.audio_done.is_set())
        self.preview.play()
        self.until(lambda: self.preview.state == "playing")
        session = self.preview.session
        self.preview.stop()
        self.until(lambda: session.video_done.is_set() and session.audio_done.is_set() and not session.children)
        self.assertFalse(self.canvas.find_withtag(self.preview.tag))

    def test_switch_to_missing_file_clears_old_video(self):
        self.preview.show(self.sample(), autoplay=True)
        self.until(lambda: self.preview.index >= 1)
        session = self.preview.session
        self.preview.show(self.tmp/"missing.webm")
        self.assertEqual(self.preview.state, "error")
        self.assertFalse(self.canvas.find_withtag(self.preview.tag))
        self.until(lambda: session.video_done.is_set() and session.audio_done.is_set() and not session.children)

    @unittest.skipUnless(os.name == "nt", "Windows audio device fallback")
    def test_audio_device_failure_keeps_video_playable(self):
        with patch("viewer_video.play_stream", side_effect=OSError("no test audio device")):
            self.preview.show(self.sample(), autoplay=True)
            self.until(lambda: self.preview.state == "ended")
        self.assertEqual(self.preview.index, FPS-1)
        self.assertIn("静音预览", self.preview.detail)
