#!/usr/bin/env python3
"""Copy src/jina_api.py into skills/<name>/scripts/ for every skill, so that
each skill installs and runs on its own (`npx skills add ... --skill jina-rank`).

    python tools/sync_scripts.py           # write the copies that differ
    python tools/sync_scripts.py --check   # exit 1 if a copy differs (CI)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "jina_api.py"


def targets():
    return sorted(d / "scripts" / "jina_api.py"
                  for d in (ROOT / "skills").iterdir() if (d / "SKILL.md").is_file())


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="report stale copies, write nothing")
    a = p.parse_args()
    data = SOURCE.read_bytes()
    stale = [t for t in targets() if not t.is_file() or t.read_bytes() != data]
    for t in stale:
        rel = t.relative_to(ROOT).as_posix()
        if a.check:
            print(f"out of date: {rel} (run: python tools/sync_scripts.py)")
        else:
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(data)
            print(f"wrote {rel}")
    return 1 if a.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
