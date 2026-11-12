# Sourced by the launchers. Sets PY to the first Python >= 3.9 that really runs.
# /usr/bin/python3 on macOS is a shim that refuses to start until the Xcode licence is accepted, so it goes last.
PY=""
for cand in "${GBIG_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
  [ -n "$cand" ] && [ -x "$cand" ] || continue
  if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "STOP: no working Python 3.9+ found. Install it with: brew install python  (or from python.org)" >&2
  echo "      If macOS talks about the Xcode licence, run: sudo xcodebuild -license accept" >&2
  exit 1
fi
