#!/usr/bin/env python3
"""Make the template's example your plugin: its id and name (and, given, its author and source) in manifest.json.

    tools/rename.py --id ID --name NAME [--author AUTHOR] [--source URL]
    make rename ID=myplugin NAME="My plugin" [AUTHOR="..."] [SOURCE=https://github.com/<you>/<repository>]

The id lives in manifest.json alone: the Makefile, test.py, the Rust build's manifest (tools/rsmanifest.py) and CI
read it from there, and the programs never name it.  So this changes one file, keeping its layout, and says what
is left for you.  The id is what an Echo knows the plugin by: rename before your first release, never after (a new
id is a new plugin to every Echo, installed beside the old one, with its own settings and storage)."""
import argparse, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "manifest.json")
# hassmic's rule (sdk/plugcheck.py ID, plugin_store.c ps_id_ok): 3 to 24 of a-z, 0-9 and -, no - first, last or twice
# in a row.  21 at most here: the Rust build's id is this one with "-rs"
ID = re.compile(r"(?=[a-z0-9-]{3,21}\Z)[a-z0-9]+(?:-[a-z0-9]+)*")
URL = re.compile(r"https://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(/[A-Za-z0-9._~-]+)+")


def text(v, most, what):
    if not v or len(v.encode()) > most or any(ord(c) < 32 or ord(c) == 127 for c in v):
        sys.exit(f"rename: {what}: 1 to {most} bytes of text, one line")
    return v


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--id", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--author")
    a.add_argument("--source")
    o = a.parse_args()
    if not ID.fullmatch(o.id):
        sys.exit("rename: the id is 3 to 21 of a-z, 0-9 and - (none first, last or twice in a row), e.g. my-plugin")
    new = {"id": o.id, "name": text(o.name, 48, "the name")}
    if o.author is not None:
        new["author"] = text(o.author, 48, "the author")
    if o.source is not None:
        if not URL.fullmatch(o.source) or len(o.source) > 200:
            sys.exit("rename: the source is the address of your repository, https://..., 200 characters at most")
        new["source"] = o.source

    raw = open(MANIFEST, encoding="utf-8").read()
    old = json.loads(raw)
    out = raw
    for key, value in new.items():
        # the key with its present value, written as json.dumps writes it: exactly once in the file, so that a
        # setting's "name" is never touched
        pat = re.compile(r'("%s"\s*:\s*)%s' % (re.escape(key), re.escape(json.dumps(old[key], ensure_ascii=False))))
        if len(pat.findall(out)) != 1:
            sys.exit(f"rename: manifest.json has \"{key}\": {json.dumps(old[key])} not exactly once: change it by hand")
        out = pat.sub(lambda m: m.group(1) + json.dumps(value, ensure_ascii=False), out)
    m = json.loads(out)
    want = dict(old, **new)
    if m != want:
        sys.exit("rename: manifest.json would change more than asked: change it by hand")
    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write(out)

    print(f"manifest.json: id {old['id']} -> {m['id']}, name {old['name']!r} -> {m['name']!r}"
          + "".join(f", {k} -> {m[k]!r}" for k in ("author", "source") if k in new))
    tags = subprocess.run(["git", "-C", ROOT, "tag", "-l", "v*"], capture_output=True, text=True).stdout.split()
    if tags and old["id"] != m["id"]:
        print(f"note: this repository has release tags ({', '.join(tags[:3])}...): Echos that installed {old['id']} "
              f"will see {m['id']} as another plugin")
    left = []
    if "author" not in new and old["author"] == "Your name":
        left.append("\"author\" in manifest.json (or AUTHOR=...)")
    if "source" not in new and "your-account" in old["source"]:
        left.append("\"source\" in manifest.json: your repository's address (or SOURCE=...)")
    left += ["\"description\" in manifest.json: what the owner reads on the plugin's card",
             "LICENSE and \"license\" in manifest.json: your plugin's licence (README.md \"Licence\")",
             "README.md: your plugin's own; the template's guide is in the template's repository",
             "make key: your author key (keep secrets/author.key; README.md \"Your key\")"]
    print("left for you:\n" + "\n".join("  - " + x for x in left))


if __name__ == "__main__":
    main()
