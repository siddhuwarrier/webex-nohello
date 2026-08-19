# webex-nohello

[![CI](https://github.com/siddhuwarrier/webex-nohello/actions/workflows/ci.yml/badge.svg)](https://github.com/siddhuwarrier/webex-nohello/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/webex-nohello)](https://pypi.org/project/webex-nohello/)
[![Licence](https://img.shields.io/pypi/l/webex-nohello)](LICENSE)

Replies to content-free Webex greetings with a polite nudge towards
[nohello.net](https://nohello.net/en/).

If someone sends you a lone "hi" and nothing else, this posts a threaded reply
asking for the actual question, so the two of you do not spend a round-trip
trading hellos across timezones.

> **Replies are sent from your own Webex account, not from a bot.** Anyone who
> receives one sees it as a message from you. Read [Safety](#safety) before you
> put this on a schedule.

The project is governed by [CONSTITUTION.md](CONSTITUTION.md). If you are
changing the code, read that first.

## Quickstart

From nothing to a working schedule. **Every step but the last two is safe** — nothing is sent
to anyone until you both name them in the config *and* pass `--commit`.

```sh
# 1. Install. Needs Python 3.12+, plus `claude` or `codex` installed and signed in.
uv tool install webex-nohello        # or: pipx install webex-nohello

# 2. Sign in. Walks you through registering a Webex integration of your own -- a couple of
#    minutes, once -- then stores the tokens in your keychain and writes a starter config.
webex-nohello auth login

# 3. Check that everything an unattended run depends on actually works.
webex-nohello doctor

# 4. Decide who may receive a reply. Until you do this, nothing is sent to anybody.
webex-nohello config path
#    Open the config.toml it prints, and add:
#        allow_list = ["a-willing-colleague@example.com"]

# 5. Read the wording it would send, and which file to edit to change it.
webex-nohello config reply

# 6. See what it would do. Sends nothing and does not move your read positions,
#    so it is safe to run as often as you like.
webex-nohello run

# 7. Optional, and worth it: judge the classifier against a week of your real messages
#    before trusting it. This one cannot send at all -- it has no such flag.
webex-nohello review --lookback-days 7

# 8. Send for real, once, while you are watching.
webex-nohello run --commit

# 9. Run it unattended, every 10 minutes. Prints the exact schedule it will install,
#    refuses if preflight fails, asks, then runs once immediately so you see the result.
webex-nohello schedule install
```

Afterwards:

```sh
webex-nohello schedule status    # installed? and has the system actually loaded it?
webex-nohello config show        # which settings are in force, and where they came from
tail -f ~/"Library/Application Support/webex-nohello/run.log"

touch ~/"Library/Application Support/webex-nohello/PAUSED"   # stop, keeping the schedule
webex-nohello schedule uninstall                             # stop, removing it
```

The rest of this file explains each of those in more detail. If you read only one other
section, make it [Safety](#safety).

## Install

### Pre-requisites

- **Claude Code or Codex** installed and on `$PATH`, signed in. One is enough; see
  [Which CLI does the classifying](#which-cli-does-the-classifying) for the difference.
- A Webex account. You will register a personal integration during `auth login`.

### From PyPI

```sh
uv tool install webex-nohello
# or
pipx install webex-nohello
```

Either gives you an isolated environment and a stable path on `PATH`, which is what makes the
scheduled run reliable: launchd and cron are handed an absolute path, with no dependency on
your shell profile or an activated virtualenv.

Check it worked:

```sh
webex-nohello --help
which webex-nohello
```

To remove it: `uv tool uninstall webex-nohello`.

### From source

For working on it, or to run an unreleased change. You need
[uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
git clone https://github.com/siddhuwarrier/webex-nohello
cd webex-nohello
uv tool install --editable .
```

`--editable` keeps the installed command pointing at your working copy, so edits take effect
without reinstalling. Note that scheduling an editable install pins the path of that working
copy — move or delete it and the schedule breaks.

If you only want to work on the code and never need the command outside the directory, skip
the install and prefix everything with `uv run` — see [Development](#development).

## Which CLI does the classifying

Deciding whether a message is a bare greeting is the one part of this program that is not
deterministic. Everything else — finding candidates, applying the safety rails, posting —
is ordinary code. That judgement is delegated to a coding agent CLI you already have signed
in, which is why no separate LLM API key is needed.

Either `claude` or `codex` works. `auto`, the default, uses `claude` when it is installed and
`codex` otherwise. `auth login` tells you which one it found.

```sh
webex-nohello run --classifier codex          # for one run
```

```toml
classifier = "codex"                          # permanently, in config.toml
# classifier_model = "gpt-5-mini"             # or leave out for each CLI's default
```

### Why `auto` prefers claude

Not a view about the models. It is about Article IX.4, which requires the classifier have no
tools and in particular no route to Webex — a classifier that could read or post messages
itself would be a defect.

- **`claude`** takes `--strict-mcp-config --allowedTools ""`, and that is the whole story. It
  also accepts `--system-prompt`, which replaces Claude Code's own; without it a call ships
  roughly 25,000 tokens of agent harness instead of 3,600.
- **`codex`** has no equivalent. Its MCP servers come from *plugins* in
  `~/.codex/config.toml`, and `-c plugins={}`, `-c features.plugins=false` and
  `-c mcp_servers={}` were each measured to leave them loading anyway — including a Webex MCP
  server, if you have one. So `codex` is instead pointed at an isolated `CODEX_HOME` under
  this program's state directory, holding no config and a symlink to your real credentials.
  No config means no plugins, which means no tools. Verified: no MCP server connects.

  The credentials are symlinked, never copied — a second copy of an auth token on disk would
  be a worse problem than the one being solved. The directory is reused rather than recreated
  per call, because a cold one costs about 17 seconds against 4 to 6 warm.

Both are correct on the same set of boundary cases, which live in `tests/test_classify_live.py`
and include real conversations that previously caught misfires. Expect four to eight seconds
per message either way.

## What access it asks for

| Scope | Why it is needed |
| --- | --- |
| `spark:people_read` | Identify you, and tell real people apart from bots |
| `spark:rooms_read` | List your one-to-one spaces |
| `spark:messages_read` | Read the recent messages in those spaces |
| `spark:messages_write` | Post the reply, as a threaded reply only |

Nothing broader is requested. In particular there is no `spark:all`, and nothing
that would let this program read or write your group spaces.

Tokens and your client secret are held in your operating system's keychain, via
[keyring](https://github.com/jaraco/keyring) — never in a config file, a dotfile,
an environment variable, or a log. Webex extends the refresh token each time it is
used, so a regularly scheduled job should not need you to sign in again.

## Seeing what it would do

```sh
webex-nohello run
```

Reads your recent direct messages, asks the classifier about anything that might be a bare
greeting, and prints what it found and why. **Without `--commit` it sends nothing and does
not move your read positions**, so it is safe to run repeatedly while you decide whether you
agree with the classifier.

For each message you get the verdict, a confidence, and the model's one-line reason:

```
✓ ravi@example.com — leaving alone
    lol nice
     continues_conversation (0.98) — a casual reaction to the answer about the deploy,
     not a standalone greeting

! jane@example.com — bare greeting
    hi
     greeting_only (0.98) — bare greeting with no request or prior context
```

Then a second section says what would actually be *sent*, which is a different question —
the classifier's verdict is only the first of several gates. See
[Actually replying](#actually-replying).

To reproduce a verdict by hand, add `--explain` and you get the exact prompt and the exact
command line that produced it. Useful flags:

```sh
webex-nohello run --explain              # print the prompt and command for each verdict
webex-nohello run --no-classify          # scan only; makes no model calls
webex-nohello run --confidence 0.9       # require more certainty before a reply counts
webex-nohello run --lookback-days 7      # re-examine a window you have already read
```

## Judging the classifier against your own history

`run` only ever looks at the newest message in each space, and only once — correct for
normal use, useless for deciding whether you trust it. `review` walks a window of history
instead and classifies every message someone sent you, showing what would have happened:

```sh
webex-nohello review --lookback-days 7
```

It writes nothing and cannot post — not "does not by default", but has no such flag. It
shows the estimated time and cost and asks before spending anything.

```sh
webex-nohello review --lookback-days 7 --max-messages 20     # smaller sample
webex-nohello review --confidence 0.9                        # try a stricter threshold
webex-nohello review --json /tmp/verdicts.json               # dump for analysis
```

Output is sorted worst-first, so anything that *would* have been replied to appears at the
top — those are the ones worth reading. Each message is shown only the messages that
preceded it, never your own reply to it, since that would leak the answer and flatter the
result.

**Not everything flagged is a misfire.** Only the newest message in a space is ever a
candidate, so a greeting followed four minutes later by a real question is never replied
to — by the time a run looks, the question is the newest message and the greeting is just
context. `review` therefore reports two numbers: how many *looked* replyable in isolation,
and how many a run polling at your interval would actually have sent. Set the interval with
`--poll-minutes` (default 15).

The JSON dump contains full message text. Delete it when you are done.

## Actually replying

```sh
webex-nohello run --commit
```

**Out of the box this sends nothing to anybody**, even with `--commit`. The default is
opt-in: you have to name the people who may receive a reply. Start with yourself, from a
second Webex account or by asking a willing colleague.

`auth login` writes a commented `config.toml` for you. To see it and where it lives:

```sh
webex-nohello config show     # the settings in force, and where they came from
webex-nohello config path     # every file this program owns
webex-nohello config init     # rewrite the starter config (--force to overwrite)
```

The only line you need to change to start replying:

```toml
allow_list = ["a-willing-colleague@example.com"]
```

The rest — `opt_in_only`, `deny_list`, `cooldown_minutes`, `max_replies_per_run`,
`confidence_threshold` — are documented inline in the file itself at their defaults.

### The wording it sends

The reply is prose, so it lives in a Markdown file rather than in the TOML. By default that
is `reply.md` beside your `config.toml`, and until you write one the built-in text is sent.

```sh
webex-nohello config reply        # the text in force, and which file it came from
webex-nohello config template     # writes the default into that file so you can edit it
```

To keep your wording somewhere you already look — a notes repo, a dotfiles directory — point
`config.toml` at it. A relative path is relative to the config file, and `~` works:

```toml
reply_file = "~/notes/webex-nohello-reply.md"
```

If `reply_file` names a file that is not there, the run stops and says so. It will not fall
back to the built-in text, because that would send wording you never wrote.

Placeholders `{sender_first_name}`, `{sender_display_name}` and `{sender_email}` are
available; anything else is an error rather than rendering blank.

### Stopping it

The kill switch is a file called `PAUSED` in the state directory. On macOS:

```sh
touch ~/"Library/Application Support/webex-nohello/PAUSED"   # stop
rm ~/"Library/Application Support/webex-nohello/PAUSED"      # resume
```

If it exists, every run stops immediately — including scheduled ones, without touching the
schedule. It is a file rather than a config key on purpose: it works even if the config is
broken, and `touch` is easier to remember in a hurry than editing TOML.

### What was sent

Every reply is recorded in `replies.jsonl` in the state directory, one line each, with the
verdict, confidence and the model's reason. That file is also where cooldowns come from, so
deleting it lets everyone be replied to again.

## Running it unattended

Check that everything an unattended run needs actually works:

```sh
webex-nohello doctor
```

Eight checks, each independent, each with its own fix if it fails: the classifier answers,
Webex accepts your credentials, the config parses, the reply text renders, the state
directory is writable, the kill switch is off, and who would receive replies. It exits
non-zero on failure, so it works as a gate in a script.

Then install the schedule:

```sh
webex-nohello schedule install                 # every 10 minutes
webex-nohello schedule install --every 30      # or whatever suits
webex-nohello schedule install --show-only     # print the artefact, install nothing
webex-nohello schedule status
webex-nohello schedule uninstall
```

`install` prints the exact plist or crontab line it is about to write, tells you who will
receive replies, **runs the full preflight and refuses if anything fails**, and then asks.
There is deliberately no flag to skip the preflight: a schedule is precisely the case where
nobody is watching.

On macOS it installs a launchd agent at
`~/Library/LaunchAgents/local.webex-nohello.plist`; elsewhere a marked block in your
crontab, leaving your own entries alone. Either way the command is an absolute path, because
neither launchd nor cron inherits a usable `PATH` — that is the most common reason a
scheduled job silently does nothing.

Output goes to `run.log` in the state directory:

```sh
tail -f ~/"Library/Application Support/webex-nohello/run.log"
```

Every line in that file is timestamped, and each run is preceded by a separator, so a log of
many runs stays readable:

```
==================== run --commit at 2026-08-19 14:25:24+0100 ====================
2026-08-19 14:25:24+0100  Signed in as Someone <someone@example.com>
2026-08-19 14:25:24+0100  Reading direct spaces active since 2026-08-19 14:15...
2026-08-19 14:25:31+0100  Examined 3 direct spaces.
```

Timestamps appear only when output is redirected. Interactively they would be noise, and you
get colour instead.

Note cron cannot express every interval: below an hour it must divide 60 (5, 10, 15, 20,
30), and above an hour it must be a whole number of hours. Anything else is refused rather
than quietly rounded.

## Safety

Because replies go out under your own name and cannot be recalled, the defaults
are deliberately timid:

- **Nobody is replied to until you add them to `allow_list`.**
- `run` is a dry run unless you pass `--commit`, and a dry run prints the reply in full.
- On its very first run the program replies to nothing at all. It records where
  it has read up to and tells you what it would have considered, so it cannot
  work backwards through a year of history.
- One person gets at most one reply per cooldown window (30 minutes by default).
- There is a cap on replies per run. Wanting to exceed it is treated as a fault
  and stops the run rather than proceeding.
- Anything the classifier is not confident about is left alone.
- Only the newest message in a space is ever a candidate, so a greeting followed by a
  real question is never replied to.
- Two runs cannot overlap, and the reply is recorded before it is sent — so a crash costs
  a reply that never arrives rather than sending the same one twice.
- `schedule install` refuses to arm anything that `doctor` says would not work.

## Requirements

- Python 3.12 or newer.
- `claude` or `codex` on your `PATH`, authenticated — see
  [Which CLI does the classifying](#which-cli-does-the-classifying). Used only to decide
  whether a message is a bare greeting; reusing a CLI you already have signed in is why no
  separate LLM API key is needed.

Webex itself is reached over its REST API through the official
[webexpythonsdk](https://pypi.org/project/webexpythonsdk/), not through an agent or
an MCP server — a scheduled job that posts under your name needs a data path you can
reason about. The SDK has no OAuth token lifecycle of its own, so sign-in, storage
and refresh are handled by this tool.

## Development

```sh
uv sync
uv run pytest
uv run mypy
uv run ruff check
uv run ruff format
pre-commit install
```

Dependencies live in `pyproject.toml` and are pinned in `uv.lock`. There is no
`requirements.txt`. Adding a runtime dependency needs a one-line justification in
the pull request, per Article II.11.

## Releasing

Releases are automatic. Push to `main` and commitizen reads the commit subjects since the
last tag, works out the version, tags it, and hands off to a workflow that publishes to PyPI
via [trusted publishing](https://docs.pypi.org/trusted-publishers/) — no API token is stored
anywhere.

Which means the commit subject is the release decision:

| Subject | Result |
| --- | --- |
| `fix: …` | patch release |
| `feat: …` | minor release |
| `docs:`, `chore:`, `refactor:`, `test:`, `ci:` | no release |

A commit with no conventional type publishes nothing, which is the point: a README fix
should not ship a version.

To release by hand instead — or to re-run a failed publish:

```sh
git tag v0.3.0 && git push origin v0.3.0
```

Three gates before anything reaches PyPI:

1. The same checks as CI, run again — a tag can be pushed to a commit CI never saw.
2. The tag must match `version` in `pyproject.toml`. PyPI will not let a wrong version be
   replaced, so this is the one mistake with no undo.
3. The `pypi` GitHub environment. Add a required reviewer to it and you are asked to approve
   before the release goes out.

## Status

Released on PyPI. Implemented so far:

| Command | State |
| --- | --- |
| `webex-nohello auth login` | working |
| `webex-nohello auth status` | working |
| `webex-nohello auth refresh` | working |
| `webex-nohello auth logout` | working |
| `webex-nohello run` | working, including `--commit` |
| `webex-nohello review` | working |
| `webex-nohello doctor` | working |
| `webex-nohello schedule …` | working |
| `webex-nohello config …` | working |
