#!/usr/bin/env bash
# Reproduce everything: data collection -> features -> audit -> baseline -> EDA ->
# feature selection -> model tuning -> one-shot test evaluation.
#
#   ./run.sh
#
# First run downloads from the Chess.com API (about 10 min, ~0.8 GB cache in data/raw).
# Later runs read the cache and take a few minutes, mostly model tuning.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

for step in collect features audit baseline eda feature_selection model evaluate; do
  echo "=== $step"
  .venv/bin/python -m "src.$step"
done
echo "=== done: tables in data/processed/, results in results/"
