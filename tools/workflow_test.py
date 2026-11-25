#!/usr/bin/env python3
"""The release workflow (.github/workflows/release.yml) and CI (build.yml) read as data (a reader of the YAML they
are written in: mappings, lists, block texts; no YAML library in Python's standard one; the plugin index's own
reader, hassmic-plugins' tools/workflow.py, strict where a YAML parser could read otherwise, and held to that text
where the index's tree is beside this one): a tag releases only
after all of CI passed; the release job is exactly the one this test allows, key for key and step for step (an
allow-list: the checkout without a token left on disk, the compiler (zig as .github/zig.txt pins it, or Rust as
.github/rust.txt pins it, for a plugin released in Rust), the build job's artifact into checked/ for its SHA-256
alone, tools/release.sh, the attestation by actions/attest-build-provenance of the package it made, in release.yml's
own job, the release of dist/; only it names the signing key and an environment, and may write, sign and attest; no
`if:`); its Package step and CI's "Which hassmic" step run as GitHub runs them (bash -e), against a SHA-256 or an
sdk/VERSION that is not one (each has to fail); every action by the one commit ACTIONS names, its release tag's; CI
is the workflow release.yml calls (workflow_call), attests and releases nothing itself, never says `secrets`, names
no environment, goes on past no failure, runs the release's packing test, has the release job's compiler, uploads the
one artifact and keeps no cache, and checks hassmic out at sdk/VERSION's commit (a step of a name, an id and its run
alone).  This is the author's own check: the plugin index holds every release to its own (the attestation, every
action's commit the action's own repository's), whatever this test says.  Run by `make release-test`."""
import base64, hashlib, json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
failed = False
def check(cond, what):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    failed |= not cond


# The reader is the plugin index's own (hassmic-plugins' tools/workflow.py, the one its build reads every release
# workflow with), copied: strict where a YAML parser could read otherwise.  A character outside tab, line feed and
# printable ASCII (Python's splitlines breaks at a line or paragraph separator, NEL, a form feed, where YAML and bash
# see one line), a folded block (>: YAML folds its blank lines), a tab, an anchor, an alias, a flow mapping, a quoted
# key, a value YAML could read as more: no reading, never a part skipped (a review of 032e15c)
# a plain value YAML would read as something else when it starts so (an anchor, alias, tag, flow, block, quote,
# directive, reserved, comment, or an indicator with a blank after it)
_INDICATOR = re.compile(r"[&*!{}\[\]|>'\"%@`#,]|[-?:](\s|$)")


def scalar(t):
    """a value as YAML reads it (a text, or a list of texts from a flow list); ValueError where YAML might read it
    otherwise"""
    t = t.strip()
    if t.startswith("["):
        if not t.endswith("]") or re.search(r"[\[\]{}]", t[1:-1]):
            raise ValueError(f"a flow list not read: {t[:80]}")
        items = [x.strip() for x in t[1:-1].split(",")]
        if items[-1] == "":
            items.pop()
        if any(x == "" for x in items):
            raise ValueError(f"a flow list not read: {t[:80]}")
        return [scalar(x) for x in items]
    if t.startswith("'"):
        if len(t) < 2 or not t.endswith("'") or "'" in t[1:-1].replace("''", ""):
            raise ValueError(f"a quoted value not read: {t[:80]}")
        return t[1:-1].replace("''", "'")
    if t.startswith('"'):
        if len(t) < 2 or not t.endswith('"') or '"' in t[1:-1] or "\\" in t:
            raise ValueError(f"a quoted value not read: {t[:80]}")
        return t[1:-1]
    if _INDICATOR.match(t) or ": " in t or t.endswith(":") or " #" in t:
        raise ValueError(f"a value not read as plain: {t[:80]}")
    return t


def parse(text):
    """the subset of YAML a workflow is written in, as dicts, lists and strings, and the numbers of the lines that are
    block texts' (the rest are YAML's own).  Anything else is no reading (ValueError), never a part skipped: a key
    twice, a line left over (deeper than its place, or after the end), a key of another form.  A block text (run: |)
    is kept as written, its comment lines and its ` #` too: they are the shell's, and a check that read them cut off
    would pass what the shell runs (a review of 6bca341)"""
    text = text.replace("\r\n", "\n")
    bad = re.search(r"[^\t\n\x20-\x7e]", text)
    if bad:
        raise ValueError(f"a character outside tab, line feed and printable ASCII (U+{ord(bad.group()):04X}), line "
                         f"{text.count(chr(10), 0, bad.start()) + 1}: YAML and a reader by lines may break its lines apart differently")
    raw = text.split("\n")
    lines, blocks = [], set()
    for n, r in enumerate(raw):
        # (" #" begins YAML's comment on a line of its own; inside a quoted value it would not, and that value then
        # does not read: refused, never cut)
        s = r.split(" #")[0].rstrip()
        if s.strip() and not s.lstrip().startswith("#"):
            lines.append((len(s) - len(s.lstrip(" ")), s.strip(" "), n))
    pos = [0]

    def take():
        ind, s, n = lines[pos[0]]
        if "\t" in raw[n].split(" #")[0]:
            raise ValueError(f"a tab outside a block text, line {n + 1}: YAML takes none in indentation")
        return ind, s, n

    def block(indent):
        if pos[0] >= len(lines):
            return None
        if lines[pos[0]][1].startswith("- "):
            out = []
            while pos[0] < len(lines) and lines[pos[0]][0] == indent and lines[pos[0]][1].startswith("- "):
                ind, s, n = take()
                rest = s[2:]
                if re.match(r"[\w.-]+:( |$)", rest):
                    # a mapping as the item: its first key on the dash's line
                    lines[pos[0]] = (ind + 2, rest, n)
                    out.append(mapping(ind + 2))
                else:
                    pos[0] += 1
                    out.append(scalar(rest))
            return out
        return mapping(indent)

    def mapping(indent):
        out = {}
        while pos[0] < len(lines) and lines[pos[0]][0] == indent and not lines[pos[0]][1].startswith("- "):
            ind, s, n = take()
            m = re.match(r"([\w.-]+):(?: (.*))?$", s)
            if not m:
                raise ValueError(f"not read: line {n + 1}: {s[:80]}")
            key, val = m.group(1), (m.group(2) or "").strip()
            if key in out:
                raise ValueError(f"{key} twice, line {n + 1}")
            pos[0] += 1
            if re.fullmatch(r"\|[-+]?", val):
                # the raw lines below the key, as long as they are deeper than it (blank ones within); YAML's block
                # is as deep as its first line, and a line less deep ends it: refused here, never read into it
                end = n + 1
                while end < len(raw) and (not raw[end].strip() or len(raw[end]) - len(raw[end].lstrip(" ")) > indent):
                    end += 1
                body = raw[n + 1:end]
                while body and not body[-1].strip():
                    body.pop()
                deep = next((len(x) - len(x.lstrip(" ")) for x in body if x.strip()), 0)
                if any(x.strip() and not x.startswith(" " * deep) for x in body):
                    raise ValueError(f"a block text whose lines are less deep than its first, line {n + 2}")
                blocks.update(range(n + 1, end))
                out[key] = "\n".join(x[deep:] for x in body)
                while pos[0] < len(lines) and lines[pos[0]][2] < end:
                    pos[0] += 1
            elif val:
                out[key] = scalar(val)
            elif pos[0] < len(lines) and lines[pos[0]][0] > indent:
                out[key] = block(lines[pos[0]][0])
            elif pos[0] < len(lines) and lines[pos[0]][0] == indent and lines[pos[0]][1].startswith("- "):
                out[key] = block(indent)
            else:
                out[key] = None
        return out
    top = block(0)
    if pos[0] < len(lines):
        raise ValueError(f"not read: line {lines[pos[0]][2] + 1}: {lines[pos[0]][1][:80]}")
    return top, blocks


def read(text):
    """parse's dicts, lists and strings"""
    return parse(text)[0]


# This reader is the index's, the same text: where hassmic-plugins is beside this tree (INDEX, else
# ../hassmic-plugins), its tools/workflow.py's _INDICATOR, scalar, parse and read are held to these, as written (the
# index's own tests hold the three copies to one in its CI's siblings job; a review of 810b9e9: nothing did, though
# both said "the same text").  Not there (this repository's own CI): said, and not asked
def reader_parts(path):
    import ast
    text = open(path, encoding="utf-8").read()
    out = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name in ("scalar", "parse", "read"):
            out[node.name] = ast.get_source_segment(text, node)
        elif isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["_INDICATOR"]:
            out["_INDICATOR"] = ast.get_source_segment(text, node)
    return out


INDEX = os.path.join(os.environ.get("INDEX") or os.path.join(os.path.dirname(ROOT), "hassmic-plugins"), "tools", "workflow.py")
if os.path.isfile(INDEX):
    mine, theirs = reader_parts(os.path.abspath(__file__)), reader_parts(INDEX)
    differ = [k for k in ("_INDICATOR", "scalar", "parse", "read") if mine.get(k) is None or mine.get(k) != theirs.get(k)]
    check(not differ, f"this reader is the index's reader, the same text as {INDEX}'s" + (f": not so in {', '.join(differ)}" if differ else ""))
else:
    print(f"skip the index's reader: no {INDEX} (INDEX names a hassmic-plugins tree)")


def load(name):
    p = os.path.join(ROOT, ".github", "workflows", name)
    try:
        # (as written: no newline translation, whose lone CR would become a line break before the reader sees it)
        text = open(p, encoding="utf-8", newline="").read()
        return text, read(text)
    except (ValueError, UnicodeDecodeError) as x:
        print(f"FAIL {p} does not read: {x}")
        sys.exit(1)


rtext, rel = load("release.yml")
btext, bld = load("build.yml")
on = rel.get("on") or {}
check(set(rel) == {"name", "on", "permissions", "jobs"},
      f"release.yml has a name, its trigger, its permissions and its jobs, nothing for all jobs (no env, no defaults: a shell of its own): {sorted(rel)}")
# (one rule a check, each with words of its own: tools/workflow_breaks.py makes each check always true in turn, and
# some break has to pass then; a rule that was one operand of another check's `and` was never asked so, and most of
# them passed every break when made always true one at a time: a review of the template's 1beecab, microWakeWord's
# 3a835c0)
check(list(on) == ["push"], f"release.yml has one trigger, a push (no schedule, no other event): {list(on)}")
check(on.get("push") == {"tags": ["v*"]}, f"release.yml runs for a tag v* and nothing else: {on.get('push')}")
jobs = rel.get("jobs") or {}
check(set(jobs) == {"checks", "release"}, f"release.yml's jobs are the checks and the release job, no other: {sorted(jobs)}")
check(jobs.get("checks") == {"uses": "./.github/workflows/build.yml"},
      "release.yml: the checks are build.yml, called (all of CI; no inputs, no secrets handed on)")
check(rel.get("permissions") == {"contents": "read"}, "the workflow's permissions: read")
# (only a tag's push: the trigger the index asks of the attestation)
check("workflow_dispatch" not in on, "release.yml is never run by hand (workflow_dispatch)")
check("workflow_call" not in on, "release.yml is never called by another workflow (workflow_call)")
check("pull_request" not in on, "release.yml never runs for a pull request")

# The release job, as a list of what it may be: every key of the job and of each step, every action by its commit,
# every text as written here (a review of 6bca341: a check that looked for what must not be there passed a step
# that packed the build job's artifact after tools/release.sh, a step between Package and Attest that put it in
# dist/, a curl, a git clone of a branch, an unpinned zig, `if: always()` on the job).  So nothing runs in the job
# that attests but these steps: the checkout (no token left on disk while it builds), the zig .github/zig.txt pins
# (the build job's own Compiler step, the same text), the build job's artifact into checked/, which only the
# Package step reads, its SHA-256 alone; tools/release.sh builds, packs, signs and checks into dist/ (the one
# writer there but the attestation's bundle); the attestation of that package, right after it; the release of dist/.
# No `if:` on the job (`always()` would release after failed checks), no container, no env, no other keys.  A
# change of release.yml's release job is a change here too, which is the point: whoever changes the one sees the
# other, and the index's maintainer reads both
# Every action by the one commit named here: the commit of the release tag in the comment beside it in the
# workflows, a commit of the action's own repository (the head of that tag).  GitHub resolves <owner>/<repo>@<commit>
# across every fork of the repository, so any 40 hex digits would let a commit of anyone's fork run under the
# original's name (an impostor commit; a review of 032e15c).  Raising one is a change here too: take the commit from
# the action's own tags (gh api repos/<owner>/<repo>/tags), never from a link.  The plugin index checks the same of
# every release's workflows, whatever this test says (hassmic-plugins' tools/workflow.py: a commit on none of the
# action's own branches and tags is refused)
ACTIONS = {"actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",                  # v7.0.1
           "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",         # v8.0.2
           "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",           # v7.0.2
           "actions/attest-build-provenance": "4d101475d8b20a2381f78447822ac1eab6504dd8",   # v4.2.2
           "astral-sh/setup-uv": "c18668ad3cf93ea998bef934396af7bb5c839dc7"}                # v10.2.0


def pinned(action):
    return re.compile(re.escape(f"{action}@{ACTIONS[action]}"))


def keys(v, name, path=""):
    """the places of every key NAME in V, at any depth"""
    if isinstance(v, dict):
        return [p for k, x in v.items() for p in ([f"{path}.{k}"] if k == name else []) + keys(x, name, f"{path}.{k}")]
    if isinstance(v, list):
        return [p for i, x in enumerate(v) for p in keys(x, name, f"{path}[{i}]")]
    return []


COMPILER = """python3 -m venv "$RUNNER_TEMP/zig"
"$RUNNER_TEMP/zig/bin/pip" install --require-hashes -r .github/zig.txt
echo "PLUGIN_CC=$RUNNER_TEMP/zig/bin/python -m ziglang cc -target arm-linux-musleabihf -mcpu=cortex_a7" >> "$GITHUB_ENV\""""
# the compiler of a plugin released in Rust instead (README.md "Rust"): the toolchain .github/rust.txt pins by
# SHA-256, installed by tools/rustpin.sh, first on PATH for the job's later steps.  The release job's Compiler step
# is one of the two, and the build job's the same text
RUST_COMPILER = """rust=$(tools/rustpin.sh "$RUNNER_TEMP/rust")
echo "$rust" >> "$GITHUB_PATH\""""
COMPILERS = re.compile("|".join(re.escape(c) for c in (COMPILER, RUST_COMPILER)))
PACKAGE = """[ -n "$PLUGIN_SIGNING_KEY" ] || { echo "::error::secret PLUGIN_SIGNING_KEY missing in the environment release"; exit 1; }
id=$(python3 -c 'import json; print(json.load(open("manifest.json"))["id"])')
sha=$(cat "checked/$id.sha256")
[[ $sha =~ ^[0-9a-f]{64}$ ]] || { echo "::error::the build job's artifact holds no SHA-256 of its program ($id.sha256)"; exit 1; }
mkdir -p secrets
trap 'rm -f secrets/author.key' EXIT
(umask 077; echo "$PLUGIN_SIGNING_KEY" | base64 -d > secrets/author.key)
pkg=$(tools/release.sh "$GITHUB_REF_NAME" "$GITHUB_REPOSITORY" secrets/author.key dist "$sha")
echo "path=$pkg" >> "$GITHUB_OUTPUT\""""
PUBLISH = """name=$(python3 -c 'import json; print(json.load(open("manifest.json"))["name"])')
cp "$BUNDLE" "$PACKAGE.sigstore.json"
gh release create "$GITHUB_REF_NAME" dist/* --title "$name $GITHUB_REF_NAME" --generate-notes"""
RELEASE_JOB = {
    "needs": "checks", "runs-on": "ubuntu-24.04", "timeout-minutes": "15", "environment": "release",
    "permissions": {"contents": "write", "id-token": "write", "attestations": "write"},
    "steps": [
        {"uses": pinned("actions/checkout"), "with": {"persist-credentials": "false"}},
        {"name": "Compiler", "run": COMPILERS},
        {"uses": pinned("actions/download-artifact"), "with": {"name": "program", "path": "checked"}},
        {"name": "Package", "id": "package", "env": {"PLUGIN_SIGNING_KEY": "${{ secrets.PLUGIN_SIGNING_KEY }}"}, "run": PACKAGE},
        {"name": "Attest", "id": "attest", "if": "${{ !env.ACT }}", "uses": pinned("actions/attest-build-provenance"),
         "with": {"subject-path": "${{ steps.package.outputs.path }}"}},
        {"name": "Publish", "if": "${{ !env.ACT }}",
         "env": {"GH_TOKEN": "${{ github.token }}", "BUNDLE": "${{ steps.attest.outputs.bundle-path }}", "PACKAGE": "${{ steps.package.outputs.path }}"},
         "run": PUBLISH},
        {"name": "Attest and publish (dry run)", "if": "${{ env.ACT }}", "run": "ls -l dist"},
    ]}


def unlike(got, want, where):
    """where GOT is not WANT (an allow-list: a regex fullmatches, a dict has exactly its keys), "" where it is"""
    if isinstance(want, re.Pattern):
        return "" if isinstance(got, str) and want.fullmatch(got) else f"{where}: {str(got)[:80]!r}, not {want.pattern}"
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return f"{where}: not a mapping ({str(got)[:60]!r})"
        if set(got) != set(want):
            return f"{where}: keys {sorted(got)}, not {sorted(want)}"
        return next((u for k in want for u in [unlike(got[k], want[k], f"{where}.{k}")] if u), "")
    if isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            return f"{where}: {len(got) if isinstance(got, list) else 'no'} steps, not {len(want)}"
        return next((u for i, w in enumerate(want) for u in [unlike(got[i], w, f"{where}[{i}]")] if u), "")
    return "" if got == want else f"{where}: {str(got)[:160]!r}, not {str(want)[:160]!r}"


r = jobs.get("release") or {}
why = unlike(r, RELEASE_JOB, "jobs.release")
check(why == "", "the release job is the one this test allows, key for key and step for step (checkout, the pinned zig, the "
      "build job's artifact into checked/, tools/release.sh, the attestation, the release of dist/): " + (why or "it is"))
# no secret reaches CI: the word nowhere in build.yml (`secrets.X`, `secrets['X']`, `toJSON(secrets)` alike), and no
# job of it names an environment (a job that names one gets that environment's secrets, the signing key's too, and
# build.yml is the workflow release.yml calls; a review of 032e15c); in release.yml the release job alone
check(not re.search(r"secrets", btext), "no secret reaches CI: build.yml never says `secrets`")
check(rtext.count("secrets.") == 1, f"release.yml names one secret, once: the release job's signing key ({rtext.count('secrets.')})")
check(keys(bld, "environment") == [], f"no job of build.yml names an environment (whose secrets a job gets): {keys(bld, 'environment')}")
check(keys(rel, "environment") == [".jobs.release.environment"], f"in release.yml the release job alone names an environment: {keys(rel, 'environment')}")
check(not re.search(r"\bchecked\b", str(bld)), "build.yml never names checked/ (the release job's folder for the build job's artifact)")
check(len(re.findall(r"\bchecked\b", "\n".join(str(x) for x in r.get("steps", [])))) == 2,
      "checked/ is named twice in the release job: the download's path and the one SHA-256 the Package step reads")


# The Package step and the hassmic step run as GitHub runs them, against what they read: a step that fails where it
# has to, not a pattern in its text (a review of 6bca341: inside an argument a cat that found nothing gave
# tools/release.sh an empty SHA-256, which it took as "no check").  GitHub runs a step that names no shell, on Linux,
# as `bash -e {0}` (its docs, "Using a specific shell": `shell: bash` would be `bash --noprofile --norc -eo pipefail
# {0}`, the stricter one, which ran here before a review of 032e15c), and neither step names one, nor does either
# workflow set `defaults` (both held below): so -e, and no pipefail
def bash(script, cwd, env):
    p = os.path.join(cwd, "step.sh")
    with open(p, "w") as f:
        f.write(script + "\n")
    base = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    r = subprocess.run(["bash", "-e", p], cwd=cwd, env=dict(base, **env), capture_output=True, text=True)
    os.unlink(p)
    return r


pkg = next((x for x in r.get("steps") or [] if isinstance(x, dict) and x.get("id") == "package"), {})
man = json.load(open(os.path.join(ROOT, "manifest.json")))
SHA = hashlib.sha256(b"the build job's program").hexdigest()
for what, content, ok in (("its SHA-256", SHA + "\n", True), ("no file", None, False), ("an empty file", "", False),
                          ("63 hex digits", SHA[:63] + "\n", False), ("65 hex digits", SHA + "0\n", False),
                          ("in capitals", SHA.upper() + "\n", False), ("sha256sum's whole line", f"{SHA}  build/x\n", False),
                          ("two of them", f"{SHA}\n{SHA}\n", False)):
    with tempfile.TemporaryDirectory(prefix="package-step-") as t:
        os.makedirs(os.path.join(t, "tools"))
        os.makedirs(os.path.join(t, "checked"))
        json.dump(man, open(os.path.join(t, "manifest.json"), "w"))
        if content is not None:
            open(os.path.join(t, "checked", man["id"] + ".sha256"), "w").write(content)
        # tools/release.sh stands in: its arguments into a file, a package's path printed
        with open(os.path.join(t, "tools", "release.sh"), "w") as f:
            f.write('#!/bin/sh\nprintf "%s\\n" "$@" > args\necho dist/x.hmp\n')
        os.chmod(os.path.join(t, "tools", "release.sh"), 0o755)
        out = os.path.join(t, "output")
        run = bash(str(pkg.get("run", "")), t,
                   {"PLUGIN_SIGNING_KEY": base64.b64encode(bytes(64)).decode(), "GITHUB_REF_NAME": "v1",
                    "GITHUB_REPOSITORY": "someone/plugin", "GITHUB_OUTPUT": out})
        args = open(os.path.join(t, "args")).read().splitlines() if os.path.exists(os.path.join(t, "args")) else None
        said = open(out).read() if os.path.exists(out) else ""
        if ok:
            check(run.returncode == 0, f"the Package step, {what} in checked/: ends well, status 0 ({run.returncode} {run.stderr.strip()[-160:]})")
            check(args == ["v1", "someone/plugin", "secrets/author.key", "dist", SHA],
                  f"the Package step, {what} in checked/: tools/release.sh given it as its fifth argument ({args})")
            check(said == "path=dist/x.hmp\n", f"the Package step, {what} in checked/: the package's path is the step's output ({said!r})")
            check(not os.path.exists(os.path.join(t, "secrets", "author.key")), f"the Package step, {what} in checked/: the key gone after")
        else:
            why = run.stderr.strip().splitlines()[-1:] or ""
            check(run.returncode != 0, f"the Package step, {what} in checked/: fails, a status not 0 ({run.returncode} {why})")
            check(args is None, f"the Package step, {what} in checked/: tools/release.sh never run ({args})")
            check("path=" not in said, f"the Package step, {what} in checked/: no package's path as its output ({said!r})")

# what the release takes from outside its commit, CI's side: zig as .github/zig.txt names it, by its SHA-256, the
# build job's Compiler step the release job's text; the build job's program the one artifact (uploaded there alone,
# downloaded by the release job alone); no cache in the jobs whose bytes count; no `vars`, no release asset read back
allsteps = [x for w in (rel, bld) for j in (w.get("jobs") or {}).values() for x in (j.get("steps") or []) if isinstance(x, dict)]
bsteps = [x for x in ((bld.get("jobs") or {}).get("build") or {}).get("steps", []) if isinstance(x, dict)]
bcomp = [x for x in bsteps if x.get("name") == "Compiler"]
rcomp = next((x.get("run") for x in r.get("steps") or [] if isinstance(x, dict) and x.get("name") == "Compiler"), None)
check(len(bcomp) == 1, f"the build job has one Compiler step ({len(bcomp)})")
check(set(bcomp[0] if bcomp else {}) == {"name", "run"}, "the build job's Compiler step is its name and its run, nothing else (no if:, no shell)")
check(bcomp[0].get("run") == rcomp if bcomp else False, "the build job's compiler is the release job's, the same text")
if rcomp == COMPILER or os.path.exists(os.path.join(ROOT, ".github", "zig.txt")):
    zigtxt = open(os.path.join(ROOT, ".github", "zig.txt")).read()
    pins = [x for x in zigtxt.splitlines() if x.strip() and not x.startswith("#")]
    pips = re.findall(r"pip\b[^\n]*\binstall\b[^\n]*", "\n".join(str(x.get("run", "")) for x in allsteps))
    named = [x for x in allsteps if "PLUGIN_CC" in str(x)]
    zig = 2 if rcomp == COMPILER else 0
    check(len(pins) == 1, f".github/zig.txt pins one package, zig: {pins}")
    check(re.fullmatch(r"ziglang==[0-9.]+ --hash=sha256:[0-9a-f]{64}", pins[0] if pins else "") is not None,
          f"zig only as .github/zig.txt names it, by its version and its SHA-256: {pins[:1]}")
    check(len(pips) == zig, f"pip installs in the build job and the release job alone, where zig is their compiler: {pips}")
    check(all(x.endswith("install --require-hashes -r .github/zig.txt") for x in pips),
          f"every pip install is --require-hashes -r .github/zig.txt, nothing after it: {pips}")
    check(len(named) == zig, f"no other step names PLUGIN_CC than the zig Compiler steps ({len(named)})")
    check(all(x.get("run") == COMPILER for x in named), "every step that names PLUGIN_CC is a zig Compiler step, as written here")
# Rust: only as .github/rust.txt pins it, every file by its SHA-256 from static.rust-lang.org, the version
# rust/rust-toolchain.toml names, installed by tools/rustpin.sh; never rustup (the runner's resolves the version by a
# manifest it fetches, checked against nothing pinned here); and every step that builds Rust has it first: in its own
# run, or after its job's Compiler step that put it on PATH
rusttxt = os.path.join(ROOT, ".github", "rust.txt")
if os.path.exists(rusttxt) or rcomp == RUST_COMPILER:
    lines_ = [x for x in open(rusttxt).read().splitlines() if x.strip() and not x.startswith("#")] if os.path.exists(rusttxt) else []
    tc = open(os.path.join(ROOT, "rust", "rust-toolchain.toml")).read() if os.path.exists(os.path.join(ROOT, "rust", "rust-toolchain.toml")) else ""
    channel = re.search(r'^channel = "([0-9]+\.[0-9]+\.[0-9]+)"$', tc, re.M)
    form = [re.fullmatch(r"[0-9a-f]{64}  https://static\.rust-lang\.org/dist/[0-9]{4}-[0-9]{2}-[0-9]{2}/"
                         r"(rustc|cargo|rust-std)-([0-9]+\.[0-9]+\.[0-9]+)-([a-z0-9_]+-[a-z0-9_-]+)\.tar\.xz", x) for x in lines_]
    # (none of these passes an empty rust.txt: one version, and the three kinds, are asked of its files)
    check(all(form), f"every line of .github/rust.txt a file of static.rust-lang.org by its SHA-256: {[x[:60] for x, m in zip(lines_, form) if not m]}")
    check(channel is not None, "rust/rust-toolchain.toml names a version of Rust as its channel (never stable, beta or nightly)")
    check({m.group(2) for m in form if m} == {channel.group(1) if channel else None},
          f"Rust as .github/rust.txt pins it: the version rust/rust-toolchain.toml names, every file "
          f"({channel and channel.group(1)}: {sorted({m.group(2) for m in form if m})})")
    check({m.group(1) for m in form if m} == {"rustc", "cargo", "rust-std"},
          f"rust.txt pins rustc, cargo and rust-std, each: {sorted({m.group(1) for m in form if m})}")
    late = []
    for w in (rel, bld):
        for jn, j in (w.get("jobs") or {}).items():
            have = False
            for x in (j.get("steps") or []) if isinstance(j, dict) else []:
                t = str(x.get("run", "")) if isinstance(x, dict) else ""
                have |= t == RUST_COMPILER
                if re.search(r"\bcargo\b|rust-unit|-rs\b|\brust\b", t) and not have and "tools/rustpin.sh" not in t:
                    late.append(f"{jn}: {x.get('name')}")
    check(not late, f"every step that builds Rust has the pinned toolchain first: {late}")
    check(not re.search(r"\brustup\b", "\n".join(str(x.get("run", "")) for x in allsteps)), "no step runs rustup")
    # tools/rustpin.sh refuses a file that is not the one named, before anything of it is unpacked: each file a
    # stand-in (RUSTPIN_BASE, file://) of the right name and form whose bytes are another's
    if lines_ and all(form):
        import tarfile, io
        with tempfile.TemporaryDirectory(prefix="rustpin-test-") as t:
            for m in form:
                name = m.group(0).split("/dist/")[1]
                os.makedirs(os.path.join(t, "site", os.path.dirname(name)), exist_ok=True)
                with tarfile.open(os.path.join(t, "site", name), "w:xz") as tf:
                    # (rustpin.sh runs it as `install.sh --prefix=DIR --disable-ldconfig`: the prefix is $1.  With $2
                    # mkdir was given --disable-ldconfig/bin, an option it refuses: nothing was ever installed, and a
                    # download taken unchecked passed "installs nothing" until that rule was a check of its own)
                    body = b"#!/bin/sh\nmkdir -p \"${1#--prefix=}/bin\" && touch \"${1#--prefix=}/bin/unpacked\"\n"
                    ti = tarfile.TarInfo(os.path.basename(name)[:-len(".tar.xz")] + "/install.sh")
                    ti.size, ti.mode = len(body), 0o755
                    tf.addfile(ti, io.BytesIO(body))
            run_ = subprocess.run(["sh", os.path.join(ROOT, "tools", "rustpin.sh"), os.path.join(t, "out")], capture_output=True, text=True,
                                  env=dict(os.environ, RUSTPIN_BASE="file://" + os.path.join(t, "site") + "/"))
            last = run_.stderr.strip().splitlines()[-1:] if run_.stderr.strip() else ""
            check(run_.returncode != 0, f"tools/rustpin.sh, a download that is not the file named: fails, a status not 0 ({run_.returncode} {last})")
            check("is not the file .github/rust.txt names" in run_.stderr,
                  f"tools/rustpin.sh, a download that is not the file named: says it is not the file .github/rust.txt names, its SHA-256 ({last})")
            check(run_.stdout.strip() == "", f"tools/rustpin.sh, a download that is not the file named: names no folder ({run_.stdout.strip()[:80]!r})")
            check(not os.path.exists(os.path.join(t, "out", "bin")), "tools/rustpin.sh, a download that is not the file named: unpacks and installs nothing")
ups = [(n, x) for n, j in (bld.get("jobs") or {}).items() for x in (j.get("steps") or []) if isinstance(x, dict) and "upload-artifact" in str(x.get("uses"))]
check(len(ups) == 1, f"one artifact uploaded in build.yml: {[n for n, _ in ups]}")
check(ups[0][0] == "build" if len(ups) == 1 else False, f"the artifact uploaded by the build job, whose program it is: {[n for n, _ in ups]}")
check((ups[0][1].get("with") or {}).get("name") == "program" if len(ups) == 1 else False, "the artifact is named program, the one the release job downloads")
check("upload-artifact" not in rtext, "release.yml uploads no artifact")
def uses_of(v):
    if isinstance(v, dict):
        return ([v["uses"]] if "uses" in v else []) + [u for x in v.values() for u in uses_of(x)]
    return [u for x in v for u in uses_of(x)] if isinstance(v, list) else []
uses = uses_of(rel) + uses_of(bld)
said = len(re.findall(r"^\s*(?:-\s+)?uses:", rtext + "\n" + btext, re.M))
# (an empty list passes neither: the release job's allow-list above has its actions)
check(said == len(uses), f"every `uses:` line of the workflows read as a uses ({said} lines, {len(uses)} read)")
check(all(u == "./.github/workflows/build.yml" or u in {f"{a}@{c}" for a, c in ACTIONS.items()} for u in uses),
      f"every action by the one commit ACTIONS names, its release tag's (no other commit, nor one of a fork): {uses}")
bon = bld.get("on") or {}
check("workflow_call" in bon, f"build.yml is CI: called by release.yml, run for pushes and pull requests: {list(bon)}")
check("tags" not in str(bon.get("push")), f"build.yml runs for no tag's push (it releases nothing itself): {bon.get('push')}")
check("release" not in (bld.get("jobs") or {}), f"build.yml has no release job: {sorted(bld.get('jobs') or {})}")
check("make release-test" in str(bld), "CI runs the release's packing test (make release-test)")
# (the index takes only the provenance whose signer and run are both release.yml, for a tag's push: one made in
# build.yml, called, would name build.yml)
check("attest" not in btext, "nothing in build.yml attests")
check("id-token" not in btext, "no job of build.yml may sign (no id-token)")
check(not any(re.search(r"actions/cache|/setup-|cache", str(x.get("uses", "")) + str(x.get("with", ""))) for x in bsteps),
      "no cache in the build job, whose SHA-256 the release is held to")
said = "\n".join(str(x) for x in allsteps) + str(bld.get("env"))
check("vars." not in said, "no `vars` as an input")
check("release download" not in said, "no release asset read back by gh release download")
check("releases/download" not in said, "no release asset read back by its address (releases/download)")
# hassmic, which the test job runs, at the commit sdk/VERSION names, never a ref that moves; a VERSION that names
# none (tools/sync.sh from a hassmic that is no git tree writes "hassmic unknown") fails the step: actions/checkout
# given ref '' takes the default branch (a review of 6bca341)
others = [x for j in (bld.get("jobs") or {}).values() for x in j.get("steps", []) if isinstance(x, dict)
          and "actions/checkout" in str(x.get("uses")) and (x.get("with") or {}).get("repository")]
which = [x for j in (bld.get("jobs") or {}).values() for x in j.get("steps", []) if isinstance(x, dict) and x.get("id") == "hassmic"]
check(len(others) == 1, f"hassmic checked out by a step of its own, actions/checkout with its repository ({len(others)})")
check(all((x.get("with") or {}).get("ref") == "${{ steps.hassmic.outputs.commit }}" for x in others),
      "another repository (hassmic) checked out only at the commit the step \"Which hassmic\" names")
check(len(which) == 1, f"one step has the id hassmic, whose output names that commit ({len(which)})")
# and that step runs whenever the checkout does, its failure the job's: no `if:` (false: the step skipped, its output
# empty, and actions/checkout given ref '' takes hassmic's default branch), no continue-on-error, no shell of its own
# (it runs as this test runs it, below); no job or step of CI goes on past a failure (continue-on-error on the test
# job or on "Build, twice" would let a tag release after failed tests: release.yml needs build.yml's jobs to pass),
# and no `defaults` sets another shell for its steps (a review of 032e15c)
check(set(which[0] if which else {}) == {"name", "id", "run"}, f"\"Which hassmic\" is a name, its id and its run, nothing else (no if:, no shell): {sorted(which[0]) if which else None}")
# (the word anywhere in build.yml: a key of any job or step, or a comment that would hide one)
check("continue-on-error" not in btext, "nothing in CI goes on past a failure (no continue-on-error)")
check(keys(bld, "defaults") == [], "no `defaults` in build.yml: every step runs in GitHub's default shell, bash -e")
check(keys(rel, "defaults") == [], "no `defaults` in release.yml either")
C40 = "60a8589c7c5a9ba1fb7fd87872c94f432dfe11c5"
for what, version, ok in (("a commit", f"scopes core 1\nhassmic {C40}\n", C40), ("a commit with changes not committed", f"hassmic {C40} with changes not committed there\n", C40),
                          ("hassmic unknown (no git tree)", "scopes core 1\nhassmic unknown\n", None), ("no hassmic line", "scopes core 1\n", None),
                          ("a short commit", "hassmic 60a8589\n", None), ("two commits", f"hassmic {C40}\nhassmic {C40}\n", None), ("no file", None, None)):
    with tempfile.TemporaryDirectory(prefix="hassmic-step-") as t:
        os.makedirs(os.path.join(t, "plugin", "sdk"))
        if version is not None:
            open(os.path.join(t, "plugin", "sdk", "VERSION"), "w").write(version)
        out = os.path.join(t, "output")
        run = bash(str((which or [{}])[0].get("run", "")), t, {"GITHUB_OUTPUT": out})
        said = open(out).read() if os.path.exists(out) else ""
        if ok:
            check(run.returncode == 0, f"\"Which hassmic\", sdk/VERSION with {what}: the step ends well, status 0 ({run.returncode} {run.stderr.strip()[-120:]})")
            check(said == f"commit={ok}\n", f"\"Which hassmic\", sdk/VERSION with {what}: the step names it, commit={ok[:12]}... ({said!r})")
        else:
            check(run.returncode != 0, f"\"Which hassmic\", sdk/VERSION with {what}: the step fails, a status not 0 ({run.returncode})")
            check("commit=" not in said, f"\"Which hassmic\", sdk/VERSION with {what}: no commit named ({said!r})")
print("workflow: " + ("FAILED" if failed else "all passed"))
sys.exit(1 if failed else 0)
