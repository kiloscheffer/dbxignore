import sys
from pathlib import Path


def main() -> None:
    # When invoked as dbxignored.exe, default to the daemon subcommand.
    exe_name = Path(sys.argv[0]).stem.lower()
    if exe_name == "dbxignored" and len(sys.argv) == 1:
        sys.argv.append("daemon")
    from dbxignore.cli import main as _main

    _main()


if __name__ == "__main__":
    main()
