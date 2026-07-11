"""stage3_stt 의 device/compute_type 확정 로직(_resolve) 테스트.

CUDA 하드웨어 없이 검증하려고 _cuda_available 을 몽키패치한다.
"""

from __future__ import annotations

import unittest
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
    def test_large_models_are_capped(self):
        # large-v3 는 8GB VRAM 에서 batch16 이 스래싱하므로 안전 상한(4)까지만 사용
        self.assertEqual(stage3_stt._effective_batch_size("large-v3", 16), 4)
        self.assertEqual(stage3_stt._effective_batch_size("large-v2", 8), 4)

    def test_below_cap_is_left_alone(self):
        # 상한보다 작은 값은 그대로(더 낮추지 않음)
        self.assertEqual(stage3_stt._effective_batch_size("large-v3", 2), 2)

    def test_small_models_uncapped(self):
        # 가벼운 모델은 상한이 없어 요청값 그대로 — batch16 유지로 처리량 확보
        self.assertEqual(stage3_stt._effective_batch_size("medium", 16), 16)
        self.assertEqual(stage3_stt._effective_batch_size("small", 16), 16)


if __name__ == "__main__":
    unittest.main()
