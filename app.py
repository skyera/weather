import subprocess
import time
from datetime import datetime
from subprocess import call

from flask import Flask, render_template
from picamera import PiCamera

import bme280 as bme

app = Flask(__name__, static_url_path="/static")
filename = "/home/pi/test/weather/static/image.jpg"


def get_neofetch_info():
    try:
        neofetch_output = subprocess.check_output(
            "neofetch --stdout", shell=True, text=True
        )
        return neofetch_output
    except subprocess.CalledProcessError as e:
        return "Failed to retrieve system info"


def get_fastfetch_info():
    try:
        # Execute the fastfetch command and capture the output
        fastfetch_output = subprocess.check_output(["fastfetch"], text=True)
        return fastfetch_output
    except subprocess.CalledProcessError:
        return "Failed to retrieve system info."


@app.route("/")
def index():
    now = datetime.now()
    chip_id, chip_version = bme.readBME280ID()
    temperature, pressure, humidity = bme.readBME280All()
    camera = PiCamera()
    camera.resolution = (640, 480)

    camera.start_preview()
    time.sleep(2)
    camera.capture("/home/pi/test/weather/static/image.jpg")
    camera.stop_preview()
    camera.close()

    neofetch_info = get_neofetch_info()
    # neofetch_info = get_fastfetch_info()

    cmd = f"/usr/bin/convert {filename} -pointsize 16 -fill red -annotate +400+450 '{now}' {filename}"
    call(cmd, shell=True)

    return render_template(
        "index.html",
        curr_time=now,
        temperature=temperature,
        neofetch_info=neofetch_info,
    )
