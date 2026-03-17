import json

import pytest

from apps.cohort_feasibility.service import CohortFeasibilityService
from tests.helpers.cohort_feasibility_db import create_sample_feasibility_dataset_root

pytestmark = [pytest.mark.isolation, pytest.mark.batch_client]


@pytest.fixture()
def feasibility_service(tmp_path):
    dataset_root = create_sample_feasibility_dataset_root(tmp_path)
    return CohortFeasibilityService(dataset_root)


def test_facet_options_suppress_small_counts(feasibility_service):
    dataset_id = feasibility_service.list_datasets()[0]["id"]

    payload = feasibility_service.get_facet_options(dataset_id, "findings", "label")
    options = {item["value"]: item for item in payload["options"]}

    assert options["pneumonia"]["patient_count"]["display"] == "20"
    assert options["sarcoidosis"]["patient_count"]["display"] == "n<10"


def test_preview_returns_aggregate_only_and_hides_small_cohorts(feasibility_service):
    dataset_id = feasibility_service.list_datasets()[0]["id"]

    preview = feasibility_service.preview(
        dataset_id=dataset_id,
        focus_table="findings",
        include_mode="all",
        include_filters=[
            {"table": "findings", "field": "label", "values": ["pneumonia"]},
            {"table": "materials", "field": "label", "values": ["central_venous_catheter"]},
        ],
        exclude_filters=[],
        report_date_from="",
        report_date_to="",
    )

    assert preview["suppressed"] is False
    assert preview["suppression_reason"] is None
    assert preview["summary"]["cohort_patients"]["display"] == "20"
    assert preview["charts"]
    serialized = json.dumps(preview)
    assert "studyAnonId" not in serialized
    assert "P001" not in serialized

    rare_preview = feasibility_service.preview(
        dataset_id=dataset_id,
        focus_table="findings",
        include_mode="all",
        include_filters=[
            {"table": "findings", "field": "label", "values": ["sarcoidosis"]},
        ],
        exclude_filters=[],
        report_date_from="",
        report_date_to="",
    )

    assert rare_preview["suppressed"] is True
    assert rare_preview["suppression_reason"] == "threshold"
    assert rare_preview["summary"]["cohort_patients"]["display"] == "n<10"
    assert rare_preview["charts"] == []


def test_preview_hides_small_refinements_of_reportable_cohorts(feasibility_service):
    dataset_id = feasibility_service.list_datasets()[0]["id"]

    preview = feasibility_service.preview(
        dataset_id=dataset_id,
        focus_table="findings",
        include_mode="all",
        include_filters=[
            {"table": "findings", "field": "label", "values": ["pneumonia"]},
            {"table": "findings", "field": "additional_attributes", "values": ["patchy"]},
        ],
        exclude_filters=[],
        report_date_from="",
        report_date_to="",
    )

    assert preview["suppressed"] is True
    assert preview["suppression_reason"] == "differencing_protection"
    assert preview["summary"]["cohort_patients"]["display"] == "protected"
    assert preview["charts"] == []
    assert any("differencing risk" in note for note in preview["notes"])
