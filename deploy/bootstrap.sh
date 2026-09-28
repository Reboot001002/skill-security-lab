#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m unittest discover -s tests -v
printf '\nBootstrap complete. No model requests were made.\n'
printf 'Activate with: source .venv/bin/activate\n'
printf 'Then read README.md for the smoke test and server configuration.\n'
