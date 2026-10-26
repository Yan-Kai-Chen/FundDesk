#!/usr/bin/env python3
"""Import a locally reviewed fund/evidence manifest through the serialized API."""
import argparse
import hashlib
import http.cookiejar
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k,v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(subset(a,b) for a,b in zip(expected,actual))
    return expected == actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--refresh-nav", action="store_true")
    args = parser.parse_args()
    path = args.manifest.resolve()
    p = json.loads(path.read_text())
    for source in p["statement"]["sources"]:
        image = (path.parent / source["filename"]).resolve()
        if image.parent != path.parent or hashlib.sha256(image.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("原始资料路径或 SHA256 与清单不符")
    url = json.loads((ROOT / "data/runtime/server.json").read_text())["url"]
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    session = json.load(opener.open(url + "/api/v1/session", timeout=5))

    def call(endpoint, body=None, method=None):
        req = urllib.request.Request(url + "/api/v1" + endpoint + "?mode=real",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type":"application/json", "X-CSRF-Token":session["csrf_token"]}, method=method)
        return json.load(opener.open(req, timeout=30))

    before = call("/state")
    fund = call("/funds", p["fund"])
    fid = fund["id"]
    existing = next((f for f in before["funds"] if f["id"] == fid), None)
    old_rules = existing["rules"] if existing else {"_version":0}
    if not subset(p["rules"], old_rules):
        call(f"/funds/{fid}/rules", dict(p["rules"], expected_version=old_rules["_version"]), "PUT")
    old_statement = existing.get("statement") if existing else None
    call("/statements", dict(p["statement"], fund_id=fid,
        expected_version=old_statement["_version"] if old_statement else None))
    if args.refresh_nav:
        call(f"/funds/{fid}/nav/refresh", {})
    after = call("/state")
    selected = next(f for f in after["funds"] if f["id"] == fid)
    print(json.dumps({"fund_id":fid,"mode":"real","records":selected["statement"]["summary"]["count"],
        "nav_points":len(selected["history"]),"ledger_revision_before":before["revision"],
        "ledger_revision_after":after["revision"],"status":"AWAITING_CONFIRMATIONS"},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
