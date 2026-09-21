# StormRoute -- Stormwater Routing & Permitting

## Starting the app (Windows -- the easy way)

Double-click **`start_app.bat`**. That's it -- it starts the server and
opens the app in your browser automatically, like any other program.

A black window will open and stay open while you use the app -- that's
normal, it's running the server in the background. **Don't close it**
until you're done; closing it stops the app. When you're finished,
just close that window (or press a key when it says "The app has
stopped").

**To make this feel like a normal desktop app**: right-click
`start_app.bat` -> "Show more options" (Windows 11) -> "Send to" ->
"Desktop (create shortcut)". You can rename that shortcut to whatever
you like and it'll launch the app from your desktop.

If double-clicking doesn't work the first time, you likely need the
one-time setup below first.

## Setup (one-time)

Requires Python 3.9+ and Flask:

    pip install flask reportlab openpyxl

## Starting the app (command line / Mac / Linux)

    python3 server.py

Then open **http://127.0.0.1:5000** in your browser.

## Important: how to open this app

Do NOT double-click `app.html` directly. It has no server to talk to
that way, and every calculation will fail with "Failed to fetch."
Always start it via `start_app.bat` (Windows) or `python3 server.py`
(command line), then use the browser address shown above. (If you do
open `app.html` directly by mistake, the app now shows a clear warning
screen explaining this instead of a confusing fetch error.)

## How it works

- The browser page (`app.html`) is the UI: enter project info, edit
  existing/proposed basin geometry and structures, pick storm events.
- Clicking "Run Model" or generating a report sends your project data
  to the local server, which runs it through the real calculation
  engine (the same one validated by the test suite) and sends results
  back. No calculation happens in the browser itself.
- **Save Project** / **Open Project...** (bottom of the sidebar) save
  and reload one `.json` file per project -- your own local files, not
  stored on the server. The server holds nothing between requests.

## Well/Pump Stack Generator

Regulatory agencies expect a well rated in gpm-per-foot-of-head to be
modeled as multiple discrete pump/well structures at incrementing
elevations, since Cascade has no native continuous head-capacity
structure. In the Existing/Proposed Conditions tab, "+ Generate
Well/Pump Stack..." builds this automatically: enter the rate per well,
number of wells, starting elevation, step height, and number of
stages, and it creates one structure per stage with the correct
combined capacity at each -- removing the manual bookkeeping that's an
easy place to accidentally double an increment or skip one.

## Known limitations

- Multi-basin networks are supported (add basins, connect them with
  interbasin orifice/weir links) -- but the Excel and standalone
  swale/exfiltration PDF exports are inherently single-basin-style
  calc sheets, so they use only the FIRST basin per condition. The
  main routing results and reports handle any number of basins.
- Rainfall depths default to placeholder values -- always verify/edit
  them against your project's actual regulatory rainfall data before
  relying on results (the app's own QA findings will flag this).
- Nothing here has been validated against real Cascade output. Treat
  all results as a working calculation tool, not a permit-ready source
  of truth, until that validation exists.
