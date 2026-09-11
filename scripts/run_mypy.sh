#!/bin/bash
# Runs mypy across every checkable unit and concatenates the output.
#
# Can't run mypy once across the whole tree: worker/* and mailer/* are
# independently deployed services with no __init__.py between them, so a
# single invocation collides on repeated module names (worker/api/app.py and
# worker/analytics/app.py both resolve to module "app"). Each directory below
# is type-checked in its own invocation instead.
#
# Usage:
#   scripts/run_mypy.sh | mypy-baseline filter   # CI gate
#   scripts/run_mypy.sh | mypy-baseline sync     # regenerate mypy-baseline.txt
set -uo pipefail

cd "$(dirname "$0")/.."

TARGETS=(shared database alembic config)
for d in worker/*/ mailer/*/; do
  TARGETS+=("${d%/}")
done

for t in "${TARGETS[@]}"; do
  [ -d "$t" ] || continue
  find "$t" -name '*.py' -print -quit | grep -q . || continue
  mypy "$t" --ignore-missing-imports --no-error-summary
done

exit 0
