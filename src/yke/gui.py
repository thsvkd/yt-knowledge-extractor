"""Flet 기반 데스크톱 GUI.

CLI 와 동일한 코어(:func:`run_pipeline`)를 재사용하는 얇은 표현 계층이다. 파이프라인은
백그라운드 스레드에서 돌리고, 진행바·상태·결과 로그를 실시간으로 갱신한다. 취소는
:class:`threading.Event` 로 처리한다. STT 는 세그먼트/청크 단위로 확인해 진행 중인
영상 하나의 변환 도중에도 곧바로 멈추고, LLM 호출은 영상/단계 경계에서 멈춘다.

naver-blog-crawler 의 GUI 와 같은 명령형(imperative) flet 패턴을 따른다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from functools import partial
from pathlib import Path

import flet as ft

from . import __version__, updater
from .config import Config, LLMConfig, load_config
from .llm.claude_client import is_available as _claude_cli_available
from .run import Progress, PipelineResult, run_pipeline, _fmt_hms

logger = logging.getLogger(__name__)

# 로그 레벨별 색.
_LEVEL_COLOR: dict[str, str] = {
    "info": ft.Colors.ON_SURFACE,
    "success": ft.Colors.GREEN,
    "warning": ft.Colors.AMBER,
    "error": ft.Colors.RED,
}
# 로그 ListView 에 유지할 최대 줄 수(메모리 보호).
_MAX_LOG_ROWS = 300
# 로그 패널의 고정 높이(px). 루트가 스크롤되므로 창 높이에 의존하지 않고 이 안에서 자체 스크롤한다.
_LOG_PANEL_HEIGHT = 260
# 상태 텍스트 렌더 틱 주기(초). 짧은 시간에 몰리는 갱신을 한 번으로 합쳐 화면이 밀리지 않게 한다.
_UI_TICK_SECONDS = 0.15

# 스크립트 변환(STT) 모델 선택지. "auto" 는 장치별 기본(GPU:large-v3 / CPU:small)으로,
# 대부분의 사용자에게 권장되는 기본값이다. (engine: faster-whisper 전용 — vosk 선택 시 무시됨)
_STT_MODELS = ("auto", "tiny", "base", "small", "medium", "large-v3")
# STT 엔진 선택지: (표시명, stt.engine 값). faster-whisper(AI, 기본)와 vosk(경량 오프라인,
# non-AI 옵션 — `uv sync --extra vosk` 필요, 정확도는 낮지만 완전 오프라인·초경량) 중 선택.
_STT_ENGINES = [("AI 모델 (faster-whisper)", "faster-whisper"), ("경량 모델 (Vosk, non-AI)", "vosk")]
# GPU 가속 선택지: (표시명, stt.device 값).
_DEVICE_CHOICES = [("자동", "auto"), ("사용", "cuda"), ("사용 안함", "cpu")]
# 실행 단계 선택지: (표시명, run_pipeline stage 값). 기본값은 '스크립트 추출까지'.
_STAGE_CHOICES = [
    ("전체 (지식 문서화까지)", "all"),
    ("스크립트 추출까지", "transcript"),
]
_DEFAULT_STAGE = "transcript"
# 지식 추출·통합(LLM)이 필요한 단계.
_STAGES_NEEDING_LLM = {"all", "extract", "integrate"}
# 전체 파이프라인 타임라인 칩 — Progress.phase 값과 매핑. 실제 표시 목록은 실행 단계
# (stage_dd) 에 따라 걸러진다(예: '스크립트 추출까지'면 추출/통합 칩은 아예 안 보임).
_PHASE_LABELS: dict[str, str] = {
    "transcript": "트랜스크립트",
    "extract": "지식 추출",
    "integrate": "통합 문서화",
}
# 트랜스크립트 단계 내부의 세부 타임라인 — Progress.substep 값과 매핑(고정 순서).
# 오디오 원본은 STT 폴백일 때만 내려받으므로, 메타·자막 단계에서는 오디오를 받지 않는다
# (자막으로 처리되는 영상은 오디오 다운로드 없이 빠르게 끝난다). 그래서 'ingest' 라벨은
# '메타·자막', 오디오 다운로드는 'stt'(오디오·STT) 단계에 속한다.
_SUBSTEP_LABELS: list[tuple[str, str]] = [
    ("resolve", "소스 확인"),
    ("ingest", "메타·자막"),
    ("subtitles", "자막 확인"),
    ("stt", "오디오·STT"),
    ("clean", "텍스트 정제"),
]
# '현재 영상 진행바'를 채우는 세부 단계별 진행률(0~1). 로컬 변환(STT) 필요/불필요에 따라
# 경로가 달라지므로(자막 경로엔 STT 구간을 예약하지 않아 빠르게 채워진다), clean/stt 는
# _video_fraction 에서 경로별로 값을 정하고, 여기서는 두 경로가 공유하는 앞단만 둔다.
_VIDEO_SUBSTEP_BASE: dict[str, float] = {
    "resolve": 0.0,
    "ingest": 0.15,
    "subtitles": 0.50,  # 자막 경로: 자막 다운·검증 지점
}
# STT sub_progress(0~1, 오디오 기준) → 현재 영상 진행률 사상 구간(STT 경로 전용).
_STT_VIDEO_SPAN: tuple[float, float] = (0.20, 0.90)
# 정제(clean) 지점 — 경로별로 다르게: 자막 경로는 STT 구간이 없어 더 앞(0.90), STT 경로는
# 오디오·STT 다음이라 더 뒤(0.95).
_VIDEO_CLEAN_CAPTION = 0.90
_VIDEO_CLEAN_STT = 0.95

# 선택한 실행 단계가 전체바 100%를 나눠 갖는다(단계 가로질러 연속). phase → (시작, 폭).
# 100%는 마지막 선택 단계까지 끝나야 도달한다. GUI 드롭다운은 transcript/all 만 노출하지만,
# 방어적으로 extract/integrate 도 정의해 둔다. (전체=트랜스크립트 50·추출 35·통합 15)
_STAGE_PARTITION: dict[str, dict[str, tuple[float, float]]] = {
    "transcript": {"transcript": (0.0, 1.0)},
    "extract": {"transcript": (0.0, 0.65), "extract": (0.65, 0.35)},
    "integrate": {"transcript": (0.0, 0.50), "extract": (0.50, 0.35), "integrate": (0.85, 0.15)},
    "all": {"transcript": (0.0, 0.50), "extract": (0.50, 0.35), "integrate": (0.85, 0.15)},
}
# LLM 모델 선택지: (표시명, 모델 ID).
_LLM_MODELS = [
    ("Claude Opus 4.8 (최고 성능)", "claude-opus-4-8"),
    ("Claude Sonnet 5 (균형)", "claude-sonnet-5"),
    ("Claude Haiku 4.5 (빠름·저렴)", "claude-haiku-4-5-20251001"),
]
# 채널/재생목록에서 가져올 최근 영상 수 기본값.
_DEFAULT_LIMIT = 5

_DEFAULT_CONFIG_PATH = "config/channel.yaml"


def _load_base_config(path: str) -> Config:
    """설정 파일에서 노출하지 않는 기본값(compute_type/subtitles/청크 크기 등)을 가져온다.

    파일이 없거나 깨졌으면 pydantic 기본값(빈 videos)으로 폴백한다.
    """
    try:
        if Path(path).exists():
            return load_config(path)
    except Exception:
        logger.warning("설정 파일 로딩 실패 — 기본값 사용", exc_info=True)
    return Config(videos=[])


class PipelineGUI:
    """GUI 상태와 이벤트 처리를 담는 컨트롤러."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._stop = threading.Event()
        self._is_running = False  # 파이프라인 실행 상태
        self._last_result: PipelineResult | None = None
        self._last_out_dir: Path | None = None
        self._pending_release: updater.Release | None = None  # 다운로드 대기 중인 업데이트
        # 상태 텍스트는 백그라운드 스레드가 값만 기록하고, 렌더 틱이 일괄 반영한다.
        self._status_lock = threading.Lock()
        self._status_dirty = threading.Event()
        self._app_closing = threading.Event()
        self._status_msg = "대기 중"
        self._status_color: str | None = None
        # 실행 시작 시각(경과 시간 표시용). 실행 중에만 값이 있고, 끝나면 None 으로 되돌려
        # 렌더 틱이 더 이상 경과 시간을 갱신하지 않게 한다.
        self._run_started: float | None = None
        # 진행바 상태 — 전체 목록(영상 done/total)과 현재 영상 내부 진행률을 나눠 추적한다.
        # 경계(substep) 이벤트가 done/total 을 안 실어도 마지막 값을 유지해, 진행바가
        # 스피너로 되돌아가지 않고 실제 진행을 계속 보여 주게 한다.
        self._ov_total: int | None = None  # 전체 영상 수
        self._ov_done: int = 0  # 완료(성공/실패/스킵)한 영상 수
        self._video_frac: float = 0.0  # 현재 영상 내부 진행률(0~1)
        self._video_saw_stt: bool = False  # 현재 영상이 STT(로컬 변환) 경로로 갔는지
        self._bar_phase: str | None = None  # 진행바 기준 현재 phase(전환 시 영상 진행률 리셋)
        # 이번 실행의 실행 단계(전체바 100% 배분 기준). 실행 중 잠기므로 시작 시 고정한다.
        self._run_stage: str = _DEFAULT_STAGE
        # 타임라인 칩(phase_row/substep_row) 상태 — 중복 리렌더를 피하려고 마지막으로
        # 그린 phase/substep 을 기억한다.
        self._phase_order: list[str] = ["transcript"]
        self._last_rendered_phase: str | None = None
        self._last_rendered_substep: str | None = None
        self.base_cfg = _load_base_config(_DEFAULT_CONFIG_PATH)
        self._build()
        # 렌더 하트비트를 flet 이벤트 루프의 async task 로 돌린다. 원시 스레드에서 .update()
        # 를 호출하면(구버전) 이벤트 루프가 깨어나지 않아 갱신이 client 로 즉시 전송되지 않고
        # 다음 파이프라인 이벤트 때까지 밀렸다 — 그래서 경과 시간/진행 로그가 '뚝뚝' 끊겨
        # 보였다. 루프에서 주기적으로 깨어나면 모든 스레드가 쌓아 둔 갱신이 매 틱 flush 된다.
        self.page.run_task(self._ui_ticker)
        # 시작 시 새 버전을 조용히 확인한다(네트워크/레포 미공개 실패는 무시).
        self.page.run_thread(self._auto_check_updates)

    # -- UI 구성 ---------------------------------------------------------
    def _build(self) -> None:
        page = self.page
        page.title = "유튜브 지식 추출기"
        page.theme_mode = ft.ThemeMode.SYSTEM
        page.padding = 20
        page.window.width = 820
        page.window.height = 860
        # 창 높이는 하드코딩한 최소값에 의존하지 않는다. 루트 Column 을 스크롤 가능하게 두어
        # 창이 콘텐츠보다 짧아지면 전체 페이지가 스크롤되므로, 요소가 세로로 압축("짜부")되지
        # 않는다. 최소 너비만 버튼 줄바꿈으로 높이가 늘지 않게 넉넉히 둔다.
        page.window.min_width = 640
        page.on_close = self._on_close

        cfg = self.base_cfg
        _muted_color = ft.Colors.with_opacity(0.6, ft.Colors.ON_SURFACE)
        self._muted_color = _muted_color
        _muted = ft.TextStyle(color=_muted_color)
        # 힌트는 라벨보다 더 흐리게 하여 '힌트'임을 강조한다(입력값·라벨과 시각적으로 구분).
        _hint = ft.TextStyle(color=ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE))

        # URL 입력: 영상 또는 채널/재생목록 URL 을 한 줄에 하나씩. 설정의 videos 로 초기값.
        self.url_field = ft.TextField(
            label="유튜브 URL — 영상 또는 채널 (한 줄에 하나씩)",
            label_style=_muted,
            hint_text="예: https://www.youtube.com/watch?v=...  또는  https://www.youtube.com/@channel",
            hint_style=_hint,
            value="",
            multiline=True,
            min_lines=3,
            max_lines=6,
            expand=True,
        )
        # 채널/재생목록 URL 일 때 처리할 최근 영상 수(개별 영상 URL 엔 무관).
        self.limit_field = ft.TextField(
            label="채널: 최근 영상 수",
            value=str(_DEFAULT_LIMIT),
            width=180,
            tooltip="채널/재생목록 URL 을 넣었을 때 최근 몇 개 영상을 처리할지",
        )
        self._original_limit = str(_DEFAULT_LIMIT)
        self.fetch_all_cb = ft.Checkbox(
            label="모두 가져오기",
            value=False,
            on_change=self._on_fetch_all_changed,
        )

        # 저장 폴더(캐시·최종 산출물을 한 곳에).
        self.out_field = ft.TextField(label="저장 폴더", value=cfg.output_dir, expand=True)
        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        # on_click 에는 async 핸들러를 그대로(코루틴 함수로) 넘겨야 flet 이 iscoroutinefunction
        # 판별에 성공해 await 한다. 람다로 감싸면 sync 로 취급돼 코루틴이 버려지고 무동작이 된다.
        # functools.partial 은 iscoroutinefunction 이 언랩해 코루틴 함수로 인식된다.
        self.out_browse_btn = ft.Button(
            "찾아보기", icon=ft.Icons.FOLDER_OPEN, on_click=partial(self._pick_folder, self.out_field)
        )

        # 실행 단계(2단계) + 단계에 따라 LLM UI 활성/비활성.
        self.stage_dd = ft.Dropdown(
            label="실행 단계",
            value=_DEFAULT_STAGE,
            width=260,
            options=[ft.dropdown.Option(key=v, text=label) for label, v in _STAGE_CHOICES],
            # flet 0.85 Dropdown 의 선택 변경 이벤트는 on_change 가 아니라 on_select 다.
            on_select=lambda _e: self._on_stage_changed(),
        )

        self.start_btn = ft.Button("시작", icon=ft.Icons.PLAY_ARROW, on_click=self._on_start_stop_click)
        self.open_btn = ft.Button(
            "저장 폴더 열기", icon=ft.Icons.FOLDER, on_click=self._open_folder, disabled=True
        )
        self.wiki_btn = ft.Button(
            "wiki.md 열기", icon=ft.Icons.ARTICLE, on_click=self._open_wiki, disabled=True
        )

        # 고급 옵션(접이식).
        self.language_field = ft.TextField(label="언어", value=cfg.language, width=110)
        _engine_vals = {v for _l, v in _STT_ENGINES}
        self.stt_engine_dd = ft.Dropdown(
            label="STT 엔진",
            value=cfg.stt.engine if cfg.stt.engine in _engine_vals else "faster-whisper",
            width=190,
            tooltip=(
                "AI 모델: faster-whisper(정확도 높음, 기본). "
                "경량 모델: Vosk(완전 오프라인·초경량, 정확도는 낮음 — 아래 모델/GPU 설정은 무시됨)"
            ),
            options=[ft.dropdown.Option(key=v, text=label) for label, v in _STT_ENGINES],
        )
        self.stt_model_dd = ft.Dropdown(
            label="스크립트 변환 모델",
            value=cfg.stt.model if cfg.stt.model in _STT_MODELS else "auto",
            width=180,
            tooltip="auto: GPU면 large-v3(최고 품질), GPU 없으면 small 로 자동 선택 (AI 모델 엔진 전용)",
            options=[ft.dropdown.Option(x) for x in _STT_MODELS],
        )
        _device_vals = {v for _l, v in _DEVICE_CHOICES}
        self.stt_device_dd = ft.Dropdown(
            label="GPU 가속",
            value=cfg.stt.device if cfg.stt.device in _device_vals else "auto",
            width=150,
            options=[ft.dropdown.Option(key=v, text=label) for label, v in _DEVICE_CHOICES],
        )
        self.llm_model_dd = ft.Dropdown(
            label="언어 모델 (지식 추출·통합)",
            value=cfg.llm.model,
            width=300,
            options=self._llm_options(cfg.llm.model),
            # 프리셋 3종 외의 모델 ID(신규 모델 등)도 직접 입력할 수 있게 연다.
            editable=True,
            hint_text="목록에 없으면 모델 ID를 직접 입력하세요",
            hint_style=_hint,
        )
        self.force_cb = ft.Checkbox(label="강제로 재생성", value=False)
        self.force_local_stt_cb = ft.Checkbox(
            label="강제로 로컬 변환 (자막 무시, STT만 사용)",
            value=cfg.subtitles.stt_first,
            tooltip="활성화하면 유튜브 자막을 무시하고 로컬 STT(faster-whisper)만 사용합니다",
        )

        # 지식 추출·통합은 로컬 Claude Code CLI(`claude -p`)를 호출한다. 인증은 CLI 의
        # 로그인 상태를 그대로 쓰므로 앱에서 별도로 토큰을 입력·저장하지 않는다.
        self.cred_status = ft.Text(size=12, color=_muted_color)

        # 자체 업데이트: 확인 버튼 + 상태. 새 버전이 있으면 같은 버튼이 '업데이트 후 재시작'으로 바뀐다.
        self.update_btn = ft.Button(
            "업데이트 확인", icon=ft.Icons.REFRESH, on_click=lambda _e: self._on_update_click()
        )
        self.update_status = ft.Text(f"현재 버전 v{__version__}", size=12, color=_muted_color)

        advanced = ft.ExpansionTile(
            title=ft.Text("고급 옵션"),
            controls=[
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    self.language_field,
                                    self.stt_engine_dd,
                                    self.stt_model_dd,
                                    self.stt_device_dd,
                                ],
                                wrap=True,
                            ),
                            self.llm_model_dd,
                            self.cred_status,
                            self.force_cb,
                            self.force_local_stt_cb,
                            ft.Divider(),
                            ft.Row(
                                [self.update_btn, self.update_status],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=12,
                                wrap=True,
                            ),
                        ],
                        spacing=12,
                    ),
                    padding=ft.Padding(left=16, top=20, right=16, bottom=12),
                )
            ],
        )

        # 전체 목록 진행바(채널이면 완료 영상 기준으로 채워짐) + 오른쪽 "영상 i/N" 캡션.
        self.progress = ft.ProgressBar(value=0, expand=True)
        self.overall_caption = ft.Text("", size=12, color=_muted_color)
        # 현재 영상 진행바(여러 영상=채널 처리 시에만 표시) + 오른쪽 퍼센트 캡션. 스피너가
        # 아니라 실제 영상 내부 진행(메타·자막→오디오·STT→정제)을 채운다.
        self.video_progress = ft.ProgressBar(value=0, expand=True)
        self.video_caption = ft.Text("", size=12, color=_muted_color)
        self.video_row = ft.Row(
            [self.video_progress, self.video_caption],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )
        # 진행바 밑 실시간 타임라인: 큰 단계(phase_row) + 트랜스크립트 단계 내부 세부
        # 작업(substep_row, 트랜스크립트 진행 중에만 보임).
        self.phase_row = ft.Row(spacing=6, wrap=True)
        self.substep_row = ft.Row(spacing=6, wrap=True, visible=False)
        self.status = ft.Text("대기 중", size=13)
        # 실행 중 실시간 경과 시간(오른쪽). 중단/완료 시 최종 경과로 고정된다.
        self.elapsed_label = ft.Text("", size=12, color=_muted_color)
        self.copy_log_btn = ft.IconButton(
            icon=ft.Icons.CONTENT_COPY,
            icon_size=16,
            tooltip="로그 전체 복사",
            on_click=self._copy_log,
        )
        # build_controls_on_demand=False: 기본값(True)이면 뷰포트 밖 항목이 아예 빌드되지
        # 않아(가상 스크롤), SelectionArea 드래그 선택이 화면 밖 줄을 못 건너뛰어 한 줄만
        # 복사되는 문제가 있었다. 로그가 _MAX_LOG_ROWS(300줄)로 상한이 있어 전부 미리
        # 빌드해도 성능 부담이 없다.
        self.log_view = ft.ListView(
            expand=True,
            spacing=2,
            auto_scroll=True,
            build_controls_on_demand=False,
            scroll=ft.ScrollMode.ALWAYS,
            # 오른쪽에 스크롤바 두께만큼 여백을 둔다 — 안 두면 스크롤바 트랙/썸이
            # 로그 텍스트의 오른쪽 끝 글자를 덮어 가리는 문제가 있었다(실측 확인).
            padding=ft.Padding(left=0, top=0, right=14, bottom=0),
        )

        page.add(
            ft.Column(
                [
                    # 실제 콘텐츠는 오른쪽 여백을 둔 Container 안에 넣는다 — 바깥 Column 이
                    # scroll=AUTO 라 콘텐츠가 넘치면 오른쪽 끝에 스크롤바가 뜨는데, 안쪽
                    # 콘텐츠가 Column 폭에 꽉 차 있으면 그 스크롤바가 오른쪽 끝에 붙은
                    # 버튼/캡션(찾아보기, 복사 등)을 덮어 가리는 문제가 있었다(실측 확인).
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text("유튜브 지식 추출기", size=22, weight=ft.FontWeight.BOLD),
                                self.url_field,
                                ft.Row(
                                    [self.out_field, self.out_browse_btn],
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Row(
                                    [self.stage_dd, self.limit_field, self.fetch_all_cb],
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    wrap=True,
                                ),
                                advanced,
                                ft.Row(
                                    [self.start_btn, self.open_btn, self.wiki_btn], wrap=True
                                ),
                                ft.Row(
                                    [self.progress, self.overall_caption],
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                self.phase_row,
                                self.substep_row,
                                self.video_row,
                                ft.Row(
                                    [
                                        # expand=True 로 남는 폭을 모두 차지해 길게 줄바꿈되고,
                                        # elapsed_label 은 항상 고정 폭으로 오른쪽에 남는다 —
                                        # 상태 문구가 길어져도(예: "중단됨 (완료 45 · 실패 35 ·
                                        # ...)") 경과 시간이 밀려나거나 가려지지 않는다.
                                        ft.SelectionArea(content=self.status, expand=True),
                                        self.elapsed_label,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.START,
                                ),
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Text(
                                                    "진행 로그", size=12, color=_muted_color
                                                ),
                                                self.copy_log_btn,
                                            ],
                                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        ),
                                        ft.Container(
                                            content=ft.SelectionArea(content=self.log_view),
                                            # 고정 높이 패널. 내부 ListView 가 자체
                                            # 스크롤(auto_scroll)하고, 창이 짧아지면 루트
                                            # Column 이 페이지 전체를 스크롤한다.
                                            height=_LOG_PANEL_HEIGHT,
                                            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                                            border_radius=8,
                                            padding=8,
                                        ),
                                    ],
                                    spacing=6,
                                ),
                            ],
                            spacing=12,
                        ),
                        padding=ft.Padding(left=0, top=0, right=14, bottom=0),
                    ),
                ],
                # 창이 콘텐츠보다 짧으면 전체 페이지가 스크롤되어 요소가 압축되지 않는다.
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )
        )
        self._apply_llm_enabled()
        self._refresh_cred_status()
        self._reset_phase_chips()

    @staticmethod
    def _llm_options(current: str) -> list[ft.dropdown.Option]:
        """LLM 모델 드롭다운 옵션. 설정값이 목록에 없으면 그 값도 선택지로 추가한다."""
        options = [ft.dropdown.Option(key=mid, text=label) for label, mid in _LLM_MODELS]
        known = {mid for _label, mid in _LLM_MODELS}
        if current and current not in known:
            options.insert(0, ft.dropdown.Option(key=current, text=current))
        return options

    def _selected_llm_model(self) -> str:
        """언어 모델 드롭다운의 실제 선택값(모델 ID)을 얻는다.

        editable 드롭다운이라 프리셋을 고르면 ``value``(키)가, 직접 입력하면 ``text``만
        갱신될 수 있다. ``text``가 프리셋 라벨과 일치하면 그 키로, 아니면 입력값 그대로
        (커스텀 모델 ID)를 쓴다.
        """
        text = (self.llm_model_dd.text or "").strip()
        if text:
            by_label = {label: mid for label, mid in _LLM_MODELS}
            return by_label.get(text, text)
        return self.llm_model_dd.value or ""

    def _llm_controls(self) -> tuple[ft.Control, ...]:
        """'전체(지식 문서화)' 단계에서만 필요한 LLM 관련 컨트롤."""
        return (self.llm_model_dd,)

    def _apply_llm_enabled(self) -> None:
        """실행 단계가 LLM 을 쓰지 않으면(스크립트 추출까지) 언어 모델 UI 를 비활성화한다."""
        enabled = (self.stage_dd.value or _DEFAULT_STAGE) in _STAGES_NEEDING_LLM
        for c in self._llm_controls():
            c.disabled = not enabled
            self._safe_update(c)

    # -- 이벤트 ----------------------------------------------------------
    def _on_start_stop_click(self, _e: ft.ControlEvent) -> None:
        """시작/중단 버튼 클릭 — 현재 상태에 따라 시작 또는 중단."""
        if self._is_running:
            self._request_stop()
        else:
            self._start()

    def _on_fetch_all_changed(self, _e: ft.ControlEvent) -> None:
        """모두 가져오기 체크박스 변경 핸들러."""
        if self.fetch_all_cb.value:
            # 체크: 현재 값을 저장하고 limit을 0으로 설정, 필드 비활성화
            self._original_limit = self.limit_field.value
            self.limit_field.value = "0"
            self.limit_field.disabled = True
        else:
            # 체크 해제: 원래 값으로 복원, 필드 활성화
            self.limit_field.value = self._original_limit
            self.limit_field.disabled = False
        self.limit_field.update()

    async def _pick_folder(self, target: ft.TextField, _e: ft.ControlEvent | None = None) -> None:
        path = await self.file_picker.get_directory_path(dialog_title="폴더 선택")
        if path:
            target.value = path
            target.update()

    def _open_folder(self, _e: ft.ControlEvent) -> None:
        # 마지막 실행에 실제로 사용한 저장 폴더를 연다. 실행 뒤 필드를 수정해도 결과가
        # 저장된 위치를 정확히 열도록, 실행 시점에 기록해 둔 _last_out_dir 를 우선한다.
        target = self._last_out_dir or Path(self.out_field.value.strip() or "output").resolve()
        self._open_path(target)

    def _open_wiki(self, _e: ft.ControlEvent) -> None:
        result = self._last_result
        if result and result.wiki_path and result.wiki_path.exists():
            self._open_path(result.wiki_path.resolve())

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            return
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)

    async def _copy_log(self, _e: ft.ControlEvent) -> None:
        # flet(Flutter web)의 SelectionArea 는 자체 스크롤하는 ListView 안에서 드래그
        # 선택 제스처가 스크롤 제스처와 충돌해 여러 줄이 한 번에 복사되지 않는 경우가
        # 있다(실측 확인). 드래그 선택에 기대는 대신 로그 전체를 한 번에 클립보드로
        # 복사하는 버튼을 둬 안정적으로 여러 줄을 복사할 수 있게 한다.
        text = "\n".join(c.value for c in self.log_view.controls if isinstance(c, ft.Text))
        if not text:
            return
        await self.page.clipboard.set(text)
        self.copy_log_btn.icon = ft.Icons.CHECK
        self._safe_update(self.copy_log_btn)
        await asyncio.sleep(1.2)
        self.copy_log_btn.icon = ft.Icons.CONTENT_COPY
        self._safe_update(self.copy_log_btn)

    def _on_close(self, _e: ft.ControlEvent) -> None:
        """창이 닫히면 실행 중인 파이프라인을 취소하고 렌더 틱 스레드를 깔끔히 종료시킨다.

        _stop 을 세우지 않으면 flet 워커 스레드(비데몬)에서 돌던 긴 STT·LLM 배치가
        보이지 않는 채로 끝까지 실행돼 프로세스가 남는다. STT 는 세그먼트/청크 단위로
        곧 멈추고, LLM 호출은 다음 영상/단계 경계에서 멈춘다.
        """
        self._stop.set()
        self._app_closing.set()
        self._status_dirty.set()

    def _request_stop(self) -> None:
        self._stop.set()
        self.progress.value = 0
        self._safe_update(self.progress)
        self._set_status_now("중단 요청됨 — 곧 멈춥니다…", ft.Colors.AMBER)

    def _refresh_cred_status(self) -> None:
        """Claude Code CLI 감지 여부를 상태 텍스트에 반영한다."""
        if _claude_cli_available():
            self._set_cred_status("Claude CLI 감지됨 ✓", ft.Colors.GREEN)
        else:
            self._set_cred_status(
                "Claude CLI 를 찾을 수 없습니다 — '전체 (지식 문서화까지)' 단계에는 "
                "claude.com/claude-code 설치 + `claude login` 이 필요합니다.",
                self._muted_color,
            )

    @staticmethod
    def _safe_update(control: ft.Control) -> None:
        """컨트롤을 갱신하되, 아직 페이지에 붙지 않았거나 창 종료 중이면 조용히 흡수한다.

        flet 0.85 에서 페이지에 없는 컨트롤의 ``.update()``(및 ``.page`` 접근)는
        RuntimeError 를 던진다. 데몬 렌더 스레드가 조용히 죽는 것을 막고자 흡수하되,
        원인 추적이 가능하도록 디버그 로그로 남긴다(조용한 무시 아님).
        """
        try:
            control.update()
        except Exception:
            logger.debug("컨트롤 갱신 실패(미부착/창 종료 중일 수 있음)", exc_info=True)

    def _set_cred_status(self, message: str, color: str | None) -> None:
        self.cred_status.value = message
        self.cred_status.color = color
        self._safe_update(self.cred_status)

    # -- 자체 업데이트(GitHub Releases) ----------------------------------
    def _auto_check_updates(self) -> None:
        self._check_updates(manual=False)

    def _on_update_click(self) -> None:
        if self._pending_release is None:
            self._set_update_status("업데이트 확인 중…", None)
            self.page.run_thread(lambda: self._check_updates(manual=True))
        else:
            self.page.run_thread(self._download_and_apply)

    def _check_updates(self, manual: bool) -> None:
        variant, target = updater.detect_variant_target()
        try:
            release = updater.check_latest(__version__, variant, target)
        except Exception as exc:
            logger.warning("업데이트 확인 실패", exc_info=True)
            if manual:
                self._set_update_status(f"업데이트 확인 실패: {exc}", ft.Colors.AMBER)
            return
        if release is not None:
            self._pending_release = release
            # flet 0.85 Button 의 라벨은 content 다(text 로 쓰면 무시되어 라벨이 안 바뀐다).
            self.update_btn.content = f"v{release.version} 로 업데이트 후 재시작"
            self.update_btn.icon = ft.Icons.SYSTEM_UPDATE
            self._safe_update(self.update_btn)
            self._set_update_status(
                f"새 버전 v{release.version} 사용 가능 (현재 v{__version__})", ft.Colors.GREEN
            )
        elif manual:
            self._set_update_status(f"최신 버전입니다 (v{__version__}).", self._muted_color)

    def _download_and_apply(self) -> None:
        release = self._pending_release
        if release is None:
            return
        root = updater.install_root()
        if not updater.is_bundle(root):
            self._set_update_status(
                "개발 환경에서는 업데이트를 적용하지 않습니다(배포 번들에서만 동작).",
                ft.Colors.AMBER,
            )
            return
        import tempfile

        dl_dir = Path(tempfile.gettempdir()) / "yke_update"
        # 추출본은 설치 볼륨(install_dir.parent)에 둔다. 그래야 사이드카의 폴더 스왑이 원자적
        # rename 이 된다(temp 가 다른 드라이브면 비원자적 copy 라 실패 시 install 이 깨진다).
        extract_dir = root.parent / ".yke_update_staging"
        try:
            self._set_update_status(f"v{release.version} 다운로드 중… 0%", None)
            zip_path = updater.download(release, dl_dir, progress_cb=self._download_progress)
            self._set_update_status("압축 해제 중…", None)
            new_dir = updater.extract(zip_path, extract_dir)
            zip_path.unlink(missing_ok=True)  # 다운로드 zip 은 이제 불필요
        except Exception as exc:
            logger.error("업데이트 다운로드 실패", exc_info=True)
            self._set_update_status(f"업데이트 실패: {exc}", ft.Colors.RED)
            self._pending_release = None
            return
        app_exe = (
            "yt-knowledge-extractor.exe" if sys.platform == "win32" else "yt-knowledge-extractor"
        )
        self._set_update_status("업데이트를 적용하고 재시작합니다…", ft.Colors.GREEN)
        # apply_and_restart 는 os._exit 로 하드 종료하므로, 그 전에 위 안내를 클라이언트로
        # 확실히 밀어낸다(창이 아무 메시지 없이 갑자기 사라지지 않도록).
        try:
            self.page.update()
        except Exception:
            logger.debug("최종 상태 flush 실패", exc_info=True)
        time.sleep(0.4)
        # 이 호출은 사이드카를 띄우고 현재 프로세스를 종료한다(앱이 닫히고 새 버전이 재실행).
        updater.apply_and_restart(new_dir, app_exe=app_exe, install_dir=root)

    def _download_progress(self, frac: float) -> None:
        version = self._pending_release.version if self._pending_release else ""
        self._set_update_status(f"v{version} 다운로드 중… {int(frac * 100)}%", None)

    def _set_update_status(self, message: str, color: str | None) -> None:
        self.update_status.value = message
        self.update_status.color = color
        self._safe_update(self.update_status)

    # -- 실행 검증 & 시작 ------------------------------------------------
    def _urls(self) -> list[str]:
        return [line.strip() for line in (self.url_field.value or "").splitlines() if line.strip()]

    def _channel_limit(self) -> int | None:
        raw = (self.limit_field.value or "").strip()
        if not raw:
            return None
        try:
            n = int(raw)
        except ValueError:
            return None
        return n if n > 0 else None

    def _start(self) -> None:
        urls = self._urls()
        if not urls:
            self._set_status_now("유튜브 URL 을 한 줄에 하나씩 입력하세요.", ft.Colors.RED)
            return
        # 자격증명 게이트: '전체' 단계는 로컬 Claude Code CLI 가 있어야 한다.
        stage = self.stage_dd.value or _DEFAULT_STAGE
        if stage in _STAGES_NEEDING_LLM and not _claude_cli_available():
            self._set_status_now(
                "'전체 (지식 문서화까지)' 단계에는 Claude Code CLI 가 필요합니다. "
                "claude.com/claude-code 설치 후 `claude login` 으로 로그인하세요.",
                ft.Colors.RED,
            )
            return
        self._stop.clear()
        self.page.run_thread(self._run)

    # -- 파이프라인(백그라운드 스레드) -----------------------------------
    def _run(self) -> None:
        self.log_view.controls.clear()
        # 전체바 100% 배분 기준이 되는 실행 단계를 이번 실행에 고정한다.
        self._run_stage = self.stage_dd.value or _DEFAULT_STAGE
        self._reset_progress()
        self._last_result = None
        self._reset_phase_chips()
        # 상태 텍스트는 렌더 틱 스레드가 소유하므로 여기서도 틱 버퍼를 거쳐 갱신한다
        # (직접 status.value 를 쓰면 두 스레드가 같은 컨트롤을 건드린다).
        self._set_status("시작 준비 중…")
        # 경과 시간 타이머 시작 — 렌더 틱이 매초 갱신한다.
        self._run_started = time.monotonic()
        self._set_elapsed("경과 0:00")
        self._set_running(True)

        cfg = self._read_config()
        self._last_out_dir = Path(cfg.output_dir).resolve()
        try:
            result = run_pipeline(
                self._urls(),
                cfg,
                data_dir=cfg.data_dir,
                out_dir=cfg.output_dir,
                force=bool(self.force_cb.value),
                stage=self.stage_dd.value or _DEFAULT_STAGE,
                channel_limit=self._channel_limit(),
                on_progress=self._on_progress,
                should_stop=self._stop.is_set,
            )
        except Exception as exc:
            logger.error("파이프라인 실패", exc_info=True)
            # 실패해도 여기까지의 경과 시간을 고정 표시한다.
            elapsed = time.monotonic() - self._run_started if self._run_started else 0.0
            self._run_started = None
            self.progress.value = 0
            self._hide_video_bar()
            self._append_log(f"오류: {type(exc).__name__}: {exc}", "error")
            self._set_status(f"오류: {exc}  (경과 {_fmt_hms(elapsed)})", ft.Colors.RED)
            self._set_elapsed(f"경과 {_fmt_hms(elapsed)}")
            self._mark_phase_terminal("error")
            self._set_running(False)
            return

        self._finish(result)

    def _read_config(self) -> Config:
        """UI 필드로 base 설정을 덮어써 이번 실행용 Config 를 만든다.

        저장 폴더 하나로 캐시(data_dir)와 산출물(output_dir)을 모두 둔다. 노출하지 않는
        값(compute_type/word_timestamps/청크 크기)은 base 에서 계승한다.
        """
        base = self.base_cfg
        folder = self.out_field.value.strip() or "output"
        return Config(
            videos=self._urls(),
            language=self.language_field.value.strip() or "ko",
            # UI 로 노출한 engine/model/device 만 덮어쓰고, 노출하지 않는 나머지 STT 필드
            # (compute_type/word_timestamps/batched/batch_size/cpu_threads/vosk_model_size
            # 등, 미래 필드 포함)는 base 설정에서 그대로 계승한다(필드별 재조립 시 새
            # 필드가 조용히 기본값으로 되돌아가는 문제를 방지).
            stt=base.stt.model_copy(
                update={
                    "engine": self.stt_engine_dd.value or "faster-whisper",
                    "model": self.stt_model_dd.value or "auto",
                    "device": self.stt_device_dd.value or "auto",
                }
            ),
            subtitles=base.subtitles.model_copy(
                update={"stt_first": bool(self.force_local_stt_cb.value)}
            ),
            llm=LLMConfig(
                model=self._selected_llm_model() or base.llm.model,
                max_chars_per_chunk=base.llm.max_chars_per_chunk,
            ),
            output_dir=folder,
            data_dir=folder,
        )

    # -- 진행 렌더(백그라운드 스레드에서 호출) ---------------------------
    def _on_progress(self, p: Progress) -> None:
        color = _LEVEL_COLOR.get(p.level, ft.Colors.ON_SURFACE)
        # 영속 로그(스피너용 transient 제외).
        if not p.transient:
            self._append_log(p.message, p.level)
        # 상태 텍스트 = 최신 메시지.
        self._set_status(p.message, color)
        self._update_progress_bars(p)
        self._update_phase_ui(p)

    def _video_fraction(self, p: Progress) -> float | None:
        """현재 영상 내부 진행률(0~1). 로컬 변환(STT) 필요/불필요 경로에 맞춰 웨이포인트를
        다르게 잡는다 — 자막 경로엔 STT 구간을 예약하지 않아 빠르게 채워지고, STT 경로는
        오디오·STT 가 대부분(0.2~0.9)을 차지한다. substep 이 없는(경계/로그) 이벤트는 None
        을 돌려 진행률을 유지하게 한다.
        """
        if p.substep == "stt":
            self._video_saw_stt = True  # 이 영상은 로컬 변환 경로
            if p.sub_progress is not None:
                lo, hi = _STT_VIDEO_SPAN
                return lo + (hi - lo) * p.sub_progress
            return _STT_VIDEO_SPAN[0]  # 오디오 다운로드/STT 시작(세부 진행 전)
        if p.substep == "clean":
            return _VIDEO_CLEAN_STT if self._video_saw_stt else _VIDEO_CLEAN_CAPTION
        if p.substep is not None:
            return _VIDEO_SUBSTEP_BASE.get(p.substep)
        return None

    def _update_progress_bars(self, p: Progress) -> None:
        """전체 목록 진행바(선택 단계 가로질러 연속) + 현재 영상 진행바를 갱신한다.

        - 전체바 100% 기준은 '선택한 실행 단계'가 정한다(_STAGE_PARTITION): 스크립트 추출까지면
          트랜스크립트가 100%, 전체면 트랜스크립트·추출·통합으로 나눠 마지막까지 끝나야 100%.
        - 영상 내부 진행은 로컬 변환(STT) 필요/불필요 경로에 맞춰 모양이 다르다(_video_fraction).
        - 경계(substep) 이벤트가 done/total 을 안 실어도 마지막 값을 유지해 스피너로 되돌아가지
          않는다. 영상 내부 진행률은 단조 증가로 고정(경로 오판·이벤트 순서로 뒤로 가지 않게).
        """
        # phase 전환 — "done"(요약/최종 줄)은 진행바 phase 로 취급하지 않는다(중간 트랜스크립트
        # 요약이 100%로 튀는 것 방지). 실제 작업 phase(transcript/extract/integrate)만 슬라이스.
        if p.phase is not None and p.phase != "done" and p.phase != self._bar_phase:
            self._bar_phase = p.phase
            self._ov_done = 0
            self._video_frac = 0.0
            self._video_saw_stt = False
        if p.total is not None:
            self._ov_total = p.total
        if p.done is not None:
            if p.done != self._ov_done:  # 다음 영상 경계 → 현재 영상 진행 리셋
                self._video_frac = 0.0
                self._video_saw_stt = False
            self._ov_done = p.done
        vf = self._video_fraction(p)
        if vf is not None:
            self._video_frac = max(self._video_frac, vf)  # 영상 내부 단조 증가

        # 최종 완료(done phase + 완료 카운트) → 100%. 중간 요약(done/total 없음)은 무시.
        if p.phase == "done" and p.done is not None and p.total:
            self.progress.value = 1.0
            self._hide_video_bar()
            self._safe_update(self.progress)
            return

        part = _STAGE_PARTITION.get(self._run_stage) or _STAGE_PARTITION["all"]
        seg = part.get(self._bar_phase) if self._bar_phase else None
        if seg is not None:
            start, width = seg
            total = self._ov_total
            if total:
                phase_frac = min(1.0, (self._ov_done + self._video_frac) / total)
                self.progress.value = start + width * phase_frac
            elif p.indeterminate:
                self.progress.value = None  # 전체 수 미상(채널 분석 등)일 때만 스피너
            # else: 이전 값 유지
            # 현재 영상 바는 트랜스크립트 단계에서 영상을 실제로 처리 중일 때만.
            if self._bar_phase == "transcript" and p.phase != "done":
                self._update_video_bar(total)
            else:
                self._hide_video_bar()
        else:
            # 파티션에 없는 phase(방어): 기존 단순 처리 + 영상 바 숨김.
            if p.total and p.sub_progress is not None:
                self.progress.value = min(1.0, ((p.done or 0) + p.sub_progress) / p.total)
            elif p.indeterminate:
                self.progress.value = None
            elif p.total:
                self.progress.value = (p.done or 0) / p.total
            self._hide_video_bar()
        self._safe_update(self.progress)

    def _update_video_bar(self, total: int | None) -> None:
        """현재 영상 진행바/캡션 — 여러 영상(채널) 처리일 때만 보여 준다."""
        multi = bool(total and total > 1)
        if self.video_row.visible != multi:
            self.video_row.visible = multi
            self._safe_update(self.video_row)
        if not multi or total is None:
            if not multi:
                self._set_text(self.overall_caption, "")
            return
        idx = min(self._ov_done + 1, total)
        self._set_text(self.overall_caption, f"영상 {idx}/{total}")
        if self.video_progress.value != self._video_frac:
            self.video_progress.value = self._video_frac
            self._safe_update(self.video_progress)
        self._set_text(self.video_caption, f"{int(self._video_frac * 100)}%")

    def _hide_video_bar(self) -> None:
        if self.video_row.visible:
            self.video_row.visible = False
            self._safe_update(self.video_row)
        self._set_text(self.overall_caption, "")

    def _reset_progress(self) -> None:
        """실행 시작 시 진행바 상태를 초기화한다."""
        self._ov_total = None
        self._ov_done = 0
        self._video_frac = 0.0
        self._video_saw_stt = False
        self._bar_phase = None
        self.progress.value = 0
        self.video_progress.value = 0
        self.video_row.visible = False
        self.overall_caption.value = ""
        self.video_caption.value = ""
        self._safe_update(self.progress)
        self._safe_update(self.video_row)

    def _set_text(self, ctrl: ft.Text, text: str) -> None:
        """텍스트가 실제로 바뀔 때만 갱신(불필요한 리렌더/전송 방지)."""
        if ctrl.value != text:
            ctrl.value = text
            self._safe_update(ctrl)

    # -- 진행바 밑 실시간 타임라인(phase_row/substep_row) ------------------
    def _on_stage_changed(self) -> None:
        self._apply_llm_enabled()
        self._reset_phase_chips()

    @staticmethod
    def _phase_order_for_stage(stage: str) -> list[str]:
        if stage == "transcript":
            return ["transcript"]
        if stage == "extract":
            return ["transcript", "extract"]
        return ["transcript", "extract", "integrate"]

    def _reset_phase_chips(self) -> None:
        """대기 상태(또는 실행 단계 변경 시)의 타임라인 미리보기 — 전부 대기 중으로 그린다."""
        self._phase_order = self._phase_order_for_stage(self.stage_dd.value or _DEFAULT_STAGE)
        self._last_rendered_phase = None
        self._last_rendered_substep = None
        self._rebuild_phase_row(None)
        self.substep_row.visible = False
        self.substep_row.controls = []
        self._safe_update(self.phase_row)
        self._safe_update(self.substep_row)

    def _step_chip(self, label: str, state: str) -> ft.Container:
        muted = self._muted_color
        if state == "done":
            leading, color = ft.Icon(ft.Icons.CHECK_CIRCLE, size=14, color=ft.Colors.GREEN), ft.Colors.GREEN
        elif state == "active":
            leading = ft.ProgressRing(width=12, height=12, stroke_width=2, color=ft.Colors.PRIMARY)
            color = ft.Colors.PRIMARY
        elif state == "error":
            leading, color = ft.Icon(ft.Icons.ERROR, size=14, color=ft.Colors.RED), ft.Colors.RED
        elif state == "stopped":
            leading, color = ft.Icon(ft.Icons.PAUSE_CIRCLE, size=14, color=ft.Colors.AMBER), ft.Colors.AMBER
        else:  # pending
            leading, color = ft.Icon(ft.Icons.CIRCLE_OUTLINED, size=14, color=muted), muted
        return ft.Container(
            content=ft.Row(
                [leading, ft.Text(label, size=12, color=color, weight=ft.FontWeight.BOLD if state == "active" else None)],
                spacing=4,
                tight=True,
            ),
            padding=ft.Padding(left=8, top=4, right=8, bottom=4),
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.12, color) if state != "pending" else None,
        )

    def _rebuild_phase_row(self, current_phase: str | None, *, terminal_state: str | None = None) -> None:
        """전체 파이프라인 타임라인 칩(트랜스크립트→추출→통합)을 다시 그린다.

        ``current_phase`` 가 ``"done"`` 이면 전부 완료로, 목록에 없으면(대기 중) 전부
        대기 중으로 그린다. ``terminal_state``('error'|'stopped')가 있으면 실행이 멈춘
        시점의 활성 칩을 그 상태로 고정해, 다 끝난 것처럼 보이지 않게 한다.
        """
        if current_phase == "done":
            idx_current = len(self._phase_order)
        elif current_phase in self._phase_order:
            idx_current = self._phase_order.index(current_phase)
        else:
            idx_current = -1
        controls: list[ft.Control] = []
        for i, key in enumerate(self._phase_order):
            if idx_current < 0:
                state = "pending"
            elif i < idx_current:
                state = "done"
            elif i == idx_current:
                state = terminal_state or "active"
            else:
                state = "pending"
            if controls:
                controls.append(ft.Icon(ft.Icons.CHEVRON_RIGHT, size=14, color=self._muted_color))
            controls.append(self._step_chip(_PHASE_LABELS[key], state))
        self.phase_row.controls = controls

    def _rebuild_substep_row(self, current_substep: str | None, *, terminal_state: str | None = None) -> None:
        """트랜스크립트 단계 내부 세부 타임라인(소스 확인→오디오·메타→자막 확인→STT→정제)."""
        order = [key for key, _ in _SUBSTEP_LABELS]
        labels = dict(_SUBSTEP_LABELS)
        idx_current = order.index(current_substep) if current_substep in order else -1
        controls: list[ft.Control] = []
        for i, key in enumerate(order):
            if idx_current < 0:
                state = "pending"
            elif i < idx_current:
                state = "done"
            elif i == idx_current:
                state = terminal_state or "active"
            else:
                state = "pending"
            if controls:
                controls.append(ft.Icon(ft.Icons.CHEVRON_RIGHT, size=12, color=self._muted_color))
            controls.append(self._step_chip(labels[key], state))
        self.substep_row.controls = controls

    def _update_phase_ui(self, p: Progress) -> None:
        """``_on_progress`` 에서 매 이벤트마다 호출 — phase/substep 이 실제로 바뀔 때만
        해당 Row 를 다시 그려(불필요한 리렌더 방지) 실시간으로 갱신한다."""
        phase = p.phase
        if phase is not None and phase != self._last_rendered_phase:
            self._last_rendered_phase = phase
            self._rebuild_phase_row(phase)
            self._safe_update(self.phase_row)

        show_substep = phase == "transcript"
        if show_substep != self.substep_row.visible:
            self.substep_row.visible = show_substep
            if not show_substep:
                self.substep_row.controls = []
                self._last_rendered_substep = None
            self._safe_update(self.substep_row)

        if show_substep and p.substep and p.substep != self._last_rendered_substep:
            self._last_rendered_substep = p.substep
            self._rebuild_substep_row(p.substep)
            self._safe_update(self.substep_row)

    def _mark_phase_terminal(self, terminal_state: str) -> None:
        """실행이 실패/중단된 채 멈췄을 때, 마지막으로 활성이던 칩을 그 상태로 고정한다.

        그냥 두면 마지막 스피너가 여전히 "진행 중"처럼 보여, 실제로는 멈춘 작업을 계속
        도는 것처럼 오인하게 만든다.
        """
        if self._last_rendered_phase in (None, "done"):
            return
        self._rebuild_phase_row(self._last_rendered_phase, terminal_state=terminal_state)
        self._safe_update(self.phase_row)
        if self.substep_row.visible and self._last_rendered_substep:
            self._rebuild_substep_row(self._last_rendered_substep, terminal_state=terminal_state)
            self._safe_update(self.substep_row)

    def _append_log(self, message: str, level: str) -> None:
        color = _LEVEL_COLOR.get(level, ft.Colors.ON_SURFACE)
        timestamp = time.strftime("%H:%M:%S")
        # 개별 Text 에 selectable=True 를 주면 Flutter 에서 각 줄이 독립된
        # SelectableText 가 되어 부모 SelectionArea 의 통합 선택 영역에 합쳐지지
        # 않고, 드래그 선택이 한 줄을 벗어나지 못하는 문제가 있었다(실측 확인).
        # 부모 SelectionArea(311줄)가 이미 선택을 제공하므로 여기서는 빼야 한다.
        self.log_view.controls.append(
            ft.Text(f"[{timestamp}] {message}", size=12, color=color, no_wrap=False)
        )
        if len(self.log_view.controls) > _MAX_LOG_ROWS:
            del self.log_view.controls[0]
        self._safe_update(self.log_view)

    def _finish(self, result: PipelineResult) -> None:
        self._last_result = result
        # 성공 완료면 전체바를 100%(가득)로, 중단이면 0으로 둔다 — '선택 단계까지 전부 완료'를
        # 눈으로 확인시킨다(중단은 부분 완료라 가득 채우지 않는다).
        self.progress.value = 0 if result.stopped else 1.0
        self._safe_update(self.progress)
        self._hide_video_bar()
        # 경과 시간 타이머를 멈추고, 이번 실행의 최종 경과로 고정한다.
        self._run_started = None
        elapsed = _fmt_hms(result.elapsed_seconds)
        self._set_elapsed(f"경과 {elapsed}")
        # 완료/실패/스킵 집계 — 상태 텍스트에 함께 보여 준다("최종 처리 결과").
        done = sum(1 for r in result.results if r.status == "done")
        failed = sum(1 for r in result.results if r.status == "failed")
        skipped = sum(1 for r in result.results if r.status == "skipped")
        tally = f"완료 {done} · 실패 {failed} · 스킵 {skipped}"
        # 채널/재생목록을 입력하면 run_pipeline 이 그 채널 전용 하위 폴더로 정리한다 —
        # 실행 전에 추정해 둔 _last_out_dir(저장 폴더 그대로) 대신, 실제로 쓰인 폴더로
        # 갱신해 '저장 폴더 열기' 가 정확한 위치를 연다.
        if result.out_dir is not None:
            self._last_out_dir = result.out_dir.resolve()
        if result.stopped:
            self._set_status(
                f"중단됨 ({tally}, 경과 {elapsed}) — 처리한 부분은 저장 폴더에 캐시되었습니다. "
                "다시 시작하면 이어서 진행합니다.",
                ft.Colors.AMBER,
            )
            self._mark_phase_terminal("stopped")
        elif result.wiki_path is not None:
            self._set_status(
                f"완료 — 개념 {result.concept_count}개 · 영상 {result.video_count}개 · "
                f"{tally} · 경과 {elapsed} · {result.wiki_path}",
                ft.Colors.GREEN,
            )
        else:
            self._set_status(
                f"완료 — 영상 {result.video_count}개 스크립트 추출 완료. "
                f"({tally}, 경과 {elapsed})",
                ft.Colors.GREEN,
            )
        self.open_btn.disabled = False
        self.wiki_btn.disabled = not (result.wiki_path and result.wiki_path.exists())
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self._is_running = running
        # 버튼 토글: 진행 중이면 "중단", 대기 중이면 "시작"
        if running:
            self.start_btn.content = "중단"
            self.start_btn.icon = ft.Icons.STOP
        else:
            self.start_btn.content = "시작"
            self.start_btn.icon = ft.Icons.PLAY_ARROW
        self._safe_update(self.start_btn)
        # 실행 중에는 입력을 잠가 진행 중 작업과 어긋나지 않게 한다.
        base = (
            self.url_field,
            self.limit_field,
            self.out_field,
            self.out_browse_btn,
            self.stage_dd,
            self.force_cb,
            self.force_local_stt_cb,
            self.language_field,
            self.stt_model_dd,
            self.stt_device_dd,
        )
        for control in base + self._llm_controls():
            control.disabled = running
        # 실행이 끝나면 LLM 컨트롤은 단계에 맞춰 다시 활성/비활성한다.
        if not running:
            llm_enabled = (self.stage_dd.value or _DEFAULT_STAGE) in _STAGES_NEEDING_LLM
            for c in self._llm_controls():
                c.disabled = not llm_enabled
        self.page.update()

    # -- 상태 텍스트 렌더 틱 ---------------------------------------------
    def _set_status(self, message: str, color: str | None = None) -> None:
        """상태 텍스트 갱신을 예약한다(렌더 틱이 일괄 반영)."""
        with self._status_lock:
            self._status_msg = message
            self._status_color = color
        self._status_dirty.set()

    def _set_status_now(self, message: str, color: str | None = None) -> None:
        """상태 텍스트를 즉시 반영한다(UI 스레드의 단발 이벤트 전용)."""
        with self._status_lock:
            self._status_msg = message
            self._status_color = color
        self._flush_status()

    async def _ui_ticker(self) -> None:
        """flet 이벤트 루프에서 도는 렌더 하트비트(~7fps).

        매 틱마다 (1) 버퍼된 상태 텍스트를 flush 하고 (2) 실행 중 경과 시간을 갱신한다.
        이 코루틴이 루프를 주기적으로 깨우므로, 백그라운드 파이프라인 스레드가 쌓아 둔
        진행 로그·진행바 갱신도 이 틱마다 함께 client 로 전송된다 — 긴 다운로드/STT
        도중에도 UI 가 멈춰 보이지 않는다. (원시 스레드에서 .update() 만 호출하던 구버전은
        이벤트 루프를 깨우지 못해 다음 파이프라인 이벤트 때까지 갱신이 밀렸다.)
        """
        while not self._app_closing.is_set():
            if self._status_dirty.is_set():
                self._status_dirty.clear()
                self._flush_status()
            self._tick_elapsed()
            try:
                await asyncio.sleep(_UI_TICK_SECONDS)
            except asyncio.CancelledError:
                break
        self._flush_status()

    def _tick_elapsed(self) -> None:
        """실행 중이면 현재까지의 경과 시간을 경과 라벨에 반영한다(렌더 틱 전용)."""
        started = self._run_started
        if self._is_running and started is not None:
            self._set_elapsed(f"경과 {_fmt_hms(time.monotonic() - started)}")

    def _set_elapsed(self, text: str) -> None:
        # 초 단위 표시라 값이 실제로 바뀔 때만 전송한다(0.15s 마다 도는 틱의 중복 전송 방지).
        if self.elapsed_label.value == text:
            return
        self.elapsed_label.value = text
        self._safe_update(self.elapsed_label)

    def _flush_status(self) -> None:
        with self._status_lock:
            message, color = self._status_msg, self._status_color
        self.status.value = message
        self.status.color = color
        self._safe_update(self.status)


def _view(page: ft.Page) -> None:
    PipelineGUI(page)


def main() -> None:
    """GUI 실행 진입점(``yke-gui``)."""
    ft.run(_view)


if __name__ == "__main__":
    main()
