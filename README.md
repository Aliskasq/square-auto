# Square Auto Bot

Автоматический бот для анализа криптовалют и постинга на Binance Square.

---

## Установка

```bash
git clone https://github.com/Aliskasq/square-auto.git
```

```bash
cd square-auto
```

```bash
python3 -m venv venv
```

```bash
source venv/bin/activate
```

```bash
pip install --upgrade pip
```

```bash
pip install -r requirements.txt
```

---

## Настройка

```bash
cp .env.example .env
```

```bash
nano .env
```

Заполни все ключи: `TG_BOT_TOKEN`, `ADMIN_ID`, `SQUARE_API_KEY`, `BINANCE_API_KEY`, `SOURCE_GROUP_ID`, `SOURCE_GROUP_2_ID`, `OPENROUTER_API_KEY` (до 5 штук).

---

## Systemd юнит

```bash
sudo tee /etc/systemd/system/square-auto.service > /dev/null << 'EOF'
[Unit]
Description=Square Auto Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/square-auto
ExecStart=/root/square-auto/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable square-auto.service
```

```bash
sudo systemctl start square-auto.service
```

---

## Логи

```bash
journalctl -u square-auto.service -f
```

Логи бота также пишутся в файл:

```bash
cat /root/square-auto/logs/bot.log
```

Записи старше 3 дней удаляются автоматически в 01:05 МСК.

---

## Обновление

```bash
cd /root/square-auto && git pull && sudo systemctl restart square-auto
```

---

## Рестарт

```bash
sudo systemctl restart square-auto.service
```

---

## Поиск в логах

```bash
grep "ERROR" /root/square-auto/logs/bot.log
```

```bash
grep "MODELS_DEAD" /root/square-auto/logs/bot.log
```

```bash
grep "POST" /root/square-auto/logs/bot.log | tail -20
```
