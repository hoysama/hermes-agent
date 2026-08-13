#!/bin/bash
# Hermes AI Trading Manager - Cron Report Runner
# Called by Hermes internal scheduler every 10 minutes
# Uses persistent Freqtrade storage

cd /root/.hermes/profiles/trader/scripts
python3 /root/.hermes/profiles/trader/scripts/hermes_full_report.py
