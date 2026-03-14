import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

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
    """Get system info using fastfetch or neofetch."""
    for cmd in [["fastfetch", "--stdout"], ["fastfetch"], ["neofetch", "--stdout"]]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return "System info unavailable"


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
    """Capture image from PiCamera with timestamp overlay."""
    if not PICAMERA_AVAILABLE:
        return False

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = IMAGE_FOLDER / f"{timestamp}.jpg"

    try:
        if IMAGE_PATH.exists():
            shutil.copy(IMAGE_PATH, backup_path)

        with PiCamera() as camera:
            camera.resolution = (1280, 720)
            camera.annotate_text_size = 24
            camera.annotate_foreground = 0xFF0000
            camera.annotate_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

    return render_template(
        "index.html",
        curr_time=datetime.now(),
        temperature=sensor_data.get("temperature"),
        pressure=sensor_data.get("pressure"),
        humidity=sensor_data.get("humidity"),
        altitude=sensor_data.get("altitude"),
        weather_icon=get_weather_icon(sensor_data.get("temperature")),
        system_info=system_info,
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
