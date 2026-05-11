$ProjectPath = "C:\Users\Admin\Desktop\vless-shopbot-reworked-git"
$LogsPath = Join-Path $ProjectPath "logs"
$NgrokOutLog = Join-Path $LogsPath "ngrok-out.log"
$NgrokErrLog = Join-Path $LogsPath "ngrok-err.log"

New-Item -ItemType Directory -Force -Path $LogsPath | Out-Null

Set-Location $ProjectPath

# Поднимаем Docker-систему
docker compose up -d

# Останавливаем старые процессы ngrok, чтобы не было конфликта endpoint already online
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force

Start-Sleep -Seconds 3

# Запускаем ngrok в фоне.
# Важно: stdout и stderr должны быть в разных файлах.
Start-Process `
  -FilePath "ngrok" `
  -ArgumentList "http --domain=rethink-verdict-enroll.ngrok-free.dev 8080 --log=stdout" `
  -WindowStyle Hidden `
  -RedirectStandardOutput $NgrokOutLog `
  -RedirectStandardError $NgrokErrLog

Start-Sleep -Seconds 5

Write-Host "SoterVPN stack started."
Write-Host "Check API:"
Write-Host "curl.exe -i https://rethink-verdict-enroll.ngrok-free.dev/docs"