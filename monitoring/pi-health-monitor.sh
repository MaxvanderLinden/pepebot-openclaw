#!/bin/bash

# Raspberry Pi Health Monitor with Telegram Alerts
LOG_FILE="$HOME/pi-health.log"
LAST_ALERT_FILE="$HOME/.pi_last_alert"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
CURRENT_TIME=$(date +%s)

# Get Telegram user ID from environment variable
TELEGRAM_USER_ID="${TELEGRAM_USER_ID:-}"  # Set via environment variable

if [ -z "$TELEGRAM_USER_ID" ]; then
    echo "ERROR: TELEGRAM_USER_ID environment variable not set"
    exit 1
fi

# Get CPU temperature
CPU_TEMP=$(vcgencmd measure_temp | egrep -o '[0-9.]+')

# Get CPU usage
CPU_USAGE=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1)

# Get memory usage
MEM_TOTAL=$(free -m | awk 'NR==2{print $2}')
MEM_USED=$(free -m | awk 'NR==2{print $3}')
MEM_PERCENT=$(awk "BEGIN {printf \"%.1f\", ($MEM_USED/$MEM_TOTAL)*100}")

# Get disk usage
DISK_USAGE=$(df -h / | awk 'NR==2{print $5}' | cut -d'%' -f1)

# Get throttling status
THROTTLED=$(vcgencmd get_throttled | cut -d'=' -f2)

# Check if OpenClaw gateway is running
OPENCLAW_STATUS=$(systemctl --user is-active openclaw-gateway 2>/dev/null || echo "unknown")

# Log to file (always)
echo "$TIMESTAMP | Temp: ${CPU_TEMP}°C | CPU: ${CPU_USAGE}% | RAM: ${MEM_USED}MB/${MEM_TOTAL}MB (${MEM_PERCENT}%) | Disk: ${DISK_USAGE}% | Throttled: $THROTTLED | OpenClaw: $OPENCLAW_STATUS" >> "$LOG_FILE"

# Build alert message if there are problems
ALERT_MSG=""

# Check for high temperature (>75°C)
if (( $(echo "$CPU_TEMP > 75" | bc -l) )); then
    ALERT_MSG="${ALERT_MSG}🔥 High temperature: ${CPU_TEMP}°C\n"
    echo "$TIMESTAMP | WARNING: High temperature detected: ${CPU_TEMP}°C" >> "$LOG_FILE"
fi

# Check for high memory usage (>85%)
if (( $(echo "$MEM_PERCENT > 85" | bc -l) )); then
    ALERT_MSG="${ALERT_MSG}⚠️ High RAM usage: ${MEM_PERCENT}%\n"
    echo "$TIMESTAMP | WARNING: High memory usage: ${MEM_PERCENT}%" >> "$LOG_FILE"
fi

# Check for throttling
if [ "$THROTTLED" != "0x0" ]; then
    ALERT_MSG="${ALERT_MSG}⚡ Throttling detected: $THROTTLED\n"
    echo "$TIMESTAMP | WARNING: Throttling detected: $THROTTLED" >> "$LOG_FILE"
fi

# Check if OpenClaw is down
if [ "$OPENCLAW_STATUS" != "active" ]; then
    ALERT_MSG="${ALERT_MSG}🦞 OpenClaw gateway is $OPENCLAW_STATUS\n"
    echo "$TIMESTAMP | WARNING: OpenClaw gateway is $OPENCLAW_STATUS" >> "$LOG_FILE"
fi

# Send Telegram alert if needed
if [ ! -z "$ALERT_MSG" ]; then
    # Check if we alerted in the last hour (prevents spam)
    SHOULD_ALERT=1
    
    if [ -f "$LAST_ALERT_FILE" ]; then
        LAST_ALERT=$(cat "$LAST_ALERT_FILE")
        TIME_DIFF=$((CURRENT_TIME - LAST_ALERT))
        
        # Don't alert if less than 1 hour has passed
        if [ $TIME_DIFF -lt 3600 ]; then
            SHOULD_ALERT=0
        fi
    fi
    
    # Send alert via Telegram
    if [ $SHOULD_ALERT -eq 1 ]; then
        FULL_ALERT="🚨 Raspberry Pi Alert

${ALERT_MSG}
Time: $TIMESTAMP"
        
        # Send message through OpenClaw to Telegram
        openclaw message send -t telegram:$TELEGRAM_USER_ID -m "$FULL_ALERT"
        
        # Update last alert time
        echo "$CURRENT_TIME" > "$LAST_ALERT_FILE"
        
        echo "$TIMESTAMP | Telegram alert sent" >> "$LOG_FILE"
    fi
fi
