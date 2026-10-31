#!/usr/bin/env bash
# Switch this folder to an earlier build (or back to the latest), then build as usual.
#
#   bash tools/use_version.sh 61        # go to build v61
#   bash tools/use_version.sh latest    # back to the newest build
#   bash stage2/tt_android_stage2.sh    # then build
#
# Builds from v56 on build cleanly this way. Older ones (v55 and earlier) expected an SDK
# patched in place by the builds before them; to go back that far, re-run stage 1 first.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
want="${1:-}"
[ -n "$want" ] || { echo "usage: bash tools/use_version.sh <number>|latest"; exit 1; }
git fetch -q origin
if [ "$want" = "latest" ]; then
  git checkout -q main && git pull -q
  echo "Now on the latest build: $(git log -1 --format=%s | cut -c1-70)"
  exit 0
fi
want="${want#v}"
# the newest commit whose message starts with "v<number>" (includes follow-up fixes like
# "v55 build fix"), searched on main so the list never depends on where you are now
commit=$(git log origin/main --format='%h %s' | grep -E "^[0-9a-f]+ v${want}([: ]|$)" | head -1 | cut -d' ' -f1 || true)
[ -n "$commit" ] || { echo "No build v$want found."; exit 1; }
git checkout -q "$commit"
echo "Now on build v$want ($commit): $(git log -1 --format=%s | cut -c1-70)"
[ "$want" -ge 56 ] 2>/dev/null || echo "Note: builds before v56 may need stage 1 re-run first."
echo "Build it with: bash stage2/tt_android_stage2.sh   (back to newest: bash tools/use_version.sh latest)"
