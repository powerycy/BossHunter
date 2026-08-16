"""Data contracts shared by all job collection platforms."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


PlatformId = Literal["boss", "zhilian"]


@dataclass(frozen=True)
class PlatformCollectionRequest:
    platform: PlatformId
    keywords: list[str]
    cities: list[str]
    city_codes: dict[str, str]
    max_pages: int = 3
    sort: str = "default"
    target_count: int | None = 10


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
        if self.platform == "zhilian":
            return f"zhilian:{self.source_job_id}"
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
            "jd": self.jd,
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
