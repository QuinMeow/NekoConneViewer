import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows source launchers")
class LauncherTests(unittest.TestCase):
    def test_source_starts_from_explorer_style_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "viewer & source (local)"
            root.mkdir()
            for name in ("Start-Viewer.cmd", "Start-Viewer.pyw"):
                shutil.copyfile(ROOT / name, root / name)
            (root / "viewer_gui.py").write_text(
                "import os; print('STARTED', os.path.basename(os.getcwd()))", encoding="ascii"
            )
            env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
            for args in ([sys.executable, str(root / "Start-Viewer.pyw")],
                         f'cmd.exe /d /s /c ""{root / "Start-Viewer.cmd"}""'):
                with self.subTest(args=args):
                    run = subprocess.run(args, cwd=tmp, env=env, stdin=subprocess.DEVNULL,
                                         capture_output=True, text=True, timeout=20)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertIn("STARTED viewer & source (local)", run.stdout)


if __name__ == "__main__":
    unittest.main()
