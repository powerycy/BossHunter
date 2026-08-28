"""Data contracts shared by all job collection platforms."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Literal

from bosshunter.collection.text import clean_job_description


PlatformId = Literal["boss", "zhilian", "51job"]


def classify_recruitment_type(title: str = "", experience: str = "", jd: str = "") -> str:
    """Classify explicit campus/social recruitment signals conservatively."""
    text = " ".join(str(value or "") for value in (title, experience, jd))
    if any(marker in text for marker in ("校招", "校园招聘", "应届", "毕业生", "管培生", "实习生")):
        return "campus"
    if any(marker in text for marker in ("社招", "社会招聘")):
        return "experienced"
    if re.search(r"\d+\s*(?:[-–~至]\s*\d+\s*)?年(?:以上|及以上)?(?:工作)?经验", text):
        return "experienced"
    if re.fullmatch(r"\s*\d+\s*(?:[-–~至]\s*\d+\s*)?年(?:以上|及以上)?\s*", str(experience or "")):
        return "experienced"
    return "unknown"


@dataclass(frozen=True)
class PlatformCollectionRequest:
    platform: PlatformId
    keywords: list[str]
    cities: list[str]
    city_codes: dict[str, str]
    max_pages: int = 3
    sort: str = "default"
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobCandidate:
    """A platform-neutral job candidate emitted by a collector."""

    platform: PlatformId
    source_job_id: str
    title: str
    company: str
    salary: str = ""
    city: str = ""
    city_code: str = ""
    experience: str = ""
    education: str = ""
    recruitment_type: str = "unknown"
    jd: str = ""
    hr_name: str = ""
    hr_title: str = ""
    hr_active: str = ""
    company_size: str = ""
    company_industry: str = ""
    url: str = ""
    source_keyword: str = ""

    @property
    def storage_id(self) -> str:
        if self.platform != "boss":
            return f"{self.platform}:{self.source_job_id}"
        return self.source_job_id

    def as_job_record(self) -> dict[str, Any]:
        return {
            "id": self.storage_id,
            "title": self.title,
            "company": self.company,
            "salary": self.salary,
            "city": self.city,
            "source_city_code": self.city_code,
            "experience": self.experience,
            "education": self.education,
            "recruitment_type": (
                self.recruitment_type
                if self.recruitment_type in {"campus", "experienced"}
                else classify_recruitment_type(self.title, self.experience, self.jd)
            ),
            "jd": clean_job_description(self.jd),
            "hr_name": self.hr_name,
            "hr_title": self.hr_title,
            "hr_active": self.hr_active,
            "company_size": self.company_size,
            "company_industry": self.company_industry,
            "url": self.url,
            "source_platform": self.platform,
            "source_job_id": self.source_job_id,
            "source_keyword": self.source_keyword,
        }


@dataclass
class CollectionProgress:
    run_id: str
    platform: PlatformId
    platform_index: int
    platform_total: int
    phase: str
    target: int | None
    seen: int = 0
    new: int = 0
    duplicate: int = 0
    filtered: int = 0
    parse_failed: int = 0
    save_failed: int = 0
    keyword: str = ""
    city: str = ""
    page: int = 0
    max_pages: int = 0
    reason_code: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def percent(self) -> int | None:
        if self.target is None or self.target <= 0:
            return None
        return min(100, int(self.new * 100 / self.target))


@dataclass
class PlatformCollectionResult:
    platform: PlatformId
    status: str
    reason_code: str = ""
    message: str = ""
    new_job_ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    error: str = ""
