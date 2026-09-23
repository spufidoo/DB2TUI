#!/usr/bin/env bash
# Per-boot startup: make sure the local Db2 server is running and seeded so the
# app can be run end to end. Idempotent and returns once Db2 is ready.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$REPO_ROOT/.cursor/db2-up.sh"
