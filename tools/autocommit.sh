#!/bin/bash
# Autosave: commit (and push) the repo every 3 minutes until Sunday's 4:00 PM code freeze.
# Stop it any time with:  touch /Users/odon/Desktop/Hackathon/.autocommit-stop
REPO=/Users/odon/Desktop/Hackathon
DEADLINE=$(date -j -f "%Y-%m-%d %H:%M" "2026-10-04 16:05" +%s)
cd "$REPO" || exit 1
rm -f .autocommit-stop
while [ "$(date +%s)" -lt "$DEADLINE" ] && [ ! -f .autocommit-stop ]; do
  if [ ! -f .git/index.lock ]; then
    git add -A
    if ! git diff --cached --quiet; then
      git commit -q -m "Autosave $(date '+%a %H:%M')

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && echo "$(date '+%H:%M:%S') committed $(git rev-parse --short HEAD)"
    fi
    git push -q origin main 2>&1 | sed "s/^/$(date '+%H:%M:%S') push: /"
  else
    echo "$(date '+%H:%M:%S') skipped: another git command is running"
  fi
  sleep 180
done
echo "$(date '+%H:%M:%S') autosave stopped"
