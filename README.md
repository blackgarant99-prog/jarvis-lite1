# Jarvis Lite v0.3

Безопасный Telegram-пульт для управления VDS через Docker.  
Работает на чистом Python + requests. Без сторонних Telegram-библиотек.

---

## Что это

Jarvis Lite — минималистичный Telegram-бот, который позволяет:

- смотреть состояние сервера (RAM, disk, uptime, load average);
- управлять Docker-контейнерами через whitelist;
- просматривать и анализировать логи прямо в Telegram;
- перезапускать контейнеры с обязательным подтверждением;
- работать через inline-кнопки (v0.3).

Доступ только для авторизованных Telegram user ID.

---

## Команды

| Команда | Описание |
|---|---|
| `/start` | Приветствие |
| `/help` | Список команд |
| `/status` | hostname, uptime, load avg, RAM, disk, docker ps |
| `/health` | Быстрая проверка: disk, RAM, containers, systemd failed |
| `/docker` | `docker ps -a` + inline-кнопки для каждого контейнера из SAFE_CONTAINERS |
| `/logs <container> [lines]` | Последние N строк логов контейнера |
| `/errors <container> [lines]` | Фильтр строк с error/exception/traceback/… |
| `/analyze_logs <container> [lines]` | Локальный или AI-анализ логов |
| `/restart <container>` | Создать запрос на перезапуск (нужен /confirm) |
| `/confirm <code>` | Подтвердить pending-действие по коду |

---

## Inline-кнопки (v0.3)

После `/docker` для каждого контейнера из `SAFE_CONTAINERS` появляются кнопки:

```
api — Up 2 hours
[📋 Logs]  [🔴 Errors]  [🔍 Analyze]  [🔄 Restart]
```

Кнопка **Restart** не выполняет перезапуск сразу — создаёт pending action  
и показывает кнопки **Confirm restart** / **Cancel**.

---

## Установка на Ubuntu/VDS

### Быстрая установка

```bash
git clone https://github.com/blackgarant99-prog/jarvis-lite1.git
cd jarvis-lite1
sudo bash scripts/install_ubuntu.sh
```

Скрипт установит Python3, venv, docker.io, создаст пользователя `jarvis`  
и настроит systemd-сервис.

### Ручная установка

```bash
# 1. Зависимости
sudo apt-get install python3 python3-venv docker.io -y

# 2. Пользователь
sudo useradd --system --home /opt/jarvis-lite --shell /usr/sbin/nologin jarvis
sudo usermod -aG docker jarvis

# 3. Файлы
sudo mkdir -p /opt/jarvis-lite
sudo cp -r app requirements.txt /opt/jarvis-lite/
sudo chown -R jarvis:jarvis /opt/jarvis-lite

# 4. venv и зависимости
sudo -u jarvis python3 -m venv /opt/jarvis-lite/.venv
sudo -u jarvis /opt/jarvis-lite/.venv/bin/pip install -r /opt/jarvis-lite/requirements.txt
```

---

## Настройка .env

```bash
sudo cp .env.example /opt/jarvis-lite/.env
sudo chmod 600 /opt/jarvis-lite/.env
sudo nano /opt/jarvis-lite/.env
```

Обязательные поля:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...  # токен от @BotFather
ALLOWED_USER_IDS=123456789        # ваш Telegram user ID
SAFE_CONTAINERS=api,bot,nginx     # контейнеры, которыми можно управлять
```

Ваш Telegram user ID можно узнать у бота [@userinfobot](https://t.me/userinfobot).

---

## Запуск вручную (для тестирования)

```bash
cd /opt/jarvis-lite
source .venv/bin/activate
python app/bot.py
```

---

## Запуск через systemd

```bash
# Установка сервиса
sudo cp systemd/jarvis-lite.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jarvis-lite

# Управление
sudo systemctl status jarvis-lite
sudo systemctl restart jarvis-lite
sudo systemctl stop jarvis-lite

# Логи
sudo journalctl -u jarvis-lite -f
```

---

## Как тестировать

```bash
# Проверить синтаксис Python
python3 -m py_compile app/bot.py && echo "OK"

# Проверить безопасность: нет shell=True с пользовательским вводом
grep -R "shell=True" app/

# Запустить локально (нужен .env)
python3 app/bot.py
```

---

## Обновление

```bash
cd /path/to/jarvis-lite1
git pull origin main

# Если изменились зависимости:
sudo -u jarvis /opt/jarvis-lite/.venv/bin/pip install -r requirements.txt

# Перезапустить сервис:
sudo systemctl restart jarvis-lite
```

---

## Правила безопасности

- Все команды выполняются только для пользователей из `ALLOWED_USER_IDS`.
- Управление контейнерами возможно только для `SAFE_CONTAINERS`.
- `/restart` требует подтверждения через `/confirm <code>` или кнопку. Код действует 90 секунд.
- Нет команд `/shell`, `/exec`, `/run`.
- `subprocess` вызывается только с фиксированными списками аргументов (`shell=False`).
- Секреты маскируются перед отправкой в AI (`TOKEN=`, `KEY=`, `PASSWORD=` → `[REDACTED]`).
- `.env` никогда не читается и не выводится через Telegram.
- Все действия логируются в `logs/audit.log`.

Подробнее: [SECURITY.md](SECURITY.md)
