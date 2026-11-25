#!/usr/bin/env python3
"""The plugin index's own check of a package, before you ask to be listed: hassmic-plugins' tools/index.py, read
from a checkout of that repository and never changed, run over your package as its build runs over the release
asset it fetches (the package's form, the manifest as an Echo reads it, its signature, the program's digest and the
rules a file shows, the capabilities, signature and LICENSE the entry names, no listing in it), and, given the
release's own asset (--released) and gh, its attestation as the index verifies it (tools/attest.py: this repository's
GitHub Actions built it at the tag's commit, on a runner GitHub hosts, in .github/workflows/release.yml's own run for
the push of the tag v<version>), and every action that workflow and those it calls use there by a commit of the
action's own repository, all the way down (tools/workflow.py: each action's own metadata at its commit too).  Then
the entry plugins/<id>.json your pull request would add, as the index's own `index.py entry` makes it, with the
commit, the workflow and the release asset's address filled in.

    tools/indexcheck.py <hassmic-plugins tree> <package.hmp> [--released]
    make index-check [INDEX=../hassmic-plugins] [RELEASED=<the release's asset, downloaded>]

The tier is community: what a plugin by anyone is listed as (the whole log is for official plugins only).  The
index builds nothing: it lists the asset your release workflow (.github/workflows/release.yml) built from the tag
v<version>, attested and published, and only where the entry names its bytes (the manifest's digest, your signature,
the LICENSE's SHA-256, the capabilities).  So the entry is to be taken from that asset (RELEASED): the package `make`
built here is a preview, and its digests are the release's only where this build is that one byte for byte (the
same commit, zig 0.13.0); said.  The commit is the tag's where the tag is here, else HEAD, said; outside a git
checkout a stand-in, said."""
import hashlib, json, os, subprocess, sys

# the workflow that builds, attests and publishes the release (the entry's "workflow": the attestation has to be its
# own run for the tag's push, and the index's maintainer reads it and everything it calls and runs)
WORKFLOW = ".github/workflows/release.yml"

a = [x for x in sys.argv[1:] if x != "--released"]
released = "--released" in sys.argv[1:]
if len(a) != 2:
    sys.exit(__doc__)
tree, package = a
if not os.path.exists(os.path.join(tree, "tools", "index.py")):
    sys.exit(f"index-check: no hassmic-plugins tree at {tree} (clone https://github.com/Gamer92000/hassmic-plugins beside "
             "this repository, or INDEX=<path>)")
sys.path.insert(0, os.path.join(tree, "tools"))
sys.dont_write_bytecode = True                  # (a read-only look: no __pycache__ left in that tree)
import index                                    # noqa: E402
if not hasattr(index, "entry_fields") or not {"package", "workflow"} <= set(getattr(index, "COMMUNITY_KEYS", ())):
    sys.exit(f"index-check: {tree} is a hassmic-plugins from before community entries named their release asset and workflow: pull it")

data = open(package, "rb").read()
files = index.members(data, package)
m = json.loads(files["manifest.json"])
try:
    v = index.entry_fields(package)
except index.Bad as e:
    sys.exit(f"index-check: the index would refuse it: {e}")
src = m.get("source", "https://github.com/<you>/<repository>")
tag = f"v{m.get('version')}"


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


head = git("rev-parse", "HEAD")
in_git = head is not None and len(head) in (40, 64)
tagged = git("rev-parse", "--verify", "-q", f"{tag}^{{commit}}") if in_git else None
# (outside a git checkout: the index's rules held to a stand-in commit, and the entry printed with a word in its place)
v["commit"] = tagged or (head if in_git else "0" * 40)
v["workflow"] = WORKFLOW
v["package"] = f"{src}/releases/download/{tag}/{m['id']}-{m['version']}.hmp"
entry = {"id": m["id"], "name": m["name"], "description": m.get("description", ""), "author": m["author"],
         "author_key": m["author_key"], "tier": "community", "source": src, "versions": [v]}
try:
    index.entry_check(f"{m['id']}.json", entry)       # the entry's own rules: its fields, the source, the commit, the asset
    index.package_check(entry, v, data)
except index.Bad as e:
    sys.exit(f"index-check: the index would refuse it: {e}")
print(f"index-check: {os.path.basename(package)}: the index's checks pass (as community)")
flags = index.broad(m.get("capabilities", {}))
print(f"index-check: workflow {WORKFLOW}: the index takes only an attestation of its own run for the push of the tag {tag}, at that "
      "commit (not one started by hand, not another workflow's); the maintainer reads it and all it runs (build.yml, tools/release.sh, the Makefile)")
if flags:
    print(f"index-check: broad capabilities, flagged on the index's page and on the Echo's \"Browse plugins\": {', '.join(flags)}")
if released and tagged:
    # the attestation, as the index's build verifies it (gh attestation verify, its answer read again): only the
    # release's own asset has one, and only GitHub's API knows it (gh needs to be logged in, or GH_TOKEN)
    try:
        import attest                           # noqa: E402 (the index's tools/attest.py)
        attest.verify(package, src, tagged, hashlib.sha256(data).hexdigest(), WORKFLOW, tag)
        print(f"index-check: its attestation holds: {src}'s own GitHub Actions built it in {WORKFLOW} for the push of {tag} ({tagged[:12]}), "
              "on a runner GitHub hosts")
    except ImportError:
        print("index-check: warning: this hassmic-plugins has no tools/attest.py: pull it to check the attestation too")
    except Exception as e:                      # (attest.Refused: a verdict; attest.Unavailable: gh not there, not logged in, offline)
        if type(e).__name__ == "Unavailable":
            print(f"index-check: warning: the attestation could not be verified here (no verdict): {e}")
        else:
            print(f"index-check: warning: the attestation was not verified here: {e}")
    # and what the workflow lets run, as the index's build asks it right after (its tools/workflow.py, through gh's
    # API): every action release.yml and build.yml use at the tag's commit by a commit of the action's own repository
    # (one of a fork runs under the original's name too: an impostor commit), every image by its digest
    try:
        import workflow                         # noqa: E402 (the index's tools/workflow.py)
        workflow.check(src, tagged, WORKFLOW)
        print(f"index-check: every action {WORKFLOW} and the workflows it calls use at {tagged[:12]} is pinned by a commit of its own repository")
    except ImportError:
        print("index-check: warning: this hassmic-plugins has no tools/workflow.py: pull it to check the workflows' actions too")
    except Exception as e:
        if type(e).__name__ == "Unavailable":
            print(f"index-check: warning: the workflows' actions could not be checked here (no verdict): {e}")
        else:
            print(f"index-check: warning: the index would refuse the release: {e}")
elif released:
    print(f"index-check: warning: no tag {tag} here: the attestation names the commit the release ran at; fetch the tags (git fetch --tags) "
          "and run this again")
if not released:
    print("index-check: warning: this is this tree's build, a preview: the entry names the release asset's bytes, which your release "
          f"workflow builds from the tag {tag}. Once it ran, check that asset: gh release download {tag} -p '{m['id']}-{m['version']}.hmp' "
          f"-D /tmp/rel && make index-check RELEASED=/tmp/rel/{m['id']}-{m['version']}.hmp")
if not in_git:
    print("index-check: warning: this is not a git checkout: the entry names the commit your release was built from; the commit below "
          "is a stand-in")
    v["commit"] = "<the commit the release was built from: 40 hex digits>"
else:
    if not tagged:
        print(f"index-check: warning: no tag {tag} here yet: the entry names the commit the release ran at (the tag's); HEAD stands in below")
    st = git("status", "--porcelain") if not released else None
    if st:
        # (files git does not have count: the Makefile's wildcard builds a new source in, and a release of the commit
        # has no such file; ignored ones, build/ and secrets/, do not)
        print(f"index-check: warning: the tree has changes that are not committed, or files git does not have ({len(st.splitlines())}): "
              "a release of the commit would not have them")
print(f"plugins/{m['id']}.json, as a pull request to hassmic-plugins would add it (the release has to be published first: the index "
      "fetches its asset and verifies its attestation):")
print(json.dumps(entry, indent=2, ensure_ascii=False))
