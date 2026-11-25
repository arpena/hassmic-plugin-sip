#!/usr/bin/env python3
"""The Makefile's sub-make of hassmic's PC build (`make test` where hassmic-host is missing), against a stand-in
hassmic tree whose Makefile has a HASSMIC of its own, as hassmic's has (its list of sources): `make test
HASSMIC=<tree>` must not hand that path down as hassmic's HASSMIC (a review of hassmic 61e3d8e, finding 2: make
hands a sub-make every variable set on its command line, and hassmic's link failed wherever its host build was not
made yet), nor any other variable of this command line (ID, KEY ...), while the jobserver still goes along.  Run by
`make test`."""
import atexit, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failed = False


def check(cond, what):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    failed |= not cond


top = tempfile.mkdtemp(prefix="makefile-test-")
atexit.register(shutil.rmtree, top, True)
tree = os.path.join(top, "hassmic")
os.makedirs(os.path.join(tree, "build"))
# (as hassmic's: HASSMIC is its own, set with :=, and the targets are files under build/)
# (each run of its make counted in build/runs, one line a run: the sub-make is asked once, both programs at once)
open(os.path.join(tree, "Makefile"), "w").write("""HASSMIC := own-sources.c
$(shell echo run >> build/runs)
build/hassmic-host:
\t@echo "HASSMIC=$(HASSMIC) ID=$(ID) KEY=$(KEY) jobs=$(findstring jobserver,$(MAKEFLAGS))" > $@.made
\t@touch $@
build/plugrun-host:
\t@touch $@
""")
runs = lambda: len(open(os.path.join(tree, "build", "runs")).read().split()) if os.path.exists(os.path.join(tree, "build", "runs")) else 0
r = subprocess.run(["make", "-C", HERE, "-j4", f"HASSMIC={tree}", "ID=someid", "KEY=somekey", "hassmic-pc"],
                   capture_output=True, text=True)
made = open(os.path.join(tree, "build", "hassmic-host.made")).read().strip() if os.path.exists(os.path.join(tree, "build", "hassmic-host.made")) else ""
check(r.returncode == 0 and made.startswith("HASSMIC=own-sources.c ID= KEY= "),
      f"hassmic's build made in its tree with its own HASSMIC, nothing of this command line ({made or (r.stdout + r.stderr).strip()[:200]})")
check(made.endswith("jobs=jobserver"), f"... the jobserver handed down all the same ({made})")
check(runs() == 1 and os.path.exists(os.path.join(tree, "build", "plugrun-host")),
      f"... once for both programs under -j4 (two targets of one rule were two runs of its recipe at once, a review of 865bacf): {runs()} run(s)")
os.unlink(os.path.join(tree, "build", "plugrun-host"))
r = subprocess.run(["make", "-C", HERE, "-j4", f"HASSMIC={tree}", "hassmic-pc"], capture_output=True, text=True)
check(r.returncode == 0 and runs() == 2 and os.path.exists(os.path.join(tree, "build", "plugrun-host")), f"one of the two missing: made, once ({runs()} runs in all)")
r = subprocess.run(["make", "-C", HERE, "-j4", f"HASSMIC={tree}", "hassmic-pc"], capture_output=True, text=True)
check(r.returncode == 0 and runs() == 2, f"both there: hassmic's make not run at all ({runs()} runs in all)")
sys.exit(1 if failed else 0)
