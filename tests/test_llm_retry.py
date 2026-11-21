"""LLM 网关退避重试的承重测试。

背景：本地部署实测推理链**偶发 503**（网关瞬时故障），同一服务重跑即恢复。
没有重试时，一次抖动会让整个诊断链失败；重试策略写错则会掩盖真实错误。
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight import agent as agent_mod  # noqa: E402
from freight.agent import LLMClient, LLMError, _TransientLLM  # noqa: E402


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __iter__(self):
        payload = json.dumps({
            "choices": [{"message": {"content": "好的，这是答案。"}}]
        }).encode()
        yield b"data: " + payload + b"\n"
        yield b"data: [DONE]\n"


def _client() -> LLMClient:
    client = LLMClient.__new__(LLMClient)
    client.base = "http://fake.local/v1"
    client.model = "mimo-v2.6-pro"
    client.key = "sk-test"
    client.headers = {}
    client.timeout = 5
    client.max_tokens = 100
    client.step_thinking = False
    client.final_thinking = True
    client.thinking_budget = 64
    client.retry_attempts = 3
    client.retry_base = 0.01
    client.retry_max = 0.05
    return client


class TestRetry:
    def test_transient_503_is_retried(self, monkeypatch):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError(
                    "http://fake", 503, "Service Unavailable", {}, io.BytesIO(b"{}"))
            return _FakeResp()

        monkeypatch.setattr(agent_mod.urllib.request, "urlopen", fake_urlopen)
        out = _client().chat([{"role": "user", "content": "hi"}])
        assert "答案" in out
        assert calls["n"] == 3, "应在第 3 次成功"

    def test_auth_error_is_not_retried(self, monkeypatch):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                "http://fake", 401, "Unauthorized", {}, io.BytesIO(b"{}"))

        monkeypatch.setattr(agent_mod.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(LLMError) as exc:
            _client().chat([{"role": "user", "content": "hi"}])
        assert "401" in str(exc.value)
        assert calls["n"] == 1, "认证失败重试没有意义，必须立即抛出"

    def test_network_error_is_retried(self, monkeypatch):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            if calls["n"] < 2:
                raise urllib.error.URLError("connection reset")
            return _FakeResp()

        monkeypatch.setattr(agent_mod.urllib.request, "urlopen", fake_urlopen)
        out = _client().chat([{"role": "user", "content": "hi"}])
        assert out
        assert calls["n"] == 2

    def test_gives_up_after_budget(self, monkeypatch):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                "http://fake", 503, "Service Unavailable", {}, io.BytesIO(b"{}"))

        monkeypatch.setattr(agent_mod.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(LLMError):
            _client().chat([{"role": "user", "content": "hi"}])
        assert calls["n"] == 3, "重试次数应有上限，不能无限重试"

    def test_empty_response_is_retried(self, monkeypatch):
        calls = {"n": 0}

        class _Empty(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                yield b"data: [DONE]\n"

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            if calls["n"] < 2:
                return _Empty()
            return _FakeResp()

        monkeypatch.setattr(agent_mod.urllib.request, "urlopen", fake_urlopen)
        assert _client().chat([{"role": "user", "content": "hi"}])
        assert calls["n"] == 2

    def test_thinking_budget_is_smaller(self, monkeypatch):
        """长推理单次耗时高，默认少给一次重试，避免总时长翻倍。"""
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                "http://fake", 503, "Service Unavailable", {}, io.BytesIO(b"{}"))

        monkeypatch.setattr(agent_mod.urllib.request, "urlopen", fake_urlopen)
        client = _client()
        with pytest.raises(LLMError):
            client.chat([{"role": "user", "content": "hi"}], thinking=True)
        assert calls["n"] == 2, "thinking 模式默认 2 次（3-1）"
