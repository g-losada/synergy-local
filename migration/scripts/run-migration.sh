#!/usr/bin/env bash
#
# Runs migrate.py against the JanusGraph Gremlin Server, handling the
# port-forward for you.
#
#   ./run-migration.sh schema     # create property keys, labels and the index
#   ./run-migration.sh load       # insert vertices then edges
#   ./run-migration.sh verify     # counts and per-label breakdown
#   ./run-migration.sh all        # the three above (default)
#
#   [your disk] extract.json --websocket--> [Gremlin Server] --> [Cassandra]
#
# The port-forward is started here and torn down on exit, deliberately: a
# long-lived one left over from a previous run keeps the local port bound while
# pointing at a pod that no longer exists, and every connection is then refused
# with no useful error.
#
# First time only:
#   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
#
set -euo pipefail

PHASE="${1:-all}"
NAMESPACE="${NAMESPACE:-janus-graph}"
SERVICE="${SERVICE:-janus-graph}"
PORT="${PORT:-8182}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-${SCRIPT_DIR}/.venv/bin/python}"

case "${PHASE}" in
  schema|load|verify|all) ;;
  *) echo "usage: $0 [schema|load|verify|all]" >&2; exit 2 ;;
esac

if [[ ! -x "${PYTHON}" ]]; then
  echo "No Python at ${PYTHON}" >&2
  echo "Create it with: python3 -m venv ${SCRIPT_DIR}/.venv && ${SCRIPT_DIR}/.venv/bin/pip install -r ${SCRIPT_DIR}/requirements.txt" >&2
  echo "Or point PYTHON= at an interpreter that has gremlinpython installed." >&2
  exit 1
fi

# If something is already serving the port, use it rather than fighting over it.
if "${PYTHON}" -c "import socket;s=socket.socket();s.settimeout(2);s.connect(('127.0.0.1',${PORT}))" 2>/dev/null; then
  echo "port ${PORT} already open -- using it"
else
  echo "starting port-forward to svc/${SERVICE} in ${NAMESPACE}"
  kubectl -n "${NAMESPACE}" port-forward "svc/${SERVICE}" "${PORT}:${PORT}" >/dev/null 2>&1 &
  PF_PID=$!
  trap 'kill "${PF_PID}" 2>/dev/null || true' EXIT

  for _ in $(seq 1 30); do
    if "${PYTHON}" -c "import socket;s=socket.socket();s.settimeout(2);s.connect(('127.0.0.1',${PORT}))" 2>/dev/null; then
      break
    fi
    sleep 2
  done

  if ! "${PYTHON}" -c "import socket;s=socket.socket();s.settimeout(2);s.connect(('127.0.0.1',${PORT}))" 2>/dev/null; then
    echo "port ${PORT} never opened. Is the pod ready?  kubectl -n ${NAMESPACE} get pods" >&2
    exit 1
  fi
fi

PHASE="${PHASE}" GREMLIN_URL="${GREMLIN_URL:-ws://localhost:${PORT}/gremlin}" \
  exec "${PYTHON}" "${SCRIPT_DIR}/migrate.py"
