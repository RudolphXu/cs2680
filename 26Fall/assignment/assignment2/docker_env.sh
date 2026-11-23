#!/bin/bash
# docker_env.sh — container setup, runs before madsLoop.py (see run_task.sh).
#
# Installs the harness dependency (openai==2.48.0) into /opt/madsloop_deps
# with --target, so the task's own Python environment is left untouched.
# run_task.sh puts that directory on PYTHONPATH for the harness only; the
# agent's bash tool removes it again.
#
# IMPORTANT: install with the SAME interpreter that runs madsLoop.py
# (python3): pydantic-core wheels are built per CPython version.
#
# Everything goes to stderr: run_task.sh reserves stdout for the patch.
set -euo pipefail
exec 1>&2
# The container runs as root; silence pip's "Running pip as the 'root' user"
# warning (an env var, so pips too old to know it just ignore it).
export PIP_ROOT_USER_ACTION=ignore

DEPS=/opt/madsloop_deps
PY="${MADSLOOP_PYTHON:-python3}"
OPENAI_VERSION=2.48.0
INDEX=https://pypi.org/simple

check() {
  PYTHONPATH="$DEPS" "$PY" -c \
    "import openai, sys; sys.exit(0 if openai.__version__ == '$OPENAI_VERSION' else 1)" 2>/dev/null
}

echo "== docker_env.sh: $("$PY" -V 2>&1) at $(command -v "$PY")"
if check; then
  echo "== openai $OPENAI_VERSION already present in $DEPS"
  exit 0
fi

# Make sure this interpreter has pip (some Debian images ship python3 without it).
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "== bootstrapping pip via get-pip.py"
  # Older interpreters need the version-specific script; fall back to the generic one.
  PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  "$PY" -c "import urllib.request as u
try:
    u.urlretrieve('https://bootstrap.pypa.io/pip/$PYVER/get-pip.py', '/tmp/get-pip.py')
except Exception:
    u.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')"
  "$PY" /tmp/get-pip.py -q --index-url "$INDEX"
fi

mkdir -p "$DEPS"
"$PY" -m pip install -q --disable-pip-version-check --no-warn-script-location \
  --index-url "$INDEX" --target "$DEPS" "openai==$OPENAI_VERSION"

if ! check; then
  echo "== ERROR: openai $OPENAI_VERSION not importable from $DEPS with $PY"
  exit 1
fi
echo "== openai $OPENAI_VERSION installed into $DEPS"
