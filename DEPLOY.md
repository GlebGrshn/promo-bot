# Развёртывание бота на сервере

Бот работает по long polling (без вебхуков), поэтому **домен и открытые порты не нужны** —
достаточно небольшого постоянно включённого Linux-сервера (VPS).

**Минимальные требования:** 1 vCPU, 1 ГБ RAM, ~5 ГБ диска, Python 3.10+.
Данные (`data/bot.db`, `data/media/`) должны лежать на постоянном диске.

Ниже два способа: **systemd** (проще, без Docker) и **Docker**. Достаточно одного.

---

## Вариант A. Linux-сервер + systemd (рекомендуется)

```bash
# 1. Заходим на сервер по SSH и ставим системные пакеты
sudo apt update && sudo apt install -y python3-venv python3-pip git

# 2. Отдельный пользователь (без root) и каталог
sudo useradd -r -m -d /opt/max-bot maxbot
sudo mkdir -p /opt/max-bot && sudo chown maxbot:maxbot /opt/max-bot

# 3. Копируем код в /opt/max-bot
#    (git clone <ваш-репозиторий> /opt/max-bot  ИЛИ  scp с локальной машины)
#    Пример через scp с Windows (PowerShell), из папки проекта:
#    scp -r app run_max.py requirements-max.txt .env maxbot@СЕРВЕР:/opt/max-bot/

# 4. Виртуальное окружение и зависимости (только МАКС)
sudo -u maxbot bash -lc '
  cd /opt/max-bot
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements-max.txt
'

# 5. Проверяем, что .env заполнен (MAX_BOT_TOKEN, MAX_OWNER_ID, MAX_ADMIN_IDS)
sudo -u maxbot nano /opt/max-bot/.env

# 6. Ставим службу автозапуска
sudo cp /opt/max-bot/deploy/max-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now max-bot

# 7. Смотрим логи
journalctl -u max-bot -f
```

Служба сама перезапускает бота при падении и после перезагрузки сервера.

**Управление:**
```bash
sudo systemctl restart max-bot   # перезапуск (например, после правок кода)
sudo systemctl stop max-bot      # остановить
sudo systemctl status max-bot    # состояние
```

---

## Вариант B. Docker

На сервере нужен Docker и Docker Compose. Код (вместе с `.env`) — в одной папке.

```bash
# заполнить .env (MAX_BOT_TOKEN, MAX_OWNER_ID, MAX_ADMIN_IDS)
docker compose up -d --build     # собрать и запустить в фоне
docker compose logs -f           # логи
docker compose restart           # перезапуск
docker compose down              # остановить
```

`.env` в образ не попадает (передаётся через `env_file`), а база и фото хранятся в
`./data` на хосте (том `./data:/app/data`).

---

## Обновление кода

- **systemd:** заменить файлы в `/opt/max-bot` (git pull / scp) → `sudo systemctl restart max-bot`.
  Если менялся `requirements-max.txt` — заново `.venv/bin/pip install -r requirements-max.txt`.
- **Docker:** обновить файлы → `docker compose up -d --build`.

## Бэкапы

Достаточно копировать каталог `data/` (там вся база и фото чеков), например по cron:
```bash
tar czf /root/backup-$(date +%F).tgz -C /opt/max-bot data
```

## Безопасность

- Файл `.env` содержит токен бота — не коммитьте его в публичный репозиторий,
  права доступа `chmod 600 .env`.
- Токен можно перевыпустить у @MasterBot, если он скомпрометирован.

## Частые вопросы

- **Нужен ли домен/SSL?** Нет. Long polling — только исходящие соединения.
- **Уснёт ли бот на «бесплатных» PaaS?** На тарифах, которые «засыпают» без входящих
  запросов, polling прерывается — берите обычный VPS или платный always-on worker.
- **Часовой пояс розыгрыша** задаётся переменной `TIMEZONE` в `.env` (по умолчанию
  `Europe/Moscow`) и не зависит от часового пояса сервера.
- **Windows-сервер?** Тоже можно (запуск `run_max.py` как службы через NSSM или
  Планировщик задач), но Linux-VPS проще и дешевле.
