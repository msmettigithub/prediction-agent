#!/bin/bash
# Paper trading cron job — run every 4 hours
# Setup: crontab -e
# Add:   0 */4 * * * /bin/bash "/Users/domitian/sfcompute agent test/prediction_agent/scripts/cron_paper.sh"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

# Source env vars
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

LOGFILE="$SCRIPT_DIR/logs/paper_trading.log"

# Log rotation: if log exceeds 10MB, rotate
if [ -f "$LOGFILE" ]; then
    SIZE=$(stat -f%z "$LOGFILE" 2>/dev/null || stat -c%s "$LOGFILE" 2>/dev/null || echo 0)
    if [ "$SIZE" -gt 10485760 ]; then
        mv "$LOGFILE" "$LOGFILE.1"
    fi
fi

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ===" >> "$LOGFILE"
MOCK_TOOLS=false python3 main.py paper --auto --edge-threshold=0.05 >> "$LOGFILE" 2>&1
echo "" >> "$LOGFILE"
