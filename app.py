from datetime import datetime

from flask import Flask, render_template

import bme280 as bme

app = Flask(__name__, static_url_path="/static")


@app.route("/")
def index():
    now = datetime.now()
    chip_id, chip_version = bme.readBME280ID()
    temperature, pressure, humidity = bme.readBME280All()

    return render_template("index.html", curr_time=now, temperature=temperature)
