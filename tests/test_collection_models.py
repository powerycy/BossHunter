import unittest

from bosshunter.collection.models import (
    CollectionProgress,
    JobCandidate,
    PlatformCollectionResult,
    classify_recruitment_type,
)


class ClassifyRecruitmentTypeTests(unittest.TestCase):
    def test_campus_markers(self):
        self.assertEqual(classify_recruitment_type(title="校园招聘"), "campus")
        self.assertEqual(classify_recruitment_type(title="应届毕业生"), "campus")
        self.assertEqual(classify_recruitment_type(jd="管培生计划"), "campus")

    def test_experienced_markers(self):
        self.assertEqual(classify_recruitment_type(title="社招"), "experienced")
        self.assertEqual(classify_recruitment_type(title="社会招聘"), "experienced")

    def test_experience_year_regex(self):
        self.assertEqual(classify_recruitment_type(title="3年以上经验"), "experienced")
        self.assertEqual(classify_recruitment_type(experience="3-5年"), "experienced")

    def test_unknown_when_no_signal(self):
        self.assertEqual(classify_recruitment_type(title="Python工程师"), "unknown")


class JobCandidateStorageIdTests(unittest.TestCase):
    def test_boss_uses_raw_source_id(self):
        candidate = JobCandidate("boss", "123", "工程师", "公司")

        self.assertEqual(candidate.storage_id, "123")

    def test_non_boss_uses_prefixed_id(self):
        candidate = JobCandidate("liepin", "456", "工程师", "公司")

        self.assertEqual(candidate.storage_id, "liepin:456")


class JobCandidateAsJobRecordTests(unittest.TestCase):
    def test_full_field_mapping(self):
        candidate = JobCandidate(
            platform="zhilian",
            source_job_id="789",
            title="后端工程师",
            company="科技公司",
            salary="20-30K",
            city="上海",
            experience="3-5年",
            education="本科",
            jd="岗位描述",
            url="https://example.com/job/789",
            source_keyword="Python",
        )
        record = candidate.as_job_record()

        self.assertEqual(record["id"], "zhilian:789")
        self.assertEqual(record["title"], "后端工程师")
        self.assertEqual(record["source_platform"], "zhilian")
        self.assertEqual(record["source_job_id"], "789")
        self.assertEqual(record["source_keyword"], "Python")
        self.assertIn("recruitment_type", record)

    def test_recruitment_type_classified_when_unknown(self):
        candidate = JobCandidate("boss", "1", "校园招聘", "公司")
        record = candidate.as_job_record()

        self.assertEqual(record["recruitment_type"], "campus")


class CollectionProgressTests(unittest.TestCase):
    def test_to_dict_returns_all_fields(self):
        progress = CollectionProgress(
            run_id="run-1",
            platform="boss",
            platform_index=0,
            platform_total=3,
            phase="loading_list",
            target=100,
        )
        result = progress.to_dict()

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["platform"], "boss")
        self.assertEqual(result["target"], 100)

    def test_percent_with_valid_target(self):
        progress = CollectionProgress(
            run_id="run-1",
            platform="boss",
            platform_index=0,
            platform_total=1,
            phase="collecting",
            target=50,
            new=25,
        )

        self.assertEqual(progress.percent, 50)

    def test_percent_none_when_target_is_none(self):
        progress = CollectionProgress(
            run_id="run-1",
            platform="boss",
            platform_index=0,
            platform_total=1,
            phase="collecting",
            target=None,
        )

        self.assertIsNone(progress.percent)

    def test_percent_none_when_target_zero(self):
        progress = CollectionProgress(
            run_id="run-1",
            platform="boss",
            platform_index=0,
            platform_total=1,
            phase="collecting",
            target=0,
        )

        self.assertIsNone(progress.percent)

    def test_percent_capped_at_100(self):
        progress = CollectionProgress(
            run_id="run-1",
            platform="boss",
            platform_index=0,
            platform_total=1,
            phase="collecting",
            target=10,
            new=20,
        )

        self.assertEqual(progress.percent, 100)


class PlatformCollectionResultTests(unittest.TestCase):
    def test_default_fields(self):
        result = PlatformCollectionResult("boss", "completed")

        self.assertEqual(result.platform, "boss")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "")
        self.assertEqual(result.new_job_ids, [])


if __name__ == "__main__":
    unittest.main()