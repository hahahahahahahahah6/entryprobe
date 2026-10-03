"""entryprobe: verify that a package's console_scripts entry points can actually start."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
