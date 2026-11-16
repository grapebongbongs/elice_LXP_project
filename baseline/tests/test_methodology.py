"""크레딧을 쓰지 않는 파이프라인/취소/응답 규격 회귀 검증."""
import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from app.common import focus, llm
from app.common.kb import KnowledgeBase, Section
from app.guard import pipeline as guard
from app.tutor import pipeline as tutor


def messages(text):
    return [{"role": "user", "content": text}]


HITS = [(Section("PY-01-01", "python", "반복", "반복문은 같은 작업을 여러 번 실행해요."), 1.0),
        (Section("PY-01-02", "python", "조건", "조건문은 조건에 따라 실행해요."), 0.5)]


class Rules(unittest.TestCase):
    def test_low_signal(self):
        for text in ("??", "ㅋㅋㅋ", "   ", "😀"):
            self.assertTrue(focus.low_signal(messages(text)))
        for text in ("가", "a", "1", "for는?"):
            self.assertFalse(focus.low_signal(messages(text)))

    def test_literal_evidence(self):
        self.assertEqual(tutor.locate_quote(HITS[0][0].text, HITS), "PY-01-01")
        self.assertIsNone(tutor.locate_quote("반복문은 같은 작업을 여러 번 실행해요!", HITS))
        self.assertIsNone(tutor.locate_quote("반복문은 다른 작업을 여러 번 실행해요.", HITS))

    def test_append_only_verified_citation_once(self):
        quote = HITS[0][0].text
        answer = tutor.append_quote("설명이에요.", quote, ["PY-01-01"], HITS)
        self.assertIn(quote, answer)
        self.assertEqual(tutor.append_quote(answer, quote, ["PY-01-01"], HITS), answer)
        self.assertEqual(tutor.append_quote("설명", quote, ["PY-01-02"], HITS), "설명")
        self.assertEqual(tutor.append_quote("설명", "거짓 근거입니다.", ["PY-01-01"], HITS), "설명")

    def test_named_rule(self):
        self.assertTrue(tutor.missing_named_rule(messages('"달빛점수" 기준은 뭐야?'), HITS))
        self.assertFalse(tutor.missing_named_rule(messages("반복문 규칙은 뭐야?"), HITS))
        self.assertFalse(tutor.missing_named_rule(messages("어떤 규칙이 있어?"), HITS))

    def test_boundary_priority(self):
        self.assertEqual(focus.combine("normal", {"injection": "jailbreak_injection", "fraud": "normal"}), "jailbreak_injection")
        self.assertEqual(focus.combine("jailbreak_injection", {"injection": "normal"}), "normal")
        self.assertEqual(focus.combine("emotional_distress", {"offtask": "off_topic"}), "emotional_distress")
        self.assertEqual(focus.combine("harmful_content", {"privacy": "normal"}), "harmful_content")

    def test_votes_shortest_and_majority(self):
        votes = [dict(grounded=False, unsupported=["긴 근거 없는 구간"], safe=True),
                 dict(grounded=True, unsupported=[], safe=True),
                 dict(grounded=False, unsupported=["구간"], safe=True)]
        self.assertEqual(guard.combine_votes(votes)["unsupported"], ["구간"])
        self.assertFalse(guard.combine_votes(votes)["grounded"])


class Pipeline(unittest.IsolatedAsyncioTestCase):
    async def test_low_signal_skips_model(self):
        with patch.object(llm, "chat_json", new_callable=AsyncMock) as call:
            result = await tutor.handle({"messages": messages("ㅋㅋㅋ")}, None)
            self.assertEqual(result["action"], "clarify")
            self.assertEqual((await guard.check_input(messages("??")))["category"], "low_signal_input")
            call.assert_not_awaited()

    async def run_tutor(self, output, category="normal"):
        with patch.object(tutor, "retrieve", AsyncMock(return_value=HITS)), \
             patch.object(tutor, "classify", AsyncMock(return_value=(category, None))), \
             patch.object(tutor, "generate", AsyncMock(return_value=output)) as generate:
            result = await tutor.handle({"messages": messages("반복문을 설명해 주세요")}, None)
            return result, generate

    async def test_top1_no_retry(self):
        result, generate = await self.run_tutor(dict(answer="설명", action="answer", quote=HITS[1][0].text))
        self.assertEqual(result["action"], "abstain")
        self.assertEqual(generate.await_count, 1)
        self.assertEqual(len(generate.call_args.args[2]), 1)

    async def test_verified_quote_and_citation(self):
        result, _ = await self.run_tutor(dict(answer="같은 작업을 반복해요.", action="answer", quote=HITS[0][0].text))
        self.assertEqual(result["action"], "answer")
        self.assertEqual(result["citations"], ["PY-01-01"])
        self.assertIn(HITS[0][0].text, result["answer"])

    async def test_fixed_delegation(self):
        result, _ = await self.run_tutor(dict(answer="사용하면 안 됨"), "academic_dishonesty")
        self.assertEqual(result["action"], "scaffold")
        self.assertEqual(result["answer"], tutor.prompts.DELEGATION_HINT)

    async def test_refusal_cancels_speculation(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def generate(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def classify(*args):
            await started.wait()
            return "jailbreak_injection", None

        with patch.object(tutor, "retrieve", AsyncMock(return_value=HITS)), \
             patch.object(tutor, "classify", classify), patch.object(tutor, "generate", generate):
            result = await asyncio.wait_for(tutor.handle({"messages": messages("반복문 설명")}, None), 1)
        self.assertEqual(result["action"], "refuse")
        self.assertTrue(cancelled.is_set())

    async def test_output_majority_cancels_straggler(self):
        cancelled = asyncio.Event()
        calls = 0

        async def once(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            await asyncio.sleep(0)
            return dict(grounded=False, unsupported=["없는 값"], safe=True)

        with patch.object(guard, "_check_output_once", once):
            result = await asyncio.wait_for(guard.check_output([], "없는 값입니다.", None), 1)
        self.assertFalse(result["grounded"])
        self.assertTrue(cancelled.is_set())

    async def test_failed_output_not_a_positive_vote(self):
        results = [None, dict(grounded=True, unsupported=[], safe=True),
                   dict(grounded=False, unsupported=["없는 값"], safe=True)]
        with patch.object(guard, "_check_output_once", AsyncMock(side_effect=results)):
            result = await guard.check_output([], "없는 값입니다.", None)
        self.assertFalse(result["grounded"])

    async def test_hedge_returns_valid_response_and_cleans_up(self):
        cancelled = asyncio.Event()

        async def call(*args, **kwargs):
            if kwargs["tag"].endswith(".hedge"):
                self.assertEqual(kwargs["reasoning_effort"], "low")
                return {"category": "normal"}
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(llm.config, "LLM_MOCK", False), patch.dict(os.environ, {"LLM_HEDGE_AFTER": "0.01"}), \
             patch.object(llm, "_chat_json", call):
            result = await asyncio.wait_for(llm.chat_json("", "", tag="guard.input", reasoning_effort="medium"), 1)
        self.assertEqual(result, {"category": "normal"})
        self.assertTrue(cancelled.is_set())

    async def test_64_concurrent_schema_requests(self):
        from app.main import app, lifespan
        from app.common.schema import GUARD_CATEGORIES, TUTOR_ACTIONS
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                async def one(i):
                    part = "tutor" if i % 2 == 0 else "guard"
                    response = await client.post("/" + part, json={"messages": messages("반복문이란?"),
                                                                 "candidate_answer": "같은 작업을 반복합니다."})
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    if part == "tutor":
                        self.assertIn(body["action"], TUTOR_ACTIONS)
                    else:
                        self.assertIn(body["input"]["category"], GUARD_CATEGORIES)
                        self.assertIsInstance(body["output"]["grounded"], bool)
                await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(64))), 20)


if __name__ == "__main__":
    unittest.main()
