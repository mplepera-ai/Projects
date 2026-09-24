"""
launch.py

Double-click entry point (via start_app.bat) for people who don't want
to type commands. Starts the Flask server and opens the default
browser to it automatically, so using this app feels like opening any
other program.

This intentionally does NOT change server.py itself -- it just runs
server.py's own code as if you'd typed "python server.py", from a
background thread that also opens your browser a moment later. If you
prefer the command-line way, python3 server.py still works exactly as
before.
"""

import os
import sys
import threading
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def open_browser_when_ready():
    # Give Flask a moment to actually start listening before pointing
    # a browser at it -- opening too early just shows a connection error.
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:5000")


def main():
    os.chdir(BASE_DIR)
    sys.path.insert(0, BASE_DIR)

    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    print("Starting Drainage Calculations app...")
    print("Your browser should open automatically in a moment.")
    print("If it doesn't, go to: http://127.0.0.1:5000")
    print()
    print("Keep this window open while using the app.")
    print("Closing this window stops the app.")
    print()

    server_path = os.path.join(BASE_DIR, "server.py")
    with open(server_path) as f:
        server_code = f.read()
    # Run server.py's own code directly, exactly as "python server.py"
    # would -- this is what actually starts Flask and blocks here.
    exec(compile(server_code, server_path, "exec"), {"__name__": "__main__", "__file__": server_path})


if __name__ == "__main__":
    main()
