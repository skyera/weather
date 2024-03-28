import time
from datetime import datetime
from subprocess import call

from flask import Flask, render_template
from picamera import PiCamera

import bme280 as bme

app = Flask(__name__, static_url_path="/static")
filename = "/home/pi/test/weather/static/image.jpg"


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

    cmd = f"/usr/bin/convert {filename} -pointsize 16 -fill red -annotate +400+450 '{now}' {filename}"
    call(cmd, shell=True)

    return render_template("index.html", curr_time=now, temperature=temperature)
