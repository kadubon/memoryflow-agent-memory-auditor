"""Allow `python -m memoryflow.cli`."""

from memoryflow.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())

