"""stage3_stt 의 device/compute_type 확정 로직(_resolve) 테스트.

CUDA 하드웨어 없이 검증하려고 _cuda_available 을 몽키패치한다.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from yke.pipeline import stage3_stt


class TestResolve(unittest.TestCase):
    def _resolve(self, device: str, compute_type: str, *, cuda: bool):
        with mock.patch.object(stage3_stt, "_cuda_available", return_value=cuda):
            return stage3_stt._resolve(device, compute_type)

    def test_auto_auto_on_gpu(self):
        # GPU 가 있으면 auto/auto → cuda/float16
        self.assertEqual(self._resolve("auto", "auto", cuda=True), ("cuda", "float16"))

    def test_auto_auto_on_cpu(self):
        # GPU 가 없으면 auto/auto → cpu/int8 (기존 CPU 동작 유지)
        self.assertEqual(self._resolve("auto", "auto", cuda=False), ("cpu", "int8"))

    def test_explicit_cpu_stays_int8(self):
        self.assertEqual(self._resolve("cpu", "auto", cuda=True), ("cpu", "int8"))

    def test_explicit_cuda_gets_float16(self):
        self.assertEqual(self._resolve("cuda", "auto", cuda=False), ("cuda", "float16"))

    def test_explicit_compute_type_is_respected(self):
        # 사용자가 명시한 compute_type 은 auto 확장을 거치지 않고 그대로 존중
        self.assertEqual(self._resolve("auto", "int8", cuda=True), ("cuda", "int8"))
        self.assertEqual(self._resolve("cuda", "int8_float16", cuda=True), ("cuda", "int8_float16"))
        self.assertEqual(self._resolve("cpu", "float16", cuda=False), ("cpu", "float16"))


class TestResolveModel(unittest.TestCase):
    def test_auto_on_gpu_is_large_v3(self):
        # GPU 는 최고 품질 large-v3 를 쓴다(고유명사·전문용어 정확). VRAM 안전 배치로 처리.
        self.assertEqual(stage3_stt._resolve_model("auto", "cuda"), "large-v3")

    def test_auto_on_cpu_is_small(self):
        # CPU 에서 large-v3 는 실시간(RTF~1.0)이라 실용 불가 → 균형점 small
        self.assertEqual(stage3_stt._resolve_model("auto", "cpu"), "small")

    def test_explicit_model_is_respected_on_any_device(self):
        # 명시 모델명은 장치와 무관하게 그대로 존중(느려도 사용자 선택)
        self.assertEqual(stage3_stt._resolve_model("large-v3", "cpu"), "large-v3")
        self.assertEqual(stage3_stt._resolve_model("tiny", "cuda"), "tiny")


class TestEffectiveBatchSize(unittest.TestCase):
    def test_large_models_are_capped_on_gpu(self):
        # large-v3 는 8GB VRAM 에서 batch16 이 스래싱하므로 안전 상한(4)까지만 사용
        self.assertEqual(stage3_stt._effective_batch_size("large-v3", "cuda", 16), 4)
        self.assertEqual(stage3_stt._effective_batch_size("large-v2", "cuda", 8), 4)

    def test_below_cap_is_left_alone(self):
        # 상한보다 작은 값은 그대로(더 낮추지 않음)
        self.assertEqual(stage3_stt._effective_batch_size("large-v3", "cuda", 2), 2)

    def test_small_models_uncapped_on_gpu(self):
        # GPU 에서 가벼운 모델은 상한이 없어 요청값 그대로 — batch16 유지로 처리량 확보
        self.assertEqual(stage3_stt._effective_batch_size("medium", "cuda", 16), 16)
        self.assertEqual(stage3_stt._effective_batch_size("small", "cuda", 16), 16)

    def test_cpu_is_capped_for_progress_granularity(self):
        # CPU 는 배치 하나가 통째로 끝나야 progress 콜백이 나오므로, 모델 크기와 무관하게
        # 작은 상한(4)을 적용해 진행바가 자주 갱신되게 한다(실측: RTF 손해는 ~12%뿐).
        self.assertEqual(stage3_stt._effective_batch_size("small", "cpu", 16), 4)
        self.assertEqual(stage3_stt._effective_batch_size("medium", "cpu", 16), 4)

    def test_cpu_cap_does_not_raise_already_small_requests(self):
        self.assertEqual(stage3_stt._effective_batch_size("small", "cpu", 2), 2)

    def test_cpu_large_model_uses_lower_of_both_caps(self):
        # large-v3 는 GPU 상한(4)과 CPU 상한(4)이 같은 값이라 결과는 동일하게 4.
        self.assertEqual(stage3_stt._effective_batch_size("large-v3", "cpu", 16), 4)


class TestTranscribeBatchedGate(unittest.TestCase):
    """실측(RTF ~2.6배 개선)에 근거해 batched 를 CPU 에서도 적용하는지 회귀 검증한다."""

    def _cfg(self, **overrides) -> SimpleNamespace:
        base = dict(
            model="small",
            device="cpu",
            compute_type="int8",
            batched=True,
            batch_size=16,
            word_timestamps=False,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_batched_applies_on_cpu(self):
        with (
            mock.patch.object(stage3_stt, "_get_model", return_value=object()),
            mock.patch.object(stage3_stt, "_run", return_value=[]) as run,
        ):
            stage3_stt.transcribe(Path("audio.webm"), "ko", self._cfg())
        self.assertTrue(run.call_args.kwargs["batched"])
        # CPU 는 진행률 콜백이 자주 나오도록 요청값(16)과 무관하게 4 로 상한이 걸린다.
        self.assertEqual(run.call_args.kwargs["batch_size"], 4)

    def test_batched_false_is_respected_on_cpu(self):
        with (
            mock.patch.object(stage3_stt, "_get_model", return_value=object()),
            mock.patch.object(stage3_stt, "_run", return_value=[]) as run,
        ):
            stage3_stt.transcribe(Path("audio.webm"), "ko", self._cfg(batched=False))
        self.assertFalse(run.call_args.kwargs["batched"])

    def test_cpu_fallback_recomputes_batch_size_for_fallback_model(self):
        # GPU 경로용으로 계산한 batch_size(medium/cuda 는 상한 없음 → 16)를 CPU 폴백에
        # 그대로 재사용하면 안 된다 — 폴백 모델(medium/cpu) 기준(상한 4)으로 다시 계산해야
        # progress 콜백이 자주 나온다는 요구를 폴백 경로에서도 지킨다.
        cfg = self._cfg(model="medium", device="cuda", batch_size=16)
        with (
            mock.patch.object(stage3_stt, "_get_model", side_effect=[RuntimeError("gpu fail"), object()]),
            mock.patch.object(stage3_stt, "_run", return_value=[]) as run,
        ):
            stage3_stt.transcribe(Path("audio.webm"), "ko", cfg)
        self.assertEqual(run.call_count, 1)  # GPU _get_model 실패 -> _run 은 CPU 폴백에서만 호출
        self.assertEqual(run.call_args.kwargs["batch_size"], 4)


class TestRunProgressCallback(unittest.TestCase):
    """_run 이 세그먼트 도착마다 on_progress(완료초, 전체초) 를 호출하는지 검증한다."""

    def test_on_progress_called_per_segment(self):
        seg1 = SimpleNamespace(start=0.0, end=2.0, text="hello")
        seg2 = SimpleNamespace(start=2.0, end=5.0, text="world")
        info = SimpleNamespace(duration=5.0)
        model = mock.Mock()
        model.transcribe.return_value = (iter([seg1, seg2]), info)
        cfg = self._cfg = SimpleNamespace(word_timestamps=False)

        calls: list[tuple[float, float]] = []
        result = stage3_stt._run(
            model, Path("a.webm"), "ko", cfg, on_progress=lambda d, t: calls.append((d, t))
        )
        self.assertEqual(calls, [(2.0, 5.0), (5.0, 5.0)])
        self.assertEqual([s.text for s in result], ["hello", "world"])


if __name__ == "__main__":
    unittest.main()
