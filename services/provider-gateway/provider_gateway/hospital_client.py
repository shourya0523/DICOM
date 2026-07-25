"""Sole module allowed to communicate with the hospital node."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import ValidationError

from provider_gateway.models import StudyRecord

logger = logging.getLogger("provider_gateway.hospital_client")


class HospitalNodeError(Exception):
    def __init__(self, message: str, *, unavailable: bool = False):
        super().__init__(message)
        self.unavailable = unavailable


class HospitalClient:
    def __init__(
        self,
        node_url: str,
        timeout_seconds: float = 30.0,
        service_account: dict[str, Any] | None = None,
    ):
        self.node_url = node_url.rstrip("/")
        self.timeout = timeout_seconds
        self.service_account = service_account
        self._token: str | None = None

    def _login(self, client: httpx.Client) -> str | None:
        """Exchange the node service account for a Bearer token, if the node has SSO."""
        if not self.service_account:
            return None
        response = client.post(f"{self.node_url}/auth/login", json=self.service_account)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise HospitalNodeError(
                f"Hospital node rejected the gateway service account (HTTP {response.status_code})"
            )
        self._token = response.json().get("access_token")
        return self._token

    def _get(self, client: httpx.Client, path: str) -> httpx.Response:
        """GET path, authenticating only if the node demands it."""
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        response = client.get(f"{self.node_url}{path}", headers=headers)
        if response.status_code in (401, 403):
            if self._login(client):
                response = client.get(
                    f"{self.node_url}{path}",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        return response

    def health(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.node_url}/health")
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HospitalNodeError("Hospital node unavailable", unavailable=True) from exc

    def fetch_studies(self) -> list[StudyRecord]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = self._get(client, "/api/studies")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise HospitalNodeError("Hospital node unavailable", unavailable=True) from exc

        if not isinstance(payload, list):
            raise HospitalNodeError("Malformed studies payload")

        studies: list[StudyRecord] = []
        for item in payload:
            try:
                studies.append(StudyRecord.model_validate(item))
            except ValidationError:
                logger.warning("GATEWAY_SKIP_MALFORMED_STUDY")
                continue
        return studies

    def fetch_study(self, study_id: str) -> StudyRecord:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = self._get(client, f"/api/studies/{study_id}")
                if response.status_code == 404:
                    raise HospitalNodeError(f"Study not found")
                response.raise_for_status()
                return StudyRecord.model_validate(response.json())
        except httpx.HTTPError as exc:
            raise HospitalNodeError("Hospital node unavailable", unavailable=True) from exc
