#!/usr/bin/env python3
"""Local web server for the Finland Fleet Dispatcher website.

Serves static assets from `website/` and exposes `/api/custom-args` (`GET` / `POST`)
to read and write `/custom_args.py` safely for `python3 dispatch.py --custom`.

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
import tempfile
from typing import Any
from urllib.parse import urlparse

WEBSITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEBSITE_DIR.parent
CUSTOM_ARGS_FILE = REPO_ROOT / "custom_args.py"

LEVELS = tuple(range(1, 9))

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
                # Take level 8 (or first provided level) as the uniform scalar
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
    # Clean float representation (preserve at least 1 decimal place, e.g. 1.5, 1.15, 0.9)
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
    """Serves `website/` static files and `/api/custom-args`."""

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
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        route = urlparse(self.path).path.rstrip("/")
        if route == "/api/custom-args":
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        route = urlparse(self.path).path.rstrip("/")
        if route == "/api/custom-args":
            try:
                self._send_json(HTTPStatus.OK, read_custom_args())
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path.rstrip("/")
        if route != "/api/custom-args":
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
            result = write_custom_args(payload)
            self._send_json(HTTPStatus.OK, result)
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body."})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Finland Fleet Dispatcher website + custom_args API.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("PORT", "8000")), help="Port to listen on (default: 8000)")
    args = parser.parse_args()

    ThreadingHTTPServer.allow_reuse_address = True
    with ThreadingHTTPServer((args.host, args.port), WebsiteRequestHandler) as httpd:
        print(f"Serving website at http://{args.host}:{args.port} (writing {CUSTOM_ARGS_FILE})", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.", flush=True)


if __name__ == "__main__":
    main()
