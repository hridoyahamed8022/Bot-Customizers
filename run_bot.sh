#!/bin/bash
export BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
export PORT="${PORT:-8080}"
cd /home/runner/workspace

RESTART_DELAY=5
MAX_DELAY=60
CRASH_COUNT=0

echo "[run_bot.sh] বট চালু হচ্ছে... (auto-restart সক্রিয়)"

while true; do
    python main.py
    EXIT_CODE=$?
    CRASH_COUNT=$((CRASH_COUNT + 1))

    if [ $EXIT_CODE -eq 0 ]; then
        echo "[run_bot.sh] বট স্বাভাবিকভাবে বন্ধ হয়েছে। পুনরায় চালু হচ্ছে..."
        RESTART_DELAY=5
    else
        echo "[run_bot.sh] বট ক্র্যাশ হয়েছে (exit code: $EXIT_CODE, crash #$CRASH_COUNT)। ${RESTART_DELAY}s পর পুনরায় চালু হবে..."
        sleep $RESTART_DELAY
        # প্রতিবার crash হলে delay একটু বাড়াও, সর্বোচ্চ MAX_DELAY পর্যন্ত
        RESTART_DELAY=$(( RESTART_DELAY * 2 > MAX_DELAY ? MAX_DELAY : RESTART_DELAY * 2 ))
    fi

    echo "[run_bot.sh] পুনরায় চালু হচ্ছে..."
done
