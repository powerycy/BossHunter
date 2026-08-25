import unittest

from bosshunter.web.resume_upload import (
    MAX_OCR_PAGE_PIXELS,
    ResumeUploadError,
    _bounded_ocr_render_scale,
    _create_ocr_engine,
    _ocr_result_to_text,
)


def _box(left: float, top: float, right: float, bottom: float):
    return [
        [left, top],
        [right, top],
        [right, bottom],
        [left, bottom],
    ]


class ResumeOcrLayoutTests(unittest.TestCase):
    def test_two_column_resume_is_read_column_by_column(self):
        boxes = [
            _box(20, 20, 150, 40),
            _box(20, 60, 150, 80),
            _box(20, 100, 150, 120),
            _box(20, 140, 150, 160),
            _box(260, 20, 470, 40),
            _box(260, 60, 470, 80),
            _box(260, 100, 470, 120),
            _box(260, 140, 470, 160),
        ]
        texts = ["L1", "L2", "L3", "L4", "R1", "R2", "R3", "R4"]

        converted = _ocr_result_to_text(boxes, texts, page_width=500)

        self.assertLess(converted.index("L4"), converted.index("R1"))
        self.assertEqual([token for token in converted.split() if token], texts)

    def test_single_column_resume_stays_in_vertical_order(self):
        boxes = [
            _box(20, 20 + index * 40, 450 - index * 10, 40 + index * 40)
            for index in range(8)
        ]
        texts = [f"Line-{index}" for index in range(8)]

        converted = _ocr_result_to_text(boxes, texts, page_width=500)

        self.assertEqual([token for token in converted.split() if token], texts)

    def test_separate_indented_sections_are_not_misread_as_two_columns(self):
        boxes = [
            _box(260, 20 + index * 40, 470, 40 + index * 40)
            for index in range(4)
        ] + [
            _box(20, 220 + index * 40, 200, 240 + index * 40)
            for index in range(4)
        ]
        texts = ["TOP-R1", "TOP-R2", "TOP-R3", "TOP-R4", "BOTTOM-L1", "BOTTOM-L2", "BOTTOM-L3", "BOTTOM-L4"]

        converted = _ocr_result_to_text(boxes, texts, page_width=500)

        self.assertEqual([token for token in converted.split() if token], texts)

    def test_render_scale_caps_page_pixels(self):
        scale = _bounded_ocr_render_scale(2000, 2000)

        self.assertLessEqual(2000 * scale * 2000 * scale, MAX_OCR_PAGE_PIXELS + 1)

    def test_oversized_page_is_rejected_before_rendering(self):
        with self.assertRaisesRegex(ResumeUploadError, "页面尺寸过大"):
            _bounded_ocr_render_scale(10000, 10000)

    def test_model_load_failure_has_online_install_and_offline_use_guidance(self):
        class BrokenRapidOcr:
            def __init__(self):
                raise OSError("model unavailable")

        with self.assertRaisesRegex(ResumeUploadError, "安装完成后可断网识别"):
            _create_ocr_engine(BrokenRapidOcr)


if __name__ == "__main__":
    unittest.main()
