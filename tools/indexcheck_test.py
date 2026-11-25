#!/usr/bin/env python3
"""tools/indexcheck.py's own words, against a stand-in of hassmic-plugins' tools/index.py and tools/attest.py (their
checks pass whatever they are given, but where told otherwise: what is tested here is what index-check says around
them): the entry names the release asset of the tag v<version> and the tag's commit; without the tag, HEAD stands
in and that is said; this tree's build is a preview and said so, the release's own asset (--released) is checked for
its attestation, with the source, the tag's commit and the package's SHA-256, and a refused one is said; a file git
does not have counts as a change; outside a git checkout it says so, not "the index would refuse it".  Run by `make
test`."""
import atexit, hashlib, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
failed = False


def check(cond, what):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    failed |= not cond


top = tempfile.mkdtemp(prefix="indexcheck-test-")
# (the folder it made, and only that, gone again at the end, whatever happened: every run left one in the temporary
# folder; a review of hassmic 3c5d7ff)
atexit.register(shutil.rmtree, top, True)
idx = os.path.join(top, "index", "tools")
os.makedirs(idx)
open(os.path.join(idx, "index.py"), "w").write('''import json
class Bad(Exception): pass
COMMUNITY_KEYS = {"version", "commit", "workflow", "manifest", "package", "signature", "license_sha256", "capabilities"}
def members(data, name): return {"manifest.json": data}
def entry_fields(package): return {"version": 1, "commit": "<commit>", "manifest": "d" * 64, "package": "<package>"}
def entry_check(name, e):
    v = e["versions"][0]
    c = v["commit"]
    if len(c) not in (40, 64) or any(x not in "0123456789abcdef" for x in c): raise Bad("the commit is a commit's id: 40 hex digits")
    if not v["package"].startswith(e["source"] + "/releases/download/"): raise Bad("the package is the plugin's own release asset")
    if v.get("workflow") != ".github/workflows/release.yml": raise Bad("the workflow is the release workflow's path")
def package_check(e, v, data): pass
def broad(caps): return []
''')
ATTESTED = os.path.join(top, "attested.json")
open(os.path.join(idx, "attest.py"), "w").write(f'''import json
class Refused(Exception): pass
class Unavailable(Exception): pass
def verify(package, source, commit, sha256, workflow, tag, bundle=None):
    json.dump([package, source, commit, sha256, workflow, tag], open({ATTESTED!r}, "w"))
    if open(package).read().find("unattested") >= 0: raise Refused("no attestations found")
    if open(package).read().find("offline") >= 0: raise Unavailable("error creating Sigstore verifier")
    return b"{{}}\\n"
''')
CHECKED = os.path.join(top, "checked.json")
open(os.path.join(idx, "workflow.py"), "w").write(f'''import json
from attest import Refused, Unavailable
def check(source, commit, workflow):
    json.dump([source, commit, workflow], open({CHECKED!r}, "w"))
    if "impostor" in source: raise Refused("1 reference not pinned to what runs: a commit of a fork")
''')
SRC = "https://github.com/someone/hassmic-plugin-example"
pkg = os.path.join(top, "p.hmp")
manifest = {"id": "example", "name": "Example", "version": 1, "author": "a", "author_key": "a" * 64, "source": SRC, "capabilities": {}}
open(pkg, "w").write(json.dumps(manifest))
bad = os.path.join(top, "unattested.hmp")
open(bad, "w").write(json.dumps(dict(manifest, name="unattested")))
offline = os.path.join(top, "offline.hmp")
open(offline, "w").write(json.dumps(dict(manifest, name="offline")))
env0 = dict(os.environ)
env0.pop("PLUGIN_CC", None)


def run(cwd, package=pkg, released=False, **env):
    r = subprocess.run([sys.executable, os.path.join(HERE, "indexcheck.py"), os.path.join(top, "index"), package] + (["--released"] if released else []),
                       cwd=cwd, env=dict(env0, **env), capture_output=True, text=True)
    out = r.stdout + r.stderr
    try:
        entry = json.loads(out[out.index("\n{") + 1:])
    except ValueError:
        entry = {}
    return r.returncode, out, entry


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True, text=True).stdout.strip()


repo = os.path.join(top, "repo")
os.makedirs(os.path.join(repo, "src"))
open(os.path.join(repo, "src", "main.c"), "w").write("int main(void) { return 0; }\n")
open(os.path.join(repo, ".gitignore"), "w").write("build/\n")
git(repo, "init", "-q")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "c")
first = git(repo, "rev-parse", "HEAD")

rc, out, entry = run(repo)
v = (entry.get("versions") or [{}])[0]
check(rc == 0 and "checks pass" in out and "no tag v1 here yet" in out and v.get("commit") == first
      and v.get("package") == SRC + "/releases/download/v1/example-1.hmp" and v.get("workflow") == ".github/workflows/release.yml"
      and "workflow .github/workflows/release.yml" in out and "push of the tag v1" in out,
      "no tag yet: HEAD stands in for the commit, said; the package is the release asset of the tag v1, the workflow release.yml (printed, and what it means)")
check("a preview" in out and "make index-check RELEASED=" in out and not os.path.exists(ATTESTED),
      "this tree's build: said to be a preview, how to check the release's asset instead; no attestation asked for")
git(repo, "tag", "v1")
open(os.path.join(repo, "src", "main.c"), "a").write("/* after the tag */\n")
git(repo, "commit", "-q", "-am", "after")
rc, out, entry = run(repo, released=True)
v = (entry.get("versions") or [{}])[0]
asked = json.load(open(ATTESTED)) if os.path.exists(ATTESTED) else []
check(rc == 0 and v.get("commit") == first and "no tag" not in out and "a preview" not in out,
      "the tag v1 here: its commit is the entry's, not HEAD's (a commit after it); the release's asset is no preview")
check(asked == [pkg, SRC, first, hashlib.sha256(open(pkg, "rb").read()).hexdigest(), ".github/workflows/release.yml", "v1"] and "its attestation holds" in out,
      f"the release's asset: its attestation verified as the index does, with the source, the tag's commit, its SHA-256, the workflow and the tag ({asked})")
pinned = json.load(open(CHECKED)) if os.path.exists(CHECKED) else []
check(pinned == [SRC, first, ".github/workflows/release.yml"] and "is pinned by a commit of its own repository" in out,
      f"and every action the release workflow uses, checked as the index does, at the tag's commit ({pinned})")
rc, out, entry = run(repo, package=bad, released=True)
check(rc == 0 and "the attestation was not verified here: no attestations found" in out, "an asset without an attestation: said")
rc, out, entry = run(repo, package=offline, released=True)
check(rc == 0 and "could not be verified here (no verdict): error creating Sigstore verifier" in out, "gh without a verdict (offline): said as that, not as a refusal")
os.unlink(os.path.join(idx, "workflow.py"))
rc, out, entry = run(repo, released=True)
check(rc == 0 and "no tools/workflow.py" in out, "a hassmic-plugins without workflow.py: said, to pull it")
os.unlink(os.path.join(idx, "attest.py"))
rc, out, entry = run(repo, released=True)
check(rc == 0 and "no tools/attest.py" in out, "a hassmic-plugins without attest.py: said, to pull it")
os.makedirs(os.path.join(repo, "build"))
open(os.path.join(repo, "build", "x"), "w").write("x")
rc, out, entry = run(repo)
check(rc == 0 and "not committed" not in out, "a file git ignores (build/): no change")
open(os.path.join(repo, "src", "extra.c"), "w").write("int extra;\n")
rc, out, entry = run(repo)
check(rc == 0 and "files git does not have" in out, "a new source git does not have (the Makefile's wildcard builds it in): said")
plain = os.path.join(top, "plain")
os.makedirs(plain)
rc, out, entry = run(plain, GIT_CEILING_DIRECTORIES=top)
check(rc == 0 and "not a git checkout" in out and "would refuse" not in out and '"commit": "<the commit' in out,
      "outside a git checkout: said as that, the entry's commit a stand-in, not \"the index would refuse it\"")
open(os.path.join(top, "index", "tools", "index.py"), "a").write("COMMUNITY_KEYS = set()\n")
rc, out, entry = run(repo)
check(rc != 0 and "pull it" in out, "a hassmic-plugins from before entries named their release asset and workflow: said, to pull it")
print("indexcheck: " + ("FAILED" if failed else "all passed"))
sys.exit(1 if failed else 0)
