#!/usr/bin/env bash
# Pohst Signal: bring up Postiz and (re)apply server-side patches.
# Use this INSTEAD of 'docker compose up -d' after any compose change or upgrade.
# Retries container restarts because Postiz v2.24 can hang at startup (Node module-load deadlock).
set -uo pipefail
cd /path/to/postiz

ATTEMPTS=3          # restarts before giving up
WAIT_SECS=180       # per attempt

backend_up() {
  docker compose exec -T postiz node -e "require('http').get('http://127.0.0.1:3000/',r=>process.exit(0)).on('error',()=>process.exit(1))" >/dev/null 2>&1
}

wait_backend() {
  local i
  for i in $(seq 1 $((WAIT_SECS/10))); do
    backend_up && return 0
    sleep 10
  done
  return 1
}

# Ensure backend is up; restart container and retry if it hangs.
ensure_up() {
  echo "== Waiting for backend (up to ${WAIT_SECS}s)..."
  wait_backend && { echo "OK    Backend is up"; return 0; }
  local a
  for a in $(seq 1 $ATTEMPTS); do
    echo "WARN  Backend not responding - restart attempt $a/$ATTEMPTS"
    docker compose restart postiz >/dev/null
    wait_backend && { echo "OK    Backend is up after restart $a"; return 0; }
  done
  echo "ERROR Backend still down after $ATTEMPTS restarts - check: docker compose logs --tail=50 postiz"
  exit 1
}

echo "== docker compose up -d $*"
docker compose up -d "$@" || { echo "ERROR docker compose up failed"; exit 1; }
ensure_up

echo "== Applying patches"
OUT=$( { python3 postiz_update.py --apply; python3 postiz_r2_delete_fix.py --apply; python3 postiz_stripe_match.py --apply; } 2>&1 )
STATUS=$?
echo "$OUT" | grep -E "OK|WARN|ERROR|Skipped|Pricing files|Text files|rewrote.*\[en\]" | tail -25
if [ $STATUS -ne 0 ] || grep -q "ERROR" <<<"$OUT"; then
  echo "ERROR A patch script failed - run them manually to see full output"
  exit 1
fi

if grep -q "Written and verified" <<<"$OUT"; then
  echo "== Patches written - restarting Postiz"
  docker compose restart postiz >/dev/null
  ensure_up
else
  echo "OK    Nothing new written - no restart needed"
fi

echo "== Verification"
python3 postiz_update.py | grep -E "Text: .* files OK|Skipped|All files OK"
python3 postiz_r2_delete_fix.py | grep -E "already patched|will soft-delete"
python3 postiz_stripe_match.py | grep -E "already patched|by metadata"
docker compose logs postiz 2>&1 | grep -E "Email service provider|Missing environment variable" | tail -2
echo "== Done. Branding/hiding is handled by nginx + pohst-brand.css (no action needed)."
