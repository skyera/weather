import os
import shutil
import subprocess
import time
import random
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests
from flask import Flask, render_template, jsonify, url_for, request

# Camera availability detection
PICAMERA_AVAILABLE = False
try:
    from picamera import PiCamera
    PICAMERA_AVAILABLE = True
except Exception:
    PICAMERA_AVAILABLE = False

# We'll also detect if raspistill (legacy) or libcamera-still (new API) are available
def command_exists(cmd):
    from shutil import which
    return which(cmd) is not None

RASPISILL_AVAILABLE = command_exists('raspistill')
LIBCAMERA_STILL_AVAILABLE = command_exists('libcamera-still')


try:
    import bme280 as bme
    BME280_AVAILABLE = True
except ImportError:
    BME280_AVAILABLE = False

from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__, static_url_path="/static")

BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"
IMAGE_PATH = STATIC_DIR / "image.jpg"
IMAGE_FOLDER = Path.home() / "Pictures"
DB_PATH = BASE_DIR / "weather_history.db"

STATIC_DIR.mkdir(exist_ok=True)
IMAGE_FOLDER.mkdir(exist_ok=True)

# Database lock for thread-safe operations
DB_LOCK = threading.Lock()


def init_db():
    """Initialize the temperature history database."""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS temperature_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                temperature REAL,
                pressure REAL,
                humidity REAL
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON temperature_readings(timestamp)')
        conn.commit()
        conn.close()


def record_temperature(temperature, pressure, humidity):
    """Record temperature reading in the database."""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO temperature_readings (temperature, pressure, humidity) VALUES (?, ?, ?)',
                (temperature, pressure, humidity)
            )
            conn.commit()
            
            # Clean up old data (keep only last 7 days)
            week_ago = datetime.now() - timedelta(days=7)
            cursor.execute('DELETE FROM temperature_readings WHERE timestamp < ?', (week_ago,))
            conn.commit()
            conn.close()
        except Exception as e:
            app.logger.error(f"Error recording temperature: {e}")


def get_temperature_history(hours=24):
    """Get temperature history for the last N hours."""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            cutoff_time = datetime.now() - timedelta(hours=hours)
            cursor.execute(
                'SELECT timestamp, temperature FROM temperature_readings WHERE timestamp > ? ORDER BY timestamp ASC',
                (cutoff_time,)
            )
            rows = cursor.fetchall()
            conn.close()
            
            return [
                {
                    'timestamp': row[0],
                    'temperature': row[1]
                }
                for row in rows
            ]
        except Exception as e:
            app.logger.error(f"Error retrieving temperature history: {e}")
            return []


# Initialize database on startup
init_db()


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
            
            # Store raw seconds for badge display
            info["uptime_seconds"] = uptime_seconds
    except (FileNotFoundError, IOError, ValueError):
        info["uptime"] = "Uptime unavailable"
        info["uptime_seconds"] = 0
    
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
    
    # Camera hardware info
    camera_info = {
        "picamera": PICAMERA_AVAILABLE,
        "raspistill": RASPISILL_AVAILABLE,
        "libcamera": LIBCAMERA_STILL_AVAILABLE
    }
    try:
        if command_exists('vcgencmd'):
            res = subprocess.run(['vcgencmd', 'get_camera'], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                camera_info["vcgencmd"] = res.stdout.strip()
            else:
                camera_info["vcgencmd"] = "vcgencmd present but command failed"
        else:
            camera_info["vcgencmd"] = "vcgencmd not found"
    except Exception as e:
        camera_info["vcgencmd"] = f"Error: {e}"

    info["camera"] = camera_info

    # BME280 hardware info
    try:
        if BME280_AVAILABLE:
            try:
                bme_id = bme.readBME280ID()
                info["bme280"] = {"present": True, "id": str(bme_id)}
            except Exception as e:
                info["bme280"] = {"present": True, "error": str(e)}
        else:
            info["bme280"] = {"present": False}
    except Exception as e:
        info["bme280"] = {"present": False, "error": str(e)}

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


def get_random_word():
    """Get a random word with definition and example from dictionary APIs."""
    try:
        # Fetch random word from Random Word API
        word_response = requests.get('https://random-word-api.herokuapp.com/word', timeout=5)
        if word_response.status_code != 200:
            return get_fallback_word()
        
        word = word_response.json()[0]
        
        # Fetch definition from Free Dictionary API
        def_response = requests.get(f'https://api.dictionaryapi.dev/api/v2/entries/en/{word}', timeout=5)
        if def_response.status_code != 200:
            return get_fallback_word()
        
        data = def_response.json()[0]
        
        # Extract definition and example
        definition = "No definition available"
        example = "No example available"
        
        if data.get('meanings') and len(data['meanings']) > 0:
            meaning = data['meanings'][0]
            if meaning.get('definitions') and len(meaning['definitions']) > 0:
                definition = meaning['definitions'][0].get('definition', definition)
                example = meaning['definitions'][0].get('example', example)
        
        return {
            "word": word.capitalize(),
            "definition": definition,
            "example": example
        }
    
    except Exception as e:
        app.logger.warning(f"Failed to fetch random word: {e}")
        return get_fallback_word()


def get_fallback_word():
    """Return a fallback word when API is unavailable."""
    fallback_words = [
        {
            "word": "Serendipity",
            "definition": "The occurrence of events by chance in a happy or beneficial way",
            "example": "Finding that perfect book at the library was pure serendipity."
        },
        {
            "word": "Ephemeral",
            "definition": "Lasting for a very short time; transitory",
            "example": "The beauty of cherry blossoms is ephemeral, blooming for only a few weeks."
        },
        {
            "word": "Eloquent",
            "definition": "Fluent or persuasive in speaking or writing",
            "example": "The speaker gave an eloquent address about climate change."
        },
        {
            "word": "Benevolent",
            "definition": "Kind and generous; showing goodwill",
            "example": "The benevolent philanthropist donated millions to charity."
        },
        {
            "word": "Ubiquitous",
            "definition": "Present, appearing, or found everywhere",
            "example": "Smartphones have become ubiquitous in modern society."
        },
        {
            "word": "Resilient",
            "definition": "Able to withstand or recover quickly from difficult conditions",
            "example": "She proved to be a resilient person despite all her hardships."
        },
        {
            "word": "Aesthetic",
            "definition": "Concerned with beauty or the appreciation of beauty",
            "example": "The museum's aesthetic design draws visitors around the world."
        },
        {
            "word": "Pragmatic",
            "definition": "Dealing with things in a practical, realistic way based on actual circumstances",
            "example": "We need a pragmatic approach to solve this budget crisis."
        }
    ]
    
    return random.choice(fallback_words)


def get_random_nature_photo():
    """Get a random nature/landscape photo URL from Picsum (no API key required).

    Uses the Picsum list API to select a random image id and returns a sized URL.
    """
    try:
        # Request a page of images and pick one at random
        page = random.randint(1, 10)
        resp = requests.get('https://picsum.photos/v2/list', params={'page': page, 'limit': 30}, timeout=5)
        if resp.status_code == 200:
            items = resp.json()
            if items:
                item = random.choice(items)
                img_id = item.get('id')
                author = item.get('author', 'Unknown')
                # Construct a predictable sized URL (Picsum will serve the image)
                url = f'https://picsum.photos/id/{img_id}/1200/800'
                return {
                    'url': url,
                    'alt': item.get('alt_description', 'Nature photo') if isinstance(item, dict) else 'Nature photo',
                    'author': author,
                    'source': 'Picsum'
                }
    except Exception as e:
        app.logger.warning(f"Picsum fetch failed: {e}")

    # Final fallback to a public image
    return {
        'url': 'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=800&h=600&fit=crop',
        'alt': 'Mountain landscape',
        'author': 'Unsplash',
        'source': 'Unsplash'
    }


def get_sunrise_sunset():
    """Get sunrise and sunset times using the sunrise-sunset.org API."""
    try:
        # Use a default location (San Francisco). Can be customized or detected.
        response = requests.get(
            'https://api.sunrise-sunset.org/json',
            params={'lat': 37.7749, 'lng': -122.4194},
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'OK':
                results = data.get('results', {})
                return {
                    'sunrise': results.get('sunrise', 'N/A'),
                    'sunset': results.get('sunset', 'N/A'),
                    'civil_twilight_begin': results.get('civil_twilight_begin', 'N/A'),
                    'civil_twilight_end': results.get('civil_twilight_end', 'N/A'),
                }
    except (requests.RequestException, ValueError, KeyError):
        pass
    
    # Fallback values
    return {
        'sunrise': '6:30 AM',
        'sunset': '6:45 PM',
        'civil_twilight_begin': '6:00 AM',
        'civil_twilight_end': '7:15 PM',
    }


def get_sensor_data():
    """Read BME280 sensor data with fallback values."""
    if not BME280_AVAILABLE:
        data = {
            "temperature": 22.5,
            "pressure": 1013.25,
            "humidity": 45.0,
            "altitude": 0
        }
    else:
        try:
            _, _ = bme.readBME280ID()
            temperature, pressure, humidity = bme.readBME280All()
            altitude = 44330 * (1 - (pressure / 1013.25) ** 0.1903)
            data = {
                "temperature": round(temperature, 1),
                "pressure": round(pressure, 2),
                "humidity": round(humidity, 1),
                "altitude": round(altitude, 1)
            }
        except Exception as e:
            app.logger.error(f"Sensor error: {e}")
            data = {
                "temperature": None,
                "pressure": None,
                "humidity": None,
                "altitude": None,
                "error": str(e)
            }
    
    # Record the reading if temperature is available
    if data.get("temperature") is not None:
        record_temperature(data["temperature"], data.get("pressure"), data.get("humidity"))
    
    return data


def add_timestamp_to_image(image_path):
    """Add a terminal-style timestamp overlay to the image."""
    try:
        # Get Pacific timezone
        tz_pacific = pytz.timezone('US/Pacific')
        now_pacific = datetime.now(tz_pacific)
        timestamp_text = now_pacific.strftime("%Y-%m-%d %H:%M:%S %Z")

        # Open the captured image
        with Image.open(image_path) as img:
            draw = ImageDraw.Draw(img)
            
            # Try to load a monospaced font
            font_paths = [
                "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"
            ]
            font = None
            for path in font_paths:
                if os.path.exists(path):
                    font = ImageFont.truetype(path, 24)
                    break
            if not font:
                font = ImageFont.load_default()

            # Background rectangle for better readability (terminal feel)
            # Position: bottom right
            text_bbox = draw.textbbox((0, 0), timestamp_text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            margin = 20
            x = img.width - text_width - margin - 10
            y = img.height - text_height - margin - 10
            
            # Draw semi-transparent background box
            rect_padding = 5
            draw.rectangle(
                [x - rect_padding, y - rect_padding, x + text_width + rect_padding, y + text_height + rect_padding],
                fill=(0, 0, 0, 160)
            )
            
            # Draw the text in White
            draw.text((x, y), timestamp_text, font=font, fill=(255, 255, 255))
            
            img.save(image_path, quality=95)
        return True
    except Exception as e:
        app.logger.error(f"Error adding timestamp to image: {e}")
        return False


def capture_image():
    """Capture image using the available camera method.

    Order of preference:
      1. picamera (PiCamera)
      2. raspistill (legacy stack)
      3. libcamera-still (modern stack)

    Saves to static/image.jpg and creates timestamped backups.
    """
    # Get Pacific timezone
    tz_pacific = pytz.timezone('US/Pacific')
    now_pacific = datetime.now(tz_pacific)
    timestamp = now_pacific.strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = IMAGE_FOLDER / f"{timestamp}.jpg"

    # If no camera capabilities detected, skip
    if not (PICAMERA_AVAILABLE or RASPISILL_AVAILABLE or LIBCAMERA_STILL_AVAILABLE):
        app.logger.info("No camera method available; skipping capture.")
        return False

    success = False
    try:
        if IMAGE_PATH.exists():
            shutil.copy(IMAGE_PATH, backup_path)

        # Try picamera first
        if PICAMERA_AVAILABLE:
            try:
                with PiCamera() as camera:
                    camera.resolution = (1280, 720)
                    camera.start_preview()
                    time.sleep(1)
                    camera.capture(str(IMAGE_PATH))
                    camera.stop_preview()
                success = True
            except Exception as e:
                app.logger.warning(f"picamera capture failed: {e}")

        # Next try raspistill (legacy)
        if not success and RASPISILL_AVAILABLE:
            try:
                cmd = [
                    'raspistill',
                    '-o', str(IMAGE_PATH),
                    '-t', '1000',
                    '-w', '1280',
                    '-h', '720',
                    '-q', '85'
                ]
                subprocess.run(cmd, check=True, timeout=10)
                success = True
            except Exception as e:
                app.logger.warning(f"raspistill capture failed: {e}")

        # Finally try libcamera-still
        if not success and LIBCAMERA_STILL_AVAILABLE:
            try:
                cmd = [
                    'libcamera-still',
                    '-o', str(IMAGE_PATH),
                    '--timeout', '1000',
                    '--width', '1280',
                    '--height', '720'
                ]
                subprocess.run(cmd, check=True, timeout=15)
                success = True
            except Exception as e:
                app.logger.warning(f"libcamera-still capture failed: {e}")

        # Add timestamp overlay using PIL for consistency
        if success and IMAGE_PATH.exists():
            add_timestamp_to_image(IMAGE_PATH)
            # Also update the backup with the timestamped version
            shutil.copy(IMAGE_PATH, backup_path)
            return True

        return False

    except Exception as e:
        app.logger.error(f"Camera overall error: {e}")
        return False


@app.route('/api/photo')
def api_photo():
    """Return a JSON object with a random nature photo."""
    photo = get_random_nature_photo()
    return jsonify(photo)


@app.route('/api/capture', methods=['POST'])
def api_capture():
    """Trigger a camera capture and return the static image URL with cache-bust."""
    try:
        ok = capture_image()
        url = url_for('static', filename='image.jpg') + '?t=' + str(time.time())
        if ok and IMAGE_PATH.exists():
            return jsonify({'ok': True, 'url': url})
        else:
            # Return current image URL even if capture failed, with non-200 status
            return jsonify({'ok': False, 'url': url}), 500
    except Exception as e:
        app.logger.error(f"api_capture error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


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
    random_word = get_random_word()
    sunrise_sunset = get_sunrise_sunset()

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
        random_word=random_word,
        sunrise_sunset=sunrise_sunset,
        nature_photo=get_random_nature_photo(),
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


@app.route("/api/temperature-history")
def api_temperature_history():
    """API endpoint for temperature history (last 24 hours)."""
    hours = request.args.get('hours', default=24, type=int)
    history = get_temperature_history(hours=hours)
    return jsonify({
        "data": history,
        "count": len(history)
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
