import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

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
    """Get a random Bible verse with English and Chinese translations."""
    verse_en = {}
    verse_zh = {}
    
    try:
        # Get random English verse
        response = requests.get("https://bible-api.com/?random=books", timeout=5)
        if response.status_code == 200:
            data = response.json()
            verse_en = {
                "reference": data.get("reference", ""),
                "text": data.get("text", "").strip(),
                "translation": data.get("translation_name", "Web English Bible")
            }
            
            # Try to get Chinese translation of the same verse
            reference = verse_en.get("reference", "John 1:1")
            try:
                # Try to get from bible-api with different format or fallback service
                # For now, create a hardcoded mapping of common verses
                verse_zh = get_chinese_verse(reference)
            except:
                verse_zh = {"text": "仰望耶稣，信心创造者和完成者。"}
    except (requests.RequestException, ValueError, KeyError):
        pass
    
    if not verse_en:
        # Fallback verse
        verse_en = {
            "reference": "Psalm 23:1",
            "text": "The Lord is my shepherd; I shall not want.",
            "translation": "King James Version"
        }
        verse_zh = {"text": "耶和华是我的牧者，我必不至缺乏。"}
    
    return {
        "reference": verse_en.get("reference", ""),
        "text_en": verse_en.get("text", ""),
        "translation_en": verse_en.get("translation", ""),
        "text_zh": verse_zh.get("text", "")
    }


def get_chinese_verse(reference):
    """Get Chinese translation of a Bible verse."""
    # Hardcoded mapping of common Bible verses
    verses_map = {
        "John 1:1": "太初有道，道与神同在，道就是神。",
        "Psalm 23:1": "耶和华是我的牧者，我必不至缺乏。",
        "Matthew 5:3": "虚心的人有福了，因为天国是他们的。",
        "Romans 3:23": "因为世人都犯了罪，亏缺了神的荣耀。",
        "John 3:16": "神爱世人，甚至将他的独生子赐给他们，叫一切信他的，不至灭亡，反得永生。",
        "1 John 4:7": "亲爱的弟兄啊，我们应当彼此相爱，因为爱是从神来的。凡有爱心的，都是由神而生，并且认识神。",
        "Proverbs 3:5": "你要尽心、尽性、尽力爱主你的神。",
        "Philippians 4:6": "应当一无挂虑，只要凡事借着祷告、祈求，和感谢，将你们所要的告诉神。",
        "Proverbs 3:6": "你要在你一切所行的事上都认定他，他必指引你的路。",
        "1 Peter 5:7": "你们要将一切的忧虑卸给神，因为他关心你们。",
    }
    
    # Try to find the verse in the map
    for key, value in verses_map.items():
        if key.lower() in reference.lower():
            return {"text": value}
    
    # Default Chinese verse if not found
    return {"text": "主啊，求你保守我们的心，胜过万物，因为一生的果效，是由心发出的。"}


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
