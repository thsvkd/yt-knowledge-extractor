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


if __name__ == "__main__":
    unittest.main()
