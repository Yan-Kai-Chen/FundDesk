"""Frozen executable entry point. All writable paths stay in the workspace."""
import argparse
from pathlib import Path
from app.runtime import serve


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--resources", required=True, type=Path)
    p.add_argument("--instance", required=True)
    p.add_argument("--parent-pid", type=int, default=0)
    args = p.parse_args()
    serve(args.root, args.resources, 0, args.instance, "desktop", args.parent_pid)


if __name__ == "__main__":
    main()
