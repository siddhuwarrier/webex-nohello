# webex-nohello — Project Constitution

This document governs the project. It is normative: where an implementation
choice conflicts with an article below, the article wins or the article gets
amended (Article XIV). It is not a design doc and not a tutorial.

**MUST**, **MUST NOT**, **SHOULD** and **MAY** carry their RFC 2119 meanings.

---

## Preamble — what this is

`webex-nohello` is a command-line application that periodically inspects the
operator's recent Webex direct messages, identifies those that are a bare
greeting carrying no actionable content, and posts a polite threaded reply
pointing at <https://nohello.net/en/> asking for the actual request.

It exists because the operator works asynchronously across timezones, where a
lone "hi" costs a full round-trip before any work can begin.

**The single most important fact about this program:** the reply is sent *as the
operator*, from the operator's own Webex account, under the operator's own OAuth
grant. It is not a bot and cannot be mistaken for one. Every colleague who
receives a misfire sees the operator being curt to them, and the reply cannot be
recalled. Every rail in Article VIII that looks over-cautious is downstream of
that one fact.

---

## Article I — Scope

1. The program MUST only ever consider spaces of Webex type `direct`. Group
   spaces are permanently out of scope, not merely unimplemented.
2. The program MUST NOT reply to messages authored by the operator.
3. The program MUST NOT reply to messages authored by a bot or app user.
4. A reply MUST be a threaded reply to the offending message, with `parentId`
   set to that message's id — never a new top-level message.
5. The program MUST send at most one reply per offending message, ever.
6. Explicitly out of scope: group spaces, meetings, presence, attachments,
   summarising threads, attempting to answer the question, and every Webex write
   operation other than posting the single threaded reply.

## Article II — Stack and enforced tooling

1. Language: Python, `requires-python = ">=3.12"`. 3.12 is the floor because
   PEP 695 type syntax is assumed throughout.
2. Environment and packaging: **uv**. `uv.lock` MUST be committed. The project
   MUST build a wheel exposing a console entrypoint named `webex-nohello`.
   `poetry` MUST NOT appear anywhere in this project.
3. CLI framework: **Typer**, serving the command pattern of Article III.
4. Webex access: **webexpythonsdk**, the official SDK. Credential storage:
   **keyring**. Data boundaries: **Pydantic v2**. There is no separate HTTP
   client; the SDK carries its own transport.
5. Static typing: **mypy in strict mode** is a merge gate. Configuration MUST
   include at least:
   ```toml
   [tool.mypy]
   strict = true
   warn_unreachable = true
   warn_unused_ignores = true
   plugins = ["pydantic.mypy"]
   ```
   Every function signature, tests included, MUST be fully annotated in code this
   project owns. `disallow_any_explicit` MUST NOT be set: it flags every
   `class X(BaseModel)` line, which would mean silencing it in every model
   module. Third-party packages need not be typed — see II.12.
6. A `# type: ignore` MUST carry a specific error code and a same-line reason,
   e.g. `# type: ignore[arg-type]  # keyring stub omits this overload`. A bare
   `type: ignore` is a review failure.
7. Lint and format: **ruff**, for both (`ruff check`, `ruff format`). Black is
   not used. Formatting is never discussed in review — if ruff accepts it, it is
   correct.
8. The ruff rule set MUST include the complexity rules backing Article IV:
   `C901`, `PLR0911`, `PLR0912`, `PLR0913`, `PLR0915`. Rules MUST be enumerated
   explicitly in `pyproject.toml`; `select = ["ALL"]` is forbidden because its
   meaning changes silently on upgrade.
9. Tests: **pytest**, per Article XII.
10. All of the above MUST run in **pre-commit** and again in CI. CI MUST fail on
    any of `ruff check`, `ruff format --check`, `mypy`, `pytest`. A commit that
    passes locally but reds CI is a bug in the pre-commit config and MUST be
    fixed there, not worked around.
11. Adding a runtime dependency requires a one-line justification in the PR. The
    list is expected to stay at roughly typer, webexpythonsdk, pydantic, keyring
    and platformdirs. There is no LLM SDK — see Article III.2.
12. A dependency need not ship type information. `webexpythonsdk` ships no
    `py.typed` and its signatures are unannotated; this is accepted rather than
    worked around, because reimplementing it by hand would cost far more than the
    typing buys. The containment rule is Article V.1: values crossing out of an
    untyped dependency are validated into a Pydantic model at the service
    boundary, so `Any` stops there and never reaches a command.

## Article III — Architecture

Two external systems, reached two different ways, for one reason: **inference is
used only where inference is genuinely required.**

1. **Webex is reached through the official SDK, over its REST API.** The program
   MUST NOT shell out to an agent CLI, and MUST NOT act as an MCP client, for any
   Webex read or write. Rationale: a scheduled job that posts as the operator
   needs a deterministic, inspectable, mockable data path. Putting a language
   model in that path makes "why was this DM missed?" unanswerable, which
   violates Article IV.
2. **Classification is delegated to a locally installed agent CLI** — `claude`
   or `codex` — invoked as a subprocess. Rationale: it reuses the operator's
   existing subscription authentication, so the program needs no LLM API key.
3. The two agent CLIs MUST sit behind one narrow `Protocol` — a
   `ClassifierDriver` — with a module per CLI. Nothing outside those modules may
   know which CLI is in use, or contain the literal string `claude` or `codex`.
4. Command pattern: one Typer command per module under `commands/`. A command
   module validates input, calls into internal packages, and renders output. It
   MUST contain no Webex logic, no prompt text, and no business rules.
5. Prompt text MUST live in a dedicated, reviewable prompt module or template
   file. It MUST NOT be inline in control flow or assembled by concatenation
   spread across functions.
6. Layout — indicative in its naming, binding in its separation of concerns:
   ```
   src/webex_nohello/
     cli.py                      # Typer app assembly, and errors -> exit codes
     ui.py                       # every line this program prints
     clock.py                    # injectable time
     paths.py                    # where every file it owns lives
     scopes.py                   # the scope set and its justification
     models/                     # data only, one class per module (Article IV.8)
       auth/                     # credentials, tokens, sign-in value objects
       webex/                    # the shapes Webex returns
       run/                      # marks, candidates, scan results
       classify/                 # verdicts and assessments
       reply/                    # dispatch outcomes and withheld reasons
       review/                   # evaluation-only types
       audit/                    # the reply record
       config/                   # operator settings
       errors/                   # the exception hierarchy
     services/                   # behaviour
       auth.py                   # inspect / require / login -- the entry point
       oauth.py                  # authorize URL, state, loopback, token exchange
       credentials.py            # keychain persistence
       webex.py                  # SDK wrapper; the only WebexAPI reference
       scan.py                   # Article VI: what counts as unread
       classify.py               # threshold, retry, pre-filter
       classification_prompt.py  # the prompt, as reviewable prose
       agent_cli.py              # the claude driver
       dispatch.py               # every rail in Article X, and the send
       audit.py                  # append-only log; the source of cooldowns
       lock.py                   # run lock and kill switch
       reply_template.py         # the reply text and its placeholders
       state.py                  # high-water marks, atomically
       review.py                 # collecting history to judge the classifier
       config.py                 # loading and scaffolding settings
     commands/                   # one module per command group; input, call, render
   ```
7. Every I/O boundary — HTTP, subprocess, filesystem, clock — MUST be injectable,
   so Article XII can test with no network and no agent CLI installed.

## Article IV — Readability

The requirement is that an engineer can sit down and manually debug what this
program did. That is a hard requirement, not an aspiration.

1. A module SHOULD be under 200 lines and MUST be under 300. A function SHOULD be
   under 40 lines. Function-level limits are enforced by ruff per II.8; file
   length by a local pre-commit hook.
2. **Cohesion beats brevity.** A module limit is a smell detector, not a
   splitting instruction. Splitting one concern across many modules to satisfy a
   line count makes a package unreadable, which is a worse failure than a long
   file: a reader who does not know where to start has been failed completely.
   Outside `models/`, a package holding more than about six modules for a single
   concern MUST be reconsidered.
3. **Comments explain why, never what.** A comment restating the code MUST be
   deleted. A comment recording a non-obvious constraint, an upstream quirk, or
   why a tempting simpler approach fails MUST be kept.
4. Specifically forbidden, being the recognisable texture of machine-written
   code:
   - section-divider banner comments,
   - `# Step 1:` or `# Now we ...` narration of the following line,
   - docstrings that merely re-spell the function name and its parameters,
   - a docstring on every function regardless of whether it adds anything,
   - `try/except` that catches, logs, and continues without a stated reason,
   - emoji or exclamation marks in code, log output, or exception text.
5. Names carry the meaning. `messages` is a poor name for "the last ten messages
   in this DM, oldest first" — name it that.
6. A method or function returning `bool` MUST be named with an `is_` prefix, e.g.
   `is_access_token_usable`. A predicate that would read awkwardly under that rule
   is usually not really a predicate: return the data instead and let the caller
   test it, as `missing_scopes()` does rather than `is_scope_set_sufficient()`.
7. Two names that differ by one letter and mean roughly the same thing are a
   defect, not a style preference. `AuthState` beside `AuthStatus` was renamed to
   `CredentialState` and `CredentialReport` for exactly this reason.
8. Under `models/`, one class per module, the module named after the class in
   snake_case. A model module holds that class, the constants it owns, and nothing
   else. This is not the usual Python convention; it is chosen so that finding a
   type is a filesystem operation rather than a search.
9. Errors MUST be specific and actionable. `raise RuntimeError("failed")` is a
   review failure; the message MUST say what failed, on what input, and what the
   operator can do about it.
10. Prefer boring, explicit code. Metaclasses, behaviour-rewriting decorators,
    dynamic attribute access, and clever nested comprehensions lose to code that
    reads top to bottom.

## Article V — Typed boundaries

1. Every value entering the program from outside — config file, SDK return values,
   agent CLI stdout, keyring contents — MUST be parsed into a Pydantic model
   before use. Neither a raw `dict[str, Any]` nor an SDK object MUST escape the
   module that received it. For the SDK this means validating `.json_data` at the
   service boundary; this is the mechanism that contains the untyped dependency
   accepted in II.12.
2. A secret that is persisted MUST be typed `StoredSecret`, not `SecretStr`. A bare
   `SecretStr` serialises to `**********`, which stores nothing usable and fails
   only later, at the next API call.
3. Config models MUST set `extra="forbid"`, so an operator's typo fails loudly.
   Webex response models MUST tolerate unknown fields, because upstream adds them.
4. Failing to parse agent output is a normal, expected condition. It MUST be
   handled as a bounded retry and then a skip of that one candidate with a logged
   reason — never a crash of the run, and never a guess at the verdict.

## Article VI — Determining what is unread

The Webex public API exposes **no unread count and no last-seen pointer**, and
has **no mark-as-read operation**. This is a property of the platform, not of any
particular client library. Therefore:

1. "Unread" MUST be defined locally, never fetched. The program MUST maintain a
   durable per-space high-water mark: the id and timestamp of the last message it
   has examined in that DM.
2. A candidate is a message in a `direct` space that is newer than that space's
   high-water mark, is not authored by the operator, and is the most recent
   message in the space.
3. If the operator has themselves posted in that space after the candidate, the
   candidate MUST be skipped — the operator has already engaged, whatever the
   classifier thinks.
4. On the first ever run the program MUST NOT reply to anything. It MUST
   initialise high-water marks and report what it would have considered.
   Retroactively replying to a year of history is the worst failure available to
   this program, and MUST be structurally impossible rather than merely unlikely.
5. The high-water mark MUST advance for every candidate examined, including those
   judged actionable and deliberately left alone, so no message is examined twice.
6. High-water marks are the program's most safety-critical state. Their storage
   MUST be atomic per Article X.8, and their format versioned. An unreadable state
   file MUST fail loudly rather than be treated as empty, because "empty" means
   "never run" and puts the whole history back in scope.
7. **The program MUST NOT enumerate the operator's entire message history, ever —
   not even once.** There is no unread endpoint, so a naive scan costs one request
   per space on every run, and an operator with hundreds of one-to-one spaces makes
   that unusably slow. Two bounds are therefore mandatory:
   - A scan MUST stop as soon as it reaches a space whose `lastActivity` predates the
     highest activity recorded on the previous scan. `rooms.list` is ordered by that
     field descending, so every remaining space is older still. On a polling schedule
     this turns hundreds of requests into a handful.
   - **The space list MUST be consumed lazily**, and the iterator abandoned at the
     cutoff. Stopping early is worth nothing if every page has already been fetched to
     build a list first — that mistake alone cost over a minute per run.
   - A scan with no recorded position MUST bound itself to a lookback window
     (7 days by default) rather than reading everything. A greeting older than that
     has already been ignored, and replying to it would be stranger than silence.
8. **A space MUST cost exactly one request**, fetching Article IX.2's context up
   front and taking the newest message from it. Payload is not the constraint;
   request count is. An earlier version read one message to decide and the rest
   later, which halved the bytes and doubled the requests for every candidate.
   Nothing in this program may consume a paginating generator without bounding it —
   `messages.list(max=1)` drained to exhaustion walks a whole conversation history
   one request per message, which measured 15 seconds for a single space.
9. The recorded position MUST be a Webex-supplied timestamp, never a local clock
   reading, so that clock skew between this machine and Webex cannot cause a message
   to be skipped. It MUST NOT move backwards: a quiet run must not widen the window
   the next run has to read.
10. A consequence of VI.2 worth stating outright, because it is load-bearing and easy to
    forget: **a greeting overtaken by any later message is never replied to.** "hi" at
    10:30 followed by a real question at 10:34 produces no reply from a run at 10:35 —
    the question is the newest message, and the greeting has become context. Anything
    that evaluates this program's behaviour MUST account for it, or it will report
    misfires that could not occur.

## Article VII — The Webex client

1. The SDK exposes twenty-two endpoint families. This program is permitted to
   touch these five calls and no others:
   | Purpose | SDK call |
   |---|---|
   | Identify the operator | `people.me()` |
   | Enumerate DM spaces | `rooms.list(type="direct", sortBy="lastactivity")` |
   | Read a DM's recent messages | `messages.list(roomId=…, max=…)` |
   | Resolve an author, to detect bots | `people.get(personId)` |
   | Post the reply | `messages.create(roomId=…, parentId=…, markdown=…)` |
   Any addition to this table is an amendment under Article XIV. The restriction
   is not enforced by the SDK, so it is enforced by review: `services/webex.py` is
   the only module permitted to hold a `WebexAPI` reference.
2. Bot detection (I.3) MUST use the `type` field of the Person object, treating
   anything other than `person` as ineligible for a reply. Fail closed: an
   author whose type cannot be resolved MUST NOT be replied to.
3. `people.get(personId)` results MUST be cached for the life of a run, and
   SHOULD be cached across runs, so that polling does not re-resolve the same
   colleagues indefinitely.
4. Pagination is the SDK's generator, which follows `Link: rel="next"`. The
   program MUST NOT assume a page size, and MUST NOT slice a generator in a way
   that leaves later pages unread when a bound is needed — pass the bound to the
   SDK instead.
5. HTTP 429 MUST be honoured by waiting the `Retry-After` interval, via the SDK's
   `wait_on_rate_limit=True`. The program MUST NOT retry a `messages.create()`
   that may have succeeded — see X.7.
6. Every request MUST carry an explicit timeout, via `single_request_timeout`. The
   SDK's own default is 60 seconds, which is too long for a poll; a hung poll
   under launchd is invisible to the operator.
7. The program MUST NOT log tokens, and MUST keep the access token in a
   `SecretStr` so that an accidental repr, log line or traceback cannot disclose
   it. No error message may be constructed from a request header.

## Article VIII — OAuth and credentials

1. Authentication MUST be a Webex **Integration** performing the authorization
   code flow once, then refreshing thereafter. The rejected alternatives, and why:
   - a personal access token from developer.webex.com expires in 12 hours and is
     unusable under a scheduler;
   - a **bot** token never expires but cannot see the operator's 1:1 spaces at
     all, and would post as the bot rather than the operator, which defeats the
     purpose of the program.
2. `auth login` MUST perform the flow against a loopback redirect on a
   configurable port, and MUST verify the `state` parameter at the point the
   redirect is received, before any token exchange.

   **PKCE is not used.** This article previously required it. The SDK's
   `AccessTokensAPI.get()` accepts no `code_verifier`, so using the SDK for the
   token exchange and using PKCE are mutually exclusive. The SDK was chosen; PKCE
   was dropped. What is lost is defence against interception of the authorization
   code on the loopback hop; what remains is the `state` check, the client secret,
   and the fact that the redirect never leaves the machine. Reinstating PKCE means
   hand-rolling the token exchange again.
3. Scopes MUST be the minimum sufficient set and MUST be documented in the
   README alongside what each one is for. The program MUST NOT request a write
   scope beyond posting messages.
4. Refresh and access tokens, and the client secret, MUST be stored via `keyring`
   in the OS credential store, as one record so that a partial write cannot leave
   an access token without its refresh token. They MUST NOT be written to a config
   file, a dotfile, a log, or an environment variable. In particular the program
   MUST NOT use the SDK's `WEBEX_ACCESS_TOKEN` environment variable convention.
5. The access token MUST be refreshed proactively when near expiry rather than
   reactively on a 401, so that a scheduled run does not fail its first call.
6. `doctor` MUST report token validity and warn before the refresh token's
   expiry window lapses. Because Webex extends the refresh token on use, a
   regularly scheduled job should never need re-authentication; if `doctor`
   reports otherwise, that is a defect worth investigating rather than papering
   over with a re-login.
7. `auth logout` MUST revoke where possible and MUST delete local credentials
   unconditionally.
8. A manual `auth refresh` MUST exist, and MUST be documented as a diagnostic rather
   than part of normal operation — VIII.5 already refreshes without being asked. It
   earns its place for two reasons: the refresh path would otherwise go unexercised
   for a fortnight after every change to it, and it extends the refresh token's own
   window, which a proactive refresh will not do while the access token is healthy.
   It MUST report the expiry before and after, and MUST verify the new token per
   XII.5; a refresh that reports success without proving the result is the same
   mistake `auth status` once made.

## Article IX — Classification

1. Detection MUST be done by LLM inference, not by matching a list of greeting
   strings. A keyword list MAY exist solely as a cheap pre-filter to skip paying
   for inference on obviously substantial messages, and MUST NOT be capable of
   triggering a reply by itself.
2. The classifier MUST receive conversational context: the last N messages of
   the DM (default 10), each with author and timestamp, oldest first.
3. The classifier MUST run on a small, cheap model — Claude Haiku by default,
   the equivalent small model for `codex`. The model identifier MUST be
   configurable.
4. The classifier MUST be invoked with **no tools available**. It is text in,
   JSON out. A classifier that can reach Webex is a defect, not a feature.
5. The verdict MUST be schema-validated and MUST carry at least a verdict enum,
   a confidence in `[0, 1]`, and a one-sentence reason. The reason MUST be
   written to the audit log so a misfire can be explained after the fact.
6. The decisive rule, which the prompt MUST encode and the suite MUST pin in both
   directions: **does the message depend on the content of what preceded it?**
   - "lol", "thanks", "nice", "ok" do. They are meaningless without knowing what
     they respond to, so they are content-bearing in context and MUST NOT be
     replied to.
   - A greeting does not. "Hi <name>" reads identically in isolation as it does
     after twenty messages, because it answers nothing and leaves the recipient
     nothing to act on. It is therefore replyable **wherever it appears** —
     mid-thread, after a gap, or in answer to the operator calling the sender's name.

   This article previously read "a message merits a reply only if it is disconnected
   from any existing exchange", which was wrong and demonstrably so: two colleagues
   each sent a plain "Hi Siddhu" and both were let through — one classified
   `continues_conversation` because it followed a ping, the other correct but hedged to
   0.67 and so below the threshold. Connectedness is not the test. Dependence on
   content is.
7. The prompt MUST NOT tell the model to lower its confidence on ambiguity in general
   terms. Doing so depressed confidence across every case including unambiguous ones,
   and cost more in missed greetings than it bought in safety. Conservatism belongs in
   the specific instructions — what counts as a greeting, and what a reply costs — and
   in the threshold of IX.8. Not in a blanket instruction to hedge.
8. A reply MUST require confidence at or above a configurable threshold
   (default 0.8). Ambiguity resolves to silence.
9. Message text MUST be logged at debug level only. The audit log MUST retain no
   more than a short truncated excerpt of any message body.
10. The exact prompt and the exact command line used MUST be printable by a
   `--explain`-style flag, so an engineer can reproduce a verdict by hand. This
   is the specific mechanism by which Article IV applies to inference.

## Article X — Safety rails

Downstream of the Preamble: a misfire is socially expensive and cannot be undone.

1. `run` MUST default to a dry run. Posting MUST require an explicit `--commit`.
   No config setting may make posting the default for an interactive run.
2. A dry run MUST print exactly what it would post, to whom, and why, and MUST
   NOT advance high-water marks.
3. A per-sender cooldown MUST be enforced (default 30 days). One person receives
   this reply at most once per window, however many greetings they send.
4. An opt-out list MUST be supported. An opt-in-only mode, where nobody is
   replied to unless listed, MUST also be supported and is the recommended way
   to start.
5. A maximum number of replies per run MUST be enforced (default 5). A run that
   wants to exceed it MUST post nothing beyond the cap and MUST report loudly:
   wanting to send twenty replies means something is wrong, and the correct
   response is to stop, not to proceed.
6. A single global kill switch MUST exist, and MUST be honoured by scheduled runs
   without the operator having to touch the schedule.
7. Every send MUST be recorded in an append-only audit log — timestamp, space,
   recipient, message id replied to, verdict, confidence, reason — written such
   that a crash between intent and send cannot produce a duplicate on the next
   run. Where a send's outcome is genuinely unknown, the program MUST record it
   as sent and move on. Silence is a better failure than a double reply.
8. All state writes MUST be atomic (write to a temporary file, then rename). A
   half-written state file MUST NOT be able to cause a re-reply.
9. A run lock MUST prevent concurrent runs, and MUST be robust to a stale lock
   left by a killed process.

## Article XI — Configuration

1. Config lives in one operator-owned TOML file under the platform config
   directory. The program MUST work with no config file by using safe defaults,
   except that it MUST NOT invent an opt-in list.
2. `config init` MUST write a commented file whose defaults are safe.
3. The reply text MUST be operator-configurable as a template file. The shipped
   default is Appendix B.
4. Template rendering MUST fail loudly on an unknown placeholder rather than
   render it blank or literal.
5. Config MUST be validated at startup, and the program MUST refuse to run with a
   message naming the offending key, rather than starting with a silently coerced
   value.

## Article XII — Preflight

1. `doctor` MUST verify, and report on each independently:
   - an agent CLI (`claude` or `codex`) is on PATH and authenticated,
   - stored Webex credentials exist and are valid, with expiry reported,
   - a trivial Webex read (`people.me()`) actually succeeds,
   - config parses and the reply template renders,
   - the state directory is writable and its format version is understood,
   - the kill switch is not engaged.
2. Every failed check MUST print specific remediation, not a generic error. A
   missing or unauthenticated agent CLI MUST print the install and login
   commands. Missing Webex credentials MUST point at `auth login` and at the
   Integration registration steps.
3. `run` MUST perform the same preflight and MUST exit non-zero without posting
   if it fails.
4. `doctor` MUST be safe to run at any time and MUST NOT write program state — no
   high-water marks, no cooldowns, no audit entries. It MAY refresh and persist
   tokens: that is credential maintenance rather than state mutation, and because
   Webex extends the refresh token on use, doing so makes running preflight
   regularly actively useful.
5. **Any command that tells the operator their credentials are usable MUST prove it
   with a live call.** Expiry timestamps and a scope string are not evidence: a
   record can satisfy every local check while holding a token Webex refuses. This
   article exists because `auth status` once reported READY over three masked
   secrets, and a passing live sign-in did not reveal it. `CredentialState.REJECTED`
   is the outcome reserved for that case.

## Article XIII — Scheduling, testing, distribution

**Scheduling**

1. `schedule install`, `schedule uninstall` and `schedule status` MUST be
   provided, generating a launchd agent on macOS and a crontab entry on Linux.
2. Generated entries MUST invoke the entrypoint by absolute path and MUST NOT
   depend on an inherited `PATH`, an activated virtualenv, or the operator's
   shell profile. This is the most common way scheduled jobs fail silently.

   **This extends to every subprocess the run makes, not just the entrypoint.**
   Naming our own executable absolutely was not enough: the run shells out to the
   classifier CLI, `launchctl` leaves `PATH` unset for a user agent, and the minimal
   default excludes `~/.local/bin` where both `claude` and a `uv tool install` land.
   The result was a schedule that installed cleanly, passed preflight, worked
   perfectly from a shell, and could not classify anything on a timer. The generated
   artefact MUST therefore pin a `PATH` resolved at install time.

   That `PATH` MUST NOT be built from symlink-resolved paths. `which claude` returns
   `~/.local/bin/claude`, which is a symlink into a version-specific directory;
   resolving it pins today's version and breaks silently the next time the tool
   updates itself.
3. Preflight passing in the operator's shell does not mean it will pass on a timer,
   because the environments differ in exactly the way that breaks things. `schedule
   install` MUST therefore run the command once itself, as a subprocess with the
   pinned environment, and report the result. A scheduled-only failure discovered at
   install time costs a minute; discovered later it costs however long until someone
   reads the log.
4. `schedule install` arms something that posts for real. It MUST require
   explicit confirmation, MUST display the exact command and interval it is
   about to install, and MUST refuse if preflight fails.
5. The generated command MUST contain `--commit` explicitly and visibly, so that
   reading the plist or crontab tells the truth about what it does.
6. A laptop waking to many missed intervals MUST NOT produce a burst of replies;
   X.5 and X.9 together MUST make this safe.
7. `schedule uninstall` MUST be complete and idempotent.

**Testing**

8. The suite MUST run offline, with no network and no agent CLI installed.
9. The Webex client MUST be tested against recorded HTTP fixtures, including
   429s, pagination, and truncated bodies.
10. Classifier drivers MUST be tested against recorded CLI stdout fixtures,
   including malformed and truncated output.
11. The classification boundary MUST have a table-driven suite of realistic
    conversations pinning IX.6 in both directions.
12. **Every rail in Article X MUST have a test that fails if the rail is
    removed.** These matter more than any other test in the project.
13. The generated launchd plist and crontab line MUST be asserted verbatim.
14. Tests MUST NOT be written to satisfy coverage. There is no coverage target.

**Distribution**

15. Published to PyPI, installed with `uv tool install webex-nohello` or
    `pipx install webex-nohello`, which yields an isolated environment and a
    stable shim on `PATH` — the thing that makes XIII.2 achievable.
16. Release MUST be one automated command producing a tagged, versioned
    artifact. Versioning is SemVer.
17. The README MUST take an operator from zero to a working schedule, and MUST
    state plainly and early that replies are sent from the operator's own
    account.

## Article XIV — Amendment

1. Amend by editing this file in a PR that states which article changed and why.
2. Discovering that an article is wrong or impossible is a normal outcome. Amend
   it; do not quietly implement around it.
3. Appendix A is a living record. Anything learned about the Webex platform that
   bears on a decision above MUST be recorded there in the same PR as the code
   that learned it.

---

## Appendix A — Constraints, decisions, and open questions

**Platform constraints driving the design**

| Constraint | Consequence |
|---|---|
| Webex exposes no unread count and no last-seen pointer | Article VI: track high-water marks locally |
| Webex has no mark-as-read operation | Open question 1 |
| Personal access tokens expire in 12 hours | Article VIII.1: unusable under a scheduler |
| Bots cannot see a user's 1:1 spaces, and post as themselves | Article VIII.1: bot tokens cannot implement this program |
| `messages.list` requires a `roomId`; there is no cross-space message search | Enumerate DM spaces, then poll per space; watch the request count |
| Message list responses are newest-first | Reverse before handing context to the classifier (IX.2) |

Verified against `webexpythonsdk` 2.0.6:

| Finding | Consequence |
|---|---|
| `restsession` contains no refresh, expiry or reauth logic; the SDK never refreshes a token | The whole of `services/auth.py` exists because of this. The SDK's auth model is "you hand me a token that already works" |
| `WebexAPI(client_id=…, oauth_code=…)` exchanges a code but keeps only `.access_token`, discarding the refresh token | Unusable for a scheduled job; the exchange goes through `AccessTokensAPI` directly instead |
| `AccessTokensAPI.get()` takes no `code_verifier` | PKCE dropped — Article VIII.2 |
| `AccessTokensAPI.__init__` calls `validate_base_url`, which raises on `None` | Must pass the SDK's own `DEFAULT_BASE_URL`. Caught only because a review question prompted verification |
| No `py.typed`; signatures unannotated; runtime `check_type` instead | Accepted — Article II.12, contained by Article V.1 |
| `single_request_timeout` must be an `int`, and defaults to 60s | Set explicitly per Article VII.6 |
| `rooms.list()` returns a **lazy generator** that follows `Link: rel="next"` as it is consumed | Materialising it (`[r for r in listed]`) fetches every page before any work begins. Doing so made a real run hang for over a minute and rendered the Article VI.7 cutoff useless. List endpoints MUST be consumed lazily and abandoned early |
| The SDK's `max` on a list call is the **page size**, not a total cap | It does not limit how many results come back. A total cap MUST be applied by the caller, e.g. `itertools.islice`. This bit twice: `rooms.list` (a minute per run) and `messages.list(max=1)` (15 seconds per space, one request per message in the history). Every list call in this program is now bounded by `islice` at the call site |

Verified against live Webex by a completed sign-in on 2026-08-19:

| Finding | Consequence |
|---|---|
| Access token lifetime ~14 days; refresh token ~90 days | The 12-hour `REFRESH_LEEWAY` is proportionate |
| The four scopes in `scopes.py` are sufficient and are all accepted | Resolves open question 2 |
| Webex **does** echo `scope` on the token response, and reorders it relative to the request | Resolves open question 5: the fallback in `TokenResponse.to_token_set` never fires in practice, so a partial grant is detectable as `MISSING_SCOPES` |
| The token endpoint is `idbroker.webex.com/idb/oauth2/v1/access_token`, reached by redirect from the SDK's base URL | The `AccessTokensAPI` path works; a bogus code returns `invalid_grant`, a bad client returns `invalid_client` |
| `invalid_scope` is returned only after authentication, by idbroker | It cannot be diagnosed by probing the authorize endpoint unauthenticated; it means the requested scopes are not a subset of those registered on the integration |

Measured about the `claude` CLI as a classifier, 2026-08-19:

| Finding | Consequence |
|---|---|
| `claude -p` waits **three seconds** for piped stdin before giving up | stdin MUST be closed (`stdin=DEVNULL`). This was most of the wall clock on a short prompt |
| A default `-p` call sends ~**25,000** tokens of agent harness: system prompt, tool definitions, and the operator's MCP servers | `--system-prompt` (replaces Claude Code's own) plus `--strict-mcp-config` cuts it to ~3,600. Both are required, and the system prompt MUST stay byte-stable so the cached prefix is reused |
| `--allowedTools ""` is accepted and satisfies Article IX.4 | With `--strict-mcp-config` it also means the classifier cannot reach Webex even in principle |
| `--model haiku` resolves to `claude-haiku-4-5` | Article IX.3's "small, cheap model" is available by alias |
| `--output-format json` returns an envelope with `result`, and the model fences its JSON anyway | Parse the envelope, then strip a code fence before validating. `is_error` must be checked |
| Cost and latency: roughly **$0.005** and **4.6–9.8 seconds** per classification | Tolerable for a handful of candidates per run. It is the reason for Article IX.1's pre-filter, and it would not survive classifying every message |

Pydantic behaviour that silently broke persistence, found by the test suite:

| Finding | Consequence |
|---|---|
| `model_dump_json()` serialises a bare `SecretStr` as `**********` | Every secret written to the keychain was ten asterisks. `auth status` still reported READY, because it reads only expiry timestamps and scopes. Fixed by `models/serialisable_secret.py`: `StoredSecret` keeps the `repr` masking Article VII.7 needs, and adds a `PlainSerializer` so JSON carries the real value. **Any new persisted secret field MUST use `StoredSecret`, not `SecretStr`.** |

**Recorded deviations from the original requirements**

The original brief specified that the program drive the Webex MCP server at
<https://github.com/siddhuwarrier/webex-mcp-plugin> through `claude` or `codex`.
This was changed during stack selection, deliberately:

- *Requirement 3 (require the webex MCP server) is withdrawn.* Webex is reached
  over REST, per Article III.1, for determinism and debuggability. The plugin is no
  longer a prerequisite and MUST NOT be listed as one.
- *Requirement 2 (require `claude` or `codex`) is retained*, narrowed to
  classification only, per Article III.2. Keeping the CLI as the inference path
  means the operator needs no LLM API key.

The cost of this trade is accepted knowingly: the program now holds a Webex
refresh token, which Article VIII exists to govern.

Subsequent amendments, in order:

1. *Hand-rolled REST replaced by `webexpythonsdk`.* Chosen for the pagination and
   rate-limit handling `run` will need, over writing that by hand. Cost: an
   untyped dependency (II.12) and the loss of PKCE (VIII.2). `httpx` left the
   dependency list entirely.
2. *Models moved to a package, one class per module* (IV.8), and the exception
   hierarchy moved with them. Chosen for navigability by a reader coming from
   Java. This is not idiomatic Python and is a deliberate house style.
3. *Module length limits raised from 150/250 to 200/300, and IV.2 added.* The
   original limit had fragmented the auth code across ten modules, which made the
   package unreadable — the exact failure Article IV exists to prevent.
4. *`disallow_any_explicit` removed* (II.5). It flagged every pydantic model
   declaration. The pydantic mypy plugin was added in its place.

**Open questions, to be resolved during implementation and recorded here**

1. **The default reply text claims the message "has been marked as read".** No
   mark-as-read API exists. The claim is expected to hold anyway, on the
   assumption that posting into a space as the authenticated user advances that
   user's own read pointer in Webex clients. This MUST be verified against a real
   client before release. If it does not hold, the default text in Appendix B
   MUST be corrected rather than shipped as a falsehood.
3. ~~Whether enumerating all `direct` spaces per run is acceptable at the operator's
   scale.~~ **Resolved, 2026-08-19: it is not.** The first implementation read every
   space on every run and was unusably slow against a real account. Both bounds in
   Article VI.7 exist because of that, and the cost of getting it wrong is paid by
   whoever runs it, so a change that loosens either bound needs measuring first.
4. Whether `codex` can be driven to produce reliable schema-valid JSON with no
   tools available; if not, Article III.3 may need to admit a driver that is
   supported on a best-effort basis only.
5. Whether the refresh path works in practice, and whether Webex rotates the refresh
   token on use. The exchange is proven; `AccessTokensAPI.refresh()` has not yet run
   against a live grant. `auth refresh` exists to settle both without waiting a
   fortnight — record the answer here once it has been run.

## Appendix B — Default reply text

The shipped default for the template of Article XI.3, verbatim:

> 👋 Automated reply — apologies, this message won't reach me as it stands.
>
> I'm sorry to answer with automation rather than a proper reply. Your message
> looks like a greeting on its own, so it has been marked as read automatically
> and won't stay in my unread list. I won't see it unless you follow up with what
> you actually need.
>
> I really don't mean that as a brush-off. I work asynchronously across
> timezones, and it genuinely helps if a first message carries enough for me to
> act on: the question, any relevant context, and any links. Then I can give you
> a proper answer the first time I read it, rather than the two of us trading
> hellos.
>
> The reasoning, put rather better than I can manage: https://nohello.net/en/
>
> Do send the details whenever suits you and I'll pick it up from there — and
> thank you for bearing with the automation.

Subject to open question 1.
