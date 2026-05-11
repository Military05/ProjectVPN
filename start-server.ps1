$ErrorActionPreference = "SilentlyContinue"

# Даём Windows и Happ время запуститься и подключиться
Start-Sleep -Seconds 45

# Запускаем Docker Desktop
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# Ждём, пока Docker Engine станет доступен
do {
    Start-Sleep -Seconds 5
    docker info | Out-Null
} until ($LASTEXITCODE -eq 0)

# Запускаем проект
cd "C:\Users\Admin\Desktop\vless-shopbot-reworked-git"
docker compose up -d
