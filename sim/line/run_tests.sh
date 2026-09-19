#!/bin/zsh
# Hardware-free test run for the whole line. Exit code is pytest's.
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python -m pytest line/tests -q "$@"
