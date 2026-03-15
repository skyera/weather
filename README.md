# Scripture & Skies Weather Station

```
/*       _\|/_
         (o o)
 +----oOO-{_}-OOo--------------------------+
 |__        __         _   _               |
 |\ \      / /__  __ _| |_| |__   ___ _ __ |
 | \ \ /\ / / _ \/ _` | __| '_ \ / _ \ '__||
 |  \ V  V /  __/ (_| | |_| | | |  __/ |   |
 |   \_/\_/ \___|\__,_|\__|_| |_|\___|_|   |
 |                                         |
 +----------------------------------------*/
```

A beautiful Raspberry Pi weather station dashboard combining real-time sensor data, daily Bible verses, and vocabulary learning.

## Features

- **Real-time Weather Data**: BME280 sensor readings (temperature, humidity, pressure, altitude)
- **Daily Bible Verse**: Random Scripture verses from [bible-api.com](https://bible-api.com)
- **Word of the Day**: Random English words with authentic definitions from [Random Word API](https://random-word-api.herokuapp.com) and [Free Dictionary API](https://api.dictionaryapi.dev)
- **System Information**: Raspberry Pi model, CPU uptime, memory usage, and disk space
- **Camera Integration**: Timestamped photos with Pacific timezone overlay using PiCamera
- **Production Deployment**: Gunicorn WSGI server with systemd service management
- **Terminal-style UI**: Monospace fonts with retro terminal aesthetics (black background, light gray text)

## Hardware

- Raspberry Pi (tested on Pi 4 and Pi Zero)
- BME280 environmental sensor (optional)
- PiCamera module (optional)

## Installation

```bash
# Clone repository
git clone https://github.com/skyera/weather.git
cd weather

# Install dependencies
pip install -r requirements.txt

# Run locally (development)
flask run --host 0.0.0.0 --reload
```

## Production Deployment

### 1. Install Gunicorn

```bash
pip install gunicorn
```

### 2. Create Systemd Service Configuration

Create `/etc/systemd/system/scripture-skies.service`:

```ini
[Unit]
Description=Scripture & Skies Weather Station
After=network.target

[Service]
Type=notify
User=pi
Group=pi
WorkingDirectory=/home/pi/test/weather
Environment="PATH=/home/pi/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/usr/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Key configuration options:**
- `User/Group`: Change `pi` to your actual username (e.g., `ubuntu`, `root`, `appuser`)
- `WorkingDirectory`: Path to your weather application (change `/home/pi/test/weather` to your path)
- `Environment`: Update PATH with your user's local bin directory
  - `--bind 0.0.0.0:5000`: Listen on all interfaces, port 5000
  - `--workers 2`: Number of worker processes (adjust based on available RAM)
  - `app:app`: Flask application reference
- `Restart=always`: Auto-restart on crash
- `RestartSec=10`: Wait 10 seconds before restarting

### 3. Enable and Start the Service

```bash
# Copy service file to systemd directory
sudo cp scripture-skies.service /etc/systemd/system/

# Reload systemd configuration
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable scripture-skies

# Start the service
sudo systemctl start scripture-skies

# Check status
sudo systemctl status scripture-skies

# View recent logs
sudo journalctl -u scripture-skies -n 50

# Follow logs in real-time
sudo journalctl -u scripture-skies -f
```

### 4. Troubleshooting

**Service failed to load (Invalid argument):**
```bash
# Verify service file syntax
sudo systemd-analyze verify /etc/systemd/system/scripture-skies.service
```

**CHDIR error:**
- Ensure `WorkingDirectory` exists and user has read/write permissions
- Verify path is correct: `ls -la /path/to/weather`

**Module not found (ImportError):**
- Verify virtual environment or pip packages are installed for the user
- Check: `pip list`
- Install missing packages: `pip install -r requirements.txt`

**Port already in use:**
- Check what's using port 5000: `sudo lsof -i :5000`
- Or change port in ExecStart: `--bind 0.0.0.0:8000`

**Permission denied:**
- Verify user owns the working directory
- Fix: `sudo chown -R pi:pi /home/pi/test/weather` (replace `pi` with your username)

### 5. Quick Restart Script

Use the included restart script for convenience:

```bash
./restart-service.sh
```

This runs:
```bash
sudo systemctl restart scripture-skies
sudo systemctl status scripture-skies
```

## Configuration

- **Port**: Application runs on port 5000
- **Timezone**: Configured for US/Pacific (PST/PDT)
- **Workers**: Gunicorn runs with 2 workers (adjust in systemd service based on available RAM)

## File Structure

```
.
├── app.py                    # Flask application
├── bme280.py                # BME280 sensor driver
├── templates/
│   └── index.html           # Dashboard UI
├── static/                  # Static assets (images, etc.)
├── scripture-skies.service  # Systemd service configuration
├── restart-service.sh       # Quick restart script
├── requirements.txt         # Python dependencies
└── README.md
```

## API Dependencies

- **Bible API**: https://bible-api.com (free, no auth required)
- **Random Word API**: https://random-word-api.herokuapp.com (free, no auth required)
- **Dictionary API**: https://api.dictionaryapi.dev (free, no auth required)

## Environment

This application is designed for and tested on:
- **OS**: Raspberry Pi OS (Debian-based)
- **Python**: 3.7+
- **Flask**: 2.x
- **Gunicorn**: 20.x

## License

See LICENSE file for details
