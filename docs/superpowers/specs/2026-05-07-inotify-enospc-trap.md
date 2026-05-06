# dbxignore — trap inotify resource exhaustion at daemon startup

**Date:** 2026-05-07
**Status:** Accepted. Implementation plan to follow.
**Resolves:** [BACKLOG.md item #52](../../../BACKLOG.md).

## Problem

`daemon.run` calls `Observer().schedule(handler, root, recursive=True)` for each watched root, then `observer.start()`. On Linux the watchdog backend is inotify; the kernel caps watches per user via `fs.inotify.max_user_watches` (often 8192 on default-config kernels and shared VPS images) and instances per user via `fs.inotify.max_user_instances`. Recursive watch on a Dropbox tree larger than the watch cap fails inside `observer.start()` with `OSError(errno.ENOSPC, "inotify watch limit reached")`. The exception propagates unhandled; systemd marks the unit failed and the user sees only a Python traceback in `journalctl --user -u dbxignore.service`, with no indication that the cause is a kernel sysctl knob.

Surfaced 2026-05-03 during VPS testing (`scripts/manual-test-ubuntu-vps.sh` against a personal Dropbox account on a default-limit kernel).

A user without sudo cannot self-remediate. Even users who can need to know the right sysctl key — currently undocumented in dbxignore.

## Scope

**In scope:**

- A trap inside `daemon.py` covering `OSError(errno.ENOSPC)` and `OSError(errno.EMFILE)` raised by `observer.start()`. ENOSPC maps to `fs.inotify.max_user_watches`; EMFILE maps to `fs.inotify.max_user_instances`.
- On a trapped errno: log ERROR with the matching sysctl command literals, then `sys.exit(75)` (POSIX `EX_TEMPFAIL`). systemd marks the unit `failed` rather than `succeeded`.
- A new `### Linux daemon prerequisites` README subsection inside `## Install (Linux)` documenting the watch-limit knob and the persistent sysctl change.
- Three unit tests in a new `tests/test_daemon_inotify_enospc.py`: ENOSPC trap path, EMFILE trap path, unknown-errno propagation.

**Out of scope:**

- Trapping non-inotify errnos. Other `OSError` shapes from `observer.start()` propagate as today; the trap stays narrow so a future-unknown failure mode isn't silently swallowed.
- Trapping in `cli.apply` or anywhere else dbxignore touches the filesystem. The bug surface is daemon-side observer startup.
- A `PollingObserver` fallback. Polling tens of thousands of directories at watchdog's default ~1s rate is brutal CPU-wise; the body of #52 lists this as a candidate and argues against it. We agree.
- Manual-test script changes. Triggering ENOSPC requires setting `fs.inotify.max_user_watches` to a small value, which destabilizes other inotify-using processes on the host. The validation path is the original VPS reproduction, not a scripted smoke test.
- Refactoring `cli.daemon` / `cli.daemon_main` exit-code propagation. `sys.exit(75)` raised from inside `daemon.run` propagates through Click and produces the right process exit code without callsite changes.
- Companion items #53 (sweep cost on large trees) and #54 (per-directory watches with mark/unmark lifecycle). This change addresses the failure-mode UX, not the underlying "we watch too many directories" architecture.

## User contract

Before:

```
$ systemctl --user start dbxignore.service
$ systemctl --user status dbxignore.service
● dbxignore.service - ...
     Active: failed (Result: exit-code) ...
$ journalctl --user -u dbxignore.service
... Traceback (most recent call last):
...   File ".../watchdog/observers/inotify_buffer.py", line N, in ...
... OSError: [Errno 28] inotify watch limit reached
```

After:

```
$ systemctl --user start dbxignore.service
$ systemctl --user status dbxignore.service
● dbxignore.service - ...
     Active: failed (Result: exit-code) ...
    Process: NNNN ExecStart=... (code=exited, status=75)
$ journalctl --user -u dbxignore.service
... ERROR dbxignore.daemon: inotify watch limit reached (errno ENOSPC). The
    kernel's fs.inotify.max_user_watches is exhausted; ...
        sudo sysctl -w fs.inotify.max_user_watches=524288
    To make the change persist across reboots:
        echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/99-dbxignore.conf
        sudo sysctl --system
    ... Daemon exiting with status 75.
```

Exit code 75 is `EX_TEMPFAIL` from `sysexits.h`: "the user is invited to retry the operation later." It signals "the system is not currently in a state where this can run" without claiming a permanent failure. systemd's default `Restart=` policy in our generated unit is `on-failure` with the watch-limit case excluded — we explicitly do not want a restart loop on a knob that requires sudo to change.

## Design

### Architecture

One new private helper in `daemon.py` and one small structural refactor to `daemon.run`'s observer-lifecycle block.

```
src/dbxignore/daemon.py
  + _start_observer_or_exit(observer)   # new helper: traps ENOSPC/EMFILE,
                                        #   logs sysctl block, sys.exit(75)
  ~ run()                               # hoist observer.start() out of the
                                        #   inner watch-loop try/finally so
                                        #   sys.exit doesn't trip cleanup of
                                        #   a never-started Observer thread

tests/test_daemon_inotify_enospc.py     # new file (3 tests)

README.md
  + ### Linux daemon prerequisites      # new subsection inside ## Install (Linux)
```

### `_start_observer_or_exit` — behavior

```python
def _start_observer_or_exit(observer: Observer) -> None:
    """Start ``observer``; trap kernel-watch-resource exhaustion.

    inotify's per-user limits surface here on Linux when a Dropbox tree
    exceeds ``fs.inotify.max_user_watches`` (ENOSPC) or
    ``fs.inotify.max_user_instances`` (EMFILE). Without this trap the
    daemon dies with an opaque traceback in journalctl. Other ``OSError``
    shapes propagate; we don't suppress unknown causes.

    On a trapped errno: log ERROR with the matching sysctl command,
    then ``sys.exit(75)`` (POSIX ``EX_TEMPFAIL``). systemd marks the unit
    ``failed`` so ``systemctl is-failed`` and status-bar widgets catch it.
    """
    try:
        observer.start()
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            logger.error(_ENOSPC_MESSAGE)
            sys.exit(75)
        if exc.errno == errno.EMFILE:
            logger.error(_EMFILE_MESSAGE)
            sys.exit(75)
        raise
```

`_ENOSPC_MESSAGE` and `_EMFILE_MESSAGE` are module-level string constants with the full multi-line sysctl runbook each. Keeping them as constants lets the tests assert the exact sysctl literal without embedding string fragments inline.

`sys.exit(75)` uses the literal `75` (POSIX `EX_TEMPFAIL`) rather than `os.EX_TEMPFAIL`. The constant is POSIX-only; on Windows it doesn't exist. The trap arm only fires on Linux at runtime, but the literal-with-comment avoids a `getattr` dance and survives a future refactor that might import the module on Windows under conditions where the trap branch is reachable.

### `daemon.run` refactor

Current shape (lines 443–458 of `daemon.py`):

```python
debouncer.start()
try:
    observer.start()
    logger.info("watching roots: %s", [str(r) for r in configured_roots])
    try:
        while not stop_event.is_set():
            woke = stop_event.wait(SWEEP_INTERVAL_S)
            ...
    finally:
        observer.stop()
        observer.join()
finally:
    debouncer.stop()
    logger.info("daemon stopped")
```

New shape:

```python
debouncer.start()
try:
    _start_observer_or_exit(observer)  # may sys.exit(75); on success, observer is running
    logger.info("watching roots: %s", [str(r) for r in configured_roots])
    try:
        while not stop_event.is_set():
            woke = stop_event.wait(SWEEP_INTERVAL_S)
            ...
    finally:
        observer.stop()
        observer.join()
finally:
    debouncer.stop()
    logger.info("daemon stopped")
```

The structural change is hoisting `observer.start()` out of any `try/finally` whose `finally` calls `observer.stop()` / `observer.join()`. `Observer` is a `threading.Thread` subclass; calling `stop` or `join` on a thread that never started raises `RuntimeError`. By placing the start in a position where `sys.exit` skips straight to the outer `finally` (which only stops the debouncer), we avoid that crash. `debouncer.stop()` does run on the exit path — `SystemExit` is a `BaseException`, so `finally` fires.

### Log message constants

`_ENOSPC_MESSAGE` content (single triple-quoted string emitted via one `logger.error` call so the message lands as a single journalctl record rather than nine records the user would have to reassemble):

```
inotify watch limit reached (errno ENOSPC). The kernel's fs.inotify.max_user_watches is exhausted; recursive watch on a Dropbox tree larger than the per-user limit fails at observer startup. To raise the limit, run as root:

    sudo sysctl -w fs.inotify.max_user_watches=524288

To make the change persist across reboots:

    echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/99-dbxignore.conf
    sudo sysctl --system

Alternatively, reduce the watched tree by adding rules to .dropboxignore. Daemon exiting with status 75.
```

`_EMFILE_MESSAGE` is the same shape, swapping `max_user_watches` → `max_user_instances` and `524288` → `1024`. Preamble shortened — instances exhaustion is rarer and the explanation can be terser.

The `524288` and `1024` numerals match Dropbox's documented recommendations and the de facto values used by VS Code / IntelliJ / Syncthing setup guides; copy-paste-from-journalctl yields a value that won't need re-tuning.

### README subsection

Inside `## Install (Linux)` (currently README.md:59–84), append a new `### Linux daemon prerequisites` subsection before `## Install (macOS)` at line 85. Content:

```markdown
### Linux daemon prerequisites

The daemon uses inotify to watch the Dropbox tree recursively. The kernel
caps the number of watches per user (`fs.inotify.max_user_watches`); on
default-config kernels this is often 8192, which a typical Dropbox tree
exceeds. The daemon refuses to start (exit code 75) when the limit is hit.

Raise the limit (one-time, persistent across reboots):

    echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/99-dbxignore.conf
    sudo sysctl --system

If the daemon won't start, check `journalctl --user -u dbxignore.service`
for the exact errno (ENOSPC = watch count, EMFILE = instance count) and the
sysctl command to run.
```

The exact-errno-cross-reference is load-bearing: the journalctl entry IS the runbook, not a stack trace the user has to decode.

## Test plan

New file `tests/test_daemon_inotify_enospc.py`. No platform marker (the tests are pure-Python mocks; the kernel-side behavior is Linux-only but the test logic runs cross-platform — same shape as `tests/test_daemon_singleton.py` which mocks `psutil` regardless of host OS).

### Test 1 — `test_run_traps_enospc_and_exits_75`

- Fake `Observer` subclass: `__init__` records calls; `schedule` is a no-op; `start` raises `OSError(errno.ENOSPC, "inotify watch limit reached")`.
- Fake `Debouncer`: tracks `start()` / `stop()` call ordering on a list, so the test can assert the debouncer was cleanly stopped despite the exit.
- Wire-up: `monkeypatch.setattr(daemon, "Observer", FakeObserver)`, `monkeypatch.setattr(daemon, "Debouncer", FakeDebouncer)`, `monkeypatch.setattr(daemon, "_configured_logging", contextlib.nullcontext)`, `monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])`, `monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")`.
- Assert: `pytest.raises(SystemExit) as exc_info; assert exc_info.value.code == 75`.
- Assert: `caplog` captured the ENOSPC ERROR record at `logger="dbxignore.daemon"` and the message contains the literal `fs.inotify.max_user_watches=524288`.
- Assert: the fake debouncer's call list ends in `["start", "stop"]` — proves the outer `finally` ran.

### Test 2 — `test_run_traps_emfile_and_exits_75`

Same shape as Test 1 with `errno.EMFILE`; assert the log message contains `fs.inotify.max_user_instances=1024`.

### Test 3 — `test_run_propagates_unknown_oserror`

`Observer.start()` raises `OSError(errno.EIO, "I/O error")`. Assert `pytest.raises(OSError)` (NOT `SystemExit`); pins the "narrow trap" contract — unknown failures still propagate.

### Coverage gap acknowledged

These tests pin the trap's logical behavior (errno dispatch + exit code + log message + cleanup ordering), not the real-inotify-with-real-exhausted-kernel path. The end-to-end validation path is the original 2026-05-03 VPS reproduction. There is no automated regression test for the kernel-side path; CI runs Ubuntu with a high `max_user_watches` and would not see ENOSPC.

## Risks and edge cases

- **`sys.exit` inside a try/finally with `debouncer.stop`:** Python's finally semantics handle this correctly — `SystemExit` is a `BaseException`, the outer `finally: debouncer.stop()` runs before the process exits. Verified by the unit test that asserts `["start", "stop"]` on the fake debouncer's call list.
- **The trap deviates from the daemon's existing silent-return error pattern.** The "no Dropbox roots discovered" and "already running" branches `logger.error` + `return`, leaving exit code 0. We use `sys.exit(75)` here because (a) the body of #52 specifies `EX_TEMPFAIL`, and (b) systemd's `is-failed` / status-bar integration relies on the non-zero exit. The deviation is justified but worth a comment in `_start_observer_or_exit`.
- **`_start_observer_or_exit` is called inside `_configured_logging()`,** so the ERROR log lands in `daemon.log` AND systemd-journald-via-stderr (the Linux-only stderr handler is what makes `journalctl --user -u dbxignore.service` see it). If a future refactor moves the call outside the context manager, the message goes to stderr only and journalctl loses it. Document the dependency in the helper's docstring.
- **EMFILE vs ENOSPC test fixtures could share a parametrize.** Decided against: the assertion asymmetry (different sysctl literal in each message) and the rarity-of-EMFILE warrants explicit named tests over a parametrize loop. Two tests, ten lines each, beats one parametrize with branching asserts.
- **Watchdog version dependency.** The `OSError(ENOSPC)` shape is what watchdog's inotify backend re-raises today; if a future watchdog version wraps the kernel error in a custom exception type, the trap stops matching and we regress to the original opaque-traceback failure mode. Mitigation: the test fixture pins the contract; if a watchdog upgrade ever fails Test 1 / Test 2, the failure mode is loud rather than silent.

## Backlog interactions

- **Resolves #52.** Inline `**Status: RESOLVED <date> (PR #<N>).**` marker in the item body and an entry in the bottom `## Status > Resolved` section. PR number predicted via `gh pr list --state all --limit 1` plus `gh issue list --state all --limit 1` (next available is `max(numbers) + 1`).
- **Companion #53 / #54 stay open.** Per scope-out above.
- **Future EMFILE recurrence as #52-followup.** If a beta tester ever hits EMFILE in the wild, the trap already handles it; no new item required unless the experience reveals a missed knob.

## Implementation notes

- `errno` module: standard library; already imported elsewhere in dbxignore.
- `sys.exit`: the daemon's existing code does not call `sys.exit` directly (it returns silently). Adding the call requires no new import — `sys` is already imported at the top of `daemon.py`.
- The two message constants live at module scope above `_start_observer_or_exit`. They are not part of the public API; no `__all__` change needed.
