import sqlite3
from pathlib import Path


def create_sample_feasibility_dataset_root(base_dir: Path) -> Path:
    dataset_root = base_dir / "datasets"
    output_dir = (
        dataset_root
        / "demo_cohort"
        / "results_cache"
        / "results_cache"
        / "C_unify_labels_and_create_db_outputs"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "cohort_query_no_citations.db"

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE findings (
                studyAnonId TEXT,
                label TEXT,
                organ TEXT,
                diagnosis TEXT,
                change TEXT,
                extent TEXT,
                spatial_informations TEXT,
                additional_attributes TEXT,
                staging TEXT,
                report_date TEXT
            );
            CREATE TABLE materials (
                studyAnonId TEXT,
                label TEXT,
                positional_assessment TEXT,
                structural_integrity TEXT,
                change_assessment TEXT,
                spatial_informations TEXT,
                additional_attributes TEXT,
                report_date TEXT
            );
            CREATE TABLE measurements (
                studyAnonId TEXT,
                label TEXT,
                value TEXT,
                type TEXT,
                spatial_informations TEXT,
                additional_attributes TEXT,
                report_date TEXT
            );
            CREATE TABLE procedures (
                studyAnonId TEXT,
                label TEXT,
                status TEXT,
                spatial_informations TEXT,
                additional_attributes TEXT,
                report_date TEXT
            );
            """
        )

        common_ids = [f"P{i:03d}" for i in range(1, 21)]
        rare_ids = [f"P{i:03d}" for i in range(21, 25)]

        findings_rows = []
        materials_rows = []
        measurements_rows = []
        procedures_rows = []

        for index, study_id in enumerate(common_ids):
            report_date = "2024-01-15" if index < 10 else "2025-02-20"
            finding_attributes = "patchy" if index < 16 else "confluent"
            findings_rows.append(
                (
                    study_id,
                    "pneumonia",
                    "lung",
                    "confirmed",
                    "new onset",
                    "moderate",
                    "right, lower_lobe",
                    finding_attributes,
                    "",
                    report_date,
                )
            )
            materials_rows.append(
                (
                    study_id,
                    "central_venous_catheter",
                    "correct position",
                    "intact",
                    "stable_position",
                    "right, jugular_vein",
                    "triple_lumen",
                    report_date,
                )
            )
            measurements_rows.append(
                (
                    study_id,
                    "ascending_aorta",
                    "33 mm",
                    "diameter",
                    "ascending_aorta",
                    "sclerotic",
                    report_date,
                )
            )
            procedures_rows.append(
                (
                    study_id,
                    "cholecystectomy",
                    "status_post",
                    "",
                    "laparoscopic",
                    report_date,
                )
            )

        for study_id in rare_ids:
            report_date = "2025-03-11"
            findings_rows.append(
                (
                    study_id,
                    "sarcoidosis",
                    "lung",
                    "suspected",
                    "stable",
                    "mild",
                    "left, upper_lobe",
                    "nodular",
                    "stage_2",
                    report_date,
                )
            )
            materials_rows.append(
                (
                    study_id,
                    "rare_implant",
                    "suboptimal_position",
                    "intact",
                    "stable_position",
                    "left",
                    "",
                    report_date,
                )
            )

        conn.executemany("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", findings_rows)
        conn.executemany("INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?)", materials_rows)
        conn.executemany("INSERT INTO measurements VALUES (?, ?, ?, ?, ?, ?, ?)", measurements_rows)
        conn.executemany("INSERT INTO procedures VALUES (?, ?, ?, ?, ?, ?)", procedures_rows)
        conn.commit()
    finally:
        conn.close()

    return dataset_root
