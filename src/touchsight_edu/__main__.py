from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_environment
from .server import run_server


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    load_environment(project_root / "config.env")
    parser = argparse.ArgumentParser(description="TouchSight EDU software prototype")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    run_server(args.host, args.port, project_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

