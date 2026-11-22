#!/bin/bash
# All tests, offscreen, temp files, no real claude / network. Run: tests/run.sh
cd "$(dirname "$0")/.." || exit 1
unset DISPLAY QT_SCREEN_SCALE_FACTORS SESSION_MANAGER
export QT_QPA_PLATFORM=offscreen
rc=0
for t in tests/test_counter.py tests/test_mdhtml.py tests/test_window.py tests/test_features.py; do
    echo "== $t"
    python3 -I "$t" "$@" || rc=1
done
exit $rc
