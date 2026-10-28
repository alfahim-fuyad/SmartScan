#!/usr/bin/env bash
set -euo pipefail

echo "==> Upgrading pip"
pip install --upgrade pip

echo "==> Installing Python dependencies"
pip install -r requirements.txt

echo "==> Running Django system checks"
python manage.py check

echo "==> Collecting static files (WhiteNoise)"
python manage.py collectstatic --noinput

echo "==> Applying database migrations"
python manage.py migrate --noinput

echo "==> Build complete."
