#!/bin/bash
# Hermes AI Trading Cron Job
# Runs every 3 minutes. Entry analysis runs every 20 minutes; exit review runs
# every 3 minutes for open positions only.

set -euo pipefail

HERMES_HOME="/root/.hermes/profiles/trader"
SCRIPTS_DIR="$HERMES_HOME/scripts"
FREQTRADE_DIR="/root/.freqtrade"
LOG_FILE="$HERMES_HOME/cron_output.log"
LOCK_FILE="$HERMES_HOME/trading_cycle.lock"
LOCK_OWNER="$LOCK_FILE/owner"

log_lock() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

lock_owner_active() {
    local pid="$1"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    local command
    command=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    [[ "$command" == *run_trading_cycle.sh* || "$command" == *hermes_freqtrade_controller.py* ]]
}

acquire_cycle_lock() {
    if mkdir "$LOCK_FILE" 2>/dev/null; then
        printf 'pid=%s\nstarted_at=%s\n' "$$" "$(date +%s)" > "$LOCK_OWNER"
        return 0
    fi

    local owner_pid='' started_at='' now age lock_mtime
    if [ -f "$LOCK_OWNER" ]; then
        owner_pid=$(sed -n 's/^pid=//p' "$LOCK_OWNER" | head -n 1)
        started_at=$(sed -n 's/^started_at=//p' "$LOCK_OWNER" | head -n 1)
    fi
    now=$(date +%s)
    age=0
    [[ "$started_at" =~ ^[0-9]+$ ]] && age=$((now - started_at))
    if [ "$age" -eq 0 ]; then
        lock_mtime=$(stat -c %Y "$LOCK_FILE" 2>/dev/null || printf '%s' "$now")
        [[ "$lock_mtime" =~ ^[0-9]+$ ]] && age=$((now - lock_mtime))
    fi

    if lock_owner_active "$owner_pid"; then
        log_lock "Cycle skipped: previous cycle still running (pid=$owner_pid age=${age}s)"
        return 1
    fi

    # A missing/dead owner is stale. An unreadable lock is only removed after
    # a grace period to avoid racing with a process while its metadata is being
    # written.
    if [ -n "$owner_pid" ] || [ "$age" -ge 600 ]; then
        rm -f "$LOCK_OWNER"
        rmdir "$LOCK_FILE" 2>/dev/null || true
        log_lock "Removed stale trading_cycle.lock (pid=${owner_pid:-unknown} age=${age}s)"
        mkdir "$LOCK_FILE" 2>/dev/null || return 1
        printf 'pid=%s\nstarted_at=%s\n' "$$" "$(date +%s)" > "$LOCK_OWNER"
        return 0
    fi

    log_lock "Cycle skipped: lock metadata unavailable (age=${age}s)"
    return 1
}

cleanup_cycle_lock() {
    if [ -f "$LOCK_OWNER" ] && grep -qx "pid=$$" "$LOCK_OWNER"; then
        rm -f "$LOCK_OWNER"
        rmdir "$LOCK_FILE" 2>/dev/null || true
    fi
}

if ! acquire_cycle_lock; then
    exit 0
fi
trap cleanup_cycle_lock EXIT

# Timestamp
echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG_FILE"

# 1. Run setup to ensure Freqtrade config/strategy are in place
bash "$HERMES_HOME/freqtrade/setup.sh" >> "$LOG_FILE" 2>&1

# 2. Check if Freqtrade is running, start if not
if ! curl -s -u hermes:hermes123 http://127.0.0.1:8080/api/v1/ping > /dev/null 2>&1; then
    echo "Freqtrade not responding, starting..." >> "$LOG_FILE"
    cd "$FREQTRADE_DIR"
    nohup python3 -m freqtrade trade --config config/config.json --strategy HermesExecutionStrategy >> "$LOG_FILE" 2>&1 &
    sleep 5
    
    # Wait for API to be ready
    for i in {1..10}; do
        if curl -s -u hermes:hermes123 http://127.0.0.1:8080/api/v1/ping > /dev/null 2>&1; then
            echo "Freqtrade started successfully" >> "$LOG_FILE"
            break
        fi
        sleep 2
    done
else
    echo "Freqtrade API responding" >> "$LOG_FILE"
fi

# 3. Run Hermes trading cycle. A stuck provider request must not hold the
# five-minute lock forever or suppress every later report.
cd "$SCRIPTS_DIR"
set +e
minute=$(date +%M)
if [ "$((10#$minute % 20))" -eq 0 ]; then
    cycle_mode="entry"
else
    cycle_mode="exit"
fi
echo "Cycle mode: $cycle_mode" >> "$LOG_FILE"
if [ "$cycle_mode" = "entry" ]; then
    timeout --foreground 900s python3 -u hermes_freqtrade_controller.py --cycle >> "$LOG_FILE" 2>&1
else
    timeout --foreground 300s python3 -u hermes_freqtrade_controller.py --exit-review >> "$LOG_FILE" 2>&1
fi
cycle_status=$?
set -e

if [ "$cycle_status" -eq 0 ]; then
    echo "Cycle completed" >> "$LOG_FILE"
else
    echo "Cycle ended without completion (status=$cycle_status)" >> "$LOG_FILE"
fi
echo "" >> "$LOG_FILE"

# Never publish the previous completed cycle as if it were the current one.
# A timed-out cycle gets a short status message; the full report is emitted
# only when this exact cycle completed successfully.
if [ "$cycle_status" -eq 0 ]; then
    if report_output=$(bash "$SCRIPTS_DIR/run_report.sh" 2>&1); then
        printf '%s\n' "$report_output" >> "$LOG_FILE"
        printf '%s\n' "$report_output"
    else
        printf 'Report generation failed after completed cycle\n%s\n' "$report_output" >> "$LOG_FILE"
        printf 'Report generation failed after completed cycle\n%s\n' "$report_output"
    fi
else
    incomplete="Hermes Trading Cycle incomplete: status=$cycle_status; full report withheld to avoid stale data"
    printf '%s\n' "$incomplete" >> "$LOG_FILE"
    printf '%s\n' "$incomplete"
fi
