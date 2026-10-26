import argparse
import os
from pathlib import Path
import uuid
from app.runtime import serve

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8731)
parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
args = parser.parse_args()
serve(args.root, args.root, args.port, os.environ.get("FUNDING_INSTANCE_ID") or uuid.uuid4().hex)
