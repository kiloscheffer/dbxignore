import sys
from pathlib import Path


def main() -> None:
    # When invoked as dropboxignored.exe, default to the daemon subcommand.
    exe_name = Path(sys.argv[0]).stem.lower()
    if exe_name == "dropboxignored" and len(sys.argv) == 1:
        sys.argv.append("daemon")
    from dropboxignore.cli import main as _main
    _main()


if __name__ == "__main__":
    main()
