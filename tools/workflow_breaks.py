#!/usr/bin/env python3
"""tools/workflow_test.py against broken copies of what it reads: each break below has to fail it with the FAIL line
it names, and the tree as it is has to pass (a test of the test: a check taken out of workflow_test.py, or one that
stopped holding, fails here; the reviews of 6bca341 and 032e15c broke the workflows so by hand).  Each break is one
change of a file the test reads (release.yml, build.yml, .github/zig.txt, .github/rust.txt, tools/rustpin.sh,
rust/rust-toolchain.toml, the test's own copy of the index's reader), made in a copy of those files in a folder of its own, where the copied test
runs; INDEX names a stand-in index (its tools/workflow.py the test as it is here), so that the reader's check runs
whether or not hassmic-plugins is beside this tree.

A break names its FAIL line because any FAIL passed a break whose own check had stopped holding, as long as another
check happened to fail with it (a review of 810b9e9: made always true one at a time, 19 of the test's 30 checks
passed every break).  The words a break names are words of one check(...) of the test, as written there, and of
no other.  And then, as the test of this test, every check(...) of workflow_test.py is made always true in turn
(`check(True or ...`): some break has to pass then (the ones that name that check are run first, all of them where
those do not).  So every check needs a break that names it.  And every rule of the test is a check of its own, never
one operand of another check's `and`: what this makes always true is a whole check, so a rule inside one was never
asked (a review of the template's 1beecab and microWakeWord's 3a835c0: made always true one operand at a time, 47 of
the template's 68 operands and 46 of microWakeWord's 64 passed every break).  So each rule has its break, which
names its words.  A break of a file or a text this tree does not have (the template's Rust) is
skipped, said, and so is a check only such breaks name.  The template's own (hassmic-plugin-template) and
microWakeWord's are the same file.  Run by `make release-test`: breaks and mutations, in parallel, some 20 to 40 s."""
import ast, os, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEST = os.path.join("tools", "workflow_test.py")
REL, BLD = os.path.join(".github", "workflows", "release.yml"), os.path.join(".github", "workflows", "build.yml")
# what workflow_test.py reads of the tree
FILES = [TEST, os.path.join("tools", "rustpin.sh"), "manifest.json", os.path.join("rust", "rust-toolchain.toml"),
         os.path.join(".github", "zig.txt"), os.path.join(".github", "rust.txt"), REL, BLD]
failed = False


def check(cond, what):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    failed |= not cond


def text(path):
    p = os.path.join(ROOT, path)
    return open(p, encoding="utf-8", newline="").read() if os.path.isfile(p) else None


test_text = text(TEST)
stand_in = tempfile.mkdtemp(prefix="workflow-breaks-index-")
os.makedirs(os.path.join(stand_in, "tools"))
with open(os.path.join(stand_in, "tools", "workflow.py"), "w", encoding="utf-8", newline="") as f:
    f.write(test_text)          # (the reader's four parts as this tree has them: the index this copy is held to)


def run(edits, test=None):
    """workflow_test.py's status and FAIL lines, run in a copy of the files it reads with EDITS ({path: (old, new)})
    made, TEST in place of its own text"""
    with tempfile.TemporaryDirectory(prefix="workflow-breaks-") as t:
        for f in FILES:
            body = test if f == TEST and test is not None else text(f)
            if body is None:
                continue
            if f in edits:
                body = body.replace(edits[f][0], edits[f][1], 1)
            os.makedirs(os.path.dirname(os.path.join(t, f)), exist_ok=True)
            with open(os.path.join(t, f), "w", encoding="utf-8", newline="") as out:
                out.write(body)
            shutil.copymode(os.path.join(ROOT, f), os.path.join(t, f))
        p = subprocess.run([sys.executable, os.path.join(t, TEST)], env=dict(os.environ, INDEX=stand_in), capture_output=True, text=True)
        return p.returncode, [x for x in p.stdout.splitlines() if x.startswith("FAIL")]


def R(old, new):
    return {REL: (old, new)}


def B(old, new):
    return {BLD: (old, new)}


def F(path, old, new):
    return {path: (old, new)}


CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
IMPOSTOR = "c7d749a2d57b4b375d1ebcd17cfbfb60c676f18e"      # served by GitHub as an actions/checkout commit, on none of its refs
PKG_SHA = '          [[ $sha =~ ^[0-9a-f]{64}$ ]] || { echo "::error::the build job\'s artifact holds no SHA-256 of its program ($id.sha256)"; exit 1; }\n'
RUST_STEP = '          rust=$(tools/rustpin.sh "$RUNNER_TEMP/rust")\n          PATH=$rust:$PATH make'
# name: (the words of the FAIL line it has to give, as the check(...) that gives it writes them; the change)
breaks = {
    # release.yml as a whole
    "release: env for all jobs": ("nothing for all jobs", R("permissions:\n  contents: read\n\njobs:", "permissions:\n  contents: read\n\nenv:\n  X: y\n\njobs:")),
    "release: a second tag pattern": ("release.yml runs for a tag v* and nothing else", R("    tags: ['v*']\n", "    tags: ['v*', 'x*']\n")),
    "release: the checks called with an input": ("the checks are build.yml, called",
                                                 R("    uses: ./.github/workflows/build.yml\n", "    uses: ./.github/workflows/build.yml\n    with:\n      x: y\n")),
    "release: the workflow's permissions write": ("the workflow's permissions: read", R("permissions:\n  contents: read\n\njobs:", "permissions:\n  contents: write\n\njobs:")),
    "release: run by hand too": ("release.yml is never run by hand", R("    tags: ['v*']\n", "    tags: ['v*']\n  workflow_dispatch:\n")),
    # what the release job may be (reviews of 6bca341, 032e15c)
    "release: the job if: always()": ("the release job is the one this test allows", R("    needs: checks\n", "    needs: checks\n    if: always()\n")),
    "release: a step between Package and Attest": ("the release job is the one this test allows",
                                                   R("      - name: Attest\n", "      - name: Swap\n        run: cp checked/x dist/\n      - name: Attest\n")),
    "release: the job named": ("the release job is the one this test allows", R("  release:\n    needs: checks", "  release:\n    name: x\n    needs: checks")),
    "release: checkout by a commit of a fork": ("the release job is the one this test allows", R(CHECKOUT, "actions/checkout@" + IMPOSTOR)),
    # (ACTIONS over release.yml: the allow-list above holds the same pins, so this one names the other check)
    "release: the attestation by any other commit": ("every action by the one commit ACTIONS names",
                                                     R("attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8", "attest-build-provenance@" + "e" * 40)),
    # the Package step as GitHub runs it (bash -e): it gives tools/release.sh the SHA-256, and fails without one
    "release: the Package step names no output": ("the package's path is the step's output",
                                                  R('          echo "path=$pkg" >> "$GITHUB_OUTPUT"\n', '          echo "path=$pkg"\n')),
    "release: the Package step without its SHA-256 check": ("tools/release.sh never run", R(PKG_SHA, "")),
    # every action by its one commit (1: impostor commits; a review of 810b9e9: `.` and `..` in a path)
    "build: checkout by a commit of a fork": ("every action by the one commit ACTIONS names", B(CHECKOUT, "actions/checkout@" + IMPOSTOR)),
    "build: checkout by a path with ..": ("every action by the one commit ACTIONS names", B(CHECKOUT, "actions/checkout/../checkout@" + CHECKOUT.split("@")[1])),
    "build: upload-artifact by a tag": ("every action by the one commit ACTIONS names", B("upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9", "upload-artifact@v7")),
    "build: setup-uv by another commit": ("every action by the one commit ACTIONS names", B("setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7", "setup-uv@" + "1" * 40)),
    # what the reader refuses (2): the test's own words for it ("does not read") are load()'s, not a check's
    "release: a line separator (U+2028) in the Package step": ("does not read", R("          mkdir -p secrets\n", "          mkdir -p secrets curl x | sh\n")),
    "release: a paragraph separator (U+2029)": ("does not read", R("          mkdir -p secrets\n", "          mkdir -p secrets x\n")),
    "release: NEL": ("does not read", R("          mkdir -p secrets\n", "          mkdir -p secrets\x85x\n")),
    "release: a form feed": ("does not read", R("          mkdir -p secrets\n", "          mkdir -p secrets\x0cx\n")),
    "release: a carriage return alone": ("does not read", R("          mkdir -p secrets\n", "          mkdir -p secrets\rx\n")),
    "release: a folded block": ("does not read", R("        run: |\n          [ -n", "        run: >\n          [ -n")),
    # (these two pass every other check: only the reader's refusal of the character, as written, holds them)
    "build: a line separator (U+2028) in a step's name": ("does not read", B("      - name: Build, twice\n", "      - name: Build, twice\n")),
    "build: a carriage return alone inside a run block": ("does not read", B("          cp -r . ../again", "          true\r          cp -r . ../again")),
    "release: an anchor": ("does not read", R("name: release", "name: &n release")),
    "release: a tab": ("does not read", R("          persist-credentials: false", "\t  persist-credentials: false")),
    # the reader the index's, the same text (a review of 810b9e9)
    "the reader: a line of parse() not the index's": ("is the index's reader, the same text",
                                                       F(TEST, "    pos = [0]\n", "    pos = [0]\n    # (a line of this copy's own)\n")),
    # CI goes on past no failure, and its hassmic step runs as written (3)
    "build: Which hassmic continue-on-error": ("nothing in CI goes on past a failure", B("        id: hassmic\n", "        id: hassmic\n        continue-on-error: true\n")),
    "build: Which hassmic if: false": ("is a name, its id and its run", B("        id: hassmic\n", "        id: hassmic\n        if: false\n")),
    "build: Which hassmic shell sh": ("is a name, its id and its run", B("        id: hassmic\n", "        id: hassmic\n        shell: sh\n")),
    "build: Which hassmic names a short commit": ("the step names it, commit=", B('echo "commit=$c" >> "$GITHUB_OUTPUT"', 'echo "commit=${c:0:12}" >> "$GITHUB_OUTPUT"')),
    "build: the test job continue-on-error": ("nothing in CI goes on past a failure",
                                              B("  test:\n    runs-on: ubuntu-24.04\n", "  test:\n    runs-on: ubuntu-24.04\n    continue-on-error: true\n")),
    "build: Build, twice continue-on-error": ("nothing in CI goes on past a failure", B("      - name: Build, twice\n", "      - name: Build, twice\n        continue-on-error: true\n")),
    "build: hassmic checked out at its default branch": ("another repository (hassmic) checked out only at the commit",
                                                         B("          ref: ${{ steps.hassmic.outputs.commit }}\n", "          ref: main\n")),
    # the steps run as GitHub runs a step without `shell:`, bash -e and no pipefail (8): a check whose failure a pipe
    # hides passes on GitHub, and has to fail the test too (under pipefail the test would take it)
    "build: Which hassmic's check piped": ("the step fails, a status not 0", B("[[ $c =~ ^[0-9a-f]{40}$ ]] || { echo", "[[ $c =~ ^[0-9a-f]{40}$ ]] | cat; : || { echo")),
    "build: defaults with another shell": ("no `defaults` in build.yml", B("permissions:\n  contents: read\n", "permissions:\n  contents: read\n\ndefaults:\n  run:\n    shell: sh\n")),
    # no secret, no environment in CI (4)
    "build: environment release and toJSON(secrets)": ("build.yml never says `secrets`",
                                                       B("  test:\n    runs-on: ubuntu-24.04\n", "  test:\n    runs-on: ubuntu-24.04\n    environment: release\n    env:\n      ALL: ${{ toJSON(secrets) }}\n")),
    "build: secrets['PLUGIN_SIGNING_KEY']": ("build.yml never says `secrets`",
                                             B("  test:\n    runs-on: ubuntu-24.04\n", "  test:\n    runs-on: ubuntu-24.04\n    env:\n      K: ${{ secrets['PLUGIN_SIGNING_KEY'] }}\n")),
    "build: environment release alone": ("no job of build.yml names an environment", B("  build:\n    runs-on: ubuntu-24.04\n", "  build:\n    runs-on: ubuntu-24.04\n    environment: release\n")),
    # the rest of CI's rules
    "build: a step that names checked/": ("build.yml never names checked/", B("      - name: Test\n", "      - name: Test what was checked\n")),
    "build: the build job's compiler another text": ("the build job's compiler is the release job's",
                                                     B('-mcpu=cortex_a7" >> "$GITHUB_ENV"\n', '-mcpu=cortex_a7" >> "$GITHUB_ENV"\n          echo more\n')),
    "zig.txt: zig without its hash": ("zig only as .github/zig.txt names it", F(os.path.join(".github", "zig.txt"), " --hash=sha256:", " #")),
    "build: another step names PLUGIN_CC": ("no other step names PLUGIN_CC", B("      - name: Test\n", "      - run: echo $PLUGIN_CC\n      - name: Test\n")),
    "build: CI not called by release.yml": ("build.yml is CI: called by release.yml", B("  workflow_call:\n", "")),
    "build: no make release-test": ("CI runs the release's packing test", B("        run: make release-test\n", "        run: make test\n")),
    "build: a job that may sign": ("no job of build.yml may sign",
                                   B("  test:\n    runs-on: ubuntu-24.04\n", "  test:\n    runs-on: ubuntu-24.04\n    permissions:\n      id-token: write\n")),
    "build: a cache": ("no cache in the build job", B("      - name: Compiler\n", "      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7\n      - name: Compiler\n")),
    "build: vars as an input": ("no `vars` as an input", B("      - name: Test\n", "      - name: Test\n        env:\n          X: ${{ vars.X }}\n")),
    "build: the test job uploads a program too": ("one artifact uploaded in build.yml",
                                                  B("      - name: Test\n", "      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9\n        with:\n          name: program\n          path: x\n      - name: Test\n")),
    # Rust (the template's: its toolchain pinned file by file, never rustup)
    "build: the Rust step by the runner's rustup": ("no step runs rustup", B(RUST_STEP, "          rustup show\n          make")),
    "build: the Rust step without the pinned toolchain": ("every step that builds Rust has the pinned toolchain first", B(RUST_STEP, "          make")),
    "rust.txt: a file of another version": ("Rust as .github/rust.txt pins it", F(os.path.join(".github", "rust.txt"), "rustc-1.100.0", "rustc-1.99.0")),
    "rustpin.sh: the download not checked": ("unpacks and installs nothing", F(os.path.join("tools", "rustpin.sh"), "sha256sum -c --quiet - >&2 ||", "true ||")),
    # one rule a check (a review of the template's 1beecab and microWakeWord's 3a835c0: a rule that was one operand of
    # another check's `and` passed every break when made always true): a break for each of those rules
    "release: a schedule too": ("release.yml has one trigger, a push", R("    tags: ['v*']\n", "    tags: ['v*']\n  schedule:\n    - cron: '0 3 * * *'\n")),
    "release: called by another workflow": ("release.yml is never called by another workflow", R("    tags: ['v*']\n", "    tags: ['v*']\n  workflow_call:\n")),
    "release: run for pull requests": ("release.yml never runs for a pull request", R("    tags: ['v*']\n", "    tags: ['v*']\n  pull_request:\n")),
    "release: a third job": ("release.yml's jobs are the checks and the release job",
                             R("  release:\n    needs: checks\n", "  other:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: true\n\n  release:\n    needs: checks\n")),
    "release: a second secret": ("release.yml names one secret, once", R("          GH_TOKEN: ${{ github.token }}\n", "          GH_TOKEN: ${{ github.token }}\n          X: ${{ secrets.X }}\n")),
    "release: the checks job names the environment": ("in release.yml the release job alone names an environment",
                                                      R("  checks:\n    uses: ./.github/workflows/build.yml\n", "  checks:\n    environment: release\n    uses: ./.github/workflows/build.yml\n")),
    "release: checked/ read by another step": ("checked/ is named twice in the release job",
                                               R('          sha=$(cat "checked/$id.sha256")\n', '          sha=$(cat "checked/$id.sha256")\n          ls checked\n')),
    "release: the Package step fails at its end": ("in checked/: ends well, status 0",
                                                   R('          echo "path=$pkg" >> "$GITHUB_OUTPUT"\n', '          echo "path=$pkg" >> "$GITHUB_OUTPUT"\n          false\n')),
    "release: tools/release.sh given a sixth argument": ("tools/release.sh given it as its fifth argument",
                                                         R('secrets/author.key dist "$sha")', 'secrets/author.key dist "$sha" more)')),
    "release: the key left on disk": ("the key gone after", R("          trap 'rm -f secrets/author.key' EXIT\n", "")),
    "release: the Package step's SHA-256 check ends well": ("in checked/: fails, a status not 0", R(PKG_SHA, PKG_SHA.replace("exit 1;", "exit 0;"))),
    "release: a path named before the SHA-256 check": ("no package's path as its output", R(PKG_SHA, '          echo "path=early" >> "$GITHUB_OUTPUT"\n' + PKG_SHA)),
    "build: a second Compiler step": ("the build job has one Compiler step",
                                      B("      - name: The release's packing\n", "      - name: Compiler\n        run: true\n      - name: The release's packing\n")),
    "build: the Compiler step with an if:": ("the build job's Compiler step is its name and its run",
                                             B("      - name: Compiler\n        run: |\n", "      - name: Compiler\n        if: true\n        run: |\n")),
    "zig.txt: a second package": (".github/zig.txt pins one package",
                                  F(os.path.join(".github", "zig.txt"), "ziglang==", "ziglang==0.13.0 --hash=sha256:" + "0" * 64 + "\nziglang==")),
    "build: another pip install": ("pip installs in the build job and the release job alone",
                                   B("      - name: The release's packing\n", "      - run: pip install x\n      - name: The release's packing\n")),
    "build: zig.txt's install with more after it": ("every pip install is --require-hashes -r .github/zig.txt",
                                                    B("install --require-hashes -r .github/zig.txt\n", "install --require-hashes -r .github/zig.txt --pre\n")),
    "build: the zig Compiler step with a line of its own": ("every step that names PLUGIN_CC is a zig Compiler step",
                                                            B('-mcpu=cortex_a7" >> "$GITHUB_ENV"\n', '-mcpu=cortex_a7" >> "$GITHUB_ENV"\n          echo more\n')),
    "rust.txt: a file from elsewhere": ("every line of .github/rust.txt a file of static.rust-lang.org",
                                        F(os.path.join(".github", "rust.txt"), "https://static.rust-lang.org/dist/2026-11-12/rust-std-1.100.0-x86_64-unknown-linux-musl",
                                          "https://example.org/dist/2026-11-12/rust-std-1.100.0-x86_64-unknown-linux-musl")),
    "rust-toolchain.toml: the channel stable": ("rust/rust-toolchain.toml names a version of Rust as its channel",
                                                F(os.path.join("rust", "rust-toolchain.toml"), 'channel = "1.100.0"', 'channel = "stable"')),
    "rust.txt: no cargo": ("rust.txt pins rustc, cargo and rust-std, each", F(os.path.join(".github", "rust.txt"), "/cargo-1.100.0-", "/rustc-1.100.0-")),
    "rustpin.sh: ends well at once": ("a download that is not the file named: fails", F(os.path.join("tools", "rustpin.sh"), "set -eu\n", "set -eu\nexit 0\n")),
    "rustpin.sh: another error's words": ("says it is not the file .github/rust.txt names",
                                          F(os.path.join("tools", "rustpin.sh"), "is not the file .github/rust.txt names (its SHA-256", "is not the file named (its SHA-256")),
    "rustpin.sh: a line on its output": ("a download that is not the file named: names no folder", F(os.path.join("tools", "rustpin.sh"), "set -eu\n", "set -eu\necho starting\n")),
    "build: the build job named otherwise": ("the artifact uploaded by the build job", B("jobs:\n  build:\n", "jobs:\n  make:\n")),
    "build: the artifact named otherwise": ("the artifact is named program", B("          name: program\n", "          name: other\n")),
    "release: an artifact uploaded": ("release.yml uploads no artifact",
                                      R("      - name: Attest and publish (dry run)\n", "      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9\n"
                                        "        with:\n          name: x\n          path: dist\n      - name: Attest and publish (dry run)\n")),
    "build: a uses: line inside a run block": ("every `uses:` line of the workflows read as a uses",
                                               B("          cp -r . ../again && make -C ../again clean ", "          uses: x\n          cp -r . ../again && make -C ../again clean ")),
    "build: run for a tag's push": ("build.yml runs for no tag's push", B("    branches: [main]\n", "    branches: [main]\n    tags: ['v*']\n")),
    "build: a job named release": ("build.yml has no release job",
                                   B("  test:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 30\n", "  release:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 30\n")),
    "build: an attestation": ("nothing in build.yml attests",
                              B("      - name: Test\n", "      - uses: actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8\n        with:\n          subject-path: x\n      - name: Test\n")),
    "build: gh release download": ("read back by gh release download", B("      - name: Test\n", "      - run: gh release download v1\n      - name: Test\n")),
    "build: a release asset by its address": ("read back by its address (releases/download)",
                                              B("      - name: Test\n", "      - run: curl -sSfLO https://github.com/a/b/releases/download/v1/x.hmp\n      - name: Test\n")),
    "build: hassmic's checkout without its repository": ("hassmic checked out by a step of its own", B("          repository: ${{ env.HASSMIC_REPO }}\n", "")),
    "build: Which hassmic another id": ("one step has the id hassmic", B("        id: hassmic\n", "        id: which\n")),
    "release: defaults": ("no `defaults` in release.yml either",
                          R("permissions:\n  contents: read\n\njobs:", "permissions:\n  contents: read\n\ndefaults:\n  run:\n    shell: bash\n\njobs:")),
    "build: Which hassmic fails at its end": ("the step ends well, status 0",
                                              B('          echo "commit=$c" >> "$GITHUB_OUTPUT"\n', '          echo "commit=$c" >> "$GITHUB_OUTPUT"\n          false\n')),
    "build: Which hassmic names a commit before its check": ("no commit named",
                                                            B("          [[ $c =~ ^[0-9a-f]{40}$ ]] ||", '          echo "commit=$c" >> "$GITHUB_OUTPUT"\n          [[ $c =~ ^[0-9a-f]{40}$ ]] ||')),
}

t0 = time.monotonic()
code, why = run({})
check(code == 0, f"the tree as it is: workflow_test.py passes ({why[:1]})")
# every check(...) of this tree's test, as written
calls = sorted((n.lineno, n.col_offset, ast.get_source_segment(test_text, n)) for n in ast.walk(ast.parse(test_text))
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "check")
here = {}
for name, (said, edits) in breaks.items():
    if any(text(f) is None or old not in text(f) for f, (old, _) in edits.items()):
        print(f"skip {name}: this tree has no such text")
        continue
    here[name] = (said, edits)
for name, (said, _) in here.items():
    check(said == "does not read" or any(said in seg for _, _, seg in calls), f"{name}: names the words of a check of workflow_test.py ({said})")
pool = ThreadPoolExecutor(os.cpu_count() or 4)
got = dict(zip(here, pool.map(lambda x: run(x[1]), here.values())))
for name, (said, _) in here.items():
    code, fails = got[name]
    hit = [x for x in fails if said in x]
    check(code != 0 and hit, f"{name}: workflow_test.py fails, saying \"{said}\" ({(hit or fails or ['it passes'])[0][:150]})")


# the test of the test: each check made always true, some break passes then
def mutant(line, col):
    lines = test_text.split("\n")
    lines[line - 1] = lines[line - 1][:col] + "check(True or " + lines[line - 1][col + len("check("):]
    return "\n".join(lines)


def caught(line, col, seg):
    """the first break that passes (no FAIL with its words) with that check made always true, None where none does"""
    m = mutant(line, col)
    named = [n for n, (said, _) in here.items() if said in seg]
    for group in (named, [n for n in here if n not in named]):
        for n, (code, fails) in zip(group, pool.map(lambda n: run(here[n][1], m), group)):
            if code == 0 or not any(here[n][0] in x for x in fails):
                return n
    return None


for line, col, seg in calls:
    words = " ".join(seg.split())[:90]
    if not any(said in seg for said, _ in breaks.values()):
        check(False, f"workflow_test.py line {line}: no break names this check ({words})")
    elif not any(said in seg for said, _ in here.values()):
        print(f"skip workflow_test.py line {line}: only breaks this tree has no text for name it ({words})")
    else:
        by = caught(line, col, seg)
        check(by is not None, f"workflow_test.py line {line} made always true: a break passes then ({by}; {words})")
pool.shutdown()
shutil.rmtree(stand_in)
print(f"workflow breaks: {'FAILED' if failed else 'all passed'} ({time.monotonic() - t0:.0f} s)")
sys.exit(1 if failed else 0)
