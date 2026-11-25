# A hassmic plugin: one static program for the Echos (32-bit ARM, musl) in a package (.hmp), in C on the SDK's client
# (src/, sdk/); and the same plugin in Rust (rust/), speaking the protocol from sdk/PROTOCOL.md, which is optional.
#
#   make                 build/<id>-<version>.hmp, signed with your author key (KEY): what a release publishes
#   make key             your author key: secrets/author.key (never in git, keep it) and keys/author.pub
#   make host            build/<id>-host and build/<id>-rs-host: both builds for the PC, for the test
#   make test            both in hassmic's PC build (HASSMIC: a hassmic tree with its .venv; its host build is made
#                        there when it is missing)
#   make arm-smoke       the ARM programs under qemu-arm against a stand-in broker (tools/armsmoke.py)
#   make rust            build/<id>-rs-<version>.hmp: the Rust build's package, the same author key
#   make rust-unit       the Rust client's own tests, against hassmic's test vectors (sdk/vectors/)
#   make tone            build/tone-<version>.hmp and build/tone-rs-<version>.hmp: the tone example (tone/, a music
#                        source that plays a sine; its Rust build is rust/src/bin/tone.rs), the same author key
#   make rustup          rustup into toolchain/ (git-ignored), its installer's published checksum checked
#   make index-check     the plugin index's checks on the package, its attestation, and the entry a pull request
#                        there would add (INDEX: a checkout of hassmic-plugins; RELEASED=<the release's asset>)
#   make release-test    the release's packing (tools/release.sh, as release.yml runs it) with a throwaway key
#   make sync | drift    take the SDK from HASSMIC | say where sdk/ differs from it (tools/sync.sh)
#   make rename ID=... NAME="..." [AUTHOR="..."] [SOURCE=https://...]    make the example yours (tools/rename.py)
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
LIBS :=
PKG := build/$(ID)-$(VERSION).hmp
RSPKG := build/$(ID)-rs-$(VERSION).hmp
# the tone example (tone/): its own manifest, id and version; the same SDK, key and toolchains
tone = $(shell python3 -c 'import json; print(json.load(open("tone/manifest.json"))["$(1)"])')
TONE := $(call tone,id)
TONE_VERSION := $(call tone,version)
TONE_PKG := build/$(TONE)-$(TONE_VERSION).hmp
TONE_RSPKG := build/$(TONE)-rs-$(TONE_VERSION).hmp
TONE_SRC := tone/main.c sdk/hmplugin.c

# Rust: rustup's toolchain in toolchain/ when `make rustup` put it there, else cargo and rustup from PATH;
# rust/rust-toolchain.toml names the version and the two targets, and rustup installs them on the first build
TC := $(abspath toolchain)
RUSTENV := $(if $(wildcard toolchain/cargo/bin/cargo),RUSTUP_HOME=$(TC)/rustup CARGO_HOME=$(TC)/cargo PATH=$(TC)/cargo/bin:$$PATH,)
CARGO := $(RUSTENV) cargo
RSSRC := $(wildcard rust/src/*.rs rust/src/bin/*.rs) rust/Cargo.toml rust/Cargo.lock rust/rust-toolchain.toml rust/.cargo/config.toml

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

host: build/$(ID)-host build/$(ID)-rs-host build/$(ID)-rs.json build/$(TONE)-host build/$(TONE)-rs-host build/$(TONE)-rs.json
build/$(ID)-host: $(SRC) $(HDR)
	@mkdir -p build
	cc $(CFLAGS) $(SRC) $(LIBS) -o $@

# ---- the tone example: a music source (audio.music), C on the SDK (tone/main.c) and Rust (rust/src/bin/tone.rs)

build/$(TONE): $(TONE_SRC) sdk/hassmic_plugin.h sdk/plugmsg.h
	@mkdir -p build
	$(if $(PLUGIN_CC),,$(error no musl toolchain: install zig, or set PLUGIN_CC (for example a musl cross gcc)))
	$(PLUGIN_CC) $(CFLAGS) -s $(TONE_SRC) -lm -o $@
	python3 sdk/plugcheck.py elf $@

build/$(TONE)-host: $(TONE_SRC) sdk/hassmic_plugin.h sdk/plugmsg.h
	@mkdir -p build
	cc $(CFLAGS) $(TONE_SRC) -lm -o $@

$(TONE_PKG): build/$(TONE) tone/manifest.json build/LICENSE sdk/plugcheck.py
	$(if $(wildcard $(KEY)),,$(error $(KEY): no author key (make key, or KEY=<file>)))
	python3 sdk/plugcheck.py pack tone/manifest.json build/$(TONE) $(KEY) $@ --license build/LICENSE

build/$(TONE)-rs.json: tone/manifest.json tools/rsmanifest.py
	@mkdir -p build
	python3 tools/rsmanifest.py tone/manifest.json $@

build/$(TONE)-rs: build/$(ID)-rs
	cp rust/target/armv7-unknown-linux-musleabihf/release/tone $@
	python3 sdk/plugcheck.py elf $@

build/$(TONE)-rs-host: build/$(ID)-rs-host
	cp rust/target/x86_64-unknown-linux-musl/release/tone $@

$(TONE_RSPKG): build/$(TONE)-rs build/$(TONE)-rs.json LICENSE sdk/plugcheck.py
	$(if $(wildcard $(KEY)),,$(error $(KEY): no author key (make key, or KEY=<file>)))
	python3 sdk/plugcheck.py pack build/$(TONE)-rs.json build/$(TONE)-rs $(KEY) $@ --license LICENSE

tone: $(TONE_PKG) $(TONE_RSPKG)

# ---- Rust (optional)

# the Rust build's manifest: manifest.json with the id <id>-rs and its own name (an Echo may have both builds)
build/$(ID)-rs.json: manifest.json tools/rsmanifest.py
	@mkdir -p build
	python3 tools/rsmanifest.py manifest.json $@

# (cargo builds both programs of the crate, the example's `plugin` and the tone's `tone`: build/tone-rs takes its)
build/$(ID)-rs: $(RSSRC)
	@mkdir -p build
	cd rust && $(CARGO) build --release --locked --target armv7-unknown-linux-musleabihf
	cp rust/target/armv7-unknown-linux-musleabihf/release/plugin $@
	python3 sdk/plugcheck.py elf $@

$(RSPKG): build/$(ID)-rs build/$(ID)-rs.json LICENSE sdk/plugcheck.py
	$(if $(wildcard $(KEY)),,$(error $(KEY): no author key (make key, or KEY=<file>)))
	python3 sdk/plugcheck.py pack build/$(ID)-rs.json build/$(ID)-rs $(KEY) $@ --license LICENSE

rust: $(RSPKG)

build/$(ID)-rs-host: $(RSSRC)
	@mkdir -p build
	cd rust && $(CARGO) build --release --locked --target x86_64-unknown-linux-musl
	cp rust/target/x86_64-unknown-linux-musl/release/plugin $@

rust-unit:
	cd rust && $(CARGO) test --locked --target x86_64-unknown-linux-musl

rustup:
	tools/rustup.sh

# ---- tests

QEMU_ARM ?= $(or $(shell command -v qemu-arm 2>/dev/null),$(wildcard $(HASSMIC)/toolchain/qemu-arm))
arm-smoke: build/$(ID) build/$(ID)-rs build/$(TONE) build/$(TONE)-rs
	$(if $(QEMU_ARM),,$(error no qemu-arm: install it (qemu-user), or set QEMU_ARM))
	python3 tools/armsmoke.py $(QEMU_ARM) build/$(ID) $(ID)
	python3 tools/armsmoke.py $(QEMU_ARM) build/$(ID)-rs $(ID)-rs
	python3 tools/armsmoke_tone.py $(QEMU_ARM) build/$(TONE) $(TONE)
	python3 tools/armsmoke_tone.py $(QEMU_ARM) build/$(TONE)-rs $(TONE)-rs

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

test: host hassmic-pc
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

# ---- your key, the SDK, the name

key:
	$(if $(wildcard secrets/author.key),$(error secrets/author.key is there already: an Echo takes updates of an installed plugin only from the key it was installed with (README.md "Your key")))
	@mkdir -p secrets keys
	python3 sdk/plugcheck.py keygen secrets/author.key keys/author.pub

sync:
	tools/sync.sh $(HASSMIC)
drift:
	tools/sync.sh --check $(HASSMIC)

# (ID given on the command line overrides the manifest's for this run: it is the new one here)
rename:
	$(if $(and $(filter command line,$(origin ID)),$(NAME)),,$(error make rename ID=<new id> NAME="<new name>" [AUTHOR="..."] [SOURCE=https://...]))
	python3 tools/rename.py --id "$(ID)" --name "$(NAME)" $(if $(AUTHOR),--author "$(AUTHOR)") $(if $(SOURCE),--source "$(SOURCE)")

clean:
	rm -rf build rust/target

.PHONY: all host tone rust rust-unit rustup arm-smoke hassmic-pc test index-check release-test key sync drift rename clean
