#!/bin/bash
# Double-click (macOS) or run (Linux) to install everything and start Lab Mate.
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then python3 run.py
else echo "Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/ and run this again."; read -r -p "Press Enter to close"; fi
