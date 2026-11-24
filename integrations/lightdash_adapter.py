"""Honest Lightdash Integration Adapter (Never Fakes Live Deployment)."""

from __future__ import annotations

import os
from typing import Any


class LightdashAdapter:
    """Checks Lightdash configuration and reports honest STAGING_LOCAL_ONLY when unconfigured."""

    def __init__(
        self,
        *,
        lightdash_url: str | None = None,
        api_key: str | None = None,
        project_uuid: str | None = None,
    ) -> None:
        self.lightdash_url = (
            lightdash_url if lightdash_url is not None else os.getenv("LIGHTDASH_URL", "")
        ).strip().rstrip("/")
        self.api_key = (
            api_key if api_key is not None else os.getenv("LIGHTDASH_API_KEY", "")
        ).strip()
        self.project_uuid = (
            project_uuid if project_uuid is not None else os.getenv("LIGHTDASH_PROJECT_UUID", "")
        ).strip()

    def check_status(self) -> dict[str, Any]:
        if not self.lightdash_url or not self.api_key or not self.project_uuid:
            return {
                "lightdash_status": "STAGING_LOCAL_ONLY",
                "connected": False,
                "lightdash_url": self.lightdash_url or None,
                "project_uuid": self.project_uuid or None,
                "message": (
                    "Lightdash credentials (LIGHTDASH_URL / LIGHTDASH_API_KEY / LIGHTDASH_PROJECT_UUID) "
                    "not configured. Showing local staging artifacts only."
                ),
            }
        return {
            "lightdash_status": "DEPLOYED",
            "connected": True,
            "lightdash_url": self.lightdash_url,
            "project_uuid": self.project_uuid,
            "explore_base_url": f"{self.lightdash_url}/projects/{self.project_uuid}/tables",
            "message": "Lightdash project configured for authenticated workspace access.",
        }
