"""Both bootloaders use this entry; the GUI does not create a console window."""
import multiprocessing
from pathlib import Path
import runpy
import sys
from types import ModuleType

WORKERS = {"export_builtin", "export_images", "export_cubism", "export_video",
           "export_audio", "import_bundles", "render_native", "render_spine"}


def main():
    if Path(sys.executable).stem.casefold() == "nekoconneworker":
        if len(sys.argv) < 2 or sys.argv[1] not in WORKERS:
            print("Available workers: " + ", ".join(sorted(WORKERS)), file=sys.stderr)
            return 2
        tool = sys.argv.pop(1)
        if tool == "export_video" and "--ffmpeg" not in sys.argv and "--help" not in sys.argv:
            print("Video export requires FFmpeg on PATH or in .tools/video/.", file=sys.stderr)
            return 2
        if tool == "export_audio" and "--cli" in sys.argv:
            if not Path(sys.argv[sys.argv.index("--cli") + 1]).is_file():
                print("Install vgmstream-cli on PATH or in .tools/audio/vgmstream-r2168/.", file=sys.stderr)
                return 2
        # UnityPy imports its optional FMOD AudioClip backend eagerly, even for PNG.
        # This build never calls that backend; all sound jobs use external vgmstream.
        fmod = ModuleType("fmod_toolkit")
        def unavailable(*args, **kwargs):
            raise RuntimeError("FMOD AudioClip decoding is not included in this build.")
        fmod.raw_to_wav = unavailable
        sys.modules["fmod_toolkit"] = fmod
        runpy.run_module("tools." + tool, run_name="__main__", alter_sys=True)
    else:
        import tkinter as tk
        from viewer_gui import Viewer
        root = tk.Tk()
        Viewer(root)
        root.mainloop()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
