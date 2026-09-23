#!/usr/bin/env bash
# Bring up a local Db2 (LUW) Community Edition server for development and
# testing of DB2TUI. Idempotent: safe to run on every boot. It starts the
# Docker daemon, creates/starts the Db2 container, waits for readiness and
# seeds a small demo dataset (DEMO.CUSTOMERS).
set -euo pipefail

DB2_IMAGE="icr.io/db2_community/db2:latest"
DB2_CONTAINER="db2"
DB2_PW="db2inst1pw"
DB2_DB="TESTDB"

log() { echo "[db2-up] $*"; }

ensure_docker() {
  if sudo docker info >/dev/null 2>&1; then
    return 0
  fi
  log "starting dockerd (vfs storage driver)"
  sudo bash -c 'nohup dockerd --storage-driver=vfs >/var/log/dockerd.log 2>&1 &'
  for _ in $(seq 1 30); do
    sudo docker info >/dev/null 2>&1 && return 0
    sleep 1
  done
  log "ERROR: docker daemon did not become ready"
  sudo tail -n 20 /var/log/dockerd.log || true
  return 1
}

ensure_image() {
  if ! sudo docker image inspect "$DB2_IMAGE" >/dev/null 2>&1; then
    log "pulling $DB2_IMAGE (large, one-time)"
    sudo docker pull "$DB2_IMAGE"
  fi
}

ensure_container() {
  if ! sudo docker ps -a --format '{{.Names}}' | grep -qx "$DB2_CONTAINER"; then
    log "creating Db2 container"
    sudo docker run -itd --name "$DB2_CONTAINER" --privileged=true -p 50000:50000 \
      -e LICENSE=accept -e DB2INST1_PASSWORD="$DB2_PW" -e DBNAME="$DB2_DB" \
      -e ARCHIVE_LOGS=false -e AUTOCONFIG=false "$DB2_IMAGE"
  elif [ "$(sudo docker inspect -f '{{.State.Running}}' "$DB2_CONTAINER" 2>/dev/null)" != "true" ]; then
    log "starting existing Db2 container"
    sudo docker start "$DB2_CONTAINER"
  else
    log "Db2 container already running"
  fi
}

wait_ready() {
  log "waiting for Db2 to accept connections..."
  for _ in $(seq 1 120); do
    if sudo docker exec "$DB2_CONTAINER" su - db2inst1 -c "db2 connect to $DB2_DB" >/dev/null 2>&1; then
      log "Db2 is ready"
      return 0
    fi
    sleep 5
  done
  log "ERROR: Db2 did not become ready in time"
  sudo docker logs --tail 30 "$DB2_CONTAINER" || true
  return 1
}

seed_demo() {
  log "seeding demo data (idempotent)"
  sudo docker exec -i "$DB2_CONTAINER" su - db2inst1 -c "db2 -stf /dev/stdin" >/dev/null 2>&1 <<SQL || true
CONNECT TO $DB2_DB;
DROP TABLE DEMO.CUSTOMERS;
CREATE SCHEMA DEMO;
CREATE TABLE DEMO.CUSTOMERS (ID INT NOT NULL PRIMARY KEY, NAME VARCHAR(40), CITY VARCHAR(30), BALANCE DECIMAL(9,2));
INSERT INTO DEMO.CUSTOMERS VALUES (1,'Alice','London',1200.50),(2,'Bob','Berlin',980.00),(3,'Carol','Paris',4300.75),(4,'Dan','Athens',120.00),(5,'Eve','Madrid',7650.25);
COMMIT;
CONNECT RESET;
SQL
}

ensure_docker
ensure_image
ensure_container
wait_ready
seed_demo
log "done"
