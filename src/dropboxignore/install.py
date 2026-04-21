"""Generate and install the Windows Task Scheduler entry for the daemon."""

from __future__ import annotations

import getpass
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

TASK_NAME = "dropboxignore"


def detect_invocation() -> tuple[Path, str]:
    """Return (executable, arguments) to run the daemon in the current install."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable), ""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw, "-m dropboxignore daemon"


def build_task_xml(exe_path: Path, arguments: str = "") -> str:
    """Return a Task Scheduler v1.2 XML document for a logon-trigger daemon."""
    user = getpass.getuser()
    args_element = f"<Arguments>{arguments}</Arguments>" if arguments else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>dropboxignore daemon: sync com.dropbox.ignored with .dropboxignore</Description>
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
      <Command>{exe_path}</Command>
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


def uninstall_task() -> None:
    """Remove the Task Scheduler entry; raises RuntimeError if schtasks fails."""
    result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks /Delete returned {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    logger.info("Uninstalled scheduled task %s", TASK_NAME)
