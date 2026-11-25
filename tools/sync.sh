#!/bin/sh
# The SDK, copied in from a hassmic tree (sync.sh [HASSMIC]) or compared with one (sync.sh --check [HASSMIC]).
# HASSMIC defaults to ../echo-dot-assist, hassmic's repository checked out beside this one.
#
#   sdk/   hassmic's plugin SDK as its tree has it: the C client (hassmic_plugin.h, hmplugin.c, plugmsg.h), the
#          written protocol (PROTOCOL.md) and the table it is made from (messages.def), the sandbox's list of system
#          calls (filter.def), the package tool (plugcheck.py, with scripts/otatool.py beside it for the signatures),
#          the protocol's messages as bytes (vectors/, which the Rust client checks itself against), and hassmic's
#          LICENSE, under which all of it is yours to use.
#   sdk/VERSION   which scope versions and which hassmic commit the copy is: written here, never edited by hand.
#
# A copy, so that this repository builds by itself and a build names exactly the SDK it was made with.  An older
# SDK stays good for good: hassmic's scopes only ever add (sdk/PROTOCOL.md "Versions"), and a plugin speaks what its
# manifest's "requires" names.  Take a newer one when you want a later version of a scope, or what it fixed
# (README.md "Keeping the SDK in sync").
set -e
cd "$(dirname "$0")/.."
check=; [ "$1" = --check ] && { check=1; shift; }
H=${1:-../echo-dot-assist}
[ -f "$H/sdk/hassmic_plugin.h" ] || { echo "sync: no hassmic tree at $H (give its path: make sync HASSMIC=<path>)" >&2; exit 1; }
bad=0
each() {        # <file in hassmic's tree> <file here>
    if [ -n "$check" ]; then
        cmp -s "$H/$1" "$2" || { echo "differs: $2 (hassmic: $1)"; bad=1; }
    else
        cp "$H/$1" "$2"
    fi
}
mkdir -p sdk
for f in hassmic_plugin.h hmplugin.c plugmsg.h plugcheck.py PROTOCOL.md messages.def filter.def; do each sdk/$f sdk/$f; done
each scripts/otatool.py sdk/otatool.py
each LICENSE sdk/LICENSE
if [ -n "$check" ]; then
    diff -r -q -x __pycache__ "$H/sdk/vectors" sdk/vectors >/dev/null 2>&1 || { echo "differs: sdk/vectors/ (hassmic: sdk/vectors/)"; bad=1; }
    [ $bad = 0 ] && echo "sync: sdk/ is as hassmic's tree at $H"
    exit $bad
fi
rm -rf sdk/vectors && cp -r "$H/sdk/vectors" sdk/vectors
# the scopes and the version of each this SDK has, as its plugmsg.h makes them: "core 1 storage 1 ..."
scopes=$(python3 - <<'PY'
import re
h = open("sdk/plugmsg.h").read()
names = re.findall(r'case PM_SCOPE_\w+: return "([a-z0-9._]+)";', h)
vers = dict(re.findall(r"PM_SCOPE_VERSION_(\w+) = (\d+)", h))
print(" ".join(f"{n} {vers[n.upper().replace('.', '_')]}" for n in names))
PY
)
[ -n "$scopes" ] || { echo "sync: no scopes in sdk/plugmsg.h" >&2; exit 1; }
commit=$(git -C "$H" rev-parse HEAD 2>/dev/null || echo unknown)
[ -z "$(git -C "$H" status --porcelain -- sdk scripts/otatool.py LICENSE 2>/dev/null)" ] || commit="$commit with changes not committed there"
printf 'scopes %s\nhassmic %s\n' "$scopes" "$commit" > sdk/VERSION
cat sdk/VERSION
# what a manifest here requires that this SDK's client does not speak (a later version than its own, a scope it has not)
for m in manifest.json; do
    [ -f "$m" ] || continue
    SCOPES="$scopes" python3 - "$m" <<'PY' >&2 || true
import json, os, sys
have = dict(zip(*[iter(os.environ["SCOPES"].split())] * 2))
r = json.load(open(sys.argv[1])).get("requires") or {}
for k, v in r.items():
    if k not in have or int(have[k]) < v:
        print(f"sync: {sys.argv[1]} requires {k} {v}, which this SDK's client does not speak (README.md \"Versions\")")
PY
done
