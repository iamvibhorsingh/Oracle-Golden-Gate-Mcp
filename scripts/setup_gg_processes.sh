#!/usr/bin/env bash
# setup_gg_processes.sh
# Configures 10 Extracts + 10 Replicats on the GoldenGate Free Docker instance
# via the Microservices REST API.
#
# Usage:
#   bash scripts/setup_gg_processes.sh [GG_URL] [ADMIN_USER] [ADMIN_PWD] [DEPLOYMENT]
#
# Defaults match docker-compose.yml / .env.local values.

set -euo pipefail

GG_URL="${1:-https://localhost:9100}"
ADMIN="${2:-oggadmin}"
PWD="${3:-Welcome1}"
DEPLOY="${4:-LocalTest}"
DB_HOST="oracle-db"
DB_PORT="1521"
DB_SERVICE="FREEPDB1"
SRC_USER="ggadmin"
SRC_PWD="GGMCP_Admin123"

CURL="curl -sk -u ${ADMIN}:${PWD} -H Content-Type:application/json"

TABLES=(customers orders order_items products inventory_events \
        payments audit_log user_sessions notifications metrics_raw)

echo "=== GoldenGate Stress-Test Process Setup ==="
echo "Target: ${GG_URL}  Deployment: ${DEPLOY}"

# ── 0. Wait for GG to be healthy ────────────────────────────
echo -n "Waiting for GoldenGate REST API..."
for i in $(seq 1 60); do
  if $CURL "${GG_URL}/services/v2/deployments" -o /dev/null 2>/dev/null; then
    echo " ready."
    break
  fi
  echo -n "."
  sleep 5
done

# ── 1. Add database credentials ─────────────────────────────
echo "Adding database credentials..."
for ALIAS in ggadmin_src ggadmin_tgt; do
  $CURL -X POST "${GG_URL}/services/v2/credentials/OracleGoldenGate/${DEPLOY}/OracleGoldenGate/${ALIAS}" \
    -d "{
      \"userid\": \"${SRC_USER}@${DB_HOST}:${DB_PORT}/${DB_SERVICE}\",
      \"password\": \"${SRC_PWD}\"
    }" 2>/dev/null && echo "  credential ${ALIAS}: OK" || echo "  credential ${ALIAS}: SKIP (may exist)"
done

# ── 2. Create 10 Extract processes ──────────────────────────
echo "Creating Extract processes..."
for i in $(seq 0 9); do
  TBL="${TABLES[$i]}"
  # EXT name max 8 chars for classic-mode compat
  NAME=$(printf "EXT%02d" $((i+1)))
  TRAIL_CODE=$(printf "%c%c" $(printf "\\x$(printf '%02x' $((97+i)))") "a")

  PARAM="EXTRACT ${NAME}\nUSERIDALIAS ggadmin_src DOMAIN OracleGoldenGate\nEXTTRAIL /u03/trails/${TRAIL_CODE}\nTABLE FREEPDB1.GG_SRC.${TBL};"

  echo -n "  ${NAME} (${TBL})... "
  $CURL -X POST \
    "${GG_URL}/services/v2/extracts/${NAME}" \
    -d "{
      \"config\": [\"$(echo -e ${PARAM} | sed 's/$/\\n/' | tr -d '\n' | sed 's/\\n$//; s/\\n/\",\"/g')\"],
      \"source\": { \"trailName\": \"/u03/trails/${TRAIL_CODE}\" },
      \"begin\": \"now\",
      \"status\": \"stopped\"
    }" 2>/dev/null && echo "OK" || echo "SKIP"
done

# ── 3. Create 10 Replicat processes ─────────────────────────
echo "Creating Replicat processes..."
for i in $(seq 0 9); do
  TBL="${TABLES[$i]}"
  NAME=$(printf "REP%02d" $((i+1)))
  TRAIL_CODE=$(printf "%c%c" $(printf "\\x$(printf '%02x' $((97+i)))") "a")

  PARAM="REPLICAT ${NAME}\nUSERIDALIAS ggadmin_tgt DOMAIN OracleGoldenGate\nMAP FREEPDB1.GG_SRC.${TBL}, TARGET FREEPDB1.GG_TGT.${TBL};"

  echo -n "  ${NAME} (${TBL})... "
  $CURL -X POST \
    "${GG_URL}/services/v2/replicats/${NAME}" \
    -d "{
      \"config\": [\"$(echo -e ${PARAM} | sed 's/$/\\n/' | tr -d '\n' | sed 's/\\n$//; s/\\n/\",\"/g')\"],
      \"source\": { \"trailName\": \"/u03/trails/${TRAIL_CODE}\" },
      \"begin\": \"now\",
      \"mode\": \"parallel\",
      \"status\": \"stopped\"
    }" 2>/dev/null && echo "OK" || echo "SKIP"
done

# ── 4. Start all processes ──────────────────────────────────
echo "Starting Extract processes..."
for i in $(seq 1 10); do
  NAME=$(printf "EXT%02d" $i)
  echo -n "  Starting ${NAME}... "
  $CURL -X PATCH \
    "${GG_URL}/services/v2/extracts/${NAME}" \
    -d '{"status":"running"}' 2>/dev/null && echo "OK" || echo "SKIP"
done

echo "Starting Replicat processes..."
for i in $(seq 1 10); do
  NAME=$(printf "REP%02d" $i)
  echo -n "  Starting ${NAME}... "
  $CURL -X PATCH \
    "${GG_URL}/services/v2/replicats/${NAME}" \
    -d '{"status":"running"}' 2>/dev/null && echo "OK" || echo "SKIP"
done

# ── 5. Verify ───────────────────────────────────────────────
echo ""
echo "=== Process Summary ==="
echo "Extracts:"
$CURL "${GG_URL}/services/v2/extracts" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "(could not list)"
echo ""
echo "Replicats:"
$CURL "${GG_URL}/services/v2/replicats" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "(could not list)"
echo ""
echo "Done. Run the MCP stress test:"
echo "  pytest tests/integration/test_stress_mcp.py -v --tb=short"
