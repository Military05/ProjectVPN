SoterVPN / vless-shopbot

роект для продажи VPN-доступа через Telegram-бота и VLESS/Xray/3X-UI инфраструктуру.

 Я AI / CHATGPT:

еред любыми правками читать:

docs/AI_PROJECT_CONTEXT.txt
docs/WORKFLOW.txt

 этих файлах описаны:
- структура проекта;
- рабочая production subscription-ссылка;
- VPS subscription API;
- правила редактирования файлов;
- Docker workflow;
- Git workflow;
- будущая индивидуальная система пользователей.

PRODUCTION SUBSCRIPTION:

абочая ссылка подписки:

https://sub.sotervpn.ru/sub/soter

на отдаёт одну subscription-ссылку для V2RayTun/Raytan, внутри которой 15 каналов.

CURRENT ARCHITECTURE:

Telegram bot / main project:
Windows Docker project

Subscription API:
German VPS
/opt/sotervpn-sub
systemd service: sotervpn-sub.service

USEFUL WINDOWS COMMANDS:

cd "C:\Users\Admin\Desktop\vless-shopbot-reworked-git"
docker compose down
docker compose build --no-cache
docker compose up

USEFUL VPS COMMANDS:

systemctl status sotervpn-sub
systemctl restart sotervpn-sub
journalctl -u sotervpn-sub -n 100 --no-pager
curl https://sub.sotervpn.ru/channels
curl -I https://sub.sotervpn.ru/sub/soter

DO NOT COMMIT SECRETS:

Do not commit:
.env
tokens
passwords
certificates
private keys

Use .env.example for placeholders.
