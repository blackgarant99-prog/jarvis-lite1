#!/usr/bin/env python3
"""Jarvis Lite v0.3 — Telegram VDS management bot with inline buttons and audit log."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_IDS: set[int] = {
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}
SAFE_CONTAINERS: list[str] = [
    c.strip()
    for c in os.environ.get("SAFE_CONTAINERS", "api,bot,nginx,postgres").split(",")
    if c.strip()
]
COMMAND_TIMEOUT = int(os.environ.get("COMMAND_TIMEOUT", "20"))
DEFAULT_LOG_LINES = int(os.environ.get("DEFAULT_LOG_LINES", "80"))
MAX_LOG_LINES = int(os.environ.get("MAX_LOG_LINES", "500"))
AI_PROVIDER = os.environ.get("AI_PROVIDER", "local").lower()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")

API_BASE = f"https://api.telegram.org/bot{TOKEN}"
POLL_TIMEOUT = 30
ACTION_TTL = 90  # seconds until pending restart expires

# ── Logging ────────────────────────────────────────────────────────────────────

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("jarvis")

_audit_logger = logging.getLogger("audit")
_audit_handler = logging.FileHandler(LOG_DIR / "audit.log")
_audit_handler.setFormatter(logging.Formatter("%(message)s"))
_audit_logger.addHandler(_audit_handler)
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False


def audit(user_id: int, username: str, action: str, target: str, status: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "username": username,
        "action": action,
        "target": target,
        "status": status,
    }
    _audit_logger.info(json.dumps(entry, ensure_ascii=False))


# ── Pending restart actions ────────────────────────────────────────────────────

# code -> {user_id, container, expires_at}
_pending: dict[str, dict] = {}


def _purge_expired() -> None:
    now = time.time()
    expired = [k for k, v in _pending.items() if v["expires_at"] < now]
    for k in expired:
        del _pending[k]


def create_pending(user_id: int, container: str) -> str:
    _purge_expired()
    code = secrets.token_hex(4)
    _pending[code] = {
        "user_id": user_id,
        "container": container,
        "expires_at": time.time() + ACTION_TTL,
    }
    return code


def pop_pending(code: str, user_id: int) -> dict | None:
    _purge_expired()
    entry = _pending.get(code)
    if entry is None:
        return None
    if entry["user_id"] != user_id:
        return None
    if entry["expires_at"] < time.time():
        _pending.pop(code, None)
        return None
    return _pending.pop(code)


# ── Telegram API helpers ───────────────────────────────────────────────────────

def _tg_post(method: str, payload: dict, timeout: int = 10) -> dict:
    try:
        r = requests.post(f"{API_BASE}/{method}", json=payload, timeout=timeout)
        return r.json()
    except Exception as exc:
        logger.error("Telegram %s error: %s", method, exc)
        return {}


def send_message(
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
) -> dict:
    payload: dict = {"chat_id": chat_id, "text": text[:4096]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _tg_post("sendMessage", payload)


def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> dict:
    payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text[:4096]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _tg_post("editMessageText", payload)


def answer_callback(callback_id: str, text: str = "") -> dict:
    return _tg_post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})


def get_updates(offset: int) -> list:
    try:
        r = requests.post(
            f"{API_BASE}/getUpdates",
            json={
                "offset": offset,
                "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=POLL_TIMEOUT + 10,
        )
        return r.json().get("result", [])
    except requests.exceptions.Timeout:
        return []
    except Exception as exc:
        logger.error("getUpdates error: %s", exc)
        return []


# ── Shell helpers ──────────────────────────────────────────────────────────────

def run_cmd(args: list) -> tuple:
    """Run a command with a fixed argument list — never shell=True, never user input in args."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
        output = (result.stdout + result.stderr).strip()
        return output, result.returncode
    except subprocess.TimeoutExpired:
        return "Command timed out.", 1
    except FileNotFoundError:
        return f"Command not found: {args[0]}", 1
    except Exception as exc:
        return f"Execution error: {exc}", 1


def validate_container(name: str) -> bool:
    return name in SAFE_CONTAINERS


def clamp_lines(value: str | None) -> int:
    if value is None or not str(value).isdigit():
        return DEFAULT_LOG_LINES
    return max(1, min(int(value), MAX_LOG_LINES))


# ── Secret masking ─────────────────────────────────────────────────────────────

_SECRET_RE = re.compile(
    r"(?i)(token|key|secret|password|passwd|pwd|auth|bearer|api_key|apikey)\s*[=:]\s*\S+",
)


def mask_secrets(text: str) -> str:
    def _replace(m: re.Match) -> str:
        prefix = re.split(r"[=:]", m.group(0), 1)[0]
        return f"{prefix}=[REDACTED]"
    return _SECRET_RE.sub(_replace, text)


# ── Error patterns for /errors command ────────────────────────────────────────

ERROR_PATTERNS = [
    "error", "exception", "traceback", "failed", "fatal",
    "timeout", "permission denied", "connection refused",
    "database", "postgres", "redis", "502", "503", "504",
]

_SUGGESTIONS: dict[str, str] = {
    "connection refused": "Check if the target service is running and ports are open.",
    "timeout": "Service may be overloaded or unreachable — check resources.",
    "permission denied": "Check file/directory permissions and user privileges.",
    "traceback": "Python exception detected — review the full stack trace above.",
    "database": "Database connectivity issue — verify DB is running and credentials.",
    "postgres": "PostgreSQL issue — check DB container status and pg logs.",
    "redis": "Redis issue — check Redis container and memory usage.",
    "502": "Bad Gateway — upstream service may be down or crashing.",
    "503": "Service Unavailable — check if the app container is running.",
    "504": "Gateway Timeout — upstream response too slow.",
    "fatal": "Fatal error — service likely crashed, check full logs.",
    "failed": "Check service configuration and dependency health.",
}


# ── Local rule-based log analysis ─────────────────────────────────────────────

def local_analyze(logs: str) -> str:
    lines = logs.splitlines()
    found: dict[str, list[int]] = {}
    for i, line in enumerate(lines, 1):
        low = line.lower()
        for pat in ERROR_PATTERNS:
            if pat in low:
                found.setdefault(pat, []).append(i)

    if not found:
        return "No obvious issues found in the logs."

    parts = ["**Local analysis results:**\n"]
    for pat, line_nums in found.items():
        count = len(line_nums)
        sample = ", ".join(map(str, line_nums[:3]))
        parts.append(f"• `{pat}` — {count} occurrence(s), lines: {sample}")
        if pat in _SUGGESTIONS:
            parts.append(f"  → {_SUGGESTIONS[pat]}")
    return "\n".join(parts)


# ── AI-assisted analysis (optional) ───────────────────────────────────────────

_ANALYZE_PROMPT = (
    "Analyze these Docker container logs. "
    "Identify issues, explain what is wrong, and suggest fixes. "
    "Do NOT suggest running any commands automatically. "
    "Be concise (max 400 words).\n\nLogs:\n{logs}"
)


def _analyze_openai(logs: str, container: str) -> str:
    if not OPENAI_API_KEY:
        return local_analyze(logs) + "\n\n_OpenAI key not configured, used local analysis._"
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": OPENAI_MODEL,
                "messages": [{"role": "user", "content": _ANALYZE_PROMPT.format(logs=logs[-3000:])}],
                "max_tokens": 600,
            },
            timeout=30,
        )
        return r.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.error("OpenAI error: %s", exc)
        return local_analyze(logs) + "\n\n_OpenAI unavailable, used local analysis._"


def _analyze_anthropic(logs: str, container: str) -> str:
    if not ANTHROPIC_API_KEY:
        return local_analyze(logs) + "\n\n_Anthropic key not configured, used local analysis._"
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 600,
                "messages": [{"role": "user", "content": _ANALYZE_PROMPT.format(logs=logs[-3000:])}],
            },
            timeout=30,
        )
        return r.json()["content"][0]["text"]
    except Exception as exc:
        logger.error("Anthropic error: %s", exc)
        return local_analyze(logs) + "\n\n_Anthropic unavailable, used local analysis._"


def run_analysis(raw_logs: str, container: str) -> str:
    masked = mask_secrets(raw_logs)
    if AI_PROVIDER == "openai":
        return _analyze_openai(masked, container)
    if AI_PROVIDER == "anthropic":
        return _analyze_anthropic(masked, container)
    return local_analyze(masked)


# ── Inline keyboard builders ───────────────────────────────────────────────────

def _container_buttons(container: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "📋 Logs", "callback_data": f"logs:{container}"},
            {"text": "🔴 Errors", "callback_data": f"errors:{container}"},
            {"text": "🔍 Analyze", "callback_data": f"analyze:{container}"},
            {"text": "🔄 Restart", "callback_data": f"restart:{container}"},
        ]]
    }


def _confirm_buttons(code: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Confirm restart", "callback_data": f"confirm_restart:{code}"},
            {"text": "❌ Cancel", "callback_data": f"cancel_restart:{code}"},
        ]]
    }


# ── Command handlers ───────────────────────────────────────────────────────────

def cmd_start(chat_id: int, user_id: int, username: str) -> None:
    audit(user_id, username, "/start", "", "completed")
    send_message(chat_id, "Jarvis Lite v0.3\nType /help for available commands.")


def cmd_help(chat_id: int, user_id: int, username: str) -> None:
    audit(user_id, username, "/help", "", "completed")
    text = (
        "*Jarvis Lite v0.3 — Commands*\n\n"
        "/status — server status (hostname, uptime, RAM, disk, docker)\n"
        "/health — quick health check\n"
        "/docker — list all containers + inline action buttons\n"
        "/logs `<container>` `[lines]` — show container logs\n"
        "/errors `<container>` `[lines]` — filter error/exception lines\n"
        "/analyze\\_logs `<container>` `[lines]` — AI/local log analysis\n"
        "/restart `<container>` — request restart (confirmation required)\n"
        "/confirm `<code>` — confirm a pending restart action\n\n"
        "All commands require authorised user ID.\n"
        "Containers must be in SAFE\\_CONTAINERS whitelist."
    )
    send_message(chat_id, text, parse_mode="Markdown")


def cmd_status(chat_id: int, user_id: int, username: str) -> None:
    audit(user_id, username, "/status", "", "requested")

    hostname, _ = run_cmd(["hostname"])
    uptime, _ = run_cmd(["uptime", "-p"])
    loadavg, _ = run_cmd(["cat", "/proc/loadavg"])
    free_out, _ = run_cmd(["free", "-h"])
    df_out, _ = run_cmd(["df", "-h", "/"])
    docker_out, _ = run_cmd(["docker", "ps", "--format", "{{.Names}}: {{.Status}}"])

    text = (
        "*Server Status*\n\n"
        f"Hostname: `{hostname}`\n"
        f"Uptime: {uptime}\n"
        f"Load avg: `{loadavg}`\n\n"
        f"*RAM:*\n```\n{free_out}\n```\n"
        f"*Disk /:*\n```\n{df_out}\n```\n"
        f"*Running containers:*\n```\n{docker_out or 'none'}\n```"
    )
    audit(user_id, username, "/status", "", "completed")
    send_message(chat_id, text, parse_mode="Markdown")


def cmd_health(chat_id: int, user_id: int, username: str) -> None:
    audit(user_id, username, "/health", "", "requested")

    df_out, _ = run_cmd(["df", "-h", "/"])
    free_out, _ = run_cmd(["free", "-h"])
    docker_ids, _ = run_cmd(["docker", "ps", "-q"])
    container_count = len([l for l in docker_ids.splitlines() if l.strip()])
    systemd_failed, _ = run_cmd(["systemctl", "--failed", "--no-legend"])
    failed_count = len([l for l in systemd_failed.splitlines() if l.strip()])

    text = (
        "*Health Check*\n\n"
        f"Disk /:\n```\n{df_out}\n```\n"
        f"RAM:\n```\n{free_out}\n```\n"
        f"Running containers: `{container_count}`\n"
        f"Systemd failed units: `{failed_count}`"
    )
    audit(user_id, username, "/health", "", "completed")
    send_message(chat_id, text, parse_mode="Markdown")


def cmd_docker(chat_id: int, user_id: int, username: str) -> None:
    audit(user_id, username, "/docker", "", "requested")

    out, rc = run_cmd(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"])
    if rc != 0 or not out:
        send_message(chat_id, "Docker unavailable or no containers found.")
        return

    header_lines = ["*Docker containers (all):*\n"]
    for line in out.splitlines():
        parts = line.split("\t")
        name = parts[0] if parts else "?"
        status = parts[1] if len(parts) > 1 else "?"
        header_lines.append(f"`{name}` — {status}")

    send_message(chat_id, "\n".join(header_lines), parse_mode="Markdown")

    for container in SAFE_CONTAINERS:
        send_message(
            chat_id,
            f"Container: `{container}`",
            reply_markup=_container_buttons(container),
            parse_mode="Markdown",
        )

    audit(user_id, username, "/docker", "", "completed")


def cmd_logs(chat_id: int, user_id: int, username: str, args: list) -> None:
    if not args:
        send_message(chat_id, "Usage: /logs <container> [lines]")
        return

    container = args[0]
    if not validate_container(container):
        audit(user_id, username, "/logs", container, "rejected")
        send_message(chat_id, f"Container `{container}` is not in the allowed list.")
        return

    lines = clamp_lines(args[1] if len(args) > 1 else None)
    audit(user_id, username, "/logs", container, "requested")

    out, _ = run_cmd(["docker", "logs", "--tail", str(lines), container])
    text = f"*Logs: {container}* (last {lines} lines)\n```\n{out[:3500] or '(empty)'}\n```"
    audit(user_id, username, "/logs", container, "completed")
    send_message(chat_id, text, parse_mode="Markdown")


def cmd_errors(chat_id: int, user_id: int, username: str, args: list) -> None:
    if not args:
        send_message(chat_id, "Usage: /errors <container> [lines]")
        return

    container = args[0]
    if not validate_container(container):
        audit(user_id, username, "/errors", container, "rejected")
        send_message(chat_id, f"Container `{container}` is not in the allowed list.")
        return

    lines = clamp_lines(args[1] if len(args) > 1 else None)
    audit(user_id, username, "/errors", container, "requested")

    out, _ = run_cmd(["docker", "logs", "--tail", str(lines), container])
    error_lines = [
        line for line in out.splitlines()
        if any(pat in line.lower() for pat in ERROR_PATTERNS)
    ]

    if not error_lines:
        text = f"No error patterns found in `{container}` (last {lines} lines)."
    else:
        sample = "\n".join(error_lines[:50])
        text = f"*Errors in {container}* ({len(error_lines)} found):\n```\n{sample[:3000]}\n```"

    audit(user_id, username, "/errors", container, "completed")
    send_message(chat_id, text, parse_mode="Markdown")


def cmd_analyze_logs(chat_id: int, user_id: int, username: str, args: list) -> None:
    if not args:
        send_message(chat_id, "Usage: /analyze_logs <container> [lines]")
        return

    container = args[0]
    if not validate_container(container):
        audit(user_id, username, "/analyze_logs", container, "rejected")
        send_message(chat_id, f"Container `{container}` is not in the allowed list.")
        return

    lines = clamp_lines(args[1] if len(args) > 1 else None)
    audit(user_id, username, "/analyze_logs", container, "requested")

    send_message(chat_id, f"Analyzing `{container}` logs ({AI_PROVIDER} mode)...")
    out, _ = run_cmd(["docker", "logs", "--tail", str(lines), container])
    analysis = run_analysis(out, container)

    text = f"*Analysis: {container}*\n\n{analysis}"
    audit(user_id, username, "/analyze_logs", container, "completed")
    send_message(chat_id, text[:4096], parse_mode="Markdown")


def cmd_restart(chat_id: int, user_id: int, username: str, args: list) -> None:
    if not args:
        send_message(chat_id, "Usage: /restart <container>")
        return

    container = args[0]
    if not validate_container(container):
        audit(user_id, username, "/restart", container, "rejected")
        send_message(chat_id, f"Container `{container}` is not in the allowed list.")
        return

    audit(user_id, username, "/restart", container, "requested")
    code = create_pending(user_id, container)

    text = (
        f"⚠️ Restart `{container}`?\n\n"
        f"Confirmation code: `{code}`\n"
        f"Expires in {ACTION_TTL}s\n\n"
        f"Press a button or type /confirm {code}"
    )
    send_message(chat_id, text, reply_markup=_confirm_buttons(code), parse_mode="Markdown")


def cmd_confirm(chat_id: int, user_id: int, username: str, args: list) -> None:
    if not args:
        send_message(chat_id, "Usage: /confirm <code>")
        return

    code = args[0]
    entry = pop_pending(code, user_id)
    if entry is None:
        audit(user_id, username, "/confirm", code, "failed")
        send_message(chat_id, "Invalid or expired confirmation code.")
        return

    container = entry["container"]
    audit(user_id, username, "/confirm", container, "requested")

    out, rc = run_cmd(["docker", "restart", container])
    if rc == 0:
        audit(user_id, username, "/confirm", container, "completed")
        send_message(chat_id, f"✅ Container `{container}` restarted.", parse_mode="Markdown")
    else:
        audit(user_id, username, "/confirm", container, "failed")
        send_message(
            chat_id,
            f"❌ Failed to restart `{container}`:\n```\n{out[:500]}\n```",
            parse_mode="Markdown",
        )


# ── Callback query handler ─────────────────────────────────────────────────────

_ALLOWED_CB_ACTIONS = frozenset(
    ["logs", "errors", "analyze", "restart", "confirm_restart", "cancel_restart"]
)


def handle_callback(callback_query: dict) -> None:
    cb_id = callback_query.get("id", "")
    user = callback_query.get("from", {})
    user_id = user.get("id", 0)
    username = user.get("username") or user.get("first_name", "unknown")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id", 0)
    data = callback_query.get("data", "")

    if user_id not in ALLOWED_USER_IDS:
        answer_callback(cb_id, "Access denied.")
        audit(user_id, username, "callback", data[:50], "rejected")
        return

    parts = data.split(":", 1)
    if not parts or parts[0] not in _ALLOWED_CB_ACTIONS:
        answer_callback(cb_id, "Unknown action.")
        return

    action = parts[0]
    payload = parts[1] if len(parts) > 1 else ""

    # Container-level actions
    if action in ("logs", "errors", "analyze", "restart"):
        container = payload
        if not validate_container(container):
            answer_callback(cb_id, "Container not in whitelist.")
            audit(user_id, username, f"cb:{action}", container, "rejected")
            return

        if action == "logs":
            answer_callback(cb_id, "Fetching logs...")
            audit(user_id, username, "cb:logs", container, "requested")
            out, _ = run_cmd(["docker", "logs", "--tail", str(DEFAULT_LOG_LINES), container])
            text = f"*Logs: {container}*\n```\n{out[:3500] or '(empty)'}\n```"
            send_message(chat_id, text, parse_mode="Markdown")
            audit(user_id, username, "cb:logs", container, "completed")

        elif action == "errors":
            answer_callback(cb_id, "Filtering errors...")
            audit(user_id, username, "cb:errors", container, "requested")
            out, _ = run_cmd(["docker", "logs", "--tail", str(DEFAULT_LOG_LINES), container])
            error_lines = [
                l for l in out.splitlines()
                if any(pat in l.lower() for pat in ERROR_PATTERNS)
            ]
            if error_lines:
                text = (
                    f"*Errors: {container}* ({len(error_lines)} found)\n"
                    f"```\n{chr(10).join(error_lines[:50])[:3000]}\n```"
                )
            else:
                text = f"No errors found in `{container}`."
            send_message(chat_id, text, parse_mode="Markdown")
            audit(user_id, username, "cb:errors", container, "completed")

        elif action == "analyze":
            answer_callback(cb_id, "Analyzing...")
            audit(user_id, username, "cb:analyze", container, "requested")
            out, _ = run_cmd(["docker", "logs", "--tail", str(DEFAULT_LOG_LINES), container])
            analysis = run_analysis(out, container)
            text = f"*Analysis: {container}*\n\n{analysis}"
            send_message(chat_id, text[:4096], parse_mode="Markdown")
            audit(user_id, username, "cb:analyze", container, "completed")

        elif action == "restart":
            # Never restart immediately — create pending action first
            audit(user_id, username, "cb:restart", container, "requested")
            code = create_pending(user_id, container)
            text = (
                f"⚠️ Restart `{container}`?\n\n"
                f"Code: `{code}`\n"
                f"Expires in {ACTION_TTL}s"
            )
            answer_callback(cb_id)
            send_message(chat_id, text, reply_markup=_confirm_buttons(code), parse_mode="Markdown")

    # Confirmation/cancellation actions
    elif action == "confirm_restart":
        code = payload
        entry = pop_pending(code, user_id)
        if entry is None:
            answer_callback(cb_id, "Expired or invalid code.")
            audit(user_id, username, "cb:confirm_restart", code, "failed")
            return

        container = entry["container"]
        audit(user_id, username, "cb:confirm_restart", container, "requested")
        answer_callback(cb_id, f"Restarting {container}...")

        out, rc = run_cmd(["docker", "restart", container])
        if rc == 0:
            audit(user_id, username, "cb:confirm_restart", container, "completed")
            send_message(chat_id, f"✅ Container `{container}` restarted.", parse_mode="Markdown")
        else:
            audit(user_id, username, "cb:confirm_restart", container, "failed")
            send_message(
                chat_id,
                f"❌ Failed to restart `{container}`:\n```\n{out[:500]}\n```",
                parse_mode="Markdown",
            )

    elif action == "cancel_restart":
        code = payload
        entry = _pending.pop(code, None)
        container = entry["container"] if entry else "(unknown)"
        audit(user_id, username, "cb:cancel_restart", container, "cancelled")
        answer_callback(cb_id, "Restart cancelled.")
        send_message(chat_id, f"❌ Restart of `{container}` cancelled.", parse_mode="Markdown")


# ── Update dispatcher ──────────────────────────────────────────────────────────

def _get_user(obj: dict) -> tuple:
    user = obj.get("from", {})
    return user.get("id", 0), (user.get("username") or user.get("first_name", "unknown"))


def dispatch(update: dict) -> None:
    if "message" in update:
        msg = update["message"]
        chat_id = msg.get("chat", {}).get("id", 0)
        user_id, username = _get_user(msg)
        text = msg.get("text", "")

        if not text or not chat_id:
            return

        if user_id not in ALLOWED_USER_IDS:
            audit(user_id, username, "message", text[:50], "rejected")
            send_message(chat_id, "Access denied.")
            return

        parts = text.split()
        # Strip @BotName suffix from command
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]

        handlers = {
            "/start": cmd_start,
            "/help": cmd_help,
            "/status": cmd_status,
            "/health": cmd_health,
            "/docker": cmd_docker,
        }

        if cmd in handlers:
            if cmd in ("/status", "/health", "/docker"):
                handlers[cmd](chat_id, user_id, username)
            else:
                handlers[cmd](chat_id, user_id, username)
        elif cmd == "/logs":
            cmd_logs(chat_id, user_id, username, args)
        elif cmd == "/errors":
            cmd_errors(chat_id, user_id, username, args)
        elif cmd == "/analyze_logs":
            cmd_analyze_logs(chat_id, user_id, username, args)
        elif cmd == "/restart":
            cmd_restart(chat_id, user_id, username, args)
        elif cmd == "/confirm":
            cmd_confirm(chat_id, user_id, username, args)
        else:
            send_message(chat_id, "Unknown command. Use /help.")

    elif "callback_query" in update:
        handle_callback(update["callback_query"])


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)
    if not ALLOWED_USER_IDS:
        logger.error("ALLOWED_USER_IDS is not set. Exiting.")
        sys.exit(1)

    logger.info("Jarvis Lite v0.3 starting (AI provider: %s)", AI_PROVIDER)
    logger.info("Allowed users: %s", sorted(ALLOWED_USER_IDS))
    logger.info("Safe containers: %s", SAFE_CONTAINERS)

    offset = 0
    while True:
        try:
            updates = get_updates(offset)
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            break
        except Exception as exc:
            logger.error("Poll loop error: %s", exc)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                dispatch(update)
            except Exception as exc:
                logger.error("Dispatch error on update %s: %s", update.get("update_id"), exc)


if __name__ == "__main__":
    main()
