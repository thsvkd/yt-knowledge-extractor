"""Flet 기반 데스크톱 GUI.

CLI 와 동일한 코어(:func:`run_pipeline`)를 재사용하는 얇은 표현 계층이다. 파이프라인은
백그라운드 스레드에서 돌리고, 진행바·상태·결과 로그를 실시간으로 갱신한다. 취소는
:class:`threading.Event` 로 영상/단계 경계에서 협조적으로 처리한다(진행 중인 단일 영상의
STT·LLM 호출은 끊지 않고 다음 경계에서 멈춘다).

naver-blog-crawler 의 GUI 와 같은 명령형(imperative) flet 패턴을 따른다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import flet as ft

from .config import Config, LLMConfig, STTConfig, load_config
from .run import Progress, PipelineResult, run_pipeline
from .utils import load_dotenv

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
# 상태 텍스트 렌더 틱 주기(초). 짧은 시간에 몰리는 갱신을 한 번으로 합쳐 화면이 밀리지 않게 한다.
_UI_TICK_SECONDS = 0.15

# STT 모델 / 디바이스 선택지.
_STT_MODELS = ("tiny", "base", "small", "medium", "large-v3")
_STT_DEVICES = ("auto", "cuda", "cpu")
# 단계 선택지: (표시명, run_pipeline stage 값).
_STAGE_CHOICES = [
    ("전체 (통합까지)", "all"),
    ("트랜스크립트만 (1~4단계)", "transcript"),
    ("추출까지 (5단계)", "extract"),
]
# 5단계 이상은 LLM 자격증명이 필요하다.
_STAGES_NEEDING_LLM = {"all", "extract", "integrate"}

_DEFAULT_CONFIG_PATH = "config/channel.yaml"


def _has_llm_credentials() -> bool:
    return bool(
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


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
        self._last_result: PipelineResult | None = None
        self._last_out_dir: Path | None = None
        # 상태 텍스트는 백그라운드 스레드가 값만 기록하고, 렌더 틱이 일괄 반영한다.
        self._status_lock = threading.Lock()
        self._status_dirty = threading.Event()
        self._app_closing = threading.Event()
        self._status_msg = "대기 중"
        self._status_color: str | None = None
        self.base_cfg = _load_base_config(_DEFAULT_CONFIG_PATH)
        self._build()
        # 데몬 스레드라 창을 닫으면 함께 종료된다.
        self._render_thread = threading.Thread(
            target=self._ui_ticker, name="gui-render-tick", daemon=True
        )
        self._render_thread.start()

    # -- UI 구성 ---------------------------------------------------------
    def _build(self) -> None:
        page = self.page
        page.title = "유튜브 지식 추출기"
        page.theme_mode = ft.ThemeMode.SYSTEM
        page.padding = 20
        page.window.width = 820
        page.window.height = 820
        page.window.min_width = 620
        page.window.min_height = 560
        page.on_close = self._on_close

        cfg = self.base_cfg
        _muted_color = ft.Colors.with_opacity(0.6, ft.Colors.ON_SURFACE)
        self._muted_color = _muted_color
        _muted = ft.TextStyle(color=_muted_color)

        # URL 입력: 한 줄에 하나씩. 설정 파일의 videos 로 초기값을 채운다.
        self.url_field = ft.TextField(
            label="유튜브 URL (한 줄에 하나씩)",
            label_style=_muted,
            hint_text="예: https://www.youtube.com/watch?v=8fYXYk42LxU",
            hint_style=_muted,
            value="\n".join(cfg.videos),
            multiline=True,
            min_lines=3,
            max_lines=6,
            expand=True,
        )

        # 출력/데이터 폴더.
        self.out_field = ft.TextField(label="출력 폴더", value=cfg.output_dir, expand=True)
        self.data_field = ft.TextField(label="데이터(캐시) 폴더", value=cfg.data_dir, expand=True)
        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        # on_click 에는 async 핸들러를 그대로(코루틴 함수로) 넘겨야 flet 이 iscoroutinefunction
        # 판별에 성공해 await 한다. 람다로 감싸면 sync 로 취급돼 코루틴이 버려지고 무동작이 된다.
        # functools.partial 은 iscoroutinefunction 이 언랩해 코루틴 함수로 인식된다.
        self.out_browse_btn = ft.Button(
            "찾아보기", icon=ft.Icons.FOLDER_OPEN, on_click=partial(self._pick_folder, self.out_field)
        )
        self.data_browse_btn = ft.Button(
            "찾아보기",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=partial(self._pick_folder, self.data_field),
        )

        # 실행 단계.
        self.stage_dd = ft.Dropdown(
            label="실행 단계",
            value="all",
            width=240,
            options=[ft.dropdown.Option(key=v, text=label) for label, v in _STAGE_CHOICES],
        )

        self.start_btn = ft.Button("시작", icon=ft.Icons.PLAY_ARROW, on_click=lambda _e: self._start())
        self.stop_btn = ft.Button(
            "중단", icon=ft.Icons.STOP, on_click=lambda _e: self._request_stop(), disabled=True
        )
        self.open_btn = ft.Button(
            "출력 폴더 열기", icon=ft.Icons.FOLDER, on_click=self._open_folder, disabled=True
        )
        self.wiki_btn = ft.Button(
            "wiki.md 열기", icon=ft.Icons.ARTICLE, on_click=self._open_wiki, disabled=True
        )

        # 고급 옵션(접이식).
        self.language_field = ft.TextField(label="언어", value=cfg.language, width=110)
        self.stt_model_dd = ft.Dropdown(
            label="STT 모델",
            value=cfg.stt.model if cfg.stt.model in _STT_MODELS else "large-v3",
            width=150,
            options=[ft.dropdown.Option(x) for x in _STT_MODELS],
        )
        self.stt_device_dd = ft.Dropdown(
            label="STT 디바이스",
            value=cfg.stt.device if cfg.stt.device in _STT_DEVICES else "auto",
            width=150,
            options=[ft.dropdown.Option(x) for x in _STT_DEVICES],
        )
        self.llm_model_field = ft.TextField(label="LLM 모델", value=cfg.llm.model, width=220)
        self.force_cb = ft.Checkbox(label="캐시 무시하고 재생성 (--force)", value=False)
        # LLM 토큰(선택): 비우면 .env 의 자격증명을 쓴다. 채우면 이번 세션 환경변수로 주입한다.
        self.token_field = ft.TextField(
            label="Claude/Anthropic 토큰 (선택, 비우면 .env 사용)",
            hint_text="sk-ant-oat01-...  또는  sk-ant-api03-...",
            password=True,
            can_reveal_password=True,
            expand=True,
            on_change=lambda _e: self._refresh_cred_status(),
        )
        self.cred_status = ft.Text(size=12, color=_muted_color)

        advanced = ft.ExpansionTile(
            title=ft.Text("고급 옵션"),
            controls=[
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [self.language_field, self.stt_model_dd, self.stt_device_dd],
                                wrap=True,
                            ),
                            self.llm_model_field,
                            self.force_cb,
                            ft.Row([self.data_field, self.data_browse_btn],
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.Column(
                                [
                                    self.token_field,
                                    self.cred_status,
                                ],
                                spacing=6,
                            ),
                        ],
                        spacing=12,
                    ),
                    padding=ft.Padding(left=16, top=20, right=16, bottom=12),
                )
            ],
        )

        self.progress = ft.ProgressBar(value=0)
        self.status = ft.Text("대기 중", size=13)
        self.log_view = ft.ListView(expand=True, spacing=2, auto_scroll=True)

        page.add(
            ft.Column(
                [
                    ft.Text("유튜브 지식 추출기", size=22, weight=ft.FontWeight.BOLD),
                    self.url_field,
                    ft.Row(
                        [self.out_field, self.out_browse_btn],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [self.stage_dd],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    advanced,
                    ft.Row([self.start_btn, self.stop_btn, self.open_btn, self.wiki_btn], wrap=True),
                    self.progress,
                    ft.SelectionArea(content=self.status),
                    ft.Column(
                        [
                            ft.Text("진행 로그", size=12, color=_muted_color),
                            ft.Container(
                                content=ft.SelectionArea(content=self.log_view),
                                expand=True,
                                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                                border_radius=8,
                                padding=8,
                            ),
                        ],
                        spacing=6,
                        expand=True,
                    ),
                ],
                spacing=12,
                expand=True,
            )
        )
        self._refresh_cred_status()

    # -- 이벤트 ----------------------------------------------------------
    async def _pick_folder(self, target: ft.TextField, _e: ft.ControlEvent | None = None) -> None:
        path = await self.file_picker.get_directory_path(dialog_title="폴더 선택")
        if path:
            target.value = path
            target.update()

    def _open_folder(self, _e: ft.ControlEvent) -> None:
        # 마지막 실행에 실제로 사용한 출력 폴더를 연다. 실행 뒤 필드를 수정해도 결과가
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

    def _on_close(self, _e: ft.ControlEvent) -> None:
        """창이 닫히면 실행 중인 파이프라인을 취소하고 렌더 틱 스레드를 깔끔히 종료시킨다.

        _stop 을 세우지 않으면 flet 워커 스레드(비데몬)에서 돌던 긴 STT·LLM 배치가
        보이지 않는 채로 끝까지 실행돼 프로세스가 남는다. 다음 영상/단계 경계에서 멈춘다.
        """
        self._stop.set()
        self._app_closing.set()
        self._status_dirty.set()

    def _request_stop(self) -> None:
        self._stop.set()
        self._set_status_now("중단 요청됨 — 현재 작업을 마치고 멈춥니다…", ft.Colors.AMBER)

    def _refresh_cred_status(self) -> None:
        """토큰 입력칸 + .env 자격증명 유무를 상태 텍스트에 반영한다."""
        if self.token_field.value.strip():
            self._set_cred_status("토큰 입력됨 — 이번 실행에 사용됩니다. ✓", ft.Colors.GREEN)
        elif _has_llm_credentials():
            self._set_cred_status(".env 자격증명: 있음 ✓", ft.Colors.GREEN)
        else:
            self._set_cred_status(
                ".env 자격증명: 없음 — 추출/통합 단계에 토큰이 필요합니다.", self._muted_color
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

    # -- 실행 검증 & 시작 ------------------------------------------------
    def _urls(self) -> list[str]:
        return [line.strip() for line in (self.url_field.value or "").splitlines() if line.strip()]

    def _start(self) -> None:
        urls = self._urls()
        if not urls:
            self._set_status_now("유튜브 URL 을 한 줄에 하나씩 입력하세요.", ft.Colors.RED)
            return
        # 자격증명 게이트: 토큰 필드가 채워져 있거나 .env 에 자격증명이 있으면 통과.
        # 실제 env 주입은 실행 스레드(_run)에서 실행 단위로만 하고 끝나면 되돌린다.
        stage = self.stage_dd.value or "all"
        has_token = bool(self.token_field.value.strip())
        if stage in _STAGES_NEEDING_LLM and not has_token and not _has_llm_credentials():
            self._set_status_now(
                "추출/통합 단계에는 LLM 토큰이 필요합니다. 고급 옵션에서 토큰을 넣거나 .env 를 설정하세요.",
                ft.Colors.RED,
            )
            return
        self._stop.clear()
        self.page.run_thread(self._run)

    # -- 파이프라인(백그라운드 스레드) -----------------------------------
    def _run(self) -> None:
        self.log_view.controls.clear()
        self.progress.value = None
        self.status.value = "시작 준비 중…"
        self.status.color = None
        self._last_result = None
        self._set_running(True)

        cfg = self._read_config()
        self._last_out_dir = Path(cfg.output_dir).resolve()
        restore_env = self._inject_token_env()
        try:
            result = run_pipeline(
                self._urls(),
                cfg,
                data_dir=cfg.data_dir,
                out_dir=cfg.output_dir,
                force=bool(self.force_cb.value),
                stage=self.stage_dd.value or "all",
                on_progress=self._on_progress,
                should_stop=self._stop.is_set,
            )
        except Exception as exc:
            logger.error("파이프라인 실패", exc_info=True)
            self.progress.value = 0
            self._append_log(f"오류: {type(exc).__name__}: {exc}", "error")
            self._set_status(f"오류: {exc}", ft.Colors.RED)
            self._set_running(False)
            return
        finally:
            restore_env()

        self._finish(result)

    # -- 자격증명 주입(실행 단위로만) ------------------------------------
    _CRED_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

    def _inject_token_env(self) -> Callable[[], None]:
        """토큰 필드 값을 이번 실행에 한해 환경변수로 주입하고, 원상복구 콜백을 돌려준다.

        붙여넣은 토큰이 이번 실행의 유일한 자격증명이 되도록 관련 키를 모두 비운 뒤 설정한다
        (그렇지 않으면 .env 의 OAuth 토큰이 우선순위에서 이겨 붙여넣은 API 키가 무시된다).
        실행이 끝나면 복구해, 필드를 비우면 .env 자격증명으로 자연스럽게 폴백된다.
        """
        saved = {k: os.environ.get(k) for k in self._CRED_KEYS}

        def restore() -> None:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        token = self.token_field.value.strip()
        if not token:
            return restore  # 주입 없음 — 복구도 무해(원본 그대로 되돌림)
        for k in self._CRED_KEYS:
            os.environ.pop(k, None)
        if token.startswith("sk-ant-oat"):
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
        else:
            os.environ["ANTHROPIC_API_KEY"] = token
        return restore

    def _read_config(self) -> Config:
        """UI 필드로 base 설정을 덮어써 이번 실행용 Config 를 만든다.

        노출하지 않는 값(compute_type/word_timestamps/subtitles/청크 크기)은 base 에서 계승한다.
        """
        base = self.base_cfg
        return Config(
            videos=self._urls(),
            language=self.language_field.value.strip() or "ko",
            stt=STTConfig(
                model=self.stt_model_dd.value or "large-v3",
                device=self.stt_device_dd.value or "auto",
                compute_type=base.stt.compute_type,
                word_timestamps=base.stt.word_timestamps,
            ),
            subtitles=base.subtitles,
            llm=LLMConfig(
                model=self.llm_model_field.value.strip() or base.llm.model,
                max_chars_per_chunk=base.llm.max_chars_per_chunk,
            ),
            output_dir=self.out_field.value.strip() or "output",
            data_dir=self.data_field.value.strip() or "data",
        )

    # -- 진행 렌더(백그라운드 스레드에서 호출) ---------------------------
    def _on_progress(self, p: Progress) -> None:
        color = _LEVEL_COLOR.get(p.level, ft.Colors.ON_SURFACE)
        # 영속 로그(스피너용 transient 제외).
        if not p.transient:
            self._append_log(p.message, p.level)
        # 상태 텍스트 = 최신 메시지.
        self._set_status(p.message, color)
        # 진행바.
        if p.indeterminate:
            self.progress.value = None
        elif p.total:
            self.progress.value = (p.done or 0) / p.total
        self._safe_update(self.progress)

    def _append_log(self, message: str, level: str) -> None:
        color = _LEVEL_COLOR.get(level, ft.Colors.ON_SURFACE)
        self.log_view.controls.append(
            ft.Text(message, size=12, color=color, no_wrap=False, selectable=True)
        )
        if len(self.log_view.controls) > _MAX_LOG_ROWS:
            del self.log_view.controls[0]
        self._safe_update(self.log_view)

    def _finish(self, result: PipelineResult) -> None:
        self._last_result = result
        if result.stopped:
            self._set_status(
                "중단됨 — 처리한 부분은 캐시에 저장되었습니다. 다시 시작하면 이어서 진행합니다.",
                ft.Colors.AMBER,
            )
        elif result.wiki_path is not None:
            self._set_status(
                f"완료 — 개념 {result.concept_count}개 · 영상 {result.video_count}개 · {result.wiki_path}",
                ft.Colors.GREEN,
            )
        else:
            self._set_status(
                f"완료 — 영상 {result.video_count}개 처리 (선택한 단계까지).", ft.Colors.GREEN
            )
        self.open_btn.disabled = False
        self.wiki_btn.disabled = not (result.wiki_path and result.wiki_path.exists())
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self.start_btn.disabled = running
        self.stop_btn.disabled = not running
        # 실행 중에는 입력을 잠가 진행 중 작업과 어긋나지 않게 한다.
        for control in (
            self.url_field,
            self.out_field,
            self.out_browse_btn,
            self.data_field,
            self.data_browse_btn,
            self.stage_dd,
            self.force_cb,
            self.language_field,
            self.stt_model_dd,
            self.stt_device_dd,
            self.llm_model_field,
            self.token_field,
        ):
            control.disabled = running
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

    def _ui_ticker(self) -> None:
        """상태 변경을 즉시 반영하고, 직후의 연속 변경만 묶는 렌더 루프."""
        while not self._app_closing.is_set():
            self._status_dirty.wait()
            if self._app_closing.is_set():
                break
            self._status_dirty.clear()
            self._flush_status()
            time.sleep(_UI_TICK_SECONDS)
        self._flush_status()

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
    load_dotenv()  # .env 의 CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY 를 환경변수로
    ft.run(_view)


if __name__ == "__main__":
    main()
