import pytest

from bosshunter.web.resume_upload import ResumeUploadError, prepare_resume_content


def test_markdown_upload_rejects_non_utf8_bytes():
    with pytest.raises(ResumeUploadError, match="UTF-8"):
        prepare_resume_content("resume.md", b"\xff\xfe\x00")


def test_markdown_upload_keeps_valid_utf8_content():
    content = "# 真实简历\n".encode("utf-8")
    filename, normalized, warning = prepare_resume_content("真实简历.md", content)

    assert filename == "真实简历.md"
    assert normalized == content
    assert warning is None
