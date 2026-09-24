#!/bin/sh
# Claude Code PostToolUse hook (matcher: Read), called by the shim devcon-herdr
# registers: publishes the image Claude read to its conversation's image
# viewer. The hook payload arrives on stdin.
#
# Never blocks Claude: always exits 0 and prints nothing.
command -v python3 >/dev/null 2>&1 || exit 0
case $0 in
    */*) hooks_dir=${0%/*} ;;
    *) hooks_dir=. ;;
esac
plugin_root=$(CDPATH= cd -- "$hooks_dir/.." 2>/dev/null && pwd -P) || exit 0
PYTHONPATH="$plugin_root${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m herdr_image_viewer hook claude-read >/dev/null 2>&1
exit 0
