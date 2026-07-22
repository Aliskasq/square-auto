# Square Auto Bot

Автоматический бот для анализа криптовалют и постинга на Binance Square.

## Установка

```bash
git clone https://github.com/Aliskasq/square-auto.git
cd square-auto

# Установить зависимости
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Настроить переменные окружения
cp .env.example .env
nano .env   # заполнить токены и ID
```

## Запуск

```bash
source venv/bin/activate
python3 main.py
```

## Systemd (автозапуск)

```bash
sudo tee /etc/systemd/system/square-auto.service > /dev/null << 'EOF'
[Unit]
Description=Square Auto Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/square-auto
ExecStart=/root/square-auto/venv/bin/python3 main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable square-auto
sudo systemctl start square-auto
```

## Логи

```bash
# Онлайн (follow)
sudo journalctl -u square-auto -f

# Последние 100 строк
sudo journalctl -u square-auto -n 100

# За сегодня
sudo journalctl -u square-auto --since today
```