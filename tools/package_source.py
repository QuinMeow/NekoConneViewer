"""Archive the committed source, never the working directory or local exports."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    dest = ROOT / "dist/NekoConneViewer-source.zip"
    dest.parent.mkdir(exist_ok=True)
    subprocess.run(["git", "archive", "--format=zip", "--prefix=NekoConneViewer/",
                    "-o", str(dest), "HEAD"], cwd=ROOT, check=True)
    print(dest)


if __name__ == "__main__":
    main()
