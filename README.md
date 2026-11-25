# hassmic plugin template

Where a plugin for [hassmic](https://github.com/Gamer92000/echo-dot-assist) starts. hassmic turns Amazon Echos into
Home Assistant voice satellites, and a plugin is a program of its own beside it on the Echo, in a sandbox, with only
the powers its owner accepted with the Echo's action button. hassmic ships no example plugins of its own beyond
microWakeWord: this repository is the starting point. On GitHub, **Use this template** makes your copy.

It holds an example plugin that is deliberately useless: it counts its starts and greets a server you name now and
then, and at once when the action button is held a second. What it does is a placeholder; how it does it shows each
common piece of a plugin once, with comments that say why: settings of several kinds, storage, the tick, logging,
the stop, and a connection to a server the owner entered. It is written twice, in C on hassmic's SDK (`src/main.c`, the main path) and in Rust on a client of its own
(`rust/`, optional), with the build, the tests, CI and the release around them.

Beside it, a second example: **Tone** (`tone/`), a music source that plays a steady sine on the Echo's speaker the
way a music player plays music. Where a Squeezebox or Snapcast client would decode its stream, it computes a tone;
everything around that (the stream, pause and resume from the action button, "stop" and Home Assistant, the
position) is what such a player needs ("The tone example").

**State (2026-11-23):** made for the plugin protocol's versioned scopes, every scope at version 1 (`sdk/VERSION`
names them and the hassmic commit). Both examples, each in C and Rust, pass their test in
hassmic's PC build, under qemu-arm, and the plugin index's own check of a package. hassmic's plugin system is
experimental and not released yet; what a plugin requires keeps working on every later hassmic ("Versions").
The index takes community plugins: it lists the package your own GitHub Actions built, attested and released from a
tag, and builds nothing itself ("Releases", "Getting listed"); nothing of it is published yet.

[`docs/plugins.md`]: https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md
[`PROTOCOL.md`]: sdk/PROTOCOL.md

## What a plugin is, and what it may not do

A static program for 32-bit ARM Linux (ARMv7, hard float). hassmic's launcher starts it as a user of its own, under
a system call filter (seccomp; `sdk/filter.def` is the table: about 70 calls, the rest answer `EPERM`) and with
limits on its memory and threads. What it finds:

- **No file system.** No `open`, no directory. What it keeps, hassmic keeps for it (storage); what the owner gives
  it (files), it reads through descriptors hassmic opens.
- **No sockets of its own making.** No `socket`, `connect`, `bind`. A plugin that talks to a server asks hassmic,
  which connects only to the server the owner typed into the plugin's settings, and hands over the socket.
- **stderr is hassmic's log**, as `plugin <id>: ...`, 50 lines a minute. Descriptor 3 is the control socket to
  hassmic; 0 and 1 are `/dev/null`.
- Time (`clock_gettime`) and randomness (`getrandom`, `hm_random`), memory, threads up to the manifest's number.
  Not `fork`, a second `execve`, `ptrace`, signals to others, executable memory.

It is built **statically against musl**: zig (`zig cc -target arm-linux-musleabihf`, what the Makefile uses), a musl
cross gcc, Rust's `armv7-unknown-linux-musleabihf`, Go with `CGO_ENABLED=0`. **Never the Android NDK**, although
the Echo runs Android: a static bionic program does not even start under the filter. hassmic checks every program
before it is installed (an ELF for 32-bit ARM, static, a stack marked not executable, no segment both writable and
executable); `sdk/plugcheck.py elf` runs the same checks, and the Makefile runs it on every build.

The sandbox in detail: [`docs/plugins.md`] [2.4 "The filter"](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#24-the-filter),
[2.5 "What a plugin has"](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#25-what-a-plugin-has),
[2.6 "Running, crashing, hanging"](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#26-running-crashing-hanging),
and [`PROTOCOL.md`] "What a plugin is".

How it lives: `main()` hands its callbacks to `hm_run()`, and the plugin lives inside them on one thread. hassmic
sends every setting (`config`), the plugin answers it is `ready`, then gets a `tick` about once a second and
`config` again whenever the owner changes something, until `stop`. A callback that blocks holds up the client,
which answers hassmic's PINGs between callbacks: three unanswered (30 s) and the plugin is killed. One that uses more
than a whole core for a minute is killed too. A plugin that exits by itself is started again after 1, 5 and 30 s,
then every 5 minutes; five ends in ten minutes switch it off.

## Start

1. **Use this template** on GitHub (or copy the files), and clone your copy next to a checkout of hassmic,
   `../echo-dot-assist`, which `make test` and `make sync` use.
2. Give it its name. The plugin's id lives in `manifest.json` alone (the Makefile, the tests and CI read it from
   there), so one command does it:

   ```sh
   make rename ID=my-plugin NAME="My plugin" AUTHOR="Your name" SOURCE=https://github.com/you/hassmic-plugin-my-plugin
   ```

   The id is 3 to 21 of `a-z`, `0-9` and `-` (24 is hassmic's limit; the Rust build adds `-rs`), and it is what every
   Echo knows the plugin by: settle it before your first release. Another id later is another plugin, installed
   beside the old one with its own settings and storage. `SOURCE` is your repository's address: the release job and
   the plugin index both hold the package to it. The tool says what is left: the description, the licence, this
   README (make it your plugin's; this guide stays in the template's repository).
3. Make your key:

   ```sh
   make key        # secrets/author.key (git-ignored: never commit it) and keys/author.pub (commit this one)
   ```

### Your key

Every package is signed with your author key. An Echo pins the key a plugin was installed with, and takes updates
of that plugin only when they are signed by the same key. So:

- **Keep `secrets/author.key`**, and a copy somewhere safe. Lose it and you can never update the plugin on any Echo
  it is installed on: owners would have to remove it and install a new plugin (another id, or the same id after a
  removal, which deletes its settings and storage).
- **Never publish it.** Whoever has it can sign updates of your plugin that Echos take as yours (without the
  button, where they ask for nothing new), and all the plugin index can do then is withdraw the plugin (by id or by
  key).
- For CI's releases, it goes into a secret (see "Releases"). `make key` refuses to overwrite a key that is there.

The template itself has no key, and no `keys/` folder until you make one.

## Build and test

```sh
make                 # build/<id>-<version>.hmp: the ARM program, checked, packed and signed with your key
make host            # the C and Rust builds for the PC (static, as on an Echo), for the test
make tone            # the tone example's packages: build/tone-<version>.hmp, build/tone-rs-<version>.hmp
make test            # both in hassmic's PC build (HASSMIC=../echo-dot-assist): install, settings, storage, stop,
                     # the server, the log's limit; the tone's stream, pitch, button, Home Assistant. Two minutes
make arm-smoke       # the ARM programs under qemu-arm against a stand-in broker (tools/armsmoke.py, armsmoke_tone.py)
make rust-unit       # the Rust client's own tests, against hassmic's test vectors (sdk/vectors/)
make index-check     # the plugin index's own checks on your package and the entry it takes (INDEX=../hassmic-plugins;
                     # RELEASED=<your release's asset>: that one, its attestation too)
make release-test    # the release's packing (tools/release.sh) with a throwaway key, its refusals, the
                     # release workflow read as data (tools/workflow_test.py), and that test tested
                     # (tools/workflow_breaks.py: broken copies, each rule a check of its own, each check
                     # switched off in turn; some 20 to 40 s)
```

What each needs: `make` zig (in `PATH`, or hassmic's `toolchain/zig/zig`) or `PLUGIN_CC=<a musl cross gcc>`;
`make test` a hassmic tree with its `.venv` (`uv sync` there; its PC build is made by `make test` when it is missing,
which needs libopus and libcurl), Linux, and unprivileged user namespaces (the test runs in a network namespace of
its own, with the server it greets on another host behind a veth pair); `make arm-smoke` `qemu-arm` (qemu-user, or
hassmic's `toolchain/qemu-arm`); `make rust`, `make rust-unit` Rust (below).

`make test` runs `test.py`, which uses hassmic's own test helpers (`tests/fake_plugins.py`, `tests/webclient.py`,
`tests/run.py`): it is the settings page, presses the action button (a signal to the PC build) and is the server.
Change it with the plugin: it tests the example, not hassmic. What the PC cannot show is the sandbox on an Echo's
kernel: the filter on the PC is translated to x86-64, and qemu takes no filter at all. The run on an Echo is the
proof.

### On an Echo

On the Echo's settings page: switch on **Plugins**, open the Plugins page, **Install from file…** and pick
`build/<id>-<version>.hmp`. The Echo checks the package, shows what it may do, one line per capability, says
"From a file, not reviewed" with your key's fingerprint (`plugcheck.py show` prints the same), and waits for its
action button. Then the plugin's card has its settings, its state and its log lines are in the Echo's log (the
settings page's log viewer, "plugin <id>: ...").

A package without the index's listing is a **sideload**: it installs only with the button, never syncs to another
Echo without that Echo's button, and is updated from a file the same way. An update from a file installs without
the button only when it asks for nothing new (below) and is signed by the same key.

**The development flag**, on the plugin's card: a system call the filter refuses then stops the plugin with
`SIGSYS`, and the client writes "the sandbox refused system call <number>" into the log, instead of the call
quietly answering `EPERM`. `sdk/filter.def` has the numbers.

## The example, piece by piece

`src/main.c` (and `rust/src/main.rs`, rule for rule):

| Piece | Where | What it shows |
|---|---|---|
| Settings | `on_config` | Every row arrives as text: before `ready`, and again on every change. A `bool` is `"0"`/`"1"`, an `int` its decimal, a `select` the option's text. Rows a `net.server` entry names (server, port) restart the plugin when they change; any other change (the protocol) the plugin follows itself |
| Storage | `on_ready`, `save_number` | A start counter read and written once per start; the greeting count written at stop and at most every 10 minutes. The flash takes ten writes, then one a minute: `hm_store_put` returns 0 (written), 1 (kept by the client, written later) or -1 with `HM_ERR_BUSY` (neither), and the example says so and goes on |
| The tick | `on_tick` | About once a second from `ready` on: connect when there is no socket (never in `ready`: hassmic refuses a connect before READY), greet when it is time, save now and then. Short: hassmic's messages are read between ticks |
| The server | `open_sock`, `greet` | `hm_connect(entry, 0, &why)` for the manifest's first (UDP) or second (TCP) `net.server` entry: a connected, non-blocking socket, or a reason. Backs off on failure (hassmic refuses the 31st connect in a minute), stops trying when nothing is entered (`HM_ERR_UNSET`: the owner's next change restarts it) |
| The buttons | `on_button` | `button`: the action button held a second greets at the next tick. The plugin hears what Home Assistant's "Button" event entity hears, never a press that answered a wait for the button (a login, an install); it cannot keep the button from doing what it does on the Echo |
| Logging | `hm_log` calls | One line per change of state (started, connected, lost the server), never per event. The "Log every greeting" switch is the counter-example, off by default: hassmic keeps 50 lines a minute of a plugin and then says "(N more lines in that minute, not kept)" |
| The stop | `on_stop` | Saves what a restart should find. Called when hassmic stops the plugin (switched off, its server changed, an update, a removal) and when hassmic itself ends in order (its update, a restart or reboot from the settings page: it waits up to 2 s for its plugins), not when hassmic is killed or crashes or the Echo loses power: the plugin is killed then, and a restart goes on from what it last saved, which is why the tick saves now and then too (the test shows all three) |

Not in the example, and where to look:

- **Reading the log** (`log.read`, "Reads this Echo's technical log"): add `"log.read": true` to the capabilities, a
  `log` callback, and `hm_log_follow()` from `ready` (a cursor you kept in storage, or `"start"`). Keep the cursor
  the callback gets to go on after a restart, and skip lines that begin `plugin <your id>: `: your own lines come
  back to you. What a plugin gets is less than the page shows, by hassmic's rule: never a line marked private (what
  was said to the Echo and what it answered, names of networks, devices, timers and servers, media addresses, Home
  Assistant's error texts) or sealed (a wake word plugin's), of the system's own lines only its events, MAC and
  Bluetooth addresses and the Sendspin pairing token blanked. The whole log (`log.read.full`) is for official
  plugins only. `src/main.c` has the shape of it in a comment; [`PROTOCOL.md`] "The log" the rest (flow control,
  gaps).
- **A wake word engine** (`wake`, with `files` for its models): the microWakeWord plugin,
  [hassmic-plugin-microwakeword](https://github.com/Gamer92000/hassmic-plugin-microwakeword), is the reference.
  Such a plugin hears the microphone all the time, and is sealed: besides `wake` it may have only `files` and
  `storage`, nothing that carries data out.
- **Files the owner uploads** (`files`, a settings row of type `files`): also microWakeWord's, and
  [`docs/plugins.md`] [4.3](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#43-settings-settingsc-webc-webappjs).
- **A `secret` row** (a password, a token): sealed on its way from the browser, never shown again, never
  exported. Never log its value.

## The tone example

`tone/main.c` (and `rust/src/bin/tone.rs`, rule for rule; its own manifest `tone/manifest.json`, id `tone`, with
`audio.music` alone and two settings, frequency and level):

| Piece | Where | What it shows |
|---|---|---|
| The stream | `open_stream` | `hm_music_open(48000, 2, &why)`: a non-blocking socket that plays what is written into it, at that rate and channel count (8 to 48 kHz, mono or stereo). Opening it makes the plugin the newest music source: Sendspin or a phone over Bluetooth is paused. Opened on the first tick, never in `ready` (hassmic takes a stream only after READY) |
| Writing | `on_writable` | Called while the stream takes more and the player says it plays. Writes until the socket is full (`EAGAIN`): hassmic reads it at playback speed, so the socket's buffer is the back-pressure, and a sine is computed as fast as it plays. A write that fails otherwise (`EPIPE`) means the stream ended: another music plugin took the speaker |
| What it does | `say` | `hm_music_state(PLAYING / PAUSED / STOPPED, title, artist)` after every change: the action button, "<wake word>, stop" and Home Assistant's media player go by what it said last, and the card shows the title and artist (UTF-8). What it writes while it does not say playing is dropped, read no faster than it would play |
| Asked | `on_cmd` | `MUSIC_CMD`: pause (the button, the stop word, another source that started, Home Assistant), resume (the button again, Home Assistant), stop (Home Assistant: it closes its stream), next and previous (the button pressed twice or three times; a tone has none). The player does it and says so; hassmic does not wait for it: from a pause or a stop on, what it writes is not heard until hassmic resumes it or it starts anew |
| Where it is | `on_pos` | `MUSIC_POS`, five times a second: frames that reached the speaker and when (`CLOCK_MONOTONIC`). A player in a synchronised group keeps in step by it; the tone logs it once a minute |

hassmic does the rest: the duck while the voice assistant listens and answers, the volume, the light ring's music
effects. A real player adds what it needs of the rest of the protocol: its server's address found by a search
(`net.discover`) and proposed to the owner (`hm_propose`: only the owner's click writes it), UDP of its own
(`net.udp`, for players that speak it), the volume (`audio.volume`), entities and actions in Home Assistant
(`ha.entities`, `ha.actions`). [`PROTOCOL.md`] "Music", "UDP of its own, the search, proposals" and "Home
Assistant" have them, hassmic's `tests/plugins/testmusic.c` uses each.

**Calls** (`call`) have no example here: the plugin hassmic's design has in mind for them is a SIP phone
line, which is its community author's to port (hassmic's issue #186), and a template's example is what people copy:
one that holds the room's microphone should be the real thing, not a placeholder.  hassmic's own stand-in,
`tests/plugins/testcall.c`, shows the whole flow on the C client: a call out with an action's token
(`hm_action_token`, `hm_call_out`), one rung in (`hm_call_incoming`) and answered by a person at the Echo (the
`call_answer` callback, then `hm_call_live`; no token answers it), the room's frames (`call_audio`) and the
far side's (`hm_call_audio`), the end both ways (`hm_call_end`, `call_hangup`).  [`PROTOCOL.md`] "Calls" has the
rules.  The Rust client here does not speak calls (nor actions) yet.

**A copy of each request** (`request.audio`, `request.text`) has no example here either, on purpose: a plugin
that hears everything said to the Echo should never be a placeholder that people copy and install as it is.  Write
one only for what it is for (who is speaking, a log of your own), ask for the least (`request.text` alone where the
words are enough; no `net.*` where the plugin's result can go to Home Assistant through its own entities), and know
that the Echo asks the owner for its action button twice for one with any `net.*`.  hassmic's own stand-in,
`tests/plugins/testreq.c`, shows the flow on the C client: `request_start` (the request's own socket comes with it),
`request_audio` (each block as Home Assistant got it, its offset showing a gap), `request_end`, `request_text`.
[`PROTOCOL.md`] "A request's copy" has the rules.  The Rust client here does not speak them.

## The manifest

`manifest.json`, the file the owner accepts. hassmic reads it strictly (one JSON object, UTF-8, no key twice,
whole numbers as whole numbers) and refuses the package for any field out of its rules; `make index-check` runs the
plugin index's copy of those rules. Field by field:

| Field | |
|---|---|
| `id` | 3 to 24 of `a-z0-9-`, no `-` first, last or twice in a row; 21 here (the Rust build's is `<id>-rs`). What every Echo knows the plugin by: never change it after a release |
| `name` | Up to 48 bytes, shown on the card and in the install dialog |
| `version` | A whole number from 1. It only grows: an update needs a higher number than the version installed |
| `version_name` | Up to 24 bytes, shown in brackets beside the version ("version 7 (1.2.0)"). Free to change |
| `requires` | The least version of each scope of the protocol the plugin uses: `core` and the scope of each capability it asks for, and nothing else (`{"core": 1, "storage": 1, "button": 1, "net.server": 1}` here). hassmic runs it wherever it has each at that version or later ("Versions"); `sdk/plugcheck.py requires manifest.json` prints what the capabilities call for at version 1 |
| `author` | Up to 48 bytes, shown as "Plugin by ..." |
| `source` | Your repository's address, up to 200 characters: the card's "Source and issues" link, where owners report problems. The index fetches releases from it |
| `license` | Up to 32 bytes, an SPDX name such as `MIT`, shown after "Licence". Free to change. The text goes into the package as `LICENSE` |
| `description` | Up to 240 bytes, one line: the text on the card above the settings |
| `models` | Echo models by codename (`donut`, `biscuit`, ...), or `["*"]` for all |
| `memory_mb`, `threads` | Limits, 8 to 96 MB (default 32) and 1 to 16 (default 4). Ask for what you need: the example takes 8 and 1 |
| `capabilities` | What it may do beyond running: below |
| `settings` | Up to 32 rows the owner fills in on the card: `bool`, `int` (`min`, `max`, `unit`), `select` (`options`), `text`, `host` (a name or an address, what `net.server` names), `secret`, `files`. Each with `name` (`a-z0-9_`), `label`, optional `help` and `default`. The plugin reads them, never writes them |
| `author_key`, `digest` | Written by `plugcheck.py pack`: your public key and the program's BLAKE2b-256. Not in the source file |

What the owner accepts is more than the capability lines: the capabilities with all their parameters, every
settings row whole (label, help, unit, default, limits, options), the name, the author, the description and the
source. An update that changes any of that, a help text's wording included, waits for the action button on every
Echo, and the dialog names what changed. The same or less (a capability or a row gone, a smaller number) installs
without it. Only `version`, `version_name` and `license` are free.

In hassmic's docs: [`docs/plugins.md`] [4.2 "Manifest"](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#42-manifest),
[4.3 "Settings"](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#43-settings-settingsc-webc-webappjs),
[3.3 "Capabilities"](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#33-capabilities) (what
is accepted, what an update may be without the button), [5.1 "Package"](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#51-package);
[`PROTOCOL.md`] "The manifest and the package".

## Capabilities, and what the owner sees

Without any capability a plugin can start, read its own settings, write its own lines into the log and learn the
Echo's model, name, clock and time zone. Everything else is a capability in the manifest, and the install dialog
shows one line per capability, with its parameters filled in. The owner accepts the list as a whole with the
action button; an update that widens it (a capability more, a larger number, another entry, another port, a
`requires` raised into a version that lets a plugin do more, "Versions") asks again, on every Echo it reaches. So
ask for the least that does the job.

What the protocol has (every capability of hassmic's design, each its own scope; up to eight plugins run at once on an Echo, within
128 MB of their `memory_mb` together, so ask for the memory the plugin needs, not the most):

| Capability | Gives | The owner reads |
|---|---|---|
| `storage` (kB, 1 to 1024) | values of its own on the Echo, 16 kB each, 128 keys | "Keeps up to *N* kB of its own data on this Echo" |
| `net.server` (1 to 4 entries) | sockets to the server the owner enters in a `host` row, UDP or TCP, at listed ports or the one in an `int` row | "Connects to the server you enter in "Server" (UDP, the port you enter in "Port")" |
| `log.read` | hassmic's log, less what is private (above) | "Reads this Echo's technical log" |
| `log.read.full` | the log as the settings page shows it, what was said included (never a wake word plugin's lines); for plugins the index lists as official only: hassmic refuses any other | "Reads this Echo's whole log, including what was said" |
| `net.lan` (1 to 4 entries `{"proto", "ports"}`, ports a list or `"any"`) | sockets to any device of the home network at those ports: an address the plugin names (`hm_connect_lan`, `connect_lan` in Rust), private or link-local, never a name | "Connects to any device on your network (TCP, ports 80, 443)" |
| `net.internet` (the same entries) | sockets to any address or name, at those ports (`hm_connect_internet`, `connect_internet`) | "Connects to the internet (TCP, port 443)" |
| `files` (MB, 1 to 64) | files the owner uploads on its card, read-only | "Reads the files you give it on its page (...)" |
| `wake` | the microphone stream, to report wake words; sealed (`files` and `storage` besides, nothing else) | "**Hears this Echo's microphone all the time**, to listen for its wake words" |
| `state` | idle, listening, thinking, speaking; mute; the volume (never what was said) | "Sees when this Echo listens and speaks" |
| `audio.music` | a stream that plays on the Echo's speaker as music, the newest source winning | "Plays sound on this Echo's speaker, whenever it likes" |
| `audio.volume` | sets the Echo's volume, as its keys do | "Changes this Echo's volume" |
| `net.udp` (1 to 4) | UDP ports of its own (28940 to 28999, the same at every start), heard only from the home network, sending only to it | "Can be reached from your network, and sends to devices on it (*N* UDP ports of its own)" |
| `net.discover` (`{"udp": port, "to": "broadcast" or a group}`) | a search for its server at that port (never one of hassmic's own: 2330, 28928 to 28999), once a second; the answers, and anything else from the home network, come to a port of its own; what it finds is proposed to the owner (`hm_propose`, with `net.server`) | "Searches your network for its server (UDP port *P*, to every device on it), and can be reached from your network on a UDP port of its own, where the answers come" |
| `ha.entities` (up to 16, no two of one label) | sensors, switches, numbers, selects, buttons, events of its own in Home Assistant, named "Plugin *length of its id* *id*: *label*" ("Plugin 7 example: Level" for a number labelled "Level" of the plugin "example", entity id `number.<node>_plugin_7_example_level`: never one of hassmic's own or another plugin's). An entity marked `"page": true` (true or false, nothing else; not on a button or an event, which have no state to show) is also a read-only row of the plugin's own on its card, its label and the state the plugin said, while it runs. It is no power: `page` added or taken away in an update asks for no button | "Shows in Home Assistant: "*label*" (*kind*), ..." |
| `ha.actions` (up to 8, no two of one label) | actions Home Assistant can call (`esphome.<node>_p<length of the id>_<id>_<name>`), with up to 4 arguments; without arguments also a button on its card; with `call`, one marked `"calls": true` brings a call token (`hm_action_token`), good for one call going out (`hm_call_out`) and nothing else | "Home Assistant can tell it to: "*label*", ..." ("... (and have it place a call)" for one marked `calls`) |
| `request.audio` | a copy of each request's audio as Home Assistant got it, from the wake word or the button to the end of the request, one way (the plugin cannot change, end, start or answer a request); with any `net.*` the owner presses the action button twice (no example here, on purpose: above) | "**Hears everything said to this Echo after the wake word or the button**" |
| `request.text` | what speech to text understood of each request and the reply, as text (no example here) | "Reads what was said to this Echo and what it answered" |
| `button` | the `BUTTON` event (`button` callback; Rust: `Plugin::button`): the action button or a volume key pressed, or held a second, exactly the presses Home Assistant's "Button" event entity hears, never one that served a wait for the button (a login, a plugin's install, a phone's setup) or a hold that dismissed the settings page's note. It only hears: what the button does on the Echo happens all the same. Five a second. Not beside `wake` (sealed) | "Hears presses of this Echo's buttons, as Home Assistant's "Button" does" |
| `call` | calls through the Echo, one at a time: a call coming in rings and is answered only by a person at the Echo (the action button, the screen; no token, Home Assistant or the page answers one), the room's microphone and the Echo's call speaker as 20 ms PCM frames while the call is live, never otherwise; a call out only with an action's token ([`PROTOCOL.md`] "Calls") | "Makes and takes calls: a call coming in is answered only at the Echo, one goes out only when Home Assistant or the settings page asks. Hears the room during a call" |

The example asks for `storage` (1 kB), `net.server` with two entries on the same rows, and `button` (the action
button held a second greets at once: `on_button`). A `net.server` entry's
rows have **no `default`**: hassmic refuses a manifest with one, since a server named by the plugin's author is no
server the owner entered. Port 5555 is never allowed; loopback, the Echo itself, multicast and broadcast are refused
whatever the owner types. Every capability of hassmic's design
([`docs/plugins.md`] [3.3](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#33-capabilities))
is built; a hassmic without a scope the manifest requires (an older one) refuses it, "needs a newer hassmic: *scope*
*version*".

## Versions

The protocol is **scopes**, each with a version of its own from 1: `core` (the start, `CONFIG`, `PING`, `STOP`,
`TIME`, the answers) and one per capability, named as it (`storage`, `net.server`, `button` ...). A scope's next
version only adds (a message, a field after the last, a value, a key of the manifest), never changes or takes away
what one had: what has to break becomes a new scope. Each version above 1 is marked as one that **widens** what its
capability lets a plugin do, or one **within** it (hassmic's `sdk/messages.def`, PROTOCOL.md "Widening"). `sdk/VERSION`
lists the scopes this SDK speaks and their versions.

- **`requires` in the manifest** names the least version of each scope the plugin uses: `core`, and the scope of
  each capability it asks for, nothing else (hassmic and `plugcheck.py pack` refuse a manifest where they differ).
  A plugin that uses what `ha.entities` 2 brings requires `"ha.entities": 2`; one that does not stays at 1.
- **What hassmic does with it:** it runs the plugin wherever it has every scope required at that version or a later
  one, and speaks exactly the versions required with it: nothing a later version brought reaches the plugin. A
  hassmic update never breaks a plugin, so there is nothing to rebuild for one. A hassmic older than what a plugin
  requires (one an Echo fell back to) refuses it at install and at every start, "needs a newer hassmic: *scope*
  *version*", keeps it, and runs it again under one that has it.
- **The client** says in `HELLO` every scope it speaks and at which version (the C client's table in
  `sdk/plugmsg.h`, the Rust client's `SCOPES`), and reads in `WELCOME` what will be spoken: the manifest's
  `requires`, which is also what was granted (the C client's `hm_granted`, the Rust client's `Info::granted`). A
  packet is exactly its layout at the version spoken: bytes after its last field make it malformed, and so does a
  value of an enum the version spoken does not have; neither is acted on. A value a later version brought than the
  one spoken goes as the earlier value the protocol names for it (PROTOCOL.md "Values, constants and keys": the C
  client's `pm_fit` on every packet, the Rust client's `fit` and its `*_LATER` tables, which its tests hold to the
  vectors' `fit` lines), so nothing a plugin writes is ever dropped for it. Only an event hassmic sends whose whole
  point is the new value (`BUTTON`, marked `drop`: a key of the buttons a plugin of button 1 does not have) may have
  none (`>-`, the vectors' `unsent` lines): such a one does not reach a plugin of the earlier version at all. A
  value no version has is never written (the C client's writers answer -1, the Rust client's `None`), and a request
  of a scope that is not spoken (a capability the manifest does not ask for) is refused by the client itself with a
  code of its own, `HM_ERR_NOT_SPOKEN` (C) or `ERR_NOT_SPOKEN` (Rust), 65535, never one of hassmic's. A manifest key a later version brought needs that version in
  `requires`, or the Echo and the plugin index refuse the manifest.
- **What you do:** nothing at a hassmic update. To use what a later version of a scope brings, `make sync` from a
  hassmic that has it (`make sync` warns where the manifest requires more than the SDK speaks), raise that scope in
  `requires` and `version`, test, release. Where that version widens, the update asks your users for the button.

[`PROTOCOL.md`] "Versions";
[`docs/plugins.md`] [3.1](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#31-framing-and-versions),
[5.7](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#57-api-versions).

## Releases

`.github/workflows/build.yml`: every push and pull request builds the ARM program twice in two folders and fails
when the bytes differ (a reproducible build lets anyone check that the release is your source), checks it, runs the
release's packing with a throwaway key (`make release-test`), builds and tests the Rust client, runs both under qemu,
and runs `make test` against the hassmic commit `sdk/VERSION` names (that commit has to be on hassmic's GitHub
repository).

`.github/workflows/release.yml`: a tag `v<version>` (the manifest's `version`, e.g. `v3`) releases. It runs all of
`build.yml` first, then its job `release` builds the program again with the same pinned zig (`.github/zig.txt`, the
`ziglang` wheel by its SHA-256) and refuses it unless it is the build job's, byte for byte (the one the tests ran);
packs it and signs the manifest with your key (`tools/release.sh`: what you can run yourself); **attests** the
package (`actions/attest-build-provenance`: a GitHub artifact attestation, signed with Sigstore, that this
repository's `release.yml`, run for the push of the tag, at the tag's commit, on a runner GitHub hosts, built these
bytes); and publishes it as the
release's asset `<id>-<version>.hmp`, with the attestation's bundle beside it (`<id>-<version>.hmp.sigstore.json`)
and the package's description. That asset is what an owner installs from a file, and what the plugin index lists
("Getting listed"). Once:

1. `make key`, commit `keys/author.pub`.
2. In the repository's settings, an environment `release` (allow only tags `v*`, and add yourself as reviewer if
   you like), with the secret `PLUGIN_SIGNING_KEY`: `base64 -w0 secrets/author.key`.

The release job refuses a tag that is not the manifest's version, a key that is not `keys/author.pub`'s, a manifest
whose `source` is not this repository, and a program that is not the build job's (or a build job's SHA-256 that is
missing, empty or not 64 hex digits: no check is no release). Only that job names the secret, so pull requests never
run with it, and only it has the permissions an attestation needs (`id-token: write`, `attestations: write`) beside
`contents: write` for the release. Anyone can check a release as the index does:
`gh attestation verify <id>-<version>.hmp --repo <you>/<repository> --source-ref refs/tags/v<version>
--cert-identity-regex '^https://github\.com/(?i:<you>/<repository>)/\.github/workflows/release\.yml@refs/tags/v<version>$'
--source-digest <the tag's commit> --signer-digest <the tag's commit> --deny-self-hosted-runners` (the index's
page prints it whole per version, and also holds the run's trigger to `push`, which gh has no flag for). Publish the
key's fingerprint (`python3
sdk/plugcheck.py show build/<id>-<version>.hmp`) in your README: it is what the Echo shows for a sideload.

Keep the release a release of this repository's own source: the attestation says which workflow file of which commit
built the package for which tag's push, not what that workflow does, so the index's maintainer reads, at the commit
you enter, the workflow your entry names (`release.yml`) and everything it runs: `build.yml`, which it calls,
`tools/release.sh`, the `Makefile`, the actions it uses. A workflow that downloads a program from elsewhere, or
builds something else than the tree, is not listed. Nothing of what goes into the package may come from outside that
commit: no action (`uses:`) not pinned by a commit of the action's own repository (one on its tags or branches:
GitHub runs a fork's commit under the original's name too, an impostor commit, which the index refuses), all the way
down (a composite action's own steps, a called workflow's, a docker action's image and its Dockerfile's `FROM`, by
the same rule), no artifact of another job or run (one only to compare a build of its own with), no other job's
outputs (`needs.*.outputs`) but a SHA-256 to compare with, nothing of the event's payload (`github.event.*`; a tag's
annotation is not in the commit) but the tag's name, no other repository checked out at a ref that moves (a branch or
a tag; a commit's id stays), no container image (`container:`, `docker://`) not pinned by its digest, no cache
(`actions/cache`, the caches `setup-*` actions keep, which other runs write), no `vars` and no secret as an input
(the signing key aside, which only the release job's environment holds: no other job names an environment, whose
secrets it would get), no release asset already there, no toolchain resolved online by a channel (rustup's `stable`,
`beta` or `nightly`, in a `rust-toolchain.toml` or on its command line), and no download not pinned by its hash; and
a re-run of an old run, which GitHub attests as that tag's push again, has to make the same bytes. Keep the
attestation step in `release.yml`'s own job and the file's trigger the push of a tag alone (`make release-test` holds
both, and the release job step for step: `tools/workflow_test.py` allows that job's steps as they are written and
nothing else, so a change of the job is a change of the test too; what `release.yml` takes from outside the commit is
the program built in the release job by the zig `.github/zig.txt` pins, the build job's artifact for its SHA-256
alone, read and checked as 64 hex digits by a statement of its own, hassmic at the commit `sdk/VERSION` names, and a
Package step and a "Which hassmic" step that fail where either is not one, which the test runs): the index refuses an
attestation signed in a called workflow (`build.yml` would be its signer), of `release.yml` called by another
workflow, of a run started by hand (`workflow_dispatch`) or of a branch, and one of another tag than the release's.
Every action in both workflows is pinned by the one commit `tools/workflow_test.py` names for it (`ACTIONS`), the
commit of the release tag in the comment beside it. GitHub runs `<owner>/<repo>@<commit>` for a commit of any fork of
that repository, under the original's name (an impostor commit), so the commit has to be one of the action's own: the
index reads both workflows at your tag's commit and refuses a release that uses a commit found on none of the
action's own branches and tags, a tag or branch instead of a commit, a local action (`uses: ./<path>`), or an image
not pinned by its digest; it does so all the way down, each action's own metadata read at its commit (a composite
action's steps, another repository's reusable workflow, a docker action's image and its Dockerfile's `FROM`: an action
of your own that uses another by a tag is refused as the tag would be in `release.yml`); and it reads the workflows
with a reader that takes plain YAML only (printable ASCII, no folded `>` blocks, no anchors, no flow mappings). An
action of your own repository named by `$/<path>` is read at the same commit and held the same; a Dockerfile only
where Docker's builder cannot read it another way (no line continued by a backslash, no `# syntax=` or other
directive, no `//` or `#!` line, no JSON, no quote or backslash in an option, a mount's `from=` only of an earlier
stage). That is stricter than GitHub and refuses some honest actions (cachix/install-nix-action,
houseabsolute/actions-rust-cross, pypa/gh-action-pypi-publish, as of 2026-11-23): pick another action, or write the
step in the workflow yourself. `make index-check RELEASED=...` asks the same.

### Changing the release job

`tools/workflow_test.py` is your own check that the release stays what the index lists (the index checks the
release itself, whatever the test says: its attestation and every action's commit). It allows the release job as
it is written, so a change of the job is a change of the test, in the same commit:

- a step, a key or a step's text: `RELEASE_JOB`, step for step; the texts that are run (`COMPILER`, `RUST_COMPILER`,
  `PACKAGE`, `PUBLISH`) word for word. The Package and "Which hassmic" steps are also run, as GitHub runs a step
  without `shell:` (`bash -e`), against a build job's SHA-256 and an `sdk/VERSION` that are none: a new way to read
  either has to fail them too.
- an action raised: its commit in `ACTIONS`, taken from the action's own tags (`gh api
  repos/<owner>/<repo>/tags`), and the same commit in both workflows.
- the compiler: zig is `.github/zig.txt` (one line, by its SHA-256; installed by the Compiler step in the build job
  and in the release job, the only steps that name `PLUGIN_CC`), Rust is `.github/rust.txt` (below). The build job's
  Compiler step is the release job's, the same text.
- what the release takes from the build job stays its artifact's SHA-256 alone (`checked/`, named twice), and
  `build.yml` never says `secrets`, names no environment and has no `continue-on-error` or `defaults`: only the
  release job's environment holds your key.

`tools/workflow_breaks.py` (also in `make release-test`) runs the test against broken copies of what it reads (both
workflows, `.github/zig.txt`, `.github/rust.txt`, `tools/rustpin.sh`, `rust/rust-toolchain.toml`, its copy of the
index's reader), each of which it has to fail with the FAIL line the break names (the words of one `check(...)` of
the test, as written there, and of no other): a break you make possible by changing the test (a step it no longer
holds) shows up there. Each rule of the test is a `check(...)` of its own, never one operand of another check's
`and` (70 checks, 91 breaks). Then it makes each `check(...)` of the test always true in turn, and some break has to
pass: a check no break names, or one that no longer holds anything, fails it. What that switches off is a whole
check, so a rule written inside another check's `and` would never be asked: write a new rule as a check of its own,
and add its break, naming its words. The test's
reader is the plugin index's, the same text: where `hassmic-plugins` is beside this tree (`INDEX=<path>`), the test
holds it so.

## Getting listed

The [plugin index](https://github.com/Gamer92000/hassmic-plugins) is the one list Echos read: "Browse plugins" on
the settings page, installs and updates fetched by the Echo itself. A plugin is entered by a pull request there that
adds `plugins/<id>.json`: name, description, author, your public key, tier, source, and per version what pins it.
The index adds its own signature to the package it lists (`listing.sig`, which says "listed" and the tier): that
copy is what Echos install.

- **Official**: the project's own plugins, in repositories under the project's account, looked at by the
  maintainer. Their entry names the release asset; the index fetches it and checks it as an Echo will. Updates that
  ask for nothing new install without the button, and they copy between the Echos of a household.
- **Community**: anyone's, yours. The entry names your release's asset, as an official one does, and more of it:
  per version the commit the release ran at (the tag's), the workflow that made it (`"workflow":
  ".github/workflows/release.yml"`), the asset's address
  (`<source>/releases/download/v<version>/<id>-<version>.hmp`), the digest of its manifest, your signature over it
  (its `manifest.sig`), the SHA-256 of its `LICENSE`, and the capabilities it asks for (what the maintainer reads in
  the pull request). `make index-check RELEASED=<the asset>` (download it: `gh release download v<version> -p
  '<id>-<version>.hmp'`) runs the index's checks on it, verifies its attestation as the index does (with `gh`, logged
  in) and prints exactly that entry (from the index's own `index.py entry`); without `RELEASED` it checks this tree's
  build, a preview. The index builds nothing: it fetches the asset, checks it as an Echo will and more, and lists a
  version only where its attestation says your repository's own GitHub Actions built it at the commit the entry
  names, on a runner GitHub hosts, in the workflow the entry names as that workflow's own run for the push of the
  tag the asset is under; a package uploaded by hand, built anywhere else, or attested by another workflow or by a
  run started by hand, is refused. With the digests in the entry every byte of what is listed is the
  entry's word, whatever the asset is replaced with later. In the pull request it also runs the program for a few
  seconds under qemu against a stand-in hassmic and refuses one whose system calls the sandbox would refuse: a smoke
  test only, the Echo's own filter is what holds. Broad capabilities (any device on the network, the internet, calls,
  every request heard or read, the whole log) are flagged on the index's page and on the Echo's "Browse plugins"; the
  whole log is refused for a community plugin, and so is hearing every request together with the internet. The
  first entry is merged after a look that it is what it says it is and that the release workflow (with all it
  calls and runs) builds it from the repository's source for a tag's push (no review of the code is promised, and the page says "Not reviewed"). Any language whose
  program keeps the rules can be listed this way (Rust too: build it in `release.yml`). Not yet built there: a
  later version merging by itself (a person merges every update for now). An Echo installs community plugins only
  once its owner switched them on (its Plugins page).
- **Revocation**: the index's signed revocation list can withdraw any plugin, every tier and sideloads too, by id
  (all versions or up to one) or by author key, with a reason. An Echo stops a withdrawn plugin and shows the reason;
  the owner can remove it or run it anyway with the button. A leaked key is the case for withdrawing by key.

The index's README has the details; [`docs/plugins.md`] [5.3](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#53-index)
and [5.6](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/plugins.md#56-revocation) the design.

### Before you ask to be listed

- [ ] Renamed: your id, name, author, source, description; your README; your licence in `LICENSE` and `license`.
- [ ] Your own key, kept safe; `keys/author.pub` committed; `secrets/` not.
- [ ] The least capabilities, the smallest numbers, no defaults in `net.server`'s rows, `memory_mb` and `threads`
      as measured.
- [ ] Nothing private in your log lines; within 50 lines a minute in normal running.
- [ ] Storage written on changes and at stop, not every second; a full bucket (`HM_ERR_BUSY`) handled.
- [ ] `make test` passes with your plugin's own scenario in `test.py`, and `make arm-smoke` with yours in
      `tools/armsmoke.py`; it ran on an Echo, installed from a file, for a day.
- [ ] CI green, the release made by `release.yml` from a tag: the asset `<id>-<version>.hmp`, attested (what Echos
      install from a file, and what the index lists).
- [ ] `make index-check RELEASED=<that asset>` passes, its attestation holds, and the entry you enter is the one it
      prints.
- [ ] Issues open on your repository: owners report there.

## Keeping the SDK in sync

`sdk/` is a copy of hassmic's SDK, so that this repository builds by itself and a build names exactly the SDK it
was made with (`sdk/VERSION`: the scopes and their versions, and the hassmic commit). Never edit it: your changes would be lost at the next
sync, and the C client is the reference other clients are checked against.

```sh
make drift                    # where sdk/ differs from ../echo-dot-assist (HASSMIC=<tree> for another)
make sync                     # take it: sdk/ and sdk/VERSION from that tree
```

Take a new SDK when you want what a later version of a scope brings (then also `"requires"`, "Versions"), or when
it fixed something in the client. The test job in CI checks `sdk/` against the commit `sdk/VERSION` names. If you speak the
protocol from another language, `sdk/PROTOCOL.md` is the specification and `sdk/vectors/` its messages as bytes:
the Rust client's tests read them.

## Rust

Optional: C on the SDK's client is the main path, and the one hassmic's own plugins use. `rust/` is the same
example on a client written from `sdk/PROTOCOL.md` alone (`rust/src/client.rs`, no C SDK, no crates; `sys.rs`
declares the few musl functions std lacks), checked against hassmic's test vectors and against the same test as the
C build. Its package has its own id (`<id>-rs`, `tools/rsmanifest.py`) so that both can be installed side by side;
CI builds and tests it but releases only the C package. Delete `rust/`, `tools/rsmanifest.py` and the Rust targets
if you do not want it, or delete the C build if you write your plugin in Rust (below, "Releasing the Rust build").

```sh
make rustup          # rustup into toolchain/ (git-ignored), its installer checked against the published SHA-256;
                     # or use rustup from your PATH
make rust            # build/<id>-rs-<version>.hmp
make rust-unit
tools/rustpin.sh DIR # Rust exactly as .github/rust.txt pins it, into DIR (prints DIR/bin): what CI builds with
```

`rust/rust-toolchain.toml` names the toolchain and both targets (ARM for the Echo, x86-64 musl for the test); they
link statically with Rust's own musl and `rust-lld`, so no C cross compiler is needed. Rust's `std::net` cannot make
sockets in the sandbox: take the descriptor `connect` returns (`OwnedFd`) and use it as it is.

On your PC rustup reads `rust-toolchain.toml` and installs that version. CI does not use rustup: rustup fetches the
version's manifest and checks the files against it, and nothing pinned in the repository holds that manifest, so a
release built so would take a download not pinned by its hash (the index's reading list). `.github/rust.txt` pins
the same version file by file instead, each by its SHA-256 (taken from that version's
`channel-rust-<version>.toml`): `rustc` and `cargo` for the runner and `rust-std` for the runner and for each target.
`tools/rustpin.sh` downloads them from static.rust-lang.org, checks each before it unpacks it, and installs them into
a folder of its own; build.yml's Rust step puts that first on `PATH`. A new Rust is new lines there with their hashes
and the same version in `rust-toolchain.toml` (`make release-test` holds the two to one version). The program comes
out the same in another folder (checked: no path of the build tree in it), as the release needs.

### Releasing the Rust build

For a plugin written in Rust alone, released as `<id>` with `manifest.json`:

1. `Makefile`: `build/$(ID)` is the Rust program (`cd rust && $(CARGO) build --release --locked --target
   armv7-unknown-linux-musleabihf`, the program copied to `$@`, `plugcheck.py elf` on it), and the C build's rules
   go. `tools/release.sh` builds `build/<id>` and packs it as it does now.
2. `release.yml` and `build.yml`: the Compiler step of both the release job and the build job runs
   `RUST_COMPILER` (`tools/workflow_test.py`) instead of the zig lines, the same text in both:
   `rust=$(tools/rustpin.sh "$RUNNER_TEMP/rust")` and `echo "$rust" >> "$GITHUB_PATH"`. Every later step of the job
   then has the pinned `cargo` and `rustc` first on `PATH`.
3. Nothing else of `tools/workflow_test.py`: it allows either compiler in the release job, holds the build job's to
   the same, and holds `.github/rust.txt` to `rust-toolchain.toml`'s version, every step that builds Rust to come
   after the pinned toolchain, and no step to run rustup. The build job's "Build, twice" then checks the Rust
   program is the same in another folder, and the release job builds it a third time against that SHA-256.

## Licence

This template's own files (the example in C and Rust, the Makefile, the tools, the tests, this README) are under
the **0BSD** licence (`LICENSE`): use them for anything, under any licence of your choice, without keeping a notice.
That is why it is 0BSD and not MIT: a template is meant to disappear into your plugin, and MIT would ask you to carry
the template's copyright notice in every copy.

`sdk/` is hassmic's, under the MIT licence (`sdk/LICENSE`). `hmplugin.c` is compiled into your program, so the
Makefile packs your `LICENSE` followed by the SDK's notice into the package (`build/LICENSE`).

Choose your plugin's licence and put its text into `LICENSE` and its name into the manifest's `license`. Any licence
works for hassmic; the plugin index wants the source public. MIT, Apache-2.0, GPL-3.0 and the like are all fine; a
licence that does not let others build and fix your plugin makes it a dead end when you stop maintaining it.

## Layout

| | |
|---|---|
| `manifest.json` | the plugin's manifest: id, name, version, capabilities, settings |
| `src/main.c` | the example in C, on `sdk/`'s client |
| `tone/` | the tone example: its manifest and `main.c` (its Rust build is `rust/src/bin/tone.rs`) |
| `rust/` | the example in Rust, on its own protocol client (`src/client.rs`, `src/sys.rs`) |
| `test.py` | the test in hassmic's PC build (`make test`) |
| `tools/indexcheck_test.py` | `index-check`'s own words against a stand-in index (`make test`, first) |
| `tools/armsmoke.py`, `tools/armsmoke_tone.py` | the ARM programs under qemu against a stand-in broker |
| `tools/release.sh`, `tools/release_test.py`, `tools/workflow_test.py`, `tools/workflow_breaks.py` | the release's package (release.yml runs it), its test with a throwaway key, the workflows read as data, and that test against broken copies of them (`make release-test`) |
| `tools/rename.py`, `tools/indexcheck.py`, `tools/rsmanifest.py`, `tools/sync.sh`, `tools/rustup.sh` | `make rename`, `index-check`, the Rust build's manifest, `make sync`/`drift`, `make rustup` |
| `tools/rustpin.sh`, `.github/rust.txt` | Rust as CI builds it (and a release in Rust would): each file of the toolchain by its SHA-256 |
| `sdk/` | hassmic's SDK, copied by `make sync`: never edited here |
| `.github/workflows/build.yml` | CI: every push and pull request |
| `.github/workflows/release.yml` | a tag's release: CI, then the package built, signed, attested and published |
| `.github/zig.txt` | zig 0.13.0, by its SHA-256, for both |
