#!/bin/bash
#
# Register the ingest and daily runs with launchd on macOS.
#
# launchd rather than cron: it survives sleep. A run missed while the Mac was
# asleep fires once after it wakes, instead of being silently skipped, which is
# what a laptop-hosted schedule needs.
#
# Usage:   ./scripts/install-launchd.sh [--uninstall]
# Requires: a .venv in the project directory and a .env holding OMI_API_KEY.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$PROJECT_DIR/log"
INGEST_LABEL="com.mgc.omi-digest.ingest"
DAILY_LABEL="com.mgc.omi-digest.daily"

usage() {
    echo "usage: $(basename "$0") [--uninstall]" >&2
    exit 2
}

uninstall() {
    for label in "$INGEST_LABEL" "$DAILY_LABEL"; do
        plist="$AGENTS_DIR/$label.plist"
        if [ -f "$plist" ]; then
            launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
            rm -f "$plist"
            echo "removed $label"
        fi
    done
    echo
    echo "定期実行を解除しました。ログとデータは $PROJECT_DIR に残っています。"
}

write_plist() {
    local label="$1" schedule_xml="$2" command="$3"
    cat > "$AGENTS_DIR/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>cd '$PROJECT_DIR' &amp;&amp; exec ./.venv/bin/python -m meeting_digest $command</string>
    </array>
$schedule_xml
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/$label.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/$label.log</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLIST
}

load_agent() {
    local label="$1"
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/$label.plist"
    echo "installed $label"
}

main() {
    if [ $# -gt 1 ]; then usage; fi
    if [ "${1:-}" = "--uninstall" ]; then uninstall; return; fi
    if [ $# -eq 1 ]; then usage; fi

    if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
        echo "error: $PROJECT_DIR/.venv が見つかりません。先に以下を実行してください:" >&2
        echo "  cd '$PROJECT_DIR' && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt" >&2
        exit 1
    fi
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        echo "error: $PROJECT_DIR/.env が見つかりません。cp .env.example .env で作成し、" >&2
        echo "       OMI_API_KEY を設定してから再実行してください。" >&2
        exit 1
    fi

    mkdir -p "$AGENTS_DIR" "$LOG_DIR"

    # Hourly. StartInterval counts from load; a run missed during sleep fires
    # once on wake rather than accumulating.
    write_plist "$INGEST_LABEL" "    <key>StartInterval</key>
    <integer>3600</integer>
    <key>RunAtLoad</key>
    <true/>" "ingest --json"

    # 08:00 local. StartCalendarInterval catches up after sleep, so a Mac closed
    # overnight still produces the previous day's review in the morning.
    write_plist "$DAILY_LABEL" "    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>" "daily --json"

    load_agent "$INGEST_LABEL"
    load_agent "$DAILY_LABEL"

    echo
    echo "定期実行を登録しました。"
    echo "  取り込み : 1時間ごと（登録直後に1回実行されます）"
    echo "  振り返り : 毎朝 8:00（前日分）"
    echo
    echo "ログ      : $LOG_DIR/"
    echo "状態確認  : launchctl list | grep omi-digest"
    echo "解除      : ./scripts/install-launchd.sh --uninstall"
}

main "$@"
