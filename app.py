import os
import shutil
import subprocess
import time
import random
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
    """Get a random famous Bible verse."""
    famous_verses = [
        {
            "reference": "John 3:16",
            "text": "For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish, but have everlasting life.",
            "translation": "King James Version"
        },
        {
            "reference": "Psalm 23:1",
            "text": "The Lord is my shepherd; I shall not want.",
            "translation": "King James Version"
        },
        {
            "reference": "Matthew 5:3-10",
            "text": "Blessed are the poor in spirit: for theirs is the kingdom of heaven. Blessed are they that mourn: for they shall be comforted. Blessed are the meek: for they shall inherit the earth. Blessed are they which do hunger and thirst after righteousness: for they shall be filled.",
            "translation": "King James Version"
        },
        {
            "reference": "Romans 3:23",
            "text": "For all have sinned, and come short of the glory of God.",
            "translation": "King James Version"
        },
        {
            "reference": "John 11:25-26",
            "text": "Jesus said unto her, I am the resurrection, and the life: he that believeth in me, though he were dead, yet shall he live.",
            "translation": "King James Version"
        },
        {
            "reference": "Proverbs 3:5-6",
            "text": "Trust in the Lord with all thine heart; and lean not unto thine own understanding. In all thy ways acknowledge him, and he shall direct thy paths.",
            "translation": "King James Version"
        },
        {
            "reference": "1 Corinthians 13:4-7",
            "text": "Charity suffereth long, and is kind; charity envieth not; charity vaunteth not itself, is not puffed up. Doth not behave itself unseemly, seeketh not her own, is not easily provoked, thinketh no evil.",
            "translation": "King James Version"
        },
        {
            "reference": "Philippians 4:6-7",
            "text": "Be careful for nothing; but in every thing by prayer and supplication with thanksgiving let your requests be made known unto God. And the peace of God, which passeth all understanding, shall keep your hearts and your minds through Christ Jesus.",
            "translation": "King James Version"
        },
        {
            "reference": "Matthew 6:9-13",
            "text": "Our Father which art in heaven, Hallowed be thy name. Thy kingdom come. Thy will be done in earth, as it is in heaven. Give us this day our daily bread.",
            "translation": "King James Version"
        },
        {
            "reference": "1 John 4:7-8",
            "text": "Beloved, let us love one another: for love is of God; and every one that loveth is born of God, and knoweth God. He that loveth not knoweth not God; for God is love.",
            "translation": "King James Version"
        },
        {
            "reference": "2 Timothy 1:7",
            "text": "For God hath not given us the spirit of fear; but of power, and of love, and of a sound mind.",
            "translation": "King James Version"
        },
        {
            "reference": "Joshua 1:8-9",
            "text": "This book of the law shall not depart out of thy mouth; but thou shalt meditate therein day and night, that thou mayest observe to do according to all that is written therein: for then thou shalt make thy way prosperous, and then thou shalt have good success.",
            "translation": "King James Version"
        },
        {
            "reference": "Jeremiah 29:11",
            "text": "For I know the thoughts that I think toward you, saith the Lord, thoughts of peace, and not of evil, to give you an expected end.",
            "translation": "King James Version"
        },
        {
            "reference": "Psalm 119:105",
            "text": "Thy word is a lamp unto my feet, and a light unto my path.",
            "translation": "King James Version"
        },
        {
            "reference": "Romans 6:23",
            "text": "For the wages of sin is death; but the gift of God is eternal life through Jesus Christ our Lord.",
            "translation": "King James Version"
        },
        {
            "reference": "Ephesians 2:8-9",
            "text": "For by grace are ye saved through faith; and that not of yourselves: it is the gift of God: Not of works, lest any man should boast.",
            "translation": "King James Version"
        },
        {
            "reference": "Matthew 7:7-8",
            "text": "Ask, and it shall be given you; seek, and ye shall find; knock, and it shall be opened unto you: For every one that asketh receiveth; and he that seeketh findeth; and to him that knocketh it shall be opened.",
            "translation": "King James Version"
        },
        {
            "reference": "1 Peter 5:7",
            "text": "Casting all your care upon him; for he careth for you.",
            "translation": "King James Version"
        },
        {
            "reference": "Proverbs 22:6",
            "text": "Train up a child in the way he should go: and when he is old, he will not depart from it.",
            "translation": "King James Version"
        },
        {
            "reference": "Psalm 100:1-2",
            "text": "Make a joyful noise unto the Lord, all ye earth. Serve the Lord with gladness: come before his presence with singing.",
            "translation": "King James Version"
        }
    ]
    
    # Return a random famous verse
    return random.choice(famous_verses)


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
