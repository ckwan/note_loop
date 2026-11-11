"""Client for the Claims Feedback Loop API.

Privacy: the payload is built from ids and a fixed description only. Nothing from a
note, a session, or a patient's name ever reaches this module.
"""

import hashlib

import httpx
from temporalio.exceptions import ApplicationError

from app.config import Settings



def opaque_patient_ref(org_id: str, patient_id: int) -> str:
    digest = hashlib.sha256(f"{org_id}:{patient_id}".encode()).hexdigest()
    return f"pt_{digest[:16]}"


def external_ref_for(note_id: int, attempt: int) -> str:
    return str(note_id) if attempt == 0 else f"{note_id}-{attempt}"


def claim_amount(duration_minutes: int, rate_per_hour: float) -> float:
    return round(rate_per_hour * duration_minutes / 60, 2)


def history_notes_for(duration_minutes: int, attempt: int) -> str:
    """Non clinical. No dates (a date of service is an identifier) and no note content."""
    text = f"Outpatient session claim, {duration_minutes} minutes"
    return text if attempt == 0 else f"{text}, resubmission {attempt}"


def build_payload(
    org_id: str, external_ref: str, patient_id: int, amount: float, history_notes: str
) -> dict:
    return {
        "org_id": org_id,
        "external_ref": external_ref,
        "claimant_name": opaque_patient_ref(org_id, patient_id),
        "amount": amount,
        "history_notes": history_notes,
    }


class ClaimsApiClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.org_id = settings.claims_org_id or ""
        self._http = httpx.Client(
            base_url=(settings.claims_api_url or "").rstrip("/"),
            headers={"X-Org-Id": self.org_id},
            timeout=settings.claims_http_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:  # includes timeouts
            # Retryable. Only the exception type is reported.
            raise ApplicationError(
                f"Claims API unreachable ({type(exc).__name__})"
            ) from None
        code = response.status_code
        if code >= 500:
            raise ApplicationError(f"Claims API error {code}")
        if code >= 400:
            # Retrying the same request cannot fix a 4xx. The body is not logged.
            raise ApplicationError(f"Claims API rejected the request ({code})", non_retryable=True)
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or not data.get("id") or not data.get("status"):
            raise ApplicationError("Claims API returned an unexpected response", non_retryable=True)
        return data

    def create_claim(self, payload: dict) -> dict:
        """201 (created) and 200 (already existed) both succeed."""
        return self._request("POST", "/claims", json=payload)

    def get_claim(self, remote_id: str) -> dict:
        return self._request("GET", f"/claims/{remote_id}")


def build_claims_client(settings: Settings) -> ClaimsApiClient:
    """Tests replace this to inject a mock transport."""
    return ClaimsApiClient(settings)
