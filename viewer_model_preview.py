"""Small, cancellable preview jobs; no export worker or Tk thread is blocked."""
import subprocess
import tempfile
import threading
from viewer_runtime import SOURCE


class PreviewJob:
    def __init__(self, parent, terminate):
        self.parent = parent
        self.terminate = terminate
        self.process = self.log = self.timer = None
        parent.bind("<Destroy>", self.destroyed, add="+")

    def destroyed(self, event):
        if event.widget is self.parent:
            self.stop()

    def stop(self):
        if self.timer is not None:
            self.parent.after_cancel(self.timer)
            self.timer = None
        if self.process is not None:
            if self.process.poll() is None:
                threading.Thread(target=self.terminate, args=(self.process,), daemon=True).start()
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None

    def start(self, command, done):
        self.stop()
        try:
            self.log = tempfile.TemporaryFile()
            self.process = subprocess.Popen(command, cwd=SOURCE, stdin=subprocess.DEVNULL, stdout=self.log,
                                            stderr=subprocess.STDOUT,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self.stop()
            done(str(exc))
            return
        self.timer = self.parent.after(100, self.poll, done)

    def poll(self, done):
        self.timer = None
        code = self.process.poll()
        if code is None:
            self.timer = self.parent.after(100, self.poll, done)
            return
        message = ""
        if code:
            size = self.log.seek(0, 2)
            self.log.seek(max(0, size - 3072))
            lines = self.log.read().decode("utf8", errors="replace").splitlines()
            message = next((line for line in reversed(lines) if line.strip()), f"退出码 {code}")
        self.stop()
        done(message)
