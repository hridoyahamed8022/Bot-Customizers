#!/bin/bash
export BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
export PORT="${PORT:-8080}"
cd /home/runner/workspace
python main.py
