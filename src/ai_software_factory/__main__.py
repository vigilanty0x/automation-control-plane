"""Run the canonical Factory CLI without installing console scripts."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
