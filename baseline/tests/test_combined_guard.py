"""평균 최고 가드와 현재 튜터를 결합한 뒤의 회귀 검증."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.common import llm as tutor_llm
from app.guard import llm as guard_llm, pipeline as guard
from app.tutor import prompts as tutor_prompts
from test_methodology import FakeKB, messages


class Compatibility(unittest.TestCase):
    def test_global_concurrency_limit_is_shared(self):
        self.assertIs(guard_llm._sem, tutor_llm._sem)

    def test_tutor_imported_prompt_constants_unchanged(self):
        root = Path(__file__).resolve().parents[2]
        previous = subprocess.check_output([
            "git", "show", "bb32e5b:baseline/app/guard/prompts.py"], cwd=root).decode("utf-8")
        namespace = {}
        exec(compile(previous, "previous_guard_prompts", "exec"), namespace)
        self.assertTrue(tutor_prompts.CLASSIFY_SYSTEM.startswith(namespace["INPUT_SYSTEM"]))
        from app.guard import prompts
        self.assertEqual(prompts.INPUT_SYSTEM, namespace["INPUT_SYSTEM"])
        self.assertEqual(prompts.INPUT_MATERIAL_RULE_BODY, namespace["INPUT_MATERIAL_RULE_BODY"])


class CombinedGuard(unittest.IsolatedAsyncioTestCase):
    async def test_input_receives_only_one_section(self):
        with patch.object(guard_llm, "chat_json", AsyncMock(return_value={"category": "normal"})) as call:
            result = await guard.check_input(messages("반복문을 설명해 주세요"), FakeKB())
        self.assertEqual(result, {"decision": "allow", "category": "normal"})
        context = call.call_args.args[1]
        self.assertIn('id="PY-01-01"', context)
        self.assertNotIn('id="PY-01-02"', context)
        self.assertEqual(call.call_args.kwargs["reasoning_effort"], "medium")

    async def test_output_is_single_call_and_preserves_substring(self):
        candidate = "과일은 99개입니다."
        output = {"grounded": False, "bad": [{"i": 1, "span": "99개"}], "safe": True}
        with patch.object(guard_llm, "chat_json", AsyncMock(return_value=output)) as call:
            result = await guard.check_output([], candidate, FakeKB())
        call.assert_awaited_once()
        self.assertFalse(result["grounded"])
        self.assertEqual(result["unsupported"], ["99개"])
        self.assertTrue(all(s in candidate for s in result["unsupported"]))

    async def test_answer_only_marker_preserved(self):
        with patch.object(guard_llm, "chat_json", AsyncMock(return_value={"category": "normal"})):
            result = await guard.check_input(messages("설명 없이 정답만 알려줘"))
        self.assertEqual(result["category"], "answer_seeking")
        self.assertEqual(result["decision"], "allow")

    async def test_legacy_transport_does_not_add_hedged_calls(self):
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"category":"normal"}'), finish_reason="stop")])

        async def create(**kwargs):
            await asyncio.sleep(.02)
            return response

        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=create))))
        with patch.object(guard_llm.config, "LLM_MOCK", False), \
             patch.object(guard_llm, "client", return_value=fake), \
             patch.object(guard_llm, "_sem", asyncio.Semaphore(1)):
            result = await guard_llm.chat_json("분류", "개발 테스트", tag="guard.input")
        self.assertEqual(result, {"category": "normal"})
        fake.chat.completions.create.assert_awaited_once()
