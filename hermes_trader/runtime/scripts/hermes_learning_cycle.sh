#!/bin/bash
# hermes_learning_cycle.sh
HERMES_HOME="/root/.hermes/profiles/trader"
SCRIPTS_DIR="$HERMES_HOME/scripts"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') ===== Learning Cycle ====="
cd "$SCRIPTS_DIR"
python3 hermes_learning_engine.py --analyze 2>&1 | tail -5
echo "Learning complete"
