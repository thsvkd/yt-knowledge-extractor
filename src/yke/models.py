"""파이프라인 전 구간에서 공유하는 데이터 모델."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

KnowledgeType = Literal["fact", "opinion", "tip", "definition"]


class Segment(BaseModel):
    """트랜스크립트의 한 구간 (자막 또는 STT 결과)."""

    start: float
    end: float
    text: str


class KnowledgeUnit(BaseModel):
    """5단계: 영상별 '지식 원자 단위'.

    서술형 요약이 아니라 검증 가능한 최소 지식 조각.
    timestamp/quote_evidence 로 원본 역추적 및 할루시네이션 검증이 가능하다.
    """

    concept: str
    statement: str
    type: KnowledgeType
    source_video_id: str
    timestamp: str
    quote_evidence: str


class SourceRef(BaseModel):
    video_id: str
    timestamp: str


class ConceptPoint(BaseModel):
    statement: str
    type: KnowledgeType
    sources: list[SourceRef] = []


class ConceptCluster(BaseModel):
    """6단계: 여러 영상에서 통합된 개념 클러스터."""

    concept: str
    summary: str
    points: list[ConceptPoint] = []
    conflicts: list[str] = []
