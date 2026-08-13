#!/bin/bash
# run_weekly_optimization.sh
# Hermes Strategy Auto-Optimization - runs weekly
# Reviews all strategies, tunes parameters, evolves winning patterns

HERMES_HOME="/root/.hermes/profiles/trader"
SCRIPTS_DIR="$HERMES_HOME/scripts"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') ===== Hermes Weekly Strategy Optimization ====="

cd "$SCRIPTS_DIR"
python3 hermes_freqtrade_controller.py --optimize

echo "Optimization complete"
