"""Generate and install the Windows Task Scheduler entry for the daemon."""

from __future__ import annotations

import getpass
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

TASK_NAME = "dbxignore"


def detect_invocation() -> tuple[Path, str]:
    """Return (executable, arguments) to run the daemon in the current install.

    Frozen PyInstaller bundle: prefers the `dbxignored.exe` sibling that
    ships alongside `dbxignore.exe` (both emitted from the same PyInstaller
    Analysis), falling back to `(sys.executable, "daemon")` only if the
    sibling is somehow absent. Mirrors the macOS/Linux frozen-branch logic
    in `install/_common.py:detect_invocation` — see that docstring for the
    detailed resolution rules and the v0.4 beta-tester rationale.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if exe.name == "dbxignored.exe":
            return exe, ""
        sibling = exe.parent / "dbxignored.exe"
        if sibling.exists():
            return sibling, ""
        return exe, "daemon"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw, "-m dbxignore daemon"


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
        subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["schtasks", "/Create", "/XML", str(tmp_path), "/TN", TASK_NAME, "/F"],
            check=True,
        )
        logger.info("Installed scheduled task %s", TASK_NAME)
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
    run_result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["schtasks", "/Run", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    if run_result.returncode == 0:
        logger.info("Started scheduled task %s", TASK_NAME)
    else:
        logger.warning(
            "schtasks /Run returned %s: %s. Task is registered and will start "
            "at next logon; run `schtasks /Run /TN %s` to start now.",
            run_result.returncode,
            run_result.stderr.strip() or run_result.stdout.strip() or "(no output)",
            TASK_NAME,
        )


def uninstall_task() -> None:
    """Remove the Task Scheduler entry; raises RuntimeError if schtasks fails."""
    result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks /Delete returned {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    logger.info("Uninstalled scheduled task %s", TASK_NAME)
