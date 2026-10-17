"""Runtime configuration and engineering limits.

Everything here is deterministic and independent of the language model.
"""

from __future__ import annotations

import os
from pathlib import Path

# Hard limits for every numeric parameter: (minimum, maximum), inclusive.
# Chosen to keep the MVP to sensible bench-top brackets and to keep CAD
# generation fast.
LIMITS: dict[str, tuple[float, float]] = {
    "base_length_mm": (10.0, 500.0),
    "base_width_mm": (10.0, 500.0),
    "vertical_height_mm": (10.0, 500.0),
    "thickness_mm": (1.0, 25.0),
    "hole_diameter_mm": (1.0, 50.0),
    "holes_per_plate": (0, 4),
    "edge_margin_mm": (0.5, 100.0),
    "fillet_radius_mm": (0.0, 25.0),
}

# Human-readable names used in validation messages.
LABELS: dict[str, str] = {
    "base_length_mm": "base length",
    "base_width_mm": "base width",
    "vertical_height_mm": "vertical height",
    "thickness_mm": "thickness",
    "hole_diameter_mm": "hole diameter",
    "holes_per_plate": "holes per plate",
    "edge_margin_mm": "edge margin",
    "fillet_radius_mm": "bend fillet radius",
    "material": "material",
}

# Approximate densities in g/cm^3, used only for a labelled mass estimate.
DENSITY_G_CM3: dict[str, float] = {
    "aluminum_6061": 2.70,
    "steel_mild": 7.85,
    "stainless_304": 8.00,
    "pla": 1.24,
}

# Recommended minimum distance from a hole centre to a plate edge, as a
# multiple of hole diameter. Below this FlowCAD warns (it does not reject).
RECOMMENDED_EDGE_DISTANCE_FACTOR = 1.5

# Mesh tolerance for STL export, in millimetres.
STL_TOLERANCE_MM = 0.05
STL_ANGULAR_TOLERANCE_RAD = 0.1

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def output_dir() -> Path:
    """Directory where generated CAD files are written."""
    raw = os.environ.get("FLOWCAD_OUTPUT_DIR", str(_BACKEND_DIR.parent / "output"))
    path = Path(raw)
    if not path.is_absolute():
        path = (_BACKEND_DIR / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from the repo-root .env without overriding real
    environment variables. Kept dependency-free on purpose."""
    env_file = _BACKEND_DIR.parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class AISettings:
    """Gemma 4 via DigitalOcean Serverless Inference (OpenAI-compatible).

    Read at call time so tests and operators can change the environment
    without restarting the process.
    """

    @property
    def api_key(self) -> str:
        return os.environ.get("DO_INFERENCE_API_KEY", "").strip()

    @property
    def base_url(self) -> str:
        return os.environ.get("DO_INFERENCE_BASE_URL", "https://inference.do-ai.run").rstrip("/")

    @property
    def model(self) -> str:
        return os.environ.get("DO_INFERENCE_MODEL", "gemma-4-31B-it").strip()

    @property
    def timeout_s(self) -> float:
        return _float_env("DO_INFERENCE_TIMEOUT_S", 20.0)

    @property
    def json_mode(self) -> bool:
        # DigitalOcean does not document response_format; off by default.
        return os.environ.get("DO_INFERENCE_JSON_MODE", "false").lower() in ("1", "true", "yes")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


AI = AISettings()


def cors_origins() -> list[str]:
    raw = os.environ.get("FLOWCAD_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


def cad_isolation() -> str:
    """'process' runs CAD in a worker process with a timeout; 'inline' runs
    it in the API process (used by tests)."""
    return os.environ.get("FLOWCAD_CAD_ISOLATION", "process").lower()


def cad_timeout_s() -> float:
    return _float_env("FLOWCAD_CAD_TIMEOUT_S", 60.0)
