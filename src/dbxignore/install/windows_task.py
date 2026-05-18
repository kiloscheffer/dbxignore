"""Generate and install the Windows Task Scheduler entry for the daemon."""

from __future__ import annotations

import getpass
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

from dbxignore.install._common import detect_invocation as detect_invocation

logger = logging.getLogger(__name__)

TASK_NAME = "dbxignore"

# How long to wait for the daemon process to exit after `schtasks /End`
# before falling through to `/Delete` anyway. Mirrors the operational
# bound of the Linux/macOS synchronous-teardown paths.
_END_WAIT_TIMEOUT_S = 30.0
_END_WAIT_POLL_INTERVAL_S = 0.5


def build_task_xml(exe_path: Path, arguments: str = "") -> str:
    """Return a Task Scheduler v1.2 XML document for a logon-trigger daemon.

    All interpolated strings are passed through ``xml.sax.saxutils.escape``
    so that ``&``, ``<``, and ``>`` in usernames or install paths (e.g.
    ``C:\\Users\\Tom & Jerry\\``) do not produce malformed XML that
    ``schtasks /Create /XML`` rejects.
    """
    user = escape(getpass.getuser())
    command = escape(str(exe_path))
    args_element = f"<Arguments>{escape(arguments)}</Arguments>" if arguments else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>dbxignore daemon: sync com.dropbox.ignored with .dropboxignore</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      {args_element}
    </Exec>
  </Actions>
</Task>
"""


def install_task() -> None:
    exe, args = detect_invocation()
    xml = build_task_xml(exe, args)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as tmp:
        tmp.write(xml)
        tmp_path = Path(tmp.name)
    try:
        try:
            subprocess.run(  # noqa: S603 — hardcoded args, no user data
                ["schtasks", "/Create", "/XML", str(tmp_path), "/TN", TASK_NAME, "/F"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"schtasks /Create returned {exc.returncode}: "
                f"{(exc.stderr or '').strip() or (exc.stdout or '').strip() or '(no output)'}"
            ) from exc
        except OSError as exc:
            # `schtasks.exe` is a system binary so its absence is atypical,
            # but stripped-down sandboxes (Nano Server, some CI containers)
            # and PATH corruption can produce FileNotFoundError here. Without
            # this arm the traceback escapes; cli.install catches RuntimeError.
            raise RuntimeError(f"schtasks /Create could not be invoked: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    # Kick the task immediately so the daemon runs without waiting for next logon.
    # Linux + macOS installers do equivalent (`systemctl --user enable --now`,
    # `launchctl bootstrap` + `RunAtLoad: true`); without /Run on Windows the
    # user sees "Installed scheduled task" but `dbxignore status` reports no
    # daemon until they log out and back in.
    #
    # /Run failures are non-fatal: the task is registered and will start at
    # next logon regardless. WARNING-and-continue rather than raising so the
    # user doesn't see an install error for a partial-success state.
    try:
        run_result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["schtasks", "/Run", "/TN", TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        # See the matching arm on /Create above. Logging at WARNING preserves
        # the existing /Run "non-fatal" contract (task is already registered).
        logger.warning(
            "schtasks /Run could not be invoked: %s. Task is registered and will start "
            "at next logon; run `schtasks /Run /TN %s` to start now.",
            exc,
            TASK_NAME,
        )
    else:
        if run_result.returncode != 0:
            logger.warning(
                "schtasks /Run returned %s: %s. Task is registered and will start "
                "at next logon; run `schtasks /Run /TN %s` to start now.",
                run_result.returncode,
                run_result.stderr.strip() or run_result.stdout.strip() or "(no output)",
                TASK_NAME,
            )


def uninstall_task() -> None:
    """Remove the Task Scheduler entry; raises RuntimeError if schtasks fails.

    Runs `schtasks /End` first (best-effort), waits for the daemon process
    recorded in state.json to actually exit, then `schtasks /Delete /F`.
    Mirrors the synchronous-shutdown contract Linux's `systemctl --user
    disable --now` and macOS's `launchctl bootout` already provide:
    when this function returns, the daemon is gone (or the timeout fired
    and we logged a WARNING). Without /End first, the running task instance
    survives /Delete, and the orphaned daemon can write state.json after
    `_purge_local_state()` removes it.
    """
    # Lazy import: state imports psutil lazily inside is_daemon_alive,
    # which keeps this module's import surface small.
    from dbxignore import state as state_module

    state_obj = state_module.read()
    daemon_pid = state_obj.daemon_pid if state_obj else None
    daemon_create_time = state_obj.daemon_create_time if state_obj else None

    # /End: best-effort. The task may not be running (already crashed,
    # never started), in which case schtasks returns non-zero — that's
    # fine, we still want /Delete to clean up the definition.
    try:
        end_result: subprocess.CompletedProcess[str] | None = subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["schtasks", "/End", "/TN", TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        # schtasks itself uninvocable (rare on Windows but possible in
        # sandboxes). Skip the daemon-exit wait below — without a successful
        # /End there's no signal sent — and proceed to /Delete, which will
        # surface its own invocation failure as RuntimeError if applicable.
        logger.warning("schtasks /End could not be invoked: %s; skipping wait", exc)
        end_result = None

    # Wait for the daemon process to actually exit. /End signals the task
    # to stop but doesn't block; the daemon's signal handler runs on its
    # own clock. is_daemon_alive's name-and-create_time check rejects
    # PID-reuse cases (a recycled PID claimed by an unrelated process).
    #
    # Gate the wait on /End succeeding: per Microsoft docs (schtasks /End)
    # the command "Stops only the instances of a program started by a
    # scheduled task", so a non-zero /End cannot make a non-task-instance
    # daemon exit — e.g. a manually-launched `dbxignorew` or a stale
    # state.json pointing at any other live process. Polling such cases
    # for the full 30s would just delay /Delete with no benefit.
    if daemon_pid is not None and end_result is not None and end_result.returncode == 0:
        deadline = time.monotonic() + _END_WAIT_TIMEOUT_S
        while time.monotonic() < deadline:
            if not state_module.is_daemon_alive(daemon_pid, create_time=daemon_create_time):
                break
            time.sleep(_END_WAIT_POLL_INTERVAL_S)
        else:
            # Loop exhausted without breaking — daemon is still alive.
            # Log and proceed anyway: /Delete must always run so the next
            # `dbxignore install` doesn't fail with "task already exists."
            logger.warning(
                "daemon process pid=%s did not exit within %.0fs of schtasks /End; "
                "continuing with /Delete",
                daemon_pid,
                _END_WAIT_TIMEOUT_S,
            )

    try:
        result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"schtasks /Delete could not be invoked: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks /Delete returned {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    logger.info("Uninstalled scheduled task %s", TASK_NAME)
