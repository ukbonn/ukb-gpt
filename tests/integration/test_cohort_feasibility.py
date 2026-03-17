import pytest
import requests
import time

pytestmark = [
    pytest.mark.integration,
    pytest.mark.batch_client,
]


def _request(stack, method: str, path: str, **kwargs):
    headers = kwargs.pop("headers", {})
    headers = {"Host": stack.server_name, **headers}
    last_error = None
    for _ in range(15):
        try:
            response = requests.request(
                method,
                f"http://127.0.0.1:{stack.batch_port}{path}",
                headers=headers,
                timeout=10,
                **kwargs,
            )
            if response.status_code in {502, 503, 504}:
                time.sleep(1)
                continue
            return response
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1)
    if last_error is not None:
        raise last_error
    raise AssertionError("request retries exhausted without a response")


def test_cohort_feasibility_preview_common_cohort(batch_client_feasibility_stack):
    response = _request(
        batch_client_feasibility_stack,
        "POST",
        "/feasibility/api/cohort-preview",
        json={
            "dataset_id": _request(
                batch_client_feasibility_stack,
                "GET",
                "/feasibility/api/datasets",
            ).json()["datasets"][0]["id"],
            "focus_table": "findings",
            "include_mode": "all",
            "include_filters": [
                {"table": "findings", "field": "label", "values": ["pneumonia"]},
                {"table": "materials", "field": "label", "values": ["central_venous_catheter"]},
            ],
            "exclude_filters": [],
            "report_date_from": "",
            "report_date_to": "",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["suppressed"] is False
    assert payload["suppression_reason"] is None
    assert payload["summary"]["cohort_patients"]["display"] == "20"
    assert payload["charts"]


def test_cohort_feasibility_preview_hides_small_cohort(batch_client_feasibility_stack):
    dataset_id = _request(
        batch_client_feasibility_stack,
        "GET",
        "/feasibility/api/datasets",
    ).json()["datasets"][0]["id"]

    response = _request(
        batch_client_feasibility_stack,
        "POST",
        "/feasibility/api/cohort-preview",
        json={
            "dataset_id": dataset_id,
            "focus_table": "findings",
            "include_mode": "all",
            "include_filters": [
                {"table": "findings", "field": "label", "values": ["sarcoidosis"]},
            ],
            "exclude_filters": [],
            "report_date_from": "",
            "report_date_to": "",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["suppressed"] is True
    assert payload["suppression_reason"] == "threshold"
    assert payload["summary"]["cohort_patients"]["display"] == "n<10"
    assert "P021" not in response.text


def test_cohort_feasibility_preview_hides_small_refinement(batch_client_feasibility_stack):
    dataset_id = _request(
        batch_client_feasibility_stack,
        "GET",
        "/feasibility/api/datasets",
    ).json()["datasets"][0]["id"]

    response = _request(
        batch_client_feasibility_stack,
        "POST",
        "/feasibility/api/cohort-preview",
        json={
            "dataset_id": dataset_id,
            "focus_table": "findings",
            "include_mode": "all",
            "include_filters": [
                {"table": "findings", "field": "label", "values": ["pneumonia"]},
                {"table": "findings", "field": "additional_attributes", "values": ["patchy"]},
            ],
            "exclude_filters": [],
            "report_date_from": "",
            "report_date_to": "",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["suppressed"] is True
    assert payload["suppression_reason"] == "differencing_protection"
    assert payload["summary"]["cohort_patients"]["display"] == "protected"
    assert payload["charts"] == []
