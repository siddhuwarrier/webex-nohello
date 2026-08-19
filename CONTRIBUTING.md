# Contributing

Thanks for looking. Before anything else: **read [CONSTITUTION.md](CONSTITUTION.md).**

That is not a formality. This program posts messages to real colleagues from the
maintainer's own Webex account, and those messages cannot be unsent. The constitution is
where the reasoning behind every safety rail is written down, including several that exist
because something went wrong in testing. A change that looks like a simplification is
usually removing a rail whose reason is not visible from the code alone.

## Getting set up

```sh
git clone https://github.com/siddhuwarrier/webex-nohello
cd webex-nohello
uv sync
uv run pytest
```

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.12+.
You do **not** need Webex credentials or `claude` installed to run the test suite — it is
offline by design.

```sh
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

The second hook type checks your commit *messages*, which now decide the version — see
[Commit messages decide the release](#commit-messages-decide-the-release).

## The gates

CI runs exactly these, and they must all pass:

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

Roughly: `mypy` in strict mode, `ruff` for lint and format, and a test suite that runs with
no network. If a commit passes locally and fails CI, that is a bug in
`.pre-commit-config.yaml` — fix it there rather than working around it.

### The live tests

A separate suite calls a real model to check the classifier's decision boundary. It costs
money and takes a couple of minutes, so it is excluded by default and **not** run in CI:

```sh
uv run pytest -m live
```

Run it if you touch `services/classification_prompt.py`. The offline tests cover parsing
and thresholds; nothing else checks that the prompt still means what it says. Several of
those cases are real misfires from real conversations, kept verbatim — they are worth more
than any case anyone invented.

## House style

The constitution is authoritative; these are the parts people trip over.

- **Comments explain why, never what.** A comment restating the code gets deleted. A comment
  recording a non-obvious constraint, an upstream quirk, or why a tempting simpler approach
  fails gets kept.
- **One class per module under `models/`**, named after the class in snake_case. This is not
  idiomatic Python. It is a deliberate house style so that finding a type is a filesystem
  operation.
- **Boolean methods start with `is_`.** If that reads awkwardly, it probably is not a
  predicate — return the data and let the caller test it.
- **Errors say what failed and what to do about it.** `raise RuntimeError("failed")` will be
  sent back.
- **No `Any` in code we own.** Values crossing out of an untyped dependency are validated
  into a Pydantic model at the service boundary.
- **Commands render, services decide.** A module under `commands/` collects input, calls a
  service, and prints. It holds no business rules and no wiring.

## Changing anything that can send a message

Article X is the set of rails that stand between a wrong verdict and a colleague's inbox:
dry-run default, opt-in list, cooldown, per-run cap, kill switch, run lock, and an
append-only audit written *before* the send.

Every one has a test that fails if the rail is removed. If you are changing this area:

- Do not weaken a default. The defaults are timid on purpose.
- Keep the audit write before the send. A crash between them costs a reply that never
  arrives; the other order risks sending the same message twice, which is far worse.
- Add the failing case to the test suite before the fix.

## Changing the prompt

`services/classification_prompt.py` is prose and reviewed as prose. Two rules learned the
hard way, both recorded in Article IX:

- The test is **whether a message depends on the content of what preceded it**, not whether
  it is short and not whether a conversation is already under way.
- Do not tell the model to lower its confidence on ambiguity in general terms. It hedges
  everywhere, including on unambiguous cases, and costs more in missed greetings than it
  buys in safety.

Run `pytest -m live` and paste the before/after in your pull request.

## Amending the constitution

Expected, not discouraged. Several articles are already on their second version because the
first was wrong in a way only a real run revealed.

Amend it in the same pull request as the code, say which article changed and why, and record
anything you learned about Webex or the SDK in Appendix A. What is *not* fine is quietly
implementing around an article — the next person will read it and believe it.

## Commit messages decide the release

Pushing to `main` runs commitizen, which reads the commit subjects since the last tag,
decides the version bump, tags it, and triggers a publish to PyPI. So the subject line is not
just documentation — it is the release decision.

Use [Conventional Commits](https://www.conventionalcommits.org/):

| Subject starts with | Effect |
| --- | --- |
| `fix: …` | patch — 0.2.0 to 0.2.1 |
| `feat: …` | minor — 0.2.0 to 0.3.0 |
| `feat!: …` or a `BREAKING CHANGE:` footer | minor while the major is 0; major after 1.0.0 |
| `docs:`, `chore:`, `refactor:`, `test:`, `style:`, `ci:`, `perf:` | **no release** |

That last row is the useful part: a README fix or a test tidy-up does not ship a version to
PyPI. If nothing since the last tag has a releasing type, the bump workflow says so and stops.

```
feat: add codex as a second classifier

Longer explanation goes here, in the same prose style as the rest of the history.
Say what you changed and why, especially if it fixes something you hit in practice.
```

`pre-commit install --hook-type commit-msg` makes a malformed subject fail at the moment you
write it, rather than silently producing no release days later. Worth doing:

```sh
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

To be walked through it instead, `uv run cz commit` prompts for each part.

Releasing 1.0.0 is deliberately not something a commit message can do: `major_version_zero`
is set, so a breaking change bumps the minor until someone decides otherwise.

## Pull requests

- One concern per pull request.
- Say what you changed and why. If it fixes something you hit in practice, describe what you
  saw; that context usually belongs in a comment or the constitution too.
- A new runtime dependency needs a one-line justification.

## Licence

By contributing you agree your work is licensed under the
[Apache License 2.0](LICENSE), the same as the project.
