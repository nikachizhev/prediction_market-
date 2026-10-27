"""PreToolUse hook: never run setup_bot / run_telegram_bot locally with the production bot token.

Local setup_bot rewrites the bot's menu button to the local SITE_URL for every user, and a local
run_telegram_bot fights the production bot for updates («Conflict»). Over ssh to the server is fine.
"""
import json
import re
import sys

data = json.load(sys.stdin)
command = (data.get("tool_input") or {}).get("command", "")
if re.search(r"manage\.py\s+(setup_bot|run_telegram_bot)\b", command) and not re.match(r"\s*ssh\s", command):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "Локально setup_bot и run_telegram_bot не запускаем: токен в .env — боевой. setup_bot перепишет "
            "кнопку меню у всех пользователей на локальный SITE_URL, а второй бот даст «Conflict» с прод-ботом. "
            "Запускай на сервере через ssh (см. CLAUDE.md, «Сервер (прод)») или заведи отдельного тестового бота."
        ),
    }}, ensure_ascii=False))
