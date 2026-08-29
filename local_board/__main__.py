"""Allow `python -m local_board` alongside the installed `local-board` script."""

from .cli import main

raise SystemExit(main())
