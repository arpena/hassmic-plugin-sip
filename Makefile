# The SIP phone plugin for hassmic: one static program for the Echos (32-bit ARM, musl) in a package (.hmp), in C on
# the SDK's client (src/, sdk/).
#
#   make                 build/<id>-<version>.hmp, signed with your author key (KEY): what a release publishes
#   make key             your author key: secrets/author.key (never in git, keep it) and keys/author.pub
#   make host            build/<id>-host: the build for the PC, for the test
#   make unit            the unit tests (tests/unit.c): G.711, the 8/16 kHz filters, MD5
#   make test            the unit tests, then the plugin in hassmic's PC build against a stand-in phone system (test.py;
#                        HASSMIC: a hassmic tree with its .venv; its host build is made there when it is missing)
#   make arm-smoke       the ARM program under qemu-arm against a stand-in broker (tools/armsmoke.py)
#   make index-check     the plugin index's checks on the package, its attestation, and the entry a pull request
#                        there would add (INDEX: a checkout of hassmic-plugins; RELEASED=<the release's asset>)
#   make release-test    the release's packing (tools/release.sh, as release.yml runs it) with a throwaway key
#   make sync | drift    take the SDK from HASSMIC | say where sdk/ differs from it (tools/sync.sh)
#   make clean
#
# The plugin's id, name and version are manifest.json's, and everything here reads them from there.
#
# A plugin is never built with the Android NDK: a static bionic program does not start in the sandbox.  PLUGIN_CC
# names a compiler that links musl statically: zig when it is installed (one download, any OS) or in hassmic's
# toolchain/, else set PLUGIN_CC (a musl cross gcc, e.g. arm-linux-musleabihf-gcc).
HASSMIC ?= ../echo-dot-assist
INDEX ?= ../hassmic-plugins
manifest = $(shell python3 -c 'import json; print(json.load(open("manifest.json"))["$(1)"])')
ID := $(call manifest,id)
VERSION := $(call manifest,version)
ZIG := $(or $(shell command -v zig 2>/dev/null),$(wildcard $(HASSMIC)/toolchain/zig/zig))
PLUGIN_CC ?= $(if $(ZIG),$(ZIG) cc -target arm-linux-musleabihf -mcpu=cortex_a7,)
KEY ?= secrets/author.key

SRC := $(wildcard src/*.c) sdk/hmplugin.c
HDR := $(wildcard src/*.h) sdk/hassmic_plugin.h sdk/plugmsg.h
# -static: no interpreter (hassmic refuses PT_INTERP).  -z noexecstack: without PT_GNU_STACK the kernel makes every
# readable mapping executable, and hassmic refuses the file
CFLAGS := -O2 -Wall -Wextra -static -Wl,-z,noexecstack -Isdk -Isrc
LIBS := -lm
PKG := build/$(ID)-$(VERSION).hmp

all: $(PKG)

build/$(ID): $(SRC) $(HDR)
	@mkdir -p build
	$(if $(PLUGIN_CC),,$(error no musl toolchain: install zig, or set PLUGIN_CC (for example a musl cross gcc)))
	$(PLUGIN_CC) $(CFLAGS) -s $(SRC) $(LIBS) -o $@
	python3 sdk/plugcheck.py elf $@

# What goes into the package as LICENSE: yours, then the SDK's (hmplugin.c is compiled into the program, and its
# MIT licence asks for its notice to go with it)
build/LICENSE: LICENSE sdk/LICENSE
	@mkdir -p build
	{ cat LICENSE; printf '\n\n---- hassmic'"'"'s plugin SDK (sdk/hmplugin.c), compiled into this program:\n\n'; cat sdk/LICENSE; } > $@

# Your signature only: a package that installs from a file as a sideload.  What lists it is the plugin index,
# which adds its own signature (listing.sig) to the release asset it fetches, once its attestation says this
# repository's release workflow built it (release.yml; README.md "Getting listed")
$(PKG): build/$(ID) manifest.json build/LICENSE sdk/plugcheck.py
	$(if $(wildcard $(KEY)),,$(error $(KEY): no author key (make key, or KEY=<file>)))
	python3 sdk/plugcheck.py pack manifest.json build/$(ID) $(KEY) $@ --license build/LICENSE

host: build/$(ID)-host
build/$(ID)-host: $(SRC) $(HDR)
	@mkdir -p build
	cc $(CFLAGS) $(SRC) $(LIBS) -o $@

# ---- tests

QEMU_ARM ?= $(or $(shell command -v qemu-arm 2>/dev/null),$(wildcard $(HASSMIC)/toolchain/qemu-arm))
arm-smoke: build/$(ID)
	$(if $(QEMU_ARM),,$(error no qemu-arm: install it (qemu-user), or set QEMU_ARM))
	python3 tools/armsmoke.py $(QEMU_ARM) build/$(ID) $(ID)

# the codec's and the digest's pieces against known values, on the PC (the plugin's sources but main.c)
build/unit: tests/unit.c src/codec.c src/codec.h
	@mkdir -p build
	cc -O2 -Wall -Wextra -Isrc tests/unit.c src/codec.c -lm -o $@
unit: build/unit
	build/unit

# hassmic's PC build is made in its own tree when it is missing (it needs libopus and libcurl there, and `uv sync`
# for its .venv: its DEVELOPMENT.md).  Without what was given on this command line: make hands a sub-make every
# variable set there, in its flags (MAKEOVERRIDES) and in the environment, and `make test HASSMIC=<tree>` set
# hassmic's own HASSMIC (its list of sources) to a path, so its link failed wherever hassmic-host was not built yet
# (a review of hassmic 61e3d8e, finding 2).  hassmic's build as `make -C <tree>` makes it; the jobserver and the
# other flags still go along
# One target, one recipe, and the sub-make only where either program is missing: two targets of one rule are two rules
# of one recipe, and under make -j both ran it, two hassmic builds in one tree at once (a review of 865bacf)
CMDLINE_VARS := $(foreach v,$(.VARIABLES),$(if $(filter command line,$(origin $(v))),$(v)))
HASSMIC_PC := $(HASSMIC)/build/hassmic-host $(HASSMIC)/build/plugrun-host
hassmic-pc: MAKEOVERRIDES :=
hassmic-pc:
	$(if $(filter-out $(wildcard $(HASSMIC_PC)),$(HASSMIC_PC)),env $(addprefix -u ,$(CMDLINE_VARS)) $(MAKE) -C $(HASSMIC) build/hassmic-host build/plugrun-host,@:)

test: unit host hassmic-pc
	python3 tools/makefile_test.py
	python3 tools/indexcheck_test.py
	HASSMIC=$(abspath $(HASSMIC)) $(HASSMIC)/.venv/bin/python test.py

# RELEASED: the release's own asset, downloaded (gh release download v<version> -p '<id>-<version>.hmp'): the entry
# names its bytes, and its attestation is checked where gh is there.  Without it: this tree's build, a preview
index-check: $(if $(RELEASED),,$(PKG))
	python3 tools/indexcheck.py $(INDEX) $(or $(RELEASED),$(PKG)) $(if $(RELEASED),--released)

# tools/release.sh as the release workflow runs it, with a throwaway key, and its refusals (tools/release_test.py);
# the workflows read as data (tools/workflow_test.py) and that test against broken copies of them, each of its checks switched off in turn
# (one rule each; tools/workflow_breaks.py, some 20 to 40 s)
release-test:
	python3 tools/workflow_test.py
	python3 tools/workflow_breaks.py
	python3 tools/release_test.py

# ---- your key, the SDK

key:
	$(if $(wildcard secrets/author.key),$(error secrets/author.key is there already: an Echo takes updates of an installed plugin only from the key it was installed with (README.md "Your key")))
	@mkdir -p secrets keys
	python3 sdk/plugcheck.py keygen secrets/author.key keys/author.pub

sync:
	tools/sync.sh $(HASSMIC)
drift:
	tools/sync.sh --check $(HASSMIC)

clean:
	rm -rf build

.PHONY: all host unit arm-smoke hassmic-pc test index-check release-test key sync drift clean
