#!/usr/bin/env python

import os
import redis
from flask import Flask
import fladmin
app = Flask(__name__)

url = os.getenv('REDISTOGO_URL', 'redis://localhost:6379')
conn = redis.from_url(url)

fladmin.setup(conn, url)
app.register_blueprint(fladmin.admin, url_prefix='/admin')


@app.route('/', methods=['GET'])
def index():
    return 'test'

if __name__ == '__main__':
    app.run(debug=True)
