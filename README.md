# Phone (SIP): a hassmic plugin

The Echo as one extension of your home's phone system (Asterisk, FreePBX, a Fritz!Box). A doorbell intercom, a
desk phone or a softphone can call it, and Home Assistant can have it call one of them. A call that comes in rings
on the Echo until someone answers it there: it is never answered by itself, and Home Assistant cannot answer it.

It is a [hassmic](https://github.com/Gamer92000/echo-dot-assist) plugin: a small SIP user agent (RFC 3261 over UDP)
with RTP and G.711, for one account on one server in your own network. The call itself is hassmic's: the ring, the
light, the action button, "<wake word>, stop", the call microphone with its echo handling, one call at a time
beside Drop In.

Not in it: TLS, SRTP, NAT traversal, DTMF, transfer, hold, video, codecs other than G.711 (A-law and mu-law). The
audio is unencrypted on your network, as it is with most phones on a home PBX.

## Setting it up

1. On the phone system, make an extension for the Echo (a user name and a password).
2. On the Echo's settings page, install the plugin (from the plugin index, or "Install from file" with a release's
   `sip-<version>.hmp`) and press the action button when asked.
3. Fill in its settings:

| Setting | |
|---|---|
| Phone system | the server's name or address. Only this address can ring the Echo |
| Port | its SIP port, usually 5060 |
| Extension | the user name of the Echo's extension |
| Password | its password, sealed by the settings page; it goes to the server only as a digest |

The plugin registers at once and again before the registration expires. A wrong password is tried again only every
five minutes, so a fail2ban on the server never sees a flood.

The Echo's SIP and audio ports are hassmic's plugin ports (two between 28940 and 28999, the same at every start;
hassmic's log names them). The server learns them from the registration.

### Asterisk (PJSIP)

```ini
[echo_kitchen]
type = endpoint
context = local
disallow = all
allow = alaw,ulaw
auth = echo_kitchen
aors = echo_kitchen
direct_media = no
rtp_symmetric = yes

[echo_kitchen]
type = auth
auth_type = userpass
username = echo_kitchen
password = <a long random password>

[echo_kitchen]
type = aor
max_contacts = 1
remove_existing = yes
qualify_frequency = 60
```

and an extension that dials it, for example `exten => 70,1,Dial(PJSIP/echo_kitchen,30)` in `[local]`. It can be one
of a ring group too (a doorbell that rings several phones and the Echo).

## Home Assistant

Beside the Echo's own entities:

| Entity | |
|---|---|
| Phone | `idle`, `calling`, `ringing`, `connected` |
| Phone with | who is on the call (`Doorbell (10)`, or the number) |
| Phone line | `off`, `connecting`, `registered`, `refused` (the password), `unreachable` |
| Phone result | an event for a call this Echo placed: `ringing`, `connected`, `busy`, `declined`, `not_answered`, `not_found`, `cancelled`, `failed` |

Actions: **Call** (`number`: an extension or a number your phone system dials; it places the call, as only an action
a person started may) and **Hang up** (also a button on the plugin's card: ends the call, declines a ring, cancels a
call that rings on the far side).

## Build and test

Needs a hassmic tree (`HASSMIC`, default `../echo-dot-assist`) with its PC build and `.venv`, and zig (from PATH or
hassmic's `toolchain/`) for the ARM build.

```sh
make                 # build/sip-<version>.hmp, signed with secrets/author.key
make unit            # G.711, the 16 <-> 8 kHz filters, MD5 (tests/unit.c)
make test            # the unit tests, then the plugin in hassmic's PC build against a stand-in phone system (test.py)
make arm-smoke       # the ARM program under qemu-arm against a stand-in broker (tools/armsmoke.py)
make release-test    # the release's packing with a throwaway key, and the workflows' checks
make index-check     # the plugin index's checks (INDEX: a checkout of hassmic-plugins)
```

`test.py` runs the phone system on another host in a network namespace (hassmic's `tests/peerhost.py`). Where unprivileged user namespaces are restricted (Ubuntu's
`kernel.apparmor_restrict_unprivileged_userns`), switch that off for the run, as CI does.

It covers: registration by digest; calls in with A-law and mu-law that ring until the action button answers, the
far side's audio on the Echo's speaker and the call microphone's back, busy, the far side's BYE, "stop", no common
codec, do not disturb, cancelled, declined from Home Assistant, not answered, hassmic's limit of unanswered rings;
calls out through a 407, ringing, answered, hung up, busy, cancelled while ringing; a wrong password; the plugin
switched off (unregistered).

### Your key

`secrets/author.key` signs every release, and an Echo takes updates of an installed plugin only from the key it was
installed with. It is never in git: keep a copy somewhere safe. `keys/author.pub` is its public half.

## Releases

A tag `v<version>` (the manifest's `version`) releases through `.github/workflows/release.yml`: all of CI first
(`build.yml`), then the program built again and held to CI's bytes, packed and signed with the key in the
`release` environment's secret `PLUGIN_SIGNING_KEY` (`base64 -w0 secrets/author.key`), attested, and published as
the release's asset `sip-<version>.hmp`.

## Getting listed

The plugin index (Gamer92000/hassmic-plugins) lists a release by a pull request that adds `plugins/sip.json`;
`make index-check INDEX=../hassmic-plugins RELEASED=<the release's asset>` prints that entry and runs the index's
checks first.

## Keeping the SDK in sync

`sdk/` is hassmic's (`make sync HASSMIC=<tree>` copies it and writes the commit into `sdk/VERSION`; `make drift`
says where it differs). CI tests against that commit.

## Licence

MIT (`LICENSE`). `sdk/` is hassmic's, under the MIT licence too (`sdk/LICENSE`); its `hmplugin.c` is compiled into
the program, so the package's `LICENSE` carries both notices. The build, test and release tooling comes from
hassmic's plugin template (0BSD).

## Layout

| | |
|---|---|
| `manifest.json` | the manifest: id, name, version, capabilities, settings |
| `src/main.c` | the SIP user agent on the SDK's client: registration, calls, RTP |
| `src/codec.c`, `src/codec.h` | G.711, the 2:1 rate change around it, MD5 |
| `tests/unit.c` | their unit tests |
| `test.py` | the test in hassmic's PC build |
| `tools/armsmoke.py` | the ARM program under qemu against a stand-in broker |
| `tools/release.sh`, `tools/release_test.py`, `tools/workflow_test.py`, `tools/workflow_breaks.py` | the release's packing and its tests |
| `tools/indexcheck.py`, `tools/indexcheck_test.py`, `tools/makefile_test.py`, `tools/sync.sh` | `index-check`, its test, the Makefile's sub-make test, `make sync`/`drift` |
| `sdk/` | hassmic's SDK, never edited here |
| `.github/workflows/` | CI (`build.yml`) and the release (`release.yml`); `.github/zig.txt` pins zig |
