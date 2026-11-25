#!/usr/bin/env python3
"""tools/release.sh as .github/workflows/release.yml runs it, in a copy of this tree with a throwaway key: what a tag
would release (the package, signed by the key keys/author.pub names, checked by plugcheck.py show, its program the
one given by its SHA-256), and each refusal, one at a time: a tag that is not the manifest's version, a source that
is not the repository, a key that is not keys/author.pub's, no keys/author.pub (the template as it is), a program
that is not the build job's.  What only GitHub can do is not here: the attestation (an OIDC token, Sigstore) and the
release's upload.  Run by `make release-test` (build.yml's build job, which has zig); PLUGIN_CC as the Makefile's."""
import atexit, hashlib, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
failed = False


def check(cond, what):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    failed |= not cond


top = tempfile.mkdtemp(prefix="release-test-")
atexit.register(shutil.rmtree, top, True)
tree = os.path.join(top, "plugin")
shutil.copytree(ROOT, tree, symlinks=True, ignore=shutil.ignore_patterns(".git", "build", "target", "secrets", "toolchain", "__pycache__"))
REPO = "someone/hassmic-plugin-example"
m = json.load(open(os.path.join(tree, "manifest.json")))
ID, VERSION = m["id"], m["version"]
m["source"] = f"https://github.com/{REPO}"
open(os.path.join(tree, "manifest.json"), "w").write(json.dumps(m, indent=2) + "\n")
plugcheck = os.path.join(tree, "sdk", "plugcheck.py")
os.makedirs(os.path.join(tree, "keys"), exist_ok=True)
for k in ("author", "other"):
    subprocess.run([sys.executable, plugcheck, "keygen", os.path.join(top, k + ".key"), os.path.join(top, k + ".pub")], check=True, capture_output=True)
had_pub = os.path.exists(os.path.join(ROOT, "keys", "author.pub"))


def release(tag=f"v{VERSION}", repo=REPO, key="author", want=None, pub="author", job=None):
    """tools/release.sh's status, its output, and the package it names"""
    p = os.path.join(tree, "keys", "author.pub")
    if pub:
        shutil.copy(os.path.join(top, pub + ".pub"), p)
    elif os.path.exists(p):
        os.unlink(p)
    dist = tempfile.mkdtemp(prefix="dist-", dir=top)
    r = subprocess.run(["sh", "tools/release.sh", tag, repo, os.path.join(top, key + ".key"), dist] + ([] if want is None else [want]),
                       cwd=tree, capture_output=True, text=True,
                       env={k: v for k, v in dict(os.environ, GITHUB_JOB=job or "").items() if k != "GITHUB_JOB" or job})
    return r.returncode, r.stdout.strip(), r.stderr


rc, pkg, err = release()
check(rc == 0 and pkg.endswith(f"/{ID}-{VERSION}.hmp") and os.path.isfile(pkg) and os.path.isfile(pkg[:-4] + ".txt"),
      f"tag v{VERSION}: the package {ID}-{VERSION}.hmp and its description in the release's folder, its path printed last" + ("" if rc == 0 else ": " + err[-400:]))
if rc == 0:
    shown = subprocess.run([sys.executable, plugcheck, "show", pkg], capture_output=True, text=True)
    check(shown.returncode == 0, "plugcheck.py show takes it (it fails where the manifest's signature does not hold)")
    with __import__("tarfile").open(pkg) as t:
        names = t.getnames()
        man = json.loads(t.extractfile("manifest.json").read())
        prog = t.extractfile("plugin").read()
    check(names == ["manifest.json", "manifest.sig", "plugin", "LICENSE"] and "listing.sig" not in names, f"packed as plugcheck.py packs, no listing (the index adds it): {names}")
    check(man["author_key"] == open(os.path.join(top, "author.pub"), "rb").read().hex() and man["source"] == f"https://github.com/{REPO}",
          "the manifest names the author key keys/author.pub has, and the repository as its source")
    sha = hashlib.sha256(prog).hexdigest()
    rc2, pkg2, err2 = release(want=sha)
    check(rc2 == 0 and open(pkg2, "rb").read() == open(pkg, "rb").read(),
          "given the build job's program by its SHA-256: built again, the same, the same package byte for byte (a reproducible build)" + ("" if rc2 == 0 else ": " + err2[-300:]))
for what, kw, said in (("a tag that is not the manifest's version", {"tag": f"v{VERSION + 1}"}, "manifest.json says version"),
                       ("a tag without its v", {"tag": str(VERSION)}, "manifest.json says version"),
                       ("another repository than the manifest's source", {"repo": "someone/else"}, "not this repository"),
                       ("a key that is not keys/author.pub's", {"key": "other"}, "not the key of keys/author.pub"),
                       ("no keys/author.pub (the template as it is: make key first)", {"pub": None}, "no keys/author.pub"),
                       ("a program that is not the build job's", {"want": "0" * 64}, "not the build job's"),
                       # (a SHA-256 given that is none: an empty one is no "no check")
                       ("an empty SHA-256", {"want": ""}, "is no SHA-256"), ("a SHA-256 of 63 hex digits", {"want": "0" * 63}, "is no SHA-256"),
                       ("a SHA-256 of 65 hex digits", {"want": "0" * 65}, "is no SHA-256"), ("a SHA-256 in capitals", {"want": "A" * 64}, "is no SHA-256"),
                       ("a SHA-256 with a newline", {"want": "0" * 64 + "\n"}, "is no SHA-256"),
                       ("sha256sum's whole line", {"want": "0" * 64 + "  build/x"}, "is no SHA-256"),
                       ("no SHA-256 in the release job", {"job": "release"}, "the release job gives")):
    rc, pkg, err = release(**kw)
    check(rc != 0 and said in err and not pkg, f"refused: {what} ({err.strip().splitlines()[-1][:160] if err.strip() else 'no word'})")
check(os.path.exists(os.path.join(ROOT, "keys", "author.pub")) == had_pub, "(this tree's own keys/ untouched)")
print("release: " + ("FAILED" if failed else "all passed"))
sys.exit(1 if failed else 0)
