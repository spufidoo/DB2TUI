#!/usr/bin/env bash
# One-time (idempotent) setup for the DB2TUI development environment.
#   * system packages (Python build deps + Docker for the local Db2 server)
#   * Python virtualenv with the project dependencies
#   * the "PYTHON" CLI DSN the app connects to (db.connect(dsn="PYTHON"))
#   * pre-provisions the local Db2 server so it is baked into the snapshot
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "[install] installing system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  -o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confdef \
  python3-venv python3-dev build-essential docker.io

echo "[install] creating Python virtualenv"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "[install] configuring the PYTHON CLI DSN for the local Db2 server"
CFG_DIR="$(python -c 'import ibm_db, os; print(os.path.join(os.path.dirname(ibm_db.__file__), "clidriver", "cfg"))')"
cat > "$CFG_DIR/db2cli.ini" <<'INI'
[PYTHON]
Database=TESTDB
Protocol=TCPIP
Hostname=127.0.0.1
Port=50000
UID=db2inst1
PWD=db2inst1pw
INI

echo "[install] provisioning the local Db2 server (pull + first-time init)"
bash "$REPO_ROOT/.cursor/db2-up.sh"

echo "[install] done"
