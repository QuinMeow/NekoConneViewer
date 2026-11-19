"""Stream decoded stereo PCM to Windows' default waveform output device."""
from collections import deque
import ctypes as c
import wave


class _Header(c.Structure):
    _fields_ = [("data", c.c_void_p), ("size", c.c_uint32), ("recorded", c.c_uint32),
                ("user", c.c_size_t), ("flags", c.c_uint32), ("loops", c.c_uint32),
                ("next", c.c_void_p), ("reserved", c.c_size_t)]


class _WaveOut:
    def __init__(self, rate):
        self.rate = rate
        self.api = c.WinDLL("winmm")
        self.handle = c.c_void_p()
        self.pending = deque()
        # WAVEFORMATEX: PCM, stereo, signed 16-bit, native little-endian.
        import struct
        fmt = c.create_string_buffer(struct.pack("<HHIIHHH", 1, 2, rate, rate*4, 4, 16, 0))
        code = self.api.waveOutOpen(c.byref(self.handle), c.c_uint32(0xffffffff), fmt, 0, 0, 0)
        if code:
            raise OSError(f"Windows 音频输出不可用 ({code})")

    def call(self, name, *args):
        code = getattr(self.api, "waveOut"+name)(self.handle, *args)
        if code:
            raise OSError(f"Windows 音频输出 {name} 失败 ({code})")

    def submit(self, pcm):
        data = c.create_string_buffer(pcm)
        header = _Header(data=c.addressof(data), size=len(pcm))
        args = c.byref(header), c.sizeof(header)
        self.call("PrepareHeader", *args)
        self.pending.append((header, data))  # Both must survive until WHDR_DONE.
        self.call("Write", *args)

    def reap(self, reset=False):
        while self.pending and (reset or self.pending[0][0].flags & 1):
            header, _ = self.pending[0]
            self.call("UnprepareHeader", c.byref(header), c.sizeof(header))
            self.pending.popleft()

    def seconds(self):
        # MMTIME's union is 8 bytes; some drivers substitute ms or bytes for samples.
        pos = (c.c_uint32*3)(2, 0, 0)
        self.call("GetPosition", c.byref(pos), c.sizeof(pos))
        return pos[1] / {1: 1000, 2: self.rate, 4: self.rate*4}[pos[0]]

    def close(self):
        try:
            self.call("Reset")
            self.reap(reset=True)
        finally:
            self.call("Close")


def play_stream(stream, session):
    """Run on the audio thread; readiness/clock are consumed by the Tk thread."""
    with wave.open(stream, "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (2, 2, 48000):
            raise ValueError("不支持的预览音频格式")
        device = _WaveOut(48000)
        try:
            device.call("Pause")
            running = end = False
            while not session.cancelled.is_set():
                device.reap()
                if len(device.pending) < 4 and not end:
                    data = wav.readframes(1920)
                    if data:
                        device.submit(data)
                    else:
                        end = True
                    if len(device.pending) < 4 and not end:
                        continue
                session.audio_ready.set()
                if end and not device.pending:
                    break
                if running != session.playing.is_set():
                    running = not running
                    device.call("Restart" if running else "Pause")
                session.audio_seconds = device.seconds()
                session.cancelled.wait(.012)
        finally:
            device.close()
