"""stage6 마크다운 렌더링 특성화 테스트."""

from __future__ import annotations

import unittest

from yke.models import ConceptCluster, ConceptPoint, SourceRef
from yke.pipeline.stage6_integrate import render_markdown


class TestRenderMarkdown(unittest.TestCase):
    def _md(self) -> str:
        clusters = [
            ConceptCluster(
                concept="커피 로스팅",
                summary="요약 문장.",
                points=[
                    ConceptPoint(
                        statement="명제 A",
                        type="fact",
                        sources=[SourceRef(video_id="abc123", timestamp="08:49")],
                    )
                ],
                conflicts=["상충 지점 X"],
            )
        ]
        return render_markdown(clusters, {"abc123": {"title": "제목"}})

    def test_concept_heading_and_summary(self):
        md = self._md()
        self.assertIn("## 커피 로스팅", md)
        self.assertIn("요약 문장.", md)

    def test_type_badge_and_statement(self):
        self.assertIn("**[사실]** 명제 A", self._md())

    def test_source_link_timestamp_seconds(self):
        # 08:49 -> 529초 딥링크
        self.assertIn("https://youtu.be/abc123?t=529", self._md())

    def test_conflicts_rendered(self):
        md = self._md()
        self.assertIn("⚠️", md)
        self.assertIn("상충 지점 X", md)


if __name__ == "__main__":
    unittest.main()
