# DBXIGNORE_TEST_FAIL_* failure-injection mechanism

**Date:** 2026-05-14
**Closes:** BACKLOG #121, #127, #128, #129 (the "new exit-2 path, hard to force the underlying failure mode" rule-of-four)

## Problem

Four backlog items share one shape: a CLI command grew a new exit-2 error path,
the contract is pinned tightly by unit tests at the function boundary, but the
manual-test scripts (shell scripts driving the real `dbxignore` binary) cannot
observe the CLI's exit-2 surface because the underlying failure mode cannot be
forced on a healthy machine:

- **#121** — `scan_errors` exit-2 path for `clear` / `list` (needs `markers.is_ignored` to raise `OSError`, e.g. ENOTSUP).
- **#127** — `state_errors` exit-2 path for `uninstall --purge` (needs a state-dir `unlink` to raise `OSError`).
- **#128** — `launchctl bootout` confirmed-failure exit-2 path for `uninstall` on macOS (needs a non-zero bootout rc with non-"not loaded" stderr).
- **#129** — daemon-alive purge-refusal exit-2 path for `uninstall --purge` (needs a daemon that survives `uninstall_service`).

#128's body planted a rule-of-four tripwire: on the fourth deferral of this
shape, build a shared `DBXIGNORE_TEST_FAIL_*` env-var injection mechanism that
production code honors at strategic boundaries. #129 is the fourth; the tripwire
has fired.

A shell manual-test script's only lever into a subprocess is the environment, so
env-var-keyed failure injection is the mechanism family. The cost — production
code coupling to a test-only path — is the thing three earlier individual
deferrals judged not worth paying; the fourth occurrence is the signal that the
cross-cutting coverage gain now justifies it.

## Mechanism

New module `src/dbxignore/_testing.py` with two primitives:

```python
def raise_if_fail_point(name: str, exc: OSError | None = None) -> None:
    """Raise an injected OSError if DBXIGNORE_TEST_FAIL_<name> is set.

    Test-only; inert in normal runs. Logs a WARNING when it fires so a
    leaked env var is never a silent failure. Default exc is
    OSError(errno.ENOTSUP, "injected failure (DBXIGNORE_TEST_FAIL_<name>)").
    """

def fail_point_active(name: str) -> bool:
    """True if DBXIGNORE_TEST_FAIL_<name> is set in the environment.

    Test-only. Logs a WARNING when it returns True.
    """
```

Both resolve the env var as `os.environ.get(f"DBXIGNORE_TEST_FAIL_{name}")` and
treat any non-empty value as set.

Two primitives because the four boundaries split into two shapes:

- **Raising sites** (#121, #127) — the boundary is a call that can raise
  `OSError`, already wrapped in `except OSError` by existing error handling.
  `raise_if_fail_point` raises; the existing handler records it and drives the
  exit-2 path.
- **Value-override sites** (#128, #129) — the boundary is a value (a subprocess
  return code, a boolean), not a raising call. `fail_point_active` is a
  predicate the site uses to substitute a failing value.

A single predicate could cover both, but the raising sites would then repeat
`if fail_point_active(...): raise OSError(...)` with the errno/message inline at
each site; `raise_if_fail_point` keeps the injected `OSError` shape in one place.

### WARNING on fire

Both primitives log a `logger.warning` when a fail point is active. Failure
injection that is silent is a foot-gun: if one of these env vars ever leaks into
a real environment, `uninstall` / `clear` must say *why* it is failing, not just
exit 2 mysteriously.

## The four hook sites

Each site is a one-liner. The hooks only *trigger* code paths that already exist
and are already unit-tested — they add no new error-handling logic.

| Item | Fail point name | Env var | Site | Mechanism |
|---|---|---|---|---|
| #121 | `MARKER_READ` | `DBXIGNORE_TEST_FAIL_MARKER_READ` | `cli._walk_marked_paths` — before *every* `markers.is_ignored` read: the `target` read (cli.py:924) and both walk-loop reads (`dirnames` loop, `filenames` loop) | `raise_if_fail_point("MARKER_READ")` → caught by the existing `except OSError` at each read → recorded in the returned `errs` → `clear` / `list` populate `scan_errors` → exit 2 |
| #127 | `STATE_PURGE` | `DBXIGNORE_TEST_FAIL_STATE_PURGE` | `cli._purge_dir` — in the `f.unlink()` loop | `raise_if_fail_point("STATE_PURGE")` → caught by the existing `except OSError` → recorded in `errors` (when the caller supplied the list) → `uninstall --purge` populates `state_errors` → exit 2 |
| #128 | `BOOTOUT` | `DBXIGNORE_TEST_FAIL_BOOTOUT` | `install/macos_launchd.uninstall_agent` — after `subprocess.run` | `fail_point_active("BOOTOUT")` → synthesize a non-zero rc + non-"not loaded" stderr → the existing `_is_service_not_loaded` check fails → existing `raise RuntimeError` → `cli.uninstall` exit 2 |
| #129 | `DAEMON_ALIVE` | `DBXIGNORE_TEST_FAIL_DAEMON_ALIVE` | `cli.uninstall` — the `if purge:` daemon-alive gate | `fail_point_active("DAEMON_ALIVE")` OR'd into the gate condition → `_refuse_purge_daemon_alive` → exit 2 |

### Why these exact sites

- **#121 → `_walk_marked_paths`**, not the `uninstall --purge` marker walk.
  `_walk_marked_paths` is the single chokepoint both `clear` and `list` route
  through; #121 is specifically about the `clear` / `list` `scan_errors` surface.
  The `uninstall --purge` marker walk is a separate code path (item #98's
  `errors` accumulator) and is not what #121 tracks. The hook fires before
  *every* `markers.is_ignored` read inside `_walk_marked_paths` — the `target`
  read at cli.py:924 *and* the two walk-loop reads — so the fail point is a true
  chokepoint: a `clear <single-marked-file>` (which returns at the `target` read
  before the walk begins) injects just as reliably as a `clear <dir-tree>`.
- **#127 → `_purge_dir`**, the shared helper `_purge_local_state` calls for each
  state directory. One site covers every state-file unlink.
- **#128 → `uninstall_agent`**, after `subprocess.run`. Overriding the result
  (rather than the env var being read deeper) keeps the injection at the exact
  boundary #128 names — the bootout rc.
- **#129 → the `cli.uninstall` gate**, not inside `state.is_any_daemon_running()`.
  #129's `Touches:` names `cli.py` "env-var hook at the daemon-alive guard
  boundary". Keeping it in the gate condition leaves `state` free of test hooks
  and puts the hook where the exit-2 decision is made.

## Manual-test extensions

Pattern for every injected case: scope the env var to a single invocation
(never `export` it globally), assert exit 2 + the expected stderr phrase, then
re-run the command clean so the script's end-state is unchanged.

- Bash: `DBXIGNORE_TEST_FAIL_X=1 dbxignore ...` inline prefix.
- PowerShell (no inline env prefix): `$env:DBXIGNORE_TEST_FAIL_X = "1"; dbxignore ...; Remove-Item Env:\DBXIGNORE_TEST_FAIL_X`.

### #121 — Phase 4.5

Lands in the shared `scripts/_phase_extended_cli.sh` helper (covers
`manual-test-ubuntu-vps.sh` and `manual-test-macos.sh`) and in
`manual-test-windows.ps1`'s `Test-ExtendedCli`. New case after 4r:

- `DBXIGNORE_TEST_FAIL_MARKER_READ=1 dbxignore clear <path>` → exit 2 + the
  `_report_scan_errors` stderr phrase.
- Same for `dbxignore list <path>`.

No clean re-run needed: `clear` / `list` with the marker-read fail point injected
reads nothing successfully and mutates nothing.

### #127, #128, #129 — Phase 6

Land in all three scripts' Phase 6, after the existing happy-path purge guards.

**Each case must re-install first.** By the point these cases run, Phase 6 has
already removed the service and run a clean `--purge` — the state directory is
empty and nothing is registered. Every injected case below therefore begins with
`dbxignore install` + a short wait (so the daemon writes `state.json` /
`daemon.lock` and the service is registered), and the prior case's recovery
re-run leaves the system uninstalled again for the next. Per-case shape:
inject → assert exit 2 + stderr → recover with a clean re-run.

- **#127** — re-install (the daemon must have written `state.json` /
  `daemon.lock` so `_purge_dir`'s `f.unlink()` loop has something to fail on —
  with an empty state directory there is nothing to inject against), then
  `DBXIGNORE_TEST_FAIL_STATE_PURGE=1 dbxignore uninstall --purge` → exit 2 +
  "Could not fully purge state files". Markers *are* cleared (the failure is in
  the state-dir step, which runs after marker clearing), so the leftover is
  state files. Recovery: re-run `uninstall --purge` clean.
- **#128** (macOS script only) — re-install, then
  `DBXIGNORE_TEST_FAIL_BOOTOUT=1 dbxignore uninstall` → exit 2 + "launchctl
  bootout returned". The plist is preserved and the daemon is still registered.
  Recovery: re-run `uninstall` clean.
- **#129** — re-install, then `DBXIGNORE_TEST_FAIL_DAEMON_ALIVE=1 dbxignore
  uninstall --purge` → exit 2 + "daemon is running". The gate fires *before* the
  purge body, so nothing is cleaned. Recovery: re-run `uninstall --purge` clean.

Cross-platform note: #128 is macOS-only (launchd). #127 and #129 apply to all
three platforms.

## Unit tests

New `tests/test_testing.py` covering the helper module only:

- `raise_if_fail_point` — env unset → no raise; env set → raises the default
  `OSError(ENOTSUP, ...)`; env set + custom `exc` → raises that; WARNING logged
  when it fires.
- `fail_point_active` — env unset → False; env set → True; WARNING logged when
  True.

No new boundary tests. The four exit-2 contracts are already pinned by existing
unit tests (`test_install.py`, `test_macos_launchd.py`, the `clear` / `list`
scan-error tests). This change does not alter those contracts — it only adds the
env-var hooks that let the manual scripts reach them end-to-end.

## Docs

- `_testing.py` module docstring: a thorough header listing all four fail-point
  names, their env vars, and the boundary each one drives.
- One AGENTS.md Gotchas bullet naming `_testing.py` as the failure-injection
  convention home, so a future fifth "hard-to-force exit-2 path" deferral finds
  the mechanism instead of re-deferring.

## PR scope

Single PR, single branch `chore/test-fail-injection` (`test/` is not a valid
branch prefix per `cchk.toml`; `chore/` is the catch-all). Commits split along
revertability lines:

1. `feat(testing): add DBXIGNORE_TEST_FAIL_* failure-injection helpers` —
   `_testing.py` + the four hook sites + `tests/test_testing.py`.
2. `test: exercise exit-2 failure paths in manual-test scripts` — the Phase 4.5
   helper, `manual-test-ubuntu-vps.sh`, `manual-test-macos.sh`,
   `manual-test-windows.ps1`.
3. `docs: close BACKLOG #121/#127/#128/#129 + add failure-injection gotcha` —
   AGENTS.md Gotchas bullet + four `Status: RESOLVED` markers + four
   Resolved-section entries + Open-count line 19 → 15.

No CHANGELOG entry: `DBXIGNORE_TEST_FAIL_*` is test-only and not user-visible CLI
surface. The `feat(testing)` scope tag reflects that.

## Out of scope

- No changes to the four exit-2 contracts themselves — they are already
  implemented and tested.
- No new failure-injection sites beyond the four. The mechanism is
  forward-compatible (a fifth site is a one-liner + a docstring line) but adding
  speculative sites now would be working ahead of a trigger.
- No CHANGELOG / README / release-note surface.
