"""Production launcher: serve the control panel with waitress (not runserver).

Importing controlpanel.wsgi triggers Django startup, which (with
SCHEDULER_AUTOSTART=1) starts the in-process APScheduler that runs the syncs.
Run this under a Windows Scheduled Task so it auto-starts on logon and
auto-restarts on crash. Bind to localhost only.
"""

import os
import sys

# Ensure this file's directory (the Django project root) is importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
os.environ.setdefault("SCHEDULER_AUTOSTART", "1")

HOST = os.environ.get("OMSREE_HOST", "127.0.0.1")
PORT = int(os.environ.get("OMSREE_PORT", "8787"))


def main():
    from waitress import serve
    from controlpanel.wsgi import application

    serve(application, host=HOST, port=PORT, threads=8)


if __name__ == "__main__":
    main()
