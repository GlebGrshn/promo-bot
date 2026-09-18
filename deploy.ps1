# Быстрое обновление бота на сервере.
# Запуск из папки проекта:  .\deploy.ps1 -Server root@203.0.113.10
# Только .env (без кода):   .\deploy.ps1 -Server ... -EnvOnly
# Посмотреть логи после:    .\deploy.ps1 -Server ... -Logs
#
# Чтобы не указывать адрес каждый раз, задайте переменную окружения
# MAXBOT_DEPLOY_SERVER — например, в профиле PowerShell.

param(
    [string]$Server = $env:MAXBOT_DEPLOY_SERVER,  # user@host целевого сервера
    [string]$Key    = "$env:USERPROFILE\.ssh\maxbot_deploy",
    [string]$Remote = "/opt/max-bot",
    [switch]$EnvOnly,   # залить только .env (смена админов/токена)
    [switch]$Logs       # после обновления показать живые логи
)

$ErrorActionPreference = "Stop"

if (-not $Server) {
    throw "Не задан сервер. Укажите -Server user@host или переменную MAXBOT_DEPLOY_SERVER."
}

Write-Host "-> Загружаю файлы на сервер..." -ForegroundColor Cyan

if ($EnvOnly) {
    scp -i $Key .env "${Server}:$Remote/"
} else {
    scp -i $Key -r app run_max.py requirements-max.txt .env "${Server}:$Remote/"
}

Write-Host "-> Перезапускаю бота..." -ForegroundColor Cyan
ssh -i $Key $Server "rm -rf $Remote/app/__pycache__; chown -R maxbot:maxbot $Remote; chmod 600 $Remote/.env; systemctl restart max-bot; sleep 8; systemctl is-active max-bot"

Write-Host "-> Последние строки лога:" -ForegroundColor Cyan
ssh -i $Key $Server "journalctl -u max-bot -n 8 --no-pager"

if ($Logs) {
    Write-Host "-> Живые логи, выход по Ctrl-C:" -ForegroundColor Yellow
    ssh -i $Key $Server "journalctl -u max-bot -f"
}

Write-Host "Готово." -ForegroundColor Green
