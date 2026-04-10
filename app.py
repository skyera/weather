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


def get_nasa_apod():
    """Get NASA Astronomy Picture of the Day."""
    # Using 'DEMO_KEY' by default (limited but works)
    api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    try:
        response = requests.get(f"https://api.nasa.gov/planetary/apod?api_key={api_key}", timeout=10)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        app.logger.warning(f"Failed to fetch NASA APOD: {e}")
    return None


@app.route('/api/photo')
def api_photo():
    """Return a JSON object with a random nature photo."""
    photo = get_random_nature_photo()
    return jsonify(photo)


@app.route('/api/nasa-apod')
def api_nasa_apod():
    """Return NASA Astronomy Picture of the Day."""
    apod = get_nasa_apod()
    if apod:
        return jsonify(apod)
    return jsonify({"error": "Failed to fetch APOD"}), 500


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


@app.route('/api/algorithm')
def api_algorithm():
    """Return the algorithm of the day."""
    return jsonify(get_algorithm_of_the_day())


@app.route('/api/shortcut')
def api_shortcut():
    """Return a random keyboard shortcut tip."""
    return jsonify(get_shortcut_tip())


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


def get_hacker_news():
    """Get top 5 stories from Hacker News using the Firebase API."""
    try:
        # Get top story IDs
        top_ids_resp = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=5)
        if top_ids_resp.status_code == 200:
            top_ids = top_ids_resp.json()[:5]
            stories = []
            for item_id in top_ids:
                item_resp = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=5)
                if item_resp.status_code == 200:
                    item = item_resp.json()
                    stories.append({
                        "title": item.get("title", "No Title"),
                        "link": item.get("url") or f"https://news.ycombinator.com/item?id={item_id}",
                        "score": item.get("score", 0),
                        "comments": item.get("descendants", 0)
                    })
            return stories
    except Exception as e:
        app.logger.warning(f"Failed to fetch Hacker News: {e}")
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
    """Get a random Hollywood movie from the JSON file."""
    try:
        with open(BASE_DIR / 'movies.json', 'r') as f:
            movies = json.load(f)
        return random.choice(movies)
    except (IOError, json.JSONDecodeError, IndexError) as e:
        app.logger.error(f"Error reading or parsing movies.json: {e}")
        return {"title": "Error", "year": "N/A", "description": "Could not load movie data."}

def get_algorithm_of_the_day():
    """Get the algorithm of the day, rotating daily through the collection."""
    try:
        with open(BASE_DIR / 'algorithms.json', 'r') as f:
            algorithms = json.load(f)
        # Use the day of year to deterministically pick one algorithm per day
        day_of_year = datetime.now().timetuple().tm_yday
        index = day_of_year % len(algorithms)
        return algorithms[index]
    except (IOError, json.JSONDecodeError, IndexError) as e:
        app.logger.error(f"Error reading algorithms.json: {e}")
        return {
            "name": "Binary Search",
            "category": "Searching",
            "complexity": "O(log n)",
            "description": "Finds a target in a sorted array by halving the search space.",
            "pseudocode": "low=0, high=n-1\nwhile low<=high:\n  mid=(low+high)/2\n  if arr[mid]==target: return mid"
        }


def get_shortcut_tip():
    """Get a random keyboard shortcut tip from the JSON file."""
    try:
        with open(BASE_DIR / 'shortcuts.json', 'r') as f:
            shortcuts = json.load(f)
        return random.choice(shortcuts)
    except (IOError, json.JSONDecodeError, IndexError) as e:
        app.logger.error(f"Error reading or parsing shortcuts.json: {e}")
        return {"app": "Error", "shortcut": "N/A", "description": "Could not load shortcut data."}


def get_cpp_tip():
    """Get a random C++ best practice tip from the JSON file."""
    try:
        with open(BASE_DIR / 'cpp_tips.json', 'r') as f:
            tips = json.load(f)
        return random.choice(tips)
    except (IOError, json.JSONDecodeError, IndexError) as e:
        app.logger.error(f"Error reading or parsing cpp_tips.json: {e}")
        return "Error: Could not load C++ tips."



def get_this_day_in_history():
    """Get historical events that happened on this day using the Muffin Labs API."""
    today = datetime.now()
    month = today.month
    day = today.day
    try:
        response = requests.get(
            f"https://history.muffinlabs.com/date/{month}/{day}",
            headers={"Accept": "application/json"},
            timeout=8,
        )
        if response.status_code == 200:
            data = response.json().get("data", {})
            events = data.get("Events", [])[:5]
            births = data.get("Births", [])[:3]
            deaths = data.get("Deaths", [])[:3]
            return {
                "date": f"{today.strftime('%B')} {day}",
                "events": [
                    {"year": e.get("year", "?"), "text": e.get("text", "")}
                    for e in events
                ],
                "births": [
                    {"year": b.get("year", "?"), "text": b.get("text", "")}
                    for b in births
                ],
                "deaths": [
                    {"year": d.get("year", "?"), "text": d.get("text", "")}
                    for d in deaths
                ],
            }
    except Exception as e:
        app.logger.warning(f"Failed to fetch this day in history: {e}")

    # Fallback
    return {
        "date": f"{today.strftime('%B')} {day}",
        "events": [{"year": "—", "text": "Could not load historical events."}],
        "births": [],
        "deaths": [],
    }


def get_historical_figure():
    """Get a random historical figure with a Wikipedia link."""
    figures = [
        {"name": "Leonardo da Vinci", "title": "Polymath of the Renaissance", "link": "https://en.wikipedia.org/wiki/Leonardo_da_Vinci", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cb/Francesco_Melzi_-_Portrait_of_Leonardo.png/440px-Francesco_Melzi_-_Portrait_of_Leonardo.png"},
        {"name": "Marie Curie", "title": "Pioneer in Radioactivity", "link": "https://en.wikipedia.org/wiki/Marie_Curie", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c8/Marie_Curie_1903.jpg/440px-Marie_Curie_1903.jpg"},
        {"name": "Albert Einstein", "title": "Theoretical Physicist", "link": "https://en.wikipedia.org/wiki/Albert_Einstein", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Albert_Einstein_Head.jpg/440px-Albert_Einstein_Head.jpg"},
        {"name": "Ada Lovelace", "title": "First Computer Programmer", "link": "https://en.wikipedia.org/wiki/Ada_Lovelace", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0b/Ada_Byron_daguerreotype_by_Antoine_Claudet_1843_or_1850_-_crop.png/440px-Ada_Byron_daguerreotype_by_Antoine_Claudet_1843_or_1850_-_crop.png"},
        {"name": "Nelson Mandela", "title": "Anti-apartheid Revolutionary", "link": "https://en.wikipedia.org/wiki/Nelson_Mandela", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/02/Nelson_Mandela_1994.jpg/440px-Nelson_Mandela_1994.jpg"},
        {"name": "Cleopatra", "title": "Last Active Ruler of Ptolemaic Egypt", "link": "https://en.wikipedia.org/wiki/Cleopatra", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Kleopatra-VII.-Altes-Museum-Berlin1.jpg/440px-Kleopatra-VII.-Altes-Museum-Berlin1.jpg"},
        {"name": "Mahatma Gandhi", "title": "Leader of Indian Independence", "link": "https://en.wikipedia.org/wiki/Mahatma_Gandhi", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Mahatma-Gandhi%2C_studio%2C_1931.jpg/440px-Mahatma-Gandhi%2C_studio%2C_1931.jpg"},
        {"name": "Abraham Lincoln", "title": "16th U.S. President", "link": "https://en.wikipedia.org/wiki/Abraham_Lincoln", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Abraham_Lincoln_O-77_matte_collodion_print.jpg/440px-Abraham_Lincoln_O-77_matte_collodion_print.jpg"},
        {"name": "Joan of Arc", "title": "Heroine of France", "link": "https://en.wikipedia.org/wiki/Joan_of_Arc", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/39/Joan_of_arc_miniature_graded.jpg/440px-Joan_of_arc_miniature_graded.jpg"},
        {"name": "Nikola Tesla", "title": "Inventor and Electrical Engineer", "link": "https://en.wikipedia.org/wiki/Nikola_Tesla", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/79/Tesla_circa_1890.jpeg/440px-Tesla_circa_1890.jpeg"},
        {"name": "Galileo Galilei", "title": "Father of Modern Observational Astronomy", "link": "https://en.wikipedia.org/wiki/Galileo_Galilei", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d4/Justus_Sustermans_-_Portrait_of_Galileo_Galilei%2C_1636.jpg/440px-Justus_Sustermans_-_Portrait_of_Galileo_Galilei%2C_1636.jpg"},
        {"name": "Winston Churchill", "title": "Prime Minister of the United Kingdom", "link": "https://en.wikipedia.org/wiki/Winston_Churchill", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bc/Sir_Winston_Churchill_1941_Statesman.jpg/440px-Sir_Winston_Churchill_1941_Statesman.jpg"},
        {"name": "Hypatia", "title": "Philosopher, Astronomer, and Mathematician", "link": "https://en.wikipedia.org/wiki/Hypatia", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f2/Hypatia_by_Charles_William_Mitchell.jpg/440px-Hypatia_by_Charles_William_Mitchell.jpg"},
        {"name": "Alan Turing", "title": "Father of Theoretical Computer Science", "link": "https://en.wikipedia.org/wiki/Alan_Turing", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/17/Alan_Turing_Aged_16.jpg/440px-Alan_Turing_Aged_16.jpg"},
        {"name": "Rosa Parks", "title": "Mother of the Civil Rights Movement", "link": "https://en.wikipedia.org/wiki/Rosa_Parks", "photo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c4/Rosa_Parks_2.jpg/440px-Rosa_Parks_2.jpg"}
    ]
    return random.choice(figures)


@app.route("/")
def index():
    # This route is now very lightweight. It just renders the shell.
    # All data will be loaded by JavaScript on the client side.
    return render_template(
        "index.html",
        curr_time=datetime.now(),
        image_exists=IMAGE_PATH.exists()
    )


@app.route("/api/this-day-in-history")
def api_this_day_in_history():
    """API endpoint for this day in history."""
    return jsonify(get_this_day_in_history())


@app.route("/api/historical-figure")
def api_historical_figure():
    """API endpoint for a random historical figure."""
    return jsonify(get_historical_figure())


@app.route("/api/holidays")
def api_holidays():
    """API endpoint for upcoming holidays."""
    holidays = get_upcoming_holidays()
    return jsonify(holidays)


@app.route("/api/news")
def api_news():
    """API endpoint for news."""
    return jsonify({
        "top_stories": get_news(),
        "ai_news": get_ai_news(),
        "hn_stories": get_hacker_news()
    })


@app.route("/api/wisdom")
def api_wisdom():
    """API endpoint for wisdom (verse, quote, word)."""
    return jsonify({
        "bible_verse": get_bible_verse(),
        "famous_quote": get_famous_quote(),
        "random_word": get_random_word(),
    })


@app.route("/api/system")
def api_system():
    """API endpoint for system info."""
    return jsonify(get_system_info())


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
