from __future__ import annotations

import time
from datetime import datetime
from typing import Dict

from .version import __version__

GOTRUE_URL = "http://localhost:9999"
DEFAULT_HEADERS: Dict[str, str] = {
    "X-Client-Info": f"gotrue-py/{__version__}",
}
EXPIRY_MARGIN = 10  # seconds
MAX_RETRIES = 10
RETRY_INTERVAL = 2  # deciseconds
STORAGE_KEY = "supabase.auth.token"

API_VERSION_HEADER_NAME = "X-Supabase-Api-Version"
API_VERSIONS = {
    "2024-01-01": {
        "timestamp": time.mktime(
            datetime.strptime("2024-01-01", "%Y-%m-%d").timetuple()
        ),
        "name": "2024-01-01",
    },
}
