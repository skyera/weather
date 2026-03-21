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
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS speed_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                download REAL,
                upload REAL,
                ping REAL
            )
        ''')
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


import json

def run_speedtest_task():
    """Background task to run speedtest and record results."""
    app.logger.info("Starting background speedtest...")
    try:
        import csv
        import io
        # Run speedtest with CSV output for reliable parsing
        result = subprocess.run(['speedtest', '-f', 'csv', '--output-header'], capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            # Parse CSV format
            reader = csv.DictReader(io.StringIO(result.stdout))
            for row in reader:
                try:
                    # Values are in bytes/second, convert to Mbps
                    download = float(row.get('download', 0)) / 1_000_000
                    upload = float(row.get('upload', 0)) / 1_000_000
                    ping = float(row.get('idle latency', 0))
                    
                    with DB_LOCK:
                        conn = sqlite3.connect(str(DB_PATH))
                        cursor = conn.cursor()
                        cursor.execute(
                            'INSERT INTO speed_tests (download, upload, ping) VALUES (?, ?, ?)',
                            (download, upload, ping)
                        )
                        conn.commit()
                        conn.close()
                    app.logger.info(f"Speedtest complete: {download:.2f} Mbps down, {upload:.2f} Mbps up, {ping:.1f} ms ping")
                    break  # Only process first row
                except (ValueError, KeyError) as e:
                    app.logger.error(f"Failed to parse speedtest CSV row: {e}")
        else:
            app.logger.error(f"Speedtest failed: {result.stderr}")
    except Exception as e:
        app.logger.error(f"Background speedtest error: {e}")


def get_latest_speedtest():
    """Get the most recent speedtest result from the database."""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            cursor.execute('SELECT download, upload, ping, timestamp FROM speed_tests ORDER BY timestamp DESC LIMIT 1')
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    "download": round(row[0], 2),
                    "upload": round(row[1], 2),
                    "ping": round(row[2], 1),
                    "timestamp": row[3]
                }
        except Exception as e:
            app.logger.error(f"Error getting speedtest result: {e}")
    return None


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


@app.route('/api/movie')
def api_movie():
    """Return a random Hollywood movie."""
    movie = get_random_movie()
    return jsonify(movie)


@app.route('/api/cpp-tip')
def api_cpp_tip():
    """Return a random C++ best practice tip."""
    tip = get_cpp_tip()
    return jsonify({"tip": tip})


import xml.etree.ElementTree as ET

def get_news():
    """Get top general news headlines from Google News RSS."""
    try:
        response = requests.get("https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", timeout=5)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            news_items = []
            for item in root.findall('.//item')[:5]:
                news_items.append({
                    "title": item.find('title').text,
                    "link": item.find('link').text,
                    "pubDate": item.find('pubDate').text
                })
            return news_items
    except Exception as e:
        app.logger.warning(f"Failed to fetch news: {e}")
    
    return []


def get_ai_news():
    """Get AI-related news (Claude, Gemini, etc.) from Google News RSS."""
    try:
        # Searching specifically for AI, Claude, Gemini, GPT
        query = "Artificial Intelligence OR Claude AI OR Gemini AI OR OpenAI"
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            news_items = []
            for item in root.findall('.//item')[:5]:
                news_items.append({
                    "title": item.find('title').text,
                    "link": item.find('link').text,
                    "pubDate": item.find('pubDate').text
                })
            return news_items
    except Exception as e:
        app.logger.warning(f"Failed to fetch AI news: {e}")
    
    return []


def get_famous_quote():
    """Get a random famous quote from the ZenQuotes API."""
    try:
        response = requests.get("https://zenquotes.io/api/random", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list):
                return {
                    "text": data[0].get("q", ""),
                    "author": data[0].get("a", "Unknown")
                }
    except (requests.RequestException, ValueError, KeyError):
        pass
    
    # Fallback quote
    return {
        "text": "The only way to do great work is to love what you do.",
        "author": "Steve Jobs"
    }


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


def get_upcoming_holidays(country_code="US"):
    """Get upcoming public holidays using the Nager.Date API."""
    try:
        response = requests.get(f"https://date.nager.at/api/v3/NextPublicHolidays/{country_code}", timeout=5)
        if response.status_code == 200:
            holidays = response.json()
            return holidays[:3]  # Return the next 3 holidays
    except Exception as e:
        app.logger.warning(f"Failed to fetch holidays: {e}")
    return []


def get_random_movie():
    """Get a random Hollywood movie with title, poster, year, and description."""
    movies = [
        {"title": "The Shawshank Redemption", "year": 1994, "poster": "https://images.justwatch.com/poster/306009282/s332", "description": "Two imprisoned men bond over a number of years, finding solace and eventual redemption through acts of common decency."},
        {"title": "The Godfather", "year": 1972, "poster": "https://images.justwatch.com/poster/8371214/s332", "description": "The aging patriarch of an organized crime dynasty transfers control of his clandestine empire to his youngest and most reluctant son."},
        {"title": "The Dark Knight", "year": 2008, "poster": "https://images.justwatch.com/poster/8869150/s332", "description": "When the menace known as the Joker wreaks havoc on Gotham, Batman must accept one of the greatest tests to fight injustice."},
        {"title": "Pulp Fiction", "year": 1994, "poster": "https://images.justwatch.com/poster/8747456/s332", "description": "The lives of two mob hitmen, a boxer, a gangster and his wife intertwine in four tales of violence and redemption."},
        {"title": "Forrest Gump", "year": 1994, "poster": "https://images.justwatch.com/poster/8398938/s332", "description": "The presidencies of Kennedy and Johnson unfold through the perspective of an Alabama man with an IQ of 75."},
        {"title": "Inception", "year": 2010, "poster": "https://images.justwatch.com/poster/8395954/s332", "description": "A thief who steals corporate secrets through dream-sharing technology is given the inverse task of planting an idea."},
        {"title": "The Matrix", "year": 1999, "poster": "https://images.justwatch.com/poster/8379526/s332", "description": "A computer programmer discovers that reality as he knows it is a simulation created by machines."},
        {"title": "Titanic", "year": 1997, "poster": "https://images.justwatch.com/poster/8362883/s332", "description": "A seventeen-year-old aristocrat falls in love with a kind but poor artist aboard the luxurious, ill-fated R.M.S. Titanic."},
        {"title": "Avatar", "year": 2009, "poster": "https://images.justwatch.com/poster/8399152/s332", "description": "A paraplegic Marine dispatched to the moon Pandora on a unique mission becomes torn between following his orders and protecting the world."},
        {"title": "Interstellar", "year": 2014, "poster": "https://images.justwatch.com/poster/8688012/s332", "description": "A team of explorers travel through a wormhole in space in an attempt to ensure humanity's survival."},
        {"title": "The Avengers", "year": 2012, "poster": "https://images.justwatch.com/poster/8585598/s332", "description": "Earth's mightiest heroes must come together and learn to fight as a team to save the world from destruction."},
        {"title": "Gladiator", "year": 2000, "poster": "https://images.justwatch.com/poster/8365640/s332", "description": "A former Roman General sets out to exact vengeance against the corrupt emperor who murdered his family."},
        {"title": "Jurassic Park", "year": 1993, "poster": "https://images.justwatch.com/poster/8401346/s332", "description": "A pragmatic paleontologist tours an almost complete theme park is tasked with protecting a couple of kids."},
        {"title": "The Lion King", "year": 1994, "poster": "https://images.justwatch.com/poster/8406268/s332", "description": "Lion prince Simba and his father are targeted by his bitter uncle, who wants to ascend the throne himself."},
        {"title": "Back to the Future", "year": 1985, "poster": "https://images.justwatch.com/poster/8367688/s332", "description": "A teenager is accidentally sent 30 years into the past in a time-traveling DeLorean and must ensure his parents fall in love."},
        {"title": "The Silence of the Lambs", "year": 1991, "poster": "https://images.justwatch.com/poster/8379636/s332", "description": "A young FBI cadet must receive the help of an incarcerated cannibal killer to catch another serial killer."},
        {"title": "Schindler's List", "year": 1993, "poster": "https://images.justwatch.com/poster/8354513/s332", "description": "In German-occupied Poland during WWII, industrialist Oskar Schindler gradually becomes concerned for his workforce."},
        {"title": "Saving Private Ryan", "year": 1998, "poster": "https://images.justwatch.com/poster/8362899/s332", "description": "Following the Normandy Landings, a group of U.S. soldiers go behind enemy lines to retrieve a paratrooper."},
        {"title": "The Departed", "year": 2006, "poster": "https://images.justwatch.com/poster/8620394/s332", "description": "An undercover cop and a mole in the police attempt to identify each other while infiltrating an Irish gang in Boston."},
        {"title": "Fight Club", "year": 1999, "poster": "https://images.justwatch.com/poster/8379534/s332", "description": "An insomniac office worker and a devil-may-care soapmaker form an underground fight club that evolves into something much more."},
        {"title": "The Sixth Sense", "year": 1999, "poster": "https://images.justwatch.com/poster/8378946/s332", "description": "A frightened, withdrawn Philadelphia boy who communicates with spirits seeks the help of a disheartened child psychologist."},
        {"title": "The Usual Suspects", "year": 1995, "poster": "https://images.justwatch.com/poster/8376446/s332", "description": "A sole survivor tells of the twisty events leading up to a horrific gun battle on a boat."},
        {"title": "Goodfellas", "year": 1990, "poster": "https://images.justwatch.com/poster/8379530/s332", "description": "The story of Henry Hill and his life in the mob, covering his relationship with his wife Karen Hill and his mob partners."},
        {"title": "The Shining", "year": 1980, "poster": "https://images.justwatch.com/poster/8362819/s332", "description": "A family isolated by heavy snowfall in a remote hotel descends into madness and violence."},
        {"title": "Jaws", "year": 1975, "poster": "https://images.justwatch.com/poster/8373644/s332", "description": "When a young woman is killed by a shark, it's up to a grizzled shark hunter to protect the town's residents."},
        {"title": "E.T. the Extra-Terrestrial", "year": 1982, "poster": "https://images.justwatch.com/poster/8363347/s332", "description": "A boy befriends an alien who landed on Earth and attempts to help it return home before authorities find it."},
        {"title": "Raiders of the Lost Ark", "year": 1981, "poster": "https://images.justwatch.com/poster/8372784/s332", "description": "In 1936, archaeologist and adventurer Indiana Jones is hired by the U.S. government to find the Ark of the Covenant."},
        {"title": "The Lord of the Rings", "year": 2001, "poster": "https://images.justwatch.com/poster/8389455/s332", "description": "A hobbit is tasked with destroying a powerful ring in the fires of Mount Doom to save Middle-earth."},
        {"title": "Harry Potter", "year": 2001, "poster": "https://images.justwatch.com/poster/8390185/s332", "description": "A young wizard attends a school for magic and discovers he is the chosen one to stop an evil sorcerer."},
        {"title": "Dune", "year": 2021, "poster": "https://images.justwatch.com/poster/241854308/s332", "description": "Paul Atreides, a brilliant young man, must travel to the dangerous planet Dune to ensure the future of his family."},
        {"title": "Oppenheimer", "year": 2023, "poster": "https://images.justwatch.com/poster/302269436/s332", "description": "The story of American scientist J. Robert Oppenheimer and his role in the development of the atomic bomb."},
        {"title": "Barbie", "year": 2023, "poster": "https://images.justwatch.com/poster/302051814/s332", "description": "Barbie's perfect life in the Barbie world is interrupted when she has a chance to go to the real world."},
        {"title": "The Prestige", "year": 2006, "poster": "https://images.justwatch.com/poster/8620142/s332", "description": "After a tragic accident, two stage magicians engage in a battle to create the ultimate illusion."},
        {"title": "Memento", "year": 2000, "poster": "https://images.justwatch.com/poster/8379544/s332", "description": "A man with short-term memory loss attempts to track down his wife's murderer using notes and Polaroid pictures."},
        {"title": "The Hangover", "year": 2009, "poster": "https://images.justwatch.com/poster/8406480/s332", "description": "Three buddies wake up from a wild bachelor party in Vegas with no memory of the previous night."},
        {"title": "Superbad", "year": 2007, "poster": "https://images.justwatch.com/poster/8384370/s332", "description": "Two socially awkward teens attempt to throw a party while dealing with high school and friendship drama."},
        {"title": "Juno", "year": 2007, "poster": "https://images.justwatch.com/poster/8384294/s332", "description": "A teen becomes pregnant and faces challenging decisions about her future and the unborn child."},
        {"title": "Whiplash", "year": 2014, "poster": "https://images.justwatch.com/poster/8688192/s332", "description": "A promising young drummer is pushed to his limits by his abusive conductor at an elite music conservatory."},
        {"title": "La La Land", "year": 2016, "poster": "https://images.justwatch.com/poster/8835638/s332", "description": "While navigating their careers in Los Angeles, a pianist and an actress fall in love while pursuing their dreams."},
        {"title": "Parasite", "year": 2019, "poster": "https://images.justwatch.com/poster/8987846/s332", "description": "A poor family schemes to become employed by a wealthy household by infiltrating their lives in unexpected ways."},
        {"title": "Knives Out", "year": 2019, "poster": "https://images.justwatch.com/poster/9029226/s332", "description": "A detective investigates the death of a wealthy novelist among a dysfunctional family of suspects."},
        {"title": "Moonlight", "year": 2016, "poster": "https://images.justwatch.com/poster/8836034/s332", "description": "The life of a Black man is explored in three chapters spanning his childhood, adolescence, and adulthood."},
        {"title": "Her", "year": 2013, "poster": "https://images.justwatch.com/poster/8620568/s332", "description": "A lonely writer falls in love with an artificial intelligence operating system designed to meet his needs."},
    ]
    
    return random.choice(movies)

def get_cpp_tip():
    """Get a random C++ best practice tip."""
    tips = [
        "Use const correctness: mark functions and variables const when they shouldn't change.",
        "Prefer smart pointers (std::unique_ptr, std::shared_ptr) over raw pointers for memory safety.",
        "Use RAII (Resource Acquisition Is Initialization) to manage resources automatically.",
        "Avoid using goto; use proper control flow structures like loops and conditions.",
        "Use std::vector instead of raw C-style arrays for dynamic memory management.",
        "Prefer pass-by-const-reference over pass-by-value for large objects.",
        "Use nullptr instead of NULL or 0 for null pointer checks.",
        "Avoid global variables; use proper scoping and namespaces.",
        "Use auto for type deduction to reduce verbosity and errors.",
        "Initialize variables at declaration point, not later in code.",
        "Use enum classes instead of unscoped enums to avoid name conflicts.",
        "Prefer std::string over char* for string handling.",
        "Use constexpr for compile-time constant expressions when possible.",
        "Avoid multiple inheritance; use composition or interfaces instead.",
        "Use noexcept for functions that don't throw exceptions.",
        "Prefer move semantics over copying for better performance.",
        "Use std::array for fixed-size arrays instead of C-style arrays.",
        "Avoid using using namespace std; use explicit std:: or specific using declarations.",
        "Use override keyword when overriding virtual functions in derived classes.",
        "Prefer range-based for loops over traditional for loops.",
        "Use std::make_unique and std::make_shared for safer pointer creation.",
        "Avoid implicit type conversions; use explicit constructors.",
        "Use final keyword to prevent further derivation if not intended.",
        "Prefer algorithms in <algorithm> over manual loops.",
        "Use std::optional for optional return values instead of pointers or out-parameters.",
        "Mark single-argument constructors explicit to avoid accidental conversions.",
        "Avoid exceptions in destructors; they can cause program termination.",
        "Use static_assert for compile-time checks instead of runtime assertions.",
        "Prefer lvalues; move semantics are for optimization, not for daily use.",
        "Use std::tuple or structured bindings for returning multiple values.",
    ]
    return random.choice(tips)



@app.route("/")
def index():
    capture_image()
    sensor_data = get_sensor_data()
    system_info = get_system_info()
    bible_verse = get_bible_verse()
    random_word = get_random_word()
    sunrise_sunset = get_sunrise_sunset()
    famous_quote = get_famous_quote()
    news_items = get_news()
    ai_news = get_ai_news()
    latest_speed = get_latest_speedtest()
    holidays = get_upcoming_holidays()
    random_movie = get_random_movie()
    cpp_tip = get_cpp_tip()

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
        famous_quote=famous_quote,
        news_items=news_items,
        ai_news=ai_news,
        speedtest=latest_speed,
        holidays=holidays,
        random_movie=random_movie,
        cpp_tip=cpp_tip,
        image_exists=IMAGE_PATH.exists()
    )


@app.route("/api/speedtest", methods=['POST'])
def api_speedtest():
    """Trigger a background speedtest."""
    thread = threading.Thread(target=run_speedtest_task)
    thread.daemon = True
    thread.start()
    return jsonify({"status": "Speedtest started in background"})


@app.route("/api/data")
def api_data():
    """API endpoint for live data updates."""
    capture_image()
    sensor_data = get_sensor_data()
    return jsonify({
        "timestamp": datetime.now().isoformat(),
        **sensor_data,
        "weather_icon": get_weather_icon(sensor_data.get("temperature")),
        "speedtest": get_latest_speedtest()
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
