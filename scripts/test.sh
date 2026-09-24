#!/usr/bin/env bash
# Run the unit tests under the oldest supported Python (the macOS system 3.9,
# which Herdr may pick for plugin panes) and under the python3 on PATH.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root" || exit 1
export PYTHONDONTWRITEBYTECODE=1
status=0
for python in /usr/bin/python3 python3; do
  command -v "$python" >/dev/null 2>&1 || continue
  echo "== $("$python" --version 2>&1) ($python)"
  "$python" -m unittest discover -s tests -t . || status=1
done
exit "$status"
