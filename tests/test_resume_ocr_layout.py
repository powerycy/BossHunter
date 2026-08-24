import unittest

from bosshunter.web.resume_upload import _ocr_result_to_text


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


if __name__ == "__main__":
    unittest.main()
