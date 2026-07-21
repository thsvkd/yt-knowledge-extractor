"""Google Gemini(``google-genai``) 기반 클라이언트.

인증은 사용자별 API 키(BYOK)를 쓴다 — :mod:`.credentials` 가 keyring/환경변수에서 읽은
키로 ``genai.Client(api_key=...)`` 를 초기화한다. 소비자용 Gemini Developer API 는 키
기반이라 Google OAuth '로그인' 경로는 쓰지 않는다(그건 Vertex AI 전용이고, GCP 프로젝트·
결제·동의화면이 필요해 배포용 데스크톱 앱엔 부적합하다).

무거운 SDK(``google-genai``)는 클라이언트를 실제로 만들 때만 import 한다 — 모듈 로드나
:func:`is_available` 검사만으로는 끌어오지 않는다.
"""

from __future__ import annotations

from .credentials import get_gemini_api_key, has_gemini_api_key

# provider 를 gemini 로 바꿨는데 model 이 여전히 claude-* 등 비 Gemini ID 면 이 기본으로 대체.
# '항상 최신 Flash' 별칭이라 새 모델이 나와도 낡지 않는다(gemini-flash-latest).
_DEFAULT_MODEL = "gemini-flash-latest"


def genai_available() -> bool:
    """``google-genai`` 패키지가 import 가능한지(설치 여부)."""
    try:
        import google.genai  # noqa: F401

        return True
    except Exception:
        return False


def is_available() -> bool:
    """지금 Gemini 를 쓸 수 있는지 — SDK 설치 + API 키 존재."""
    return genai_available() and has_gemini_api_key()


def _resolve_model(model: str) -> str:
    """모델 ID 가 비었거나 Gemini 계열이 아니면 기본 Gemini 모델로 대체한다."""
    m = (model or "").strip()
    if not m or not m.lower().startswith("gemini"):
        return _DEFAULT_MODEL
    return m


# 항상 '최신'을 가리키는 별칭들(버전 하드코딩 없이 최신 모델로 라우팅). resolve_aliases 로
# 각 별칭이 현재 어떤 구체 모델을 가리키는지 이름을 얻을 수 있다.
_LATEST_ALIASES = ("gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest")


def _new_client():
    """API 키로 genai 클라이언트를 만든다(모델 조회용)."""
    from google import genai

    return genai.Client(api_key=get_gemini_api_key())


def list_generate_models() -> list[tuple[str, str]]:
    """API 키로 지금 사용 가능한 generateContent 지원 Gemini 모델 목록(최신 우선).

    각 항목은 ``(모델 ID, 표시명)`` — 표시명은 API 가 주는 ``display_name``(예: "Gemini 3.6
    Flash")을 그대로 쓴다. 그래서 Google 이 새 모델을 추가하면 정적 프리셋을 고치지 않아도
    실제 이름과 함께 목록에 자동으로 나타난다. SDK 미설치/키 없음/네트워크 실패 등 어떤
    사유로든 못 가져오면 빈 리스트를 반환한다(호출부는 정적 프리셋으로 폴백).

    임베딩/AQA 등 텍스트 생성에 쓸 수 없는 모델은 제외한다. 정렬은 이름 역순으로,
    상위 버전(gemini-3 > gemini-2.5)이 위로 오게 한다.
    """
    if not is_available():
        return []
    try:
        client = _new_client()
        found: dict[str, str] = {}
        for m in client.models.list():
            name = (getattr(m, "name", "") or "").split("/")[-1]
            if not name.startswith("gemini"):
                continue
            if "embedding" in name or name.endswith("-aqa"):
                continue
            actions = getattr(m, "supported_actions", None)
            # actions 를 안 실어 주는 경우(None/빈 값)도 있어, 그때는 이름 기준으로 포함한다.
            if actions and "generateContent" not in actions:
                continue
            found[name] = getattr(m, "display_name", None) or name
        return [(mid, found[mid]) for mid in sorted(found, reverse=True)]
    except Exception:
        return []


def resolve_aliases(aliases: tuple[str, ...] = _LATEST_ALIASES) -> dict[str, str]:
    """각 '-latest' 별칭이 지금 가리키는 구체 모델 이름을 조회한다.

    ``client.models.get(alias)`` 가 돌려주는 실제 모델 이름(예: gemini-flash-latest →
    gemini-3.6-flash)을 반환한다. 이름이 별칭 그대로면 version 을 대신 쓴다. 해석하지 못한
    별칭은 결과에서 생략한다(키 없음/미지원이면 빈 dict — 라벨은 그냥 '최신'으로 남는다).
    """
    if not is_available():
        return {}
    try:
        client = _new_client()
    except Exception:
        return {}
    out: dict[str, str] = {}
    for alias in aliases:
        try:
            m = client.models.get(model=alias)
        except Exception:
            continue
        name = (getattr(m, "name", "") or "").split("/")[-1]
        if name and name != alias:
            out[alias] = name
        elif getattr(m, "version", None):
            out[alias] = str(m.version)
    return out


class GeminiClient:
    """``client.models.generate_content`` 를 :class:`~yke.llm.LLMClient` 형태로 감싼다."""

    def __init__(self) -> None:
        if not genai_available():
            raise RuntimeError(
                "google-genai 패키지를 찾을 수 없습니다. `uv sync` 로 의존성을 설치하세요."
            )
        key = get_gemini_api_key()
        if not key:
            raise RuntimeError(
                "Gemini API 키가 설정되지 않았습니다. Google AI Studio"
                "(https://aistudio.google.com/apikey)에서 키를 발급받아 설정에 입력하세요."
            )
        from google import genai

        self._client = genai.Client(api_key=key)

    def complete(self, system: str, user: str, *, model: str) -> str:
        """단일 턴 completion. system 은 system_instruction 으로, user 는 contents 로 보낸다."""
        from google.genai import types

        try:
            resp = self._client.models.generate_content(
                model=_resolve_model(model),
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    # 지식 추출·통합·자막 보정 모두 사실 충실도가 중요하므로 낮은 온도.
                    temperature=0.2,
                ),
            )
        except Exception as exc:
            raise RuntimeError(f"Gemini 호출 실패: {type(exc).__name__}: {exc}") from exc

        text = resp.text or ""
        if not text:
            # 안전 필터/토큰 초과 등으로 빈 응답이 오면 사유를 붙여 실패로 올린다(호출부가
            # 청크 단위로 건너뛰거나 경고할 수 있게).
            reason = ""
            try:
                cand = (resp.candidates or [None])[0]
                fr = getattr(cand, "finish_reason", None)
                if fr is not None:
                    reason = f" (finish_reason={fr})"
            except Exception:
                pass
            raise RuntimeError(f"Gemini 응답이 비어 있습니다{reason}.")
        return text
