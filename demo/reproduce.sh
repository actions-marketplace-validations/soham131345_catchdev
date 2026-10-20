#!/usr/bin/env bash
# Reproduces the demo from demo/README.md: catch flags the real
# SECRET_KEY_FALLBACKS regression that lived in Flask 3.1.0 for ~4 months.
set -euo pipefail

git clone https://github.com/pallets/flask.git flask-demo
cd flask-demo
git checkout fb541598~1          # one commit before the fix — bug is live
git show fb541598:tests/test_basic.py > tests/test_basic.py
pip install -e . "pytest==8.3.5"
git add -A
catch
echo "exit code: $?"
