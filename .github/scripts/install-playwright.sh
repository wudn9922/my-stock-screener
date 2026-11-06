#!/usr/bin/env bash
# Installs Playwright browsers plus their Ubuntu packages for the Atlas review jobs.
#
# GitHub runners sometimes get an extremely slow Azure apt mirror: a single
# 13.5 MB package took more than four minutes and the 375px job hit its
# 30-minute limit before any test ran (#231, #232). Each attempt is bounded;
# a stalled one switches Ubuntu's signed packages to the official HTTPS
# archive and tries again. The browsers and tests are unchanged.
#
# Usage: install-playwright.sh <browser>...   (run from the npm project directory)
set -euo pipefail

attempts="${PLAYWRIGHT_INSTALL_ATTEMPTS:-3}"
limit="${PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS:-360}"
[ "$#" -gt 0 ] || { echo "usage: $0 <browser>..." >&2; exit 2; }

use_official_archive() {
  sudo python3 - <<'PY'
from pathlib import Path
apt = Path('/etc/apt')
paths = [apt / 'sources.list', *apt.glob('*mirrors*.txt'),
         *(apt / 'sources.list.d').glob('*.list'),
         *(apt / 'sources.list.d').glob('*.sources')]
for path in paths:
    if path.is_file():
        original = path.read_text()
        updated = original.replace('http://azure.archive.ubuntu.com/ubuntu',
                                   'https://archive.ubuntu.com/ubuntu')
        if updated != original:
            path.write_text(updated)
PY
}

# Jobs that already saw the Azure mirror stall can start on the official archive.
if [ "${PLAYWRIGHT_PREFER_OFFICIAL_ARCHIVE:-0}" = 1 ]; then use_official_archive; fi

for attempt in $(seq 1 "$attempts"); do
  started=$SECONDS
  if timeout --kill-after=30 "$limit" npx playwright install --with-deps "$@"; then
    echo "Playwright $* installed on attempt $attempt in $((SECONDS - started))s"
    exit 0
  fi
  echo "::warning::Playwright install attempt $attempt/$attempts failed or exceeded ${limit}s; switching apt to the official archive"
  # A killed apt run can leave a lock or half-configured packages behind.
  sudo pkill -x apt-get || true
  sudo pkill -x dpkg || true
  sudo dpkg --configure -a || true
  use_official_archive
done
echo "::error::Playwright install failed after $attempts attempts"
exit 1
