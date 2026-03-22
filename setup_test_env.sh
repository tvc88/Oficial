#!/bin/bash
set -e
python -m pip install --upgrade pip
pip install -r requirements.txt
apt-get update
apt-get install -y ffmpeg
