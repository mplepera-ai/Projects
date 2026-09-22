"""
server.py

Local single-user server. Run with:

    python3 server.py

then open http://127.0.0.1:5000 in a browser.

Architecture note: this server holds NO project state between requests.
Every /api/run and /api/report/* call receives the full project JSON
in the request body and returns a fresh result -- the browser is the
only place project data lives (as a JS object, and as the .json file
you Save/Open). This keeps the server stateless and simple, which is
the right amount of infrastructure for "one person, for now" -- a
database and sessions would be solving a problem that doesn't exist
yet (see the multi-user note in api/adapter.py's module docstring for
what would need to change if/when this is shared with a team).

The server's only job is: take project JSON -> run it through the
REAL engine (project/model.py, qa/validation.py, reports/*) -> return
JSON or a file. It must never reimplement calculation logic itself;
that's the entire point of building this instead of keeping the JS
engine.
"""

import io
import os
import tempfile
import traceback

from flask import Flask, request, jsonify, send_file, Response

from api.adapter import (
    run_project, build_project_from_app_json, build_report_options, AdapterError,
    run_storage_calcs, build_storage_objects, merge_swale_storage_into_basin,
    suggest_trench_options, suggest_pond_options, build_narrative_context,
)
from api.dewatering_adapter import run_dewatering, build_report_objects, DewateringAdapterError
from reports.pdf_export import export_permit_report_pdf
from reports.generator import permit_summary_markdown
from reports.calc_report_pdf import export_swale_exfiltration_calc_pdf
from reports.excel_export import export_drainage_calc_workbook
from reports.dewatering_calc_pdf import export_dewatering_calc_pdf
from qa.validation import run_qa
from project.model import run_all_scenarios

APP_HTML_PATH = os.path.join(os.path.dirname(__file__), "app.html")

app = Flask(__name__)


@app.route("/")
def index():
    with open(APP_HTML_PATH, "r") as f:
        return Response(f.read(), mimetype="text/html")


@app.route("/api/run", methods=["POST"])
def api_run():
    try:
        data = request.get_json(force=True)
        payload = run_project(data)
        return jsonify(payload)
    except AdapterError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/report/pdf", methods=["POST"])
def api_report_pdf():
    try:
        body = request.get_json(force=True)
        project_data = body["project"]
        options_data = body.get("options", {})
        storage_data = body.get("storageWQ")

        project = build_project_from_app_json(project_data)
        results = run_all_scenarios(project)
        findings = run_qa(project, results)
        options = build_report_options(options_data)
        storage_ctx = build_narrative_context(storage_data) if storage_data else None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            export_permit_report_pdf(project, results, findings, tmp.name, options, storage_ctx)
            tmp_path = tmp.name

        filename = (project.metadata.project_name or "drainage-report").strip().lower()
        filename = "".join(c if c.isalnum() else "-" for c in filename).strip("-") + ".pdf"

        response = send_file(tmp_path, mimetype="application/pdf", as_attachment=True, download_name=filename)
        response.call_on_close(lambda: os.remove(tmp_path))
        return response
    except AdapterError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/report/markdown", methods=["POST"])
def api_report_markdown():
    try:
        body = request.get_json(force=True)
        project_data = body["project"]
        options_data = body.get("options", {})
        storage_data = body.get("storageWQ")

        project = build_project_from_app_json(project_data)
        results = run_all_scenarios(project)
        findings = run_qa(project, results)
        options = build_report_options(options_data)
        storage_ctx = build_narrative_context(storage_data) if storage_data else None

        text = permit_summary_markdown(project, results, findings, options, storage_ctx)
        return jsonify({"markdown": text})
    except AdapterError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/calc/storage", methods=["POST"])
def api_calc_storage():
    try:
        data = request.get_json(force=True)
        return jsonify(run_storage_calcs(data))
    except (AdapterError, KeyError) as e:
        return jsonify({"error": f"Missing or invalid input: {e}"}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/calc/trench-suggest", methods=["POST"])
def api_trench_suggest():
    try:
        data = request.get_json(force=True)
        return jsonify(suggest_trench_options(data))
    except (AdapterError, KeyError) as e:
        return jsonify({"error": f"Missing or invalid input: {e}"}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/calc/pond-suggest", methods=["POST"])
def api_pond_suggest():
    try:
        data = request.get_json(force=True)
        return jsonify(suggest_pond_options(data))
    except (AdapterError, KeyError) as e:
        return jsonify({"error": f"Missing or invalid input: {e}"}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/calc/merge-basin-storage", methods=["POST"])
def api_merge_basin_storage():
    try:
        data = request.get_json(force=True)
        merged_points = merge_swale_storage_into_basin(data["basinStagePoints"], data["swales"])
        return jsonify({"stagePoints": merged_points})
    except (AdapterError, KeyError) as e:
        return jsonify({"error": f"Missing or invalid input: {e}"}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/report/calc-pdf", methods=["POST"])
def api_report_calc_pdf():
    try:
        data = request.get_json(force=True)
        objs = build_storage_objects(data)
        if objs["existing_runoff"] is None or objs["proposed_runoff"] is None:
            return jsonify({"error": "Site areas and soil storage inputs are required for this report."}), 400

        sa = data["siteAreas"]
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            export_swale_exfiltration_calc_pdf(
                tmp.name,
                project_name=data.get("projectName", "Untitled Project"),
                project_address=data.get("projectAddress", ""),
                existing_site_sqft=float(sa["existingSiteSqft"]), existing_pervious_sqft=float(sa["existingPerviousSqft"]),
                proposed_site_sqft=float(sa["proposedSiteSqft"]), proposed_pervious_sqft=float(sa["proposedPerviousSqft"]),
                existing_runoff=objs["existing_runoff"], proposed_runoff=objs["proposed_runoff"],
                swales=objs["swales"], exfiltration_trench=objs["trench"],
                required_wq_volume_cuft=objs["required_for_trench_cuft"],
                required_volume_basis_label=objs["required_volume_basis_label"],
                required_volume_before_swale_credit_cuft=objs["required_volume_before_swale_credit_cuft"],
                pavement_elevation_ft=(
                    float(data["pavementElevationFt"])
                    if data.get("pavementElevationFt") not in (None, "")
                    else None
                ),
            )
            tmp_path = tmp.name

        response = send_file(tmp_path, mimetype="application/pdf", as_attachment=True,
                              download_name="swale-exfiltration-calculations.pdf")
        response.call_on_close(lambda: os.remove(tmp_path))
        return response
    except (AdapterError, KeyError) as e:
        return jsonify({"error": f"Missing or invalid input: {e}"}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/report/excel", methods=["POST"])
def api_report_excel():
    try:
        data = request.get_json(force=True)
        project_data = data["project"]
        storage_data = data.get("storageWQ", {})

        project = build_project_from_app_json(project_data)
        # Calc-sheet-style exports (Excel stage-storage sheet) are
        # inherently single-basin in format; for a multi-basin network,
        # use the first basin defined in each condition and note that
        # limitation rather than silently picking one with no signal.
        existing_basins = list(project.conditions["existing"].network.basins.values())
        proposed_basins = list(project.conditions["proposed"].network.basins.values())
        existing_basin = existing_basins[0] if existing_basins else None
        proposed_basin = proposed_basins[0] if proposed_basins else None

        objs = build_storage_objects(storage_data) if storage_data else {"trench": None, "required_for_trench_cuft": 0.0}
        sa = storage_data.get("siteAreas", {})
        ss = storage_data.get("soilStorage", {})

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            export_drainage_calc_workbook(
                tmp.name,
                project_name=project.metadata.project_name,
                existing_basin=existing_basin, proposed_basin=proposed_basin,
                existing_site_sqft=float(sa.get("existingSiteSqft", 0.0)),
                existing_pervious_sqft=float(sa.get("existingPerviousSqft", 0.0)),
                proposed_site_sqft=float(sa.get("proposedSiteSqft", 0.0)),
                proposed_pervious_sqft=float(sa.get("proposedPerviousSqft", 0.0)),
                existing_compacted_soil_storage_in=float(ss.get("existingCompactedIn", 0.0)),
                proposed_compacted_soil_storage_in=float(ss.get("proposedCompactedIn", 0.0)),
                rainfall_5yr_1hr_in=float(ss.get("rainfall5yr1hrIn", 0.0)),
                exfiltration_trench=objs.get("trench"),
                required_wq_volume_cuft=objs.get("required_for_trench_cuft", 0.0),
            )
            tmp_path = tmp.name

        filename = (project.metadata.project_name or "drainage-calcs").strip().lower()
        filename = "".join(c if c.isalnum() else "-" for c in filename).strip("-") + ".xlsx"

        response = send_file(
            tmp_path, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=filename,
        )
        response.call_on_close(lambda: os.remove(tmp_path))
        return response
    except (AdapterError, KeyError) as e:
        return jsonify({"error": f"Missing or invalid input: {e}"}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/dewatering/run", methods=["POST"])
def api_dewatering_run():
    try:
        data = request.get_json(force=True)
        return jsonify(run_dewatering(data))
    except DewateringAdapterError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/api/dewatering/report/pdf", methods=["POST"])
def api_dewatering_report_pdf():
    try:
        data = request.get_json(force=True)
        core = build_report_objects(data)
        if not core["zone_results"]:
            return jsonify({"error": "Add at least one valid excavation zone before generating a report."}), 400

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            export_dewatering_calc_pdf(
                tmp.name,
                project_name=data.get("projectName", "Untitled Project"),
                project_address=data.get("projectAddress", ""),
                aquifer=core["aquifer"],
                zone_results=core["zone_results"],
                zone_errors=core["zone_errors"],
                overlap_notes=core["overlap_notes"],
                summary=core["summary"],
                permit_flags=core["permit_flags"],
                tank_inputs=core["tank_inputs"],
                tank_result=core["tank_result"],
                min_tank_result=core["min_result"],
            )
            tmp_path = tmp.name

        response = send_file(tmp_path, mimetype="application/pdf", as_attachment=True,
                              download_name="dewatering-calculations.pdf")
        response.call_on_close(lambda: os.remove(tmp_path))
        return response
    except DewateringAdapterError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Server error: {e}"}), 500


if __name__ == "__main__":
    print("Starting local server at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
