# Changelog — Jarvis Lite

All notable changes are documented here.

---

## [0.3] — 2026-05-21

### Added

- **Inline-кнопки** после `/docker`: для каждого контейнера из `SAFE_CONTAINERS`  
  появляются кнопки `📋 Logs`, `🔴 Errors`, `🔍 Analyze`, `🔄 Restart`
- **Callback query handler** с полной валидацией user_id и SAFE_CONTAINERS
- **Безопасный Restart через кнопку**: сначала появляются кнопки  
  `✅ Confirm restart` / `❌ Cancel`, только после подтверждения выполняется `docker restart`
- **Audit log** в `logs/audit.log` (JSON, одна запись на строку):  
  поля `ts`, `user_id`, `username`, `action`, `target`, `status`
- Статусы аудита: `requested`, `confirmed`, `cancelled`, `rejected`, `failed`, `completed`
- **Systemd unit** с hardening-опциями: `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`
- **Установочный скрипт** `scripts/install_ubuntu.sh` для Ubuntu/VDS
- Документация: `README.md`, `SECURITY.md`, `ROADMAP.md`, `CHANGELOG.md`

### Security

- Callback data не допускает произвольных контейнеров — только из `SAFE_CONTAINERS`
- `cancel_restart` удаляет pending action из памяти немедленно
- Pending action привязан к `user_id` — другой пользователь не может подтвердить чужой restart

---

## [0.2] — (ранее)

### Added

- Команда `/analyze_logs <container> [lines]`
- Локальный rule-based анализ: поиск `error`, `exception`, `traceback`, `timeout` и др.
- Поддержка `AI_PROVIDER=openai` (GPT-4.1-mini)
- Поддержка `AI_PROVIDER=anthropic` (Claude Haiku)
- Маскировка секретов перед отправкой в AI: `TOKEN=`, `KEY=`, `PASSWORD=` → `[REDACTED]`
- Graceful fallback: при недоступности AI API используется local-анализ
- Подсказки по частым проблемам: connection refused, postgres, redis, 502/503/504

### Changed

- `DEFAULT_LOG_LINES=80`, `MAX_LOG_LINES=500` вынесены в `.env`
- Улучшена обработка ошибок subprocess (timeout, FileNotFoundError)

---

## [0.1] — (ранее)

### Added

- Telegram long polling через `getUpdates` (чистый `requests`, без сторонних Telegram-библиотек)
- Авторизация: доступ только для `ALLOWED_USER_IDS`
- Whitelist контейнеров: `SAFE_CONTAINERS`
- Команды: `/start`, `/help`, `/status`, `/health`, `/docker`
- `/logs <container> [lines]` — просмотр логов контейнера
- `/errors <container> [lines]` — фильтрация строк с признаками ошибок
- `/restart <container>` — создание pending action (код, TTL 90s)
- `/confirm <code>` — подтверждение и выполнение `docker restart`
- `/status`: hostname, uptime, load average, RAM (`free -h`), disk `/`, running containers
- `/health`: disk, RAM, количество контейнеров, systemd failed units
- `run_cmd(args_list)` — все subprocess-вызовы без `shell=True`
- Конфигурация через `.env` + `python-dotenv`
- `.env.example`, `.gitignore`, `requirements.txt`
