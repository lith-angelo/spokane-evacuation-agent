"""Runtime configuration, read once from the environment.

Everything the demo might need to swap at short notice lives here: the
inference endpoint, the sandbox name, the live/replay toggle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    inference_base_url: str = os.getenv("EVAC_INFERENCE_BASE_URL", "http://127.0.0.1:8000/v1")
    inference_model: str = os.getenv("EVAC_INFERENCE_MODEL", "nvidia/Qwen3.6-35B-A3B-NVFP4")
    inference_api_key: str = os.getenv("EVAC_INFERENCE_API_KEY", "not-needed")

    sandbox: str = os.getenv("EVAC_SANDBOX", "my-assistant")
    openshell_bin: str = os.getenv(
        "EVAC_OPENSHELL_BIN", str(Path.home() / ".local/bin/openshell")
    )
    nemoclaw_bin: str = os.getenv("EVAC_NEMOCLAW_BIN", str(Path.home() / ".local/bin/nemoclaw"))
    egress_concurrency: int = int(os.getenv("EVAC_EGRESS_CONCURRENCY", "8"))

    # A policy denial must never fall back to host-direct. This flag only covers
    # the case where the sandbox itself is unreachable.
    allow_host_direct_fallback: bool = _bool("ALLOW_HOST_DIRECT_FALLBACK", False)

    data_mode: str = os.getenv("EVAC_DATA_MODE", "replay")  # live | replay
    db_path: Path = Path(os.getenv("EVAC_DB_PATH", str(REPO_ROOT / "data/sessions.sqlite3")))

    user_agent: str = "spokane-evac-agent/0.1 (Spark Hack Seattle prototype)"

    @property
    def replay(self) -> bool:
        return self.data_mode.strip().lower() == "replay"


settings = Settings()

# Hosts the agent is permitted to reach. This mirrors policies/spokane-evac.yaml.
# Inside the sandbox OpenShell enforces it for real; this copy is only used to
# apply the same restriction in the degraded host-direct mode, where nothing
# else would.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "services3.arcgis.com",
        "data.wsdot.wa.gov",
        "gismo.spokanecounty.org",
        "nominatim.openstreetmap.org",
        "router.project-osrm.org",
    }
)

# AGOL org ids reached on services3.arcgis.com. NIFC publishes WFIGS; SREC is the
# Tier-1 local authority for Spokane County evacuation levels. Both org paths are
# granted separately in the policy — see docs/SOURCES.md.
AGOL_ORG_NIFC = "T4QMspbfLg3qTGWY"
AGOL_ORG_SREC = "9UdSzuxhN4jGcI9p"
