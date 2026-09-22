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

## Dewatering (tab 10)

A companion tool living in the same app: excavation dewatering flow
(Sichardt radius of influence + Dupuit/Thiem radial flow), a multi-zone
water balance, general-permit threshold screening, and settling-tank
sizing (surface overflow rate + Stokes'-Law Reynolds-number check).

- Its own engine module (`dewatering/calculations.py`), validated
  against a real dewatering calc spreadsheet -- see
  `dewatering/tests/test_calculations.py`.
- Its own API routes (`/api/dewatering/run`, `/api/dewatering/report/pdf`)
  and adapter (`api/dewatering_adapter.py`), following the same "server
  never reimplements calc logic, just shape-shifts JSON" rule as the
  main app.
- Its own Save/Open file, separate from the main Project File, saved as
  `<project-name>.dewatering.json` -- same client-side Blob/FileReader
  pattern as the main app, own schema version
  (`DEWATERING_FILE_SCHEMA_VERSION`).
- Nothing is persisted server-side here either -- the server holds no
  state between requests, matching the rest of this app.
- Settling tank has two modes: check a tank size you enter, or
  auto-size the smallest tank that satisfies both the surface-overflow-
  rate (capture) criterion and the horizontal-velocity (scour)
  criterion (`size_minimum_settling_tank()` in
  `dewatering/calculations.py`) -- solves the minimum plan-view area
  from A = Q/Vs, splits it by a length:width ratio, rounds up to
  buildable dimensions, and increases depth automatically if the
  requested depth is too shallow for the resulting width to pass the
  scour check.
- "Download Calculation Report (PDF)" (`reports/dewatering_calc_pdf.py`)
  produces a backup-calculations sheet showing every formula (Sichardt
  ROI, Dupuit-Forchheimer/Thiem flow, water balance, Stokes' Law /
  SOR tank sizing) with the project's actual numbers substituted in,
  per zone -- for review or permit-submittal backup, matching the style
  of the existing swale/exfiltration calc-PDF report.

**Known limitations (v1):** no multi-well superposition (each zone is
analyzed independently -- a warning fires when a zone's radius of
influence is large relative to its footprint, but it's a heuristic, not
a real superposition calc), no confined-aquifer transmissivity input
validation beyond the basic Thiem form, no groundwater mounding/
settlement screening, no pump sizing, no Excel export, no
disposal/injection-well capacity calculation (only the extraction/
excavation side is modeled), no filter-sock/silt-sock sizing (only
gravity settling-tank sizing via Stokes'/SOR). See the "Dewatering
Calculations -- Review & Software Recommendations" project doc for the
fuller feature backlog.
