from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from appstore.mcp_server import (
    _capture_details_from_report,
    _job_result_path,
    _job_state_path,
    _read_json_file,
    _report_summary,
    _run_upload_batch_command,
    _utc_now_iso,
    _workflow_hint_from_report,
    _write_json_file,
)


def _load_request(path: Path) -> dict[str, Any]:
    payload = _read_json_file(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid job request: {path}")
    return payload


def _mark_state(job_id: str, **updates: Any) -> None:
    state_path = _job_state_path(job_id)
    state = _read_json_file(state_path) if state_path.exists() else {"job_id": job_id}
    state.update(updates)
    _write_json_file(state_path, state)


def _execute_job(request: dict[str, Any]) -> dict[str, Any]:
    command = str(request.get("command", "")).strip()
    positional = [str(item) for item in request.get("positional", [])]
    output_dir = Path(str(request.get("output_dir", "")).strip()).expanduser().resolve()
    options = request.get("options")
    if not isinstance(options, dict):
        raise RuntimeError("job request missing options")
    report_name = str(request.get("report_name", "")).strip()
    if not report_name:
        raise RuntimeError("job request missing report_name")

    result = _run_upload_batch_command(
        command=command,
        positional=positional,
        output_dir=output_dir,
        options=options,
        report_name=report_name,
    )
    postprocess = str(request.get("postprocess", "")).strip()
    if postprocess == "capture":
        result["capture_details"] = _capture_details_from_report(result.get("report"))
    elif postprocess == "auto-upload":
        result["workflow_hint"] = _workflow_hint_from_report(result.get("report"))
        capture_report_path = output_dir / "capture" / "capture-report.json"
        capture_report: Any = None
        if capture_report_path.exists():
            capture_report = json.loads(capture_report_path.read_text(encoding="utf-8"))
        result["capture_report_path"] = str(capture_report_path) if capture_report_path.exists() else ""
        result["capture_report"] = capture_report
        result["capture_details"] = _capture_details_from_report(capture_report)
    return result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: python -m appstore.mcp_job_runner <request.json>")

    request_path = Path(args[0]).expanduser().resolve()
    request = _load_request(request_path)
    job_id = str(request.get("job_id", "")).strip()
    job_type = str(request.get("job_type", "")).strip()
    if not job_id or not job_type:
        raise SystemExit("invalid job request metadata")

    _mark_state(
        job_id,
        job_type=job_type,
        status="running",
        message="job running",
        started_at=_utc_now_iso(),
    )
    try:
        result = _execute_job(request)
        _write_json_file(_job_result_path(job_id), result)
        _mark_state(
            job_id,
            job_type=job_type,
            status="completed",
            message="job completed",
            finished_at=_utc_now_iso(),
            status_counts=_report_summary(result.get("report", [])),
        )
        return 0
    except Exception as exc:
        _mark_state(
            job_id,
            job_type=job_type,
            status="failed",
            message=str(exc),
            finished_at=_utc_now_iso(),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
