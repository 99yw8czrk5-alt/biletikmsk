# VPS Deploy

Эта инструкция запускает бота не с твоего Mac, а на Linux VPS. Бот будет работать 24/7 как `systemd`-сервис и сам перезапускаться после падения или перезагрузки сервера.

## 1. Подготовить сервер

Нужен любой маленький VPS с Ubuntu/Debian и Python 3.10+.

На сервере:

```bash
sudo apt update
sudo apt install -y python3 rsync
sudo useradd --system --create-home --home-dir /opt/flight-watch --shell /usr/sbin/nologin flightwatch
```

## 2. Скопировать проект на VPS

На твоём Mac из папки проекта:

```bash
rsync -av --exclude '.git' --exclude '.env' ./ root@YOUR_SERVER_IP:/opt/flight-watch/
```

Затем на сервере:

```bash
sudo chown -R flightwatch:flightwatch /opt/flight-watch
```

## 3. Создать `.env` на сервере

На сервере:

```bash
sudo cp /opt/flight-watch/.env.example /opt/flight-watch/.env
sudo nano /opt/flight-watch/.env
```

Заполни реальные значения и закрой доступ к секретам:

```bash
sudo chown flightwatch:flightwatch /opt/flight-watch/.env
sudo chmod 600 /opt/flight-watch/.env
```

## 4. Установить сервис

На сервере:

```bash
sudo cp /opt/flight-watch/systemd/flight-watch.service /etc/systemd/system/flight-watch.service
sudo systemctl daemon-reload
sudo systemctl enable flight-watch
sudo systemctl start flight-watch
```

## 5. Проверить работу

```bash
sudo systemctl status flight-watch --no-pager
sudo journalctl -u flight-watch -f
```
