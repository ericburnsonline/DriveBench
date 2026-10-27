#!/usr/bin/env bash
# DriveBench automated test runner. No root privileges are needed.
set -uo pipefail

# Run relative to this script, even when launched from another directory.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1

mkdir -p logs || {
    echo "ERROR: Unable to create the logs/ directory." >&2
    exit 1
}

stamp="$(date +'%Y%m%d-%H%M%S')"
log_file="logs/tests-${stamp}-$$.log"

{
    printf 'DriveBench automated tests\n'
    printf 'Started: %s\n' "$(date -Is)"
    printf 'Project: %s\n' "$PWD"
    printf 'Log: %s\n\n' "$log_file"

    if ! command -v python3 >/dev/null 2>&1; then
        echo 'ERROR: Python 3 not found. Install with: sudo apt install python3'
        false
    elif [[ ! -d tests ]]; then
        echo 'ERROR: No tests/ directory found alongside run-tests.sh.'
        false
    else
        python3 --version
        python3 -m unittest discover -s tests -v
    fi
} 2>&1 | tee "$log_file"
result=${PIPESTATUS[0]}

printf '\nTest output saved to: %s/%s\n' "$PWD" "$log_file"
if (( result == 0 )); then
    echo 'Result: PASS'
else
    printf 'Result: FAIL (exit code %d)\n' "$result"
fi
exit "$result"
