#!/usr/bin/env python3
"""Local web server for the Finland Fleet Dispatcher website.

Serves static assets from `website/` and exposes:
  - `GET  /api/custom-args`        read current constants from `custom_args.py`
  - `POST /api/custom-args`        validate & write constants to `custom_args.py`
  - `POST /api/simulate`           write `custom_args.py`, run `dispatch.py --level N --custom`,
                                   generate `reveal-<world_id>/` CSVs, and expose dataset
  - `GET  /api/simulate/status`    live status & log lines for a running simulation job
  - `GET  /api/simulate/result`    generated CSV files from `reveal-<world_id>/`

Usage:
    python3 website/server.py [--port 8000] [--host 127.0.0.1]
"""
from __future__ import annotations

import argparse
import ast
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
import uuid

WEBSITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEBSITE_DIR.parent
CUSTOM_ARGS_FILE = REPO_ROOT / "custom_args.py"
DISPATCH_FILE = REPO_ROOT / "dispatch.py"
DOTENV_FILE = REPO_ROOT / ".env"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

LEVELS = tuple(range(1, 9))
WORLD_ID_RE = re.compile(r"\b(?:Practice world|Ranking run)\s+([A-Za-z0-9_-]+)")
REVEAL_CSV_NAMES = (
    "places.csv",
    "roads.csv",
    "legs.csv",
    "gigs.csv",
    "drivers.csv",
    "requests.csv",
    "events.csv",
    "views.csv",
)

DEFAULT_VALUES: dict[str, Any] = {
    # Part A — Quoting & Order Selection
    "PRICE_MULTIPLIER": {1: 1.2, 2: 1.2, 3: 1.2, 4: 1.5, 5: 1.5, 6: 1.5, 7: 2.0, 8: 1.7},
    "SLACK_BUFFER": {1: 30, 2: 30, 3: 30, 4: 60, 5: 60, 6: 60, 7: 90, 8: 120},
    "MAX_QUOTE_MULTIPLIER": 1.50,
    "PRICE_WAR_MULTIPLIER": 1.15,
    "MAX_DEALER_TRUST_PCT": 25,
    "TARGET_ONTIME_SHARE": 0.90,
    # Part B — Dispatch & Driver Fees
    "OPENING_FEE": {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0, 7: 1.4, 8: 1.3},
    "MAX_FEE_MULTIPLE": {7: 2.0, 8: 2.2},
    "DEFAULT_MAX_FEE_MULTIPLE": 2.5,
    "DEADLINE_EARLY_MINUTES": 420,
    "QUICK_BOOK_MINUTES": 60,
    "STEEP_OPENING_MULTIPLE": 1.5,
    "LOW_BATTERY_PERCENT": 30,
}

# (kind, value_type, min_val, max_val)
SCHEMA: dict[str, tuple[str, str, float, float]] = {
    "PRICE_MULTIPLIER": ("per_level", "float", 0.5, 5.0),
    "SLACK_BUFFER": ("per_level", "int", 0, 720),
    "MAX_QUOTE_MULTIPLIER": ("scalar", "float", 1.0, 5.0),
    "PRICE_WAR_MULTIPLIER": ("scalar", "float", 0.8, 5.0),
    "MAX_DEALER_TRUST_PCT": ("scalar", "int", 0, 100),
    "TARGET_ONTIME_SHARE": ("scalar", "float", 0.50, 1.00),
    "OPENING_FEE": ("per_level", "float", 0.5, 5.0),
    "MAX_FEE_MULTIPLE": ("per_level_sparse", "float", 1.0, 6.0),
    "DEFAULT_MAX_FEE_MULTIPLE": ("scalar", "float", 1.0, 6.0),
    "DEADLINE_EARLY_MINUTES": ("scalar", "int", 0, 1440),
    "QUICK_BOOK_MINUTES": ("scalar", "int", 5, 720),
    "STEEP_OPENING_MULTIPLE": ("scalar", "float", 0.5, 5.0),
    "LOW_BATTERY_PERCENT": ("scalar", "int", 0, 100),
}

PART_A_KEYS = (
    "PRICE_MULTIPLIER",
    "SLACK_BUFFER",
    "MAX_QUOTE_MULTIPLIER",
    "PRICE_WAR_MULTIPLIER",
    "MAX_DEALER_TRUST_PCT",
    "TARGET_ONTIME_SHARE",
)

PART_B_KEYS = (
    "OPENING_FEE",
    "MAX_FEE_MULTIPLE",
    "DEFAULT_MAX_FEE_MULTIPLE",
    "DEADLINE_EARLY_MINUTES",
    "QUICK_BOOK_MINUTES",
    "STEEP_OPENING_MULTIPLE",
    "LOW_BATTERY_PERCENT",
)

_SIM_LOCK = threading.Lock()
_SIM_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_JOB_ID: str | None = None
_ACTIVE_PROC: subprocess.Popen[str] | None = None


def _resolve_python_bin() -> str:
    if VENV_PYTHON.is_file():
        return str(VENV_PYTHON)
    return sys.executable


def _build_subprocess_env() -> dict[str, str]:
    """Build subprocess environment, loading `.env` keys when not already set."""
    env = os.environ.copy()
    if DOTENV_FILE.is_file():
        for raw_line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if key and not env.get(key):
                env[key] = val
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _safe_eval_node(node: ast.AST) -> Any:
    """Safely evaluate numeric literals, dicts of numeric literals, and basic arithmetic."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _safe_eval_node(node.operand)
        return +operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        return left * right
    if isinstance(node, ast.Dict):
        out: dict[int, Any] = {}
        for k_node, v_node in zip(node.keys, node.values):
            if k_node is None:
                raise ValueError("Dict unpacking not supported")
            k = _safe_eval_node(k_node)
            v = _safe_eval_node(v_node)
            out[int(k)] = v
        return out
    return ast.literal_eval(node)


def _coerce_number(val: Any, value_type: str, min_val: float, max_val: float, name: str) -> int | float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"{name} must be a number.")
    fval = float(val)
    if not math.isfinite(fval):
        raise ValueError(f"{name} must be finite.")
    if fval < min_val - 1e-9 or fval > max_val + 1e-9:
        raise ValueError(f"{name} ({fval}) out of allowed range [{min_val}, {max_val}].")
    fval = min(max(fval, min_val), max_val)
    if value_type == "int":
        return int(round(fval))
    return round(fval, 4)


def read_custom_args(path: Path = CUSTOM_ARGS_FILE) -> dict[str, Any]:
    """Read current values from `custom_args.py`, falling back to `DEFAULT_VALUES`."""
    raw_loaded: dict[str, Any] = {}
    if path.is_file():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for stmt in tree.body:
            target_name: str | None = None
            val_node: ast.AST | None = None
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target_name = stmt.targets[0].id
                val_node = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                target_name = stmt.target.id
                val_node = stmt.value
            if target_name in SCHEMA and val_node is not None:
                try:
                    raw_loaded[target_name] = _safe_eval_node(val_node)
                except Exception:
                    continue

    values: dict[str, Any] = {}
    per_level_keys = [k for k, (kind, _, _, _) in SCHEMA.items() if kind in ("per_level", "per_level_sparse")]
    scalar_per_level_count = 0

    for name, (kind, value_type, min_val, max_val) in SCHEMA.items():
        default = DEFAULT_VALUES[name]
        raw = raw_loaded.get(name, default)
        if kind in ("per_level", "per_level_sparse"):
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                scalar_per_level_count += 1
                coerced = _coerce_number(raw, value_type, min_val, max_val, name)
                values[name] = {str(lvl): coerced for lvl in LEVELS}
            elif isinstance(raw, dict):
                merged_base = dict(default)
                for k, v in raw.items():
                    lvl = int(k)
                    if lvl in LEVELS:
                        merged_base[lvl] = _coerce_number(v, value_type, min_val, max_val, f"{name}[{lvl}]")
                values[name] = {str(k): v for k, v in sorted(merged_base.items())}
            else:
                values[name] = {str(k): v for k, v in default.items()}
        else:
            try:
                values[name] = _coerce_number(raw, value_type, min_val, max_val, name)
            except ValueError:
                values[name] = default

    scope = "uniform" if (raw_loaded and scalar_per_level_count == len(per_level_keys)) else "per_level"
    return {
        "ok": True,
        "scope": scope,
        "values": values,
        "defaults": {
            k: ({str(lk): lv for lk, lv in v.items()} if isinstance(v, dict) else v)
            for k, v in DEFAULT_VALUES.items()
        },
    }


def validate_and_normalize_payload(payload: Any) -> tuple[str, dict[str, Any]]:
    """Validate incoming JSON payload and return `(scope, normalized_python_values)`."""
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    scope = payload.get("scope", "per_level")
    if scope not in ("per_level", "uniform"):
        raise ValueError("scope must be 'per_level' or 'uniform'.")

    incoming = payload.get("values")
    if not isinstance(incoming, dict):
        raise ValueError("'values' must be a JSON object.")

    unknown = set(incoming.keys()) - set(SCHEMA.keys())
    if unknown:
        raise ValueError(f"Unknown constant(s): {', '.join(sorted(unknown))}")

    normalized: dict[str, Any] = {}
    for name, (kind, value_type, min_val, max_val) in SCHEMA.items():
        if name not in incoming:
            default = DEFAULT_VALUES[name]
            normalized[name] = dict(default) if isinstance(default, dict) else default
            continue

        raw = incoming[name]
        if kind in ("per_level", "per_level_sparse"):
            if scope == "uniform" and isinstance(raw, (int, float)) and not isinstance(raw, bool):
                normalized[name] = _coerce_number(raw, value_type, min_val, max_val, name)
            elif scope == "uniform" and isinstance(raw, dict):
                sample = raw.get("8", raw.get(8, next(iter(raw.values()), None)))
                if sample is None:
                    raise ValueError(f"{name} cannot be empty.")
                normalized[name] = _coerce_number(sample, value_type, min_val, max_val, name)
            elif isinstance(raw, dict):
                cleaned_dict: dict[int, int | float] = {}
                for k, v in raw.items():
                    try:
                        lvl = int(k)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"Invalid level key {k!r} in {name}.") from exc
                    if lvl not in LEVELS:
                        raise ValueError(f"Level {lvl} in {name} must be 1..8.")
                    cleaned_dict[lvl] = _coerce_number(v, value_type, min_val, max_val, f"{name}[{lvl}]")
                if kind == "per_level":
                    base = dict(DEFAULT_VALUES[name])
                    base.update(cleaned_dict)
                    normalized[name] = {lvl: base[lvl] for lvl in LEVELS}
                else:
                    if not cleaned_dict:
                        cleaned_dict = dict(DEFAULT_VALUES[name])
                    normalized[name] = dict(sorted(cleaned_dict.items()))
            elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
                scalar = _coerce_number(raw, value_type, min_val, max_val, name)
                normalized[name] = scalar if scope == "uniform" else {lvl: scalar for lvl in LEVELS}
            else:
                raise ValueError(f"{name} must be a number or level map.")
        else:
            normalized[name] = _coerce_number(raw, value_type, min_val, max_val, name)

    if scope == "uniform" and isinstance(normalized["MAX_FEE_MULTIPLE"], (int, float)):
        normalized["DEFAULT_MAX_FEE_MULTIPLE"] = normalized["MAX_FEE_MULTIPLE"]

    return scope, normalized


def _format_value(val: Any, value_type: str) -> str:
    if isinstance(val, dict):
        parts = [f"{int(k)}: {_format_value(v, value_type)}" for k, v in sorted(val.items())]
        return "{" + ", ".join(parts) + "}"
    if value_type == "int":
        return str(int(val))
    text = f"{float(val):.4f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


def render_custom_args_py(values: dict[str, Any]) -> str:
    """Format validated constants into a clean `custom_args.py` module."""
    lines = [
        '"""Custom starting constants for `dispatch.py --custom`.',
        "",
        "When `dispatch.py` is run WITHOUT `--custom`, this file is ignored completely.",
        "When `dispatch.py` is run WITH `--custom` (e.g. `python3 dispatch.py --level 8 --custom`),",
        "`apply_custom_constants()` loads any constants defined here.",
        "",
        "Note: Per-level constants (`PRICE_MULTIPLIER`, `SLACK_BUFFER`, `OPENING_FEE`, `MAX_FEE_MULTIPLE`)",
        "accept either:",
        "  - a per-level dict (e.g. `{8: 1.5}` or `{1: 1.0, ..., 8: 1.3}`), OR",
        "  - a single number from a UI slider (e.g. `1.5`), applied across all levels.",
        '"""',
        "",
        "# Part A — Quoting & Order Selection",
    ]
    for key in PART_A_KEYS:
        _, vtype, _, _ = SCHEMA[key]
        lines.append(f"{key} = {_format_value(values[key], vtype)}")

    lines.append("")
    lines.append("# Part B — Dispatch & Driver Fees")
    for key in PART_B_KEYS:
        _, vtype, _, _ = SCHEMA[key]
        lines.append(f"{key} = {_format_value(values[key], vtype)}")

    lines.append("")
    return "\n".join(lines)


def write_custom_args(payload: Any, path: Path = CUSTOM_ARGS_FILE) -> dict[str, Any]:
    """Validate `payload` and write `custom_args.py` atomically."""
    scope, normalized = validate_and_normalize_payload(payload)
    content = render_custom_args_py(normalized)

    fd, tmp_name = tempfile.mkstemp(prefix=".custom_args_", suffix=".py", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise

    state = read_custom_args(path)
    state["savedFile"] = path.name
    return state


def _append_job_log(job: dict[str, Any], line: str) -> None:
    cleaned = line.rstrip()
    if not cleaned:
        return
    with _SIM_LOCK:
        job["logs"].append(cleaned)
        if len(job["logs"]) > 80:
            job["logs"] = job["logs"][-80:]
        job["latestLine"] = cleaned
        m = WORLD_ID_RE.search(cleaned)
        if m and not job["worldId"]:
            job["worldId"] = m.group(1)
        if cleaned.startswith("End "):
            job["summaryLine"] = re.sub(r"\s+", " ", cleaned).strip()


def _read_reveal_csv_files(reveal_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for csv_name in REVEAL_CSV_NAMES:
        csv_path = reveal_dir / csv_name
        if csv_path.is_file():
            files[csv_name] = csv_path.read_text(encoding="utf-8")
    if not any(k in files for k in ("places.csv", "legs.csv", "events.csv")):
        raise RuntimeError(f"Generated folder {reveal_dir.name} is missing expected CSV files.")
    return files


def _run_simulation_job(job_id: str, level: int) -> None:
    """Background worker: runs `dispatch.py --level N --custom` then `spread.reveal_csv <world_id>`."""
    global _ACTIVE_JOB_ID, _ACTIVE_PROC
    job = _SIM_JOBS[job_id]
    python_bin = _resolve_python_bin()
    env = _build_subprocess_env()

    try:
        # Phase 1: Run dispatch.py --level <level> --custom
        dispatch_cmd = [python_bin, str(DISPATCH_FILE), "--level", str(level), "--custom"]
        _append_job_log(job, f"$ python3 dispatch.py --level {level} --custom")
        proc = subprocess.Popen(
            dispatch_cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with _SIM_LOCK:
            _ACTIVE_PROC = proc

        assert proc.stdout is not None
        for raw_line in proc.stdout:
            _append_job_log(job, raw_line)
        rc = proc.wait()
        with _SIM_LOCK:
            _ACTIVE_PROC = None

        if rc != 0:
            last_msg = job["latestLine"] or f"dispatch.py exited with status {rc}"
            raise RuntimeError(last_msg)

        world_id = job.get("worldId")
        if not world_id:
            raise RuntimeError("Simulation completed, but no world ID was reported by dispatch.py.")

        # Phase 2: Generate reveal-<world_id>/ CSV folder
        reveal_dir = REPO_ROOT / f"reveal-{world_id}"
        with _SIM_LOCK:
            job["phase"] = "generating_csv"
            job["revealFolder"] = reveal_dir.name

        reveal_cmd = [python_bin, "-m", "spread.reveal_csv", world_id, "--out", str(reveal_dir)]
        _append_job_log(job, f"$ spread-reveal-csv {world_id}")
        reveal_proc = subprocess.Popen(
            reveal_cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with _SIM_LOCK:
            _ACTIVE_PROC = reveal_proc

        assert reveal_proc.stdout is not None
        for raw_line in reveal_proc.stdout:
            _append_job_log(job, raw_line)
        reveal_rc = reveal_proc.wait()
        with _SIM_LOCK:
            _ACTIVE_PROC = None

        if reveal_rc != 0:
            last_msg = job["latestLine"] or f"spread-reveal-csv exited with status {reveal_rc}"
            raise RuntimeError(last_msg)

        # Verify CSV files exist on disk
        _read_reveal_csv_files(reveal_dir)

        with _SIM_LOCK:
            job["phase"] = "done"
            job["status"] = "done"
            job["finishedAt"] = time.monotonic()

    except Exception as exc:
        with _SIM_LOCK:
            _ACTIVE_PROC = None
            job["phase"] = "error"
            job["status"] = "error"
            job["error"] = str(exc)
            job["finishedAt"] = time.monotonic()
    finally:
        with _SIM_LOCK:
            if _ACTIVE_JOB_ID == job_id:
                _ACTIVE_JOB_ID = None
        job["doneEvent"].set()


def start_simulation(payload: Any) -> tuple[int, dict[str, Any]]:
    """Write `custom_args.py` and start (or optionally await) a `dispatch.py --custom` simulation."""
    global _ACTIVE_JOB_ID
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    raw_level = payload.get("level", 8)
    if isinstance(raw_level, bool) or not isinstance(raw_level, int) or raw_level not in LEVELS:
        try:
            raw_level = int(raw_level)
        except (TypeError, ValueError) as exc:
            raise ValueError("Simulation level must be an integer between 1 and 8.") from exc
        if raw_level not in LEVELS:
            raise ValueError("Simulation level must be between 1 and 8.")
    level = int(raw_level)
    wait_for_completion = bool(payload.get("wait", False))

    with _SIM_LOCK:
        if _ACTIVE_JOB_ID is not None:
            active = _SIM_JOBS.get(_ACTIVE_JOB_ID)
            if active and active["status"] == "running":
                return HTTPStatus.CONFLICT, {
                    "ok": False,
                    "error": "A simulation is already running. Please wait for it to finish.",
                    "jobId": _ACTIVE_JOB_ID,
                }

        # Write custom_args.py atomically before spawning dispatch.py --custom
        saved_state = write_custom_args(payload)

        job_id = uuid.uuid4().hex[:12]
        job: dict[str, Any] = {
            "jobId": job_id,
            "status": "running",
            "phase": "running_dispatch",
            "level": level,
            "worldId": None,
            "revealFolder": None,
            "latestLine": "Starting simulation...",
            "summaryLine": None,
            "logs": [],
            "error": None,
            "startedAt": time.monotonic(),
            "finishedAt": None,
            "doneEvent": threading.Event(),
            "savedState": saved_state,
        }
        _SIM_JOBS[job_id] = job
        _ACTIVE_JOB_ID = job_id

    worker = threading.Thread(target=_run_simulation_job, args=(job_id, level), daemon=True)
    worker.start()

    if wait_for_completion:
        job["doneEvent"].wait()
        if job["status"] == "error":
            return HTTPStatus.INTERNAL_SERVER_ERROR, get_simulation_status(job_id)
        return HTTPStatus.OK, get_simulation_result(job_id)

    return HTTPStatus.OK, get_simulation_status(job_id)


def get_simulation_status(job_id: str) -> dict[str, Any]:
    with _SIM_LOCK:
        job = _SIM_JOBS.get(job_id)
        if not job:
            raise KeyError(f"Unknown simulation jobId: {job_id}")
        end_t = job["finishedAt"] or time.monotonic()
        elapsed = round(max(0.0, end_t - job["startedAt"]), 1)
        return {
            "ok": job["status"] != "error",
            "jobId": job["jobId"],
            "status": job["status"],
            "phase": job["phase"],
            "level": job["level"],
            "worldId": job["worldId"],
            "folder": job["revealFolder"],
            "latestLine": job["latestLine"],
            "summaryLine": job["summaryLine"],
            "logs": list(job["logs"]),
            "error": job["error"],
            "elapsedSeconds": elapsed,
            "customArgs": job["savedState"],
        }


def get_simulation_result(job_id: str) -> dict[str, Any]:
    status_info = get_simulation_status(job_id)
    if status_info["status"] == "running":
        raise ValueError("Simulation is still running.")
    if status_info["status"] == "error":
        raise RuntimeError(status_info["error"] or "Simulation failed.")
    folder_name = status_info["folder"]
    if not folder_name:
        raise RuntimeError("No reveal CSV folder recorded for simulation.")
    reveal_dir = REPO_ROOT / folder_name
    files = _read_reveal_csv_files(reveal_dir)
    return {
        **status_info,
        "ok": True,
        "files": files,
    }


def _cancel_active_simulation() -> None:
    with _SIM_LOCK:
        proc = _ACTIVE_PROC
    if proc is not None and proc.poll() is None:
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _allowed_cors_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    if origin == "null":
        return "null"
    parsed = urlparse(origin)
    if parsed.scheme in ("http", "https") and parsed.hostname in ("localhost", "127.0.0.1"):
        return origin
    return None


class WebsiteRequestHandler(SimpleHTTPRequestHandler):
    """Serves `website/` static files, `/api/custom-args`, and `/api/simulate*`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEBSITE_DIR), **kwargs)

    def _send_cors_headers(self) -> None:
        origin = _allowed_cors_origin(self.headers.get("Origin"))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        route = urlparse(self.path).path.rstrip("/")
        if route in ("/api/custom-args", "/api/simulate", "/api/simulate/status", "/api/simulate/result"):
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        if route == "/api/custom-args":
            try:
                self._send_json(HTTPStatus.OK, read_custom_args())
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return

        if route == "/api/simulate/status":
            job_id = (query.get("jobId") or [""])[0].strip()
            if not job_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing jobId parameter."})
                return
            try:
                self._send_json(HTTPStatus.OK, get_simulation_status(job_id))
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            return

        if route == "/api/simulate/result":
            job_id = (query.get("jobId") or [""])[0].strip()
            if not job_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing jobId parameter."})
                return
            try:
                self._send_json(HTTPStatus.OK, get_simulation_result(job_id))
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            except ValueError as exc:
                self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return

        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path.rstrip("/")
        if route not in ("/api/custom-args", "/api/simulate"):
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown API endpoint."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid Content-Length."})
            return

        if length <= 0 or length > 65536:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Request body size invalid."})
            return

        try:
            raw_body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw_body)
            if route == "/api/custom-args":
                result = write_custom_args(payload)
                self._send_json(HTTPStatus.OK, result)
            else:
                http_status, result = start_simulation(payload)
                self._send_json(http_status, result)
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body."})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Finland Fleet Dispatcher website + custom simulation API.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("PORT", "8000")), help="Port to listen on (default: 8000)")
    args = parser.parse_args()

    ThreadingHTTPServer.allow_reuse_address = True
    with ThreadingHTTPServer((args.host, args.port), WebsiteRequestHandler) as httpd:
        print(f"Serving website at http://{args.host}:{args.port} (using {_resolve_python_bin()})", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping active simulations and shutting down server...", flush=True)
        finally:
            _cancel_active_simulation()


if __name__ == "__main__":
    main()
