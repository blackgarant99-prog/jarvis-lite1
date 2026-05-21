# Roadmap — Jarvis Lite

## v0.1 — Telegram-пульт (базовая версия)

- Long polling через `getUpdates` без сторонних библиотек
- Авторизация по `ALLOWED_USER_IDS`
- Whitelist контейнеров `SAFE_CONTAINERS`
- Команды: `/start`, `/help`, `/status`, `/health`, `/docker`
- `/logs`, `/errors` с ограничением строк
- `/restart` + `/confirm` с pending action и TTL 90s
- `subprocess.run(args, shell=False)` — никакого shell injection
- Базовое логирование

## v0.2 — Анализ логов

- Команда `/analyze_logs`
- Локальный rule-based анализ с pattern matching
- Поддержка `AI_PROVIDER=openai` и `AI_PROVIDER=anthropic`
- Маскировка секретов перед отправкой в AI (`TOKEN=`, `KEY=` → `[REDACTED]`)
- Подсказки по частым ошибкам (connection refused, timeout, postgres, …)
- Graceful fallback на local-анализ при недоступности AI API

## v0.3 — Inline-кнопки + Audit log *(текущая)*

- Inline-кнопки для каждого контейнера после `/docker`: Logs / Errors / Analyze / Restart
- `callback_query` handler с валидацией user_id и SAFE_CONTAINERS
- Restart через кнопку: сначала [Confirm restart] / [Cancel], затем `docker restart`
- Audit log: `logs/audit.log` в JSON-формате (timestamp, user_id, action, target, status)
- Все действия — requested / confirmed / cancelled / rejected / failed / completed
- Systemd unit с hardening (NoNewPrivileges, ProtectSystem, PrivateTmp)
- Установочный скрипт `scripts/install_ubuntu.sh`

## v0.4 — Мониторинг и уведомления (планируется)

- Фоновый поток мониторинга (disk, RAM, CPU thresholds)
- Автоматические alert-сообщения при превышении порогов
- Настройка порогов через `.env` (`ALERT_DISK_PCT`, `ALERT_RAM_PCT`)
- Команда `/alerts` — управление подписками
- Интеграция с `systemd` failed units (alert при появлении)
- Cooldown между повторными уведомлениями

## v0.5 — Несколько VDS (планируется)

- Управление несколькими серверами из одного бота
- SSH-подключение к удалённым VDS через `paramiko` (whitelist хостов)
- Команды с указанием сервера: `/logs server1 nginx 100`
- Конфигурация серверов в `config.yaml`
- Разграничение доступа: пользователь может управлять только своими серверами

## v1.0 — Продуктовая версия (планируется)

- Веб-интерфейс (FastAPI + htmx) как дополнение к Telegram
- Хранение audit-log в SQLite или PostgreSQL
- 2FA для критических действий (перезапуск, обновление)
- Rate limiting для команд
- Интеграция с Prometheus/Grafana через метрики
- Docker Compose для полного стека
- Автоматические тесты (pytest)
- CI/CD pipeline
