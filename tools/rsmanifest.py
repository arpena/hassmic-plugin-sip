#!/usr/bin/env python3
"""The Rust build's manifest from manifest.json: its own id (<id>-rs) and name ("<name> (Rust)"), so that an Echo can
have both builds installed and tell them apart; everything it asks for, its settings and its limits the same, so
that test.py holds the two to one behaviour.  Run by make; the result is build/<id>-rs.json."""
import json, sys

src, out = sys.argv[1], sys.argv[2]
m = json.load(open(src))
rid = m["id"] + "-rs"
if len(rid) > 24:
    sys.exit(f"rsmanifest: {rid!r} is longer than an id may be (24): the Rust build needs an id of 21 at most")
base = m["name"]
while len((base + " (Rust)").encode()) > 48:       # the manifest's bound, in bytes; cut by characters
    base = base[:-1].rstrip()
m.update(id=rid, name=base + " (Rust)")
with open(out, "w") as f:
    json.dump(m, f, indent=1, ensure_ascii=False)
    f.write("\n")
