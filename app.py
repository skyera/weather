import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytz
import requests
from flask import Flask, render_template, jsonify

try:
    from picamera import PiCamera
    PICAMERA_AVAILABLE = True
except ImportError:
    PICAMERA_AVAILABLE = False

try:
    import bme280 as bme
    BME280_AVAILABLE = True
except ImportError:
    BME280_AVAILABLE = False

app = Flask(__name__, static_url_path="/static")

BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"
IMAGE_PATH = STATIC_DIR / "image.jpg"
IMAGE_FOLDER = Path.home() / "Pictures"

STATIC_DIR.mkdir(exist_ok=True)
IMAGE_FOLDER.mkdir(exist_ok=True)


def get_system_info():
    """Get Raspberry Pi model, uptime, memory, CPU temp, and disk usage."""
    info = {}
    
    # Get Raspberry Pi model
    try:
        with open("/proc/device-tree/model", "r") as f:
            info["model"] = f.read().strip().replace("\x00", "")
    except (FileNotFoundError, IOError):
        info["model"] = "Unknown Model"
    
    # Get uptime in formatted days/hours/minutes
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = int(float(f.read().split()[0]))
            days = uptime_seconds // 86400
            hours = (uptime_seconds % 86400) // 3600
            minutes = (uptime_seconds % 3600) // 60
            
            if days > 0:
                info["uptime"] = f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                info["uptime"] = f"{hours}h {minutes}m"
            else:
                info["uptime"] = f"{minutes}m"
    except (FileNotFoundError, IOError, ValueError):
        info["uptime"] = "Uptime unavailable"
    
    # Get memory info (free and total)
    try:
        result = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 7:
                    total = parts[1]
                    used = parts[2]
                    free = parts[3]
                    info["memory"] = f"Used: {used} / {total} (Free: {free})"
                else:
                    info["memory"] = "Memory info unavailable"
            else:
                info["memory"] = "Memory info unavailable"
        else:
            info["memory"] = "Memory info unavailable"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        info["memory"] = "Memory info unavailable"
    
    # Get CPU temperature
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_millidegrees = int(f.read().strip())
            temp_celsius = temp_millidegrees / 1000.0
            info["cpu_temp"] = f"{temp_celsius:.1f}°C"
    except (FileNotFoundError, IOError, ValueError):
        info["cpu_temp"] = "N/A"
    
    # Get disk usage for root partition
    try:
        result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    used = parts[2]
                    total = parts[1]
                    percent = parts[4]
                    info["disk"] = f"{used} / {total} ({percent})"
                else:
                    info["disk"] = "Disk info unavailable"
            else:
                info["disk"] = "Disk info unavailable"
        else:
            info["disk"] = "Disk info unavailable"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        info["disk"] = "Disk info unavailable"
    
    return info


def get_bible_verse():
    """Get a random Bible verse from the Bible API."""
    try:
        response = requests.get("https://bible-api.com/?random=books", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                "reference": data.get("reference", ""),
                "text": data.get("text", "").strip(),
                "translation": data.get("translation_name", "Web English Bible")
            }
    except (requests.RequestException, ValueError, KeyError):
        pass
    
    # Fallback verse
    return {
        "reference": "Psalm 23:1",
        "text": "The Lord is my shepherd; I shall not want.",
        "translation": "King James Version"
    }


def get_sensor_data():
    """Read BME280 sensor data with fallback values."""
    if not BME280_AVAILABLE:
        return {
            "temperature": 22.5,
            "pressure": 1013.25,
            "humidity": 45.0,
            "altitude": 0
        }

    try:
        _, _ = bme.readBME280ID()
        temperature, pressure, humidity = bme.readBME280All()
        altitude = 44330 * (1 - (pressure / 1013.25) ** 0.1903)
        return {
            "temperature": round(temperature, 1),
            "pressure": round(pressure, 2),
            "humidity": round(humidity, 1),
            "altitude": round(altitude, 1)
        }
    except Exception as e:
        app.logger.error(f"Sensor error: {e}")
        return {
            "temperature": None,
            "pressure": None,
            "humidity": None,
            "altitude": None,
            "error": str(e)
        }


def capture_image():
    """Capture image from PiCamera with timestamp overlay in Pacific time."""
    if not PICAMERA_AVAILABLE:
        return False

    # Get Pacific timezone
    tz_pacific = pytz.timezone('US/Pacific')
    now_pacific = datetime.now(tz_pacific)
    
    timestamp = now_pacific.strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = IMAGE_FOLDER / f"{timestamp}.jpg"

    try:
        if IMAGE_PATH.exists():
            shutil.copy(IMAGE_PATH, backup_path)

        with PiCamera() as camera:
            camera.resolution = (1280, 720)
            camera.annotate_text_size = 24
            camera.annotate_foreground = 0xFF0000
            camera.annotate_text = now_pacific.strftime("%Y-%m-%d %H:%M:%S %Z")
            camera.start_preview()
            time.sleep(1)
            camera.capture(IMAGE_PATH)
            camera.stop_preview()
        return True
    except Exception as e:
        app.logger.error(f"Camera error: {e}")
        return False


def get_weather_icon(temp):
    """Return appropriate weather icon based on temperature."""
    if temp is None:
        return "❓"
    if temp < 0:
        return "❄️"
    elif temp < 10:
        return "🧥"
    elif temp < 20:
        return "☁️"
    elif temp < 30:
        return "☀️"
    else:
        return "🔥"


@app.route("/")
def index():
    capture_image()
    sensor_data = get_sensor_data()
    system_info = get_system_info()
    bible_verse = get_bible_verse()

    return render_template(
        "index.html",
        curr_time=datetime.now(),
        temperature=sensor_data.get("temperature"),
        pressure=sensor_data.get("pressure"),
        humidity=sensor_data.get("humidity"),
        altitude=sensor_data.get("altitude"),
        weather_icon=get_weather_icon(sensor_data.get("temperature")),
        system_info=system_info,
        bible_verse=bible_verse,
        image_exists=IMAGE_PATH.exists()
    )


@app.route("/api/data")
def api_data():
    """API endpoint for live data updates."""
    capture_image()
    sensor_data = get_sensor_data()
    return jsonify({
        "timestamp": datetime.now().isoformat(),
        **sensor_data,
        "weather_icon": get_weather_icon(sensor_data.get("temperature"))
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
