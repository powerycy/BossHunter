import tempfile
from pathlib import Path

from bosshunter.ai.scorer import _build_scoring_prompt
from bosshunter.collection.models import JobCandidate, classify_recruitment_type
from bosshunter.db import get_db, insert_job_if_new


def test_recruitment_type_classification_is_conservative():
    assert classify_recruitment_type("应届产品经理", "经验不限", "") == "campus"
    assert classify_recruitment_type("产品经理", "3-5年", "") == "experienced"
    assert classify_recruitment_type("产品经理", "", "公司成立于2024年") == "unknown"


def test_platform_job_record_keeps_education_and_recruitment_type():
    record = JobCandidate(
        platform="boss",
        source_job_id="education-1",
        title="校园招聘产品经理",
        company="示例公司",
        education="本科",
        jd="面向应届毕业生",
    ).as_job_record()

    assert record["education"] == "本科"
    assert record["recruitment_type"] == "campus"


def test_database_stores_structured_education_fields():
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            inserted = insert_job_if_new(db, {
                "id": "education-db-1",
                "title": "数据分析师",
                "company": "示例公司",
                "education": "硕士",
                "recruitment_type": "experienced",
                "source_platform": "boss",
                "source_job_id": "education-db-1",
            })
            row = db.execute(
                "SELECT education, recruitment_type FROM jobs WHERE id = ?",
                ("education-db-1",),
            ).fetchone()
        finally:
            db.close()

    assert inserted is True
    assert dict(row) == {"education": "硕士", "recruitment_type": "experienced"}


def test_scoring_prompt_includes_candidate_and_job_recruitment_context():
    prompt = _build_scoring_prompt(
        {
            "title": "产品经理",
            "company": "示例公司",
            "salary": "20-30K",
            "experience": "3-5年",
            "education": "本科",
            "recruitment_type": "experienced",
            "jd": "负责产品规划。",
        },
        "候选人简历",
        {"profile": {"education": "硕士", "recruitment_type": "both"}},
    )

    assert "最高学历：硕士" in prompt
    assert "求职招聘类型：校招/社招均可" in prompt
    assert "学历要求：本科" in prompt
    assert "招聘类型：社招" in prompt
