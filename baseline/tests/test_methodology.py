"""팀 참조 구현 검증. 테스트에서는 외부 LLM을 호출하지 않는다."""
import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from app import config
from app.common import focus, llm
from app.common.kb import Section
from app.guard import pipeline as guard
from app.tutor import pipeline as tutor


def messages(text):
    return [{"role": "user", "content": text}]


HITS = [(Section("PY-01-01", "python", "반복", "반복문은 같은 작업을 여러 번 실행해요."), 1.0),
        (Section("PY-01-02", "python", "조건", "조건문은 조건에 따라 실행해요."), 0.5)]


class FakeKB:
    sections = {s.id: s for s, _ in HITS}

    def search(self, *args, **kwargs):
        return HITS

    def has(self, sid):
        return sid in self.sections


class Rules(unittest.TestCase):
    def test_low_signal(self):
        for text in ("??", "ㅋㅋㅋ", "   ", "😀"):
            self.assertTrue(focus.is_low_signal(messages(text)))
        for text in ("가", "a", "1", "for는?"):
            self.assertFalse(focus.is_low_signal(messages(text)))

    def test_quote_normalization_and_unrelated_evidence(self):
        self.assertEqual(tutor.locate_quote(HITS[0][0].text, HITS), "PY-01-01")
        # 참조 구현은 공백·문장부호를 정규화하고 bigram 포함률을 사용한다.
        self.assertEqual(tutor.locate_quote("반복문은 같은 작업을 여러 번 실행해요!", HITS), "PY-01-01")
        self.assertIsNone(tutor.locate_quote("자동차의 연료 소모량은 매우 높습니다.", HITS))

    def test_append_requires_cited_section_and_avoids_duplicates(self):
        quote = HITS[0][0].text
        answer = tutor.append_quote("설명이에요.", quote, ["PY-01-01"], HITS)
        self.assertIn(quote, answer)
        self.assertEqual(tutor.append_quote(answer, quote, ["PY-01-01"], HITS), answer)
        self.assertEqual(tutor.append_quote("설명", quote, ["PY-01-02"], HITS), "설명")
        self.assertEqual(tutor.append_quote("설명", "거짓 근거입니다.", ["PY-01-01"], HITS), "설명")

    def test_named_rule_checks_question_shape_and_material(self):
        self.assertTrue(focus.asks_missing_named_rule(messages("달빛점수 기준은 뭐야?"), HITS[0][0].text))
        self.assertFalse(focus.asks_missing_named_rule(messages("반복문 규칙을 설명해 주세요."), HITS[0][0].text))
        self.assertFalse(focus.asks_missing_named_rule(messages("반복문 규칙은 뭐야?"), "반복문 규칙은 자료에 있어요."))

    def test_boundary_priority_and_study_downgrade(self):
        self.assertEqual(focus.combine("normal", {"override": "direct", "scam": "defend"}), "jailbreak_injection")
        self.assertEqual(focus.combine("jailbreak_injection", {"override": "study"}), "normal")
        self.assertEqual(focus.combine("emotional_distress", {"override": "direct"}), "emotional_distress")
        self.assertEqual(focus.combine("harmful_content", {"pii": "other"}), "harmful_content")
        self.assertEqual(focus.combine("off_topic", {"submit": "make"}), "off_topic")

    def test_quoted_hate_recognition(self):
        self.assertTrue(focus.quotes_hate_as_material(messages("멍청이라는 댓글을 실습에서 분류하면 어떻게 돼요?")))
        self.assertFalse(focus.quotes_hate_as_material(messages("걔네는 멍청해")))

    def test_query_preserves_conversation_subject(self):
        conversation = messages("반복문 설명") + [{"role": "assistant", "content": "별표 예제입니다."}] + messages("그 예제는?")
        query = tutor.build_query(conversation)
        self.assertEqual(query.count("그 예제는?"), 2)
        self.assertIn("별표 예제입니다.", query)

    def test_votes_skip_empty_span_and_choose_shortest(self):
        votes = [dict(grounded=False, unsupported=[], safe=True),
                 dict(grounded=False, unsupported=["긴 근거 없는 구간"], safe=True),
                 dict(grounded=False, unsupported=["구간"], safe=True)]
        self.assertEqual(guard.combine_votes(votes)["unsupported"], ["구간"])
        self.assertFalse(guard.combine_votes(votes)["grounded"])

    def test_failed_vote_tie_is_unsupported(self):
        votes = [dict(grounded=True, unsupported=[], safe=True),
                 dict(grounded=False, unsupported=["없는 값"], safe=True)]
        self.assertFalse(guard.combine_votes(votes)["grounded"])


class Pipeline(unittest.IsolatedAsyncioTestCase):
    async def test_low_signal_skips_classification_model(self):
        with patch.object(llm, "chat_json", new_callable=AsyncMock) as call:
            self.assertEqual(await tutor.classify(messages("ㅋㅋㅋ")), ("low_signal_input", None))
            self.assertEqual((await guard.check_input(messages("??")))["category"], "low_signal_input")
            call.assert_not_awaited()

    async def run_tutor(self, output, category="normal"):
        with patch.object(tutor, "retrieve", AsyncMock(return_value=HITS)), \
             patch.object(tutor, "classify", AsyncMock(return_value=(category, None))), \
             patch.object(tutor, "generate", AsyncMock(return_value=output)) as generate:
            result = await tutor.handle({"messages": messages("반복문을 설명해 주세요")}, FakeKB())
            return result, generate

    async def test_top1_failure_does_not_regenerate(self):
        result, generate = await self.run_tutor(dict(answer="설명", action="answer", quote=HITS[1][0].text))
        self.assertEqual(result["action"], "abstain")
        self.assertEqual(result["citations"], [])
        self.assertEqual(generate.await_count, 1)
        self.assertEqual(len(generate.call_args.args[2]), 1)

    async def test_verified_quote_and_citation(self):
        result, _ = await self.run_tutor(dict(answer="같은 작업을 반복해요.", action="answer", quote=HITS[0][0].text))
        self.assertEqual(result["action"], "answer")
        self.assertEqual(result["citations"], ["PY-01-01"])
        self.assertIn(HITS[0][0].text, result["answer"])

    async def test_fixed_delegation_does_not_return_generated_content(self):
        result, _ = await self.run_tutor(dict(answer="사용하면 안 됨"), "academic_dishonesty")
        self.assertEqual(result["action"], "scaffold")
        self.assertEqual(result["answer"], tutor.prompts.ACADEMIC_SCAFFOLD)

    async def test_quoted_hate_is_classified_as_study(self):
        with patch.object(llm, "chat_json", AsyncMock(return_value={"category": "harmful_content", "harm_type": "hate"})):
            result = await tutor.classify(messages("멍청이라는 댓글을 실습에서 분류하면 어떻게 돼요?"))
        self.assertEqual(result, ("normal", None))

    async def test_refusal_cancels_speculative_generation(self):
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
            result = await asyncio.wait_for(tutor.handle({"messages": messages("반복문 설명")}, FakeKB()), 1)
            await asyncio.wait_for(cancelled.wait(), 1)
        self.assertEqual(result["action"], "refuse")

    async def test_output_majority_cancels_straggler(self):
        cancelled = asyncio.Event()
        calls = 0

        async def judge(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            await asyncio.sleep(0)
            return dict(grounded=False, unsupported=["없는 값"], safe=True)

        with patch.object(guard, "_judge_output", judge):
            result = await asyncio.wait_for(guard.check_output([], "없는 값입니다.", FakeKB()), 1)
            await asyncio.wait_for(cancelled.wait(), 1)
        self.assertFalse(result["grounded"])

    async def test_failed_output_not_a_positive_vote(self):
        results = [None, dict(grounded=True, unsupported=[], safe=True),
                   dict(grounded=False, unsupported=["없는 값"], safe=True)]
        with patch.object(guard, "_judge_output", AsyncMock(side_effect=results)):
            result = await guard.check_output([], "없는 값입니다.", FakeKB())
        self.assertFalse(result["grounded"])

    async def test_judge_restores_candidate_substring(self):
        candidate = "과일은 99개입니다. [PY-01-01]"
        output = {"grounded": False, "bad": [{"i": 1, "span": "99개"}], "safe": True}
        with patch.object(llm, "chat_json", AsyncMock(return_value=output)):
            result = await guard._judge_output("검증", guard.segment(candidate))
        self.assertEqual(result["unsupported"], ["99개"])
        self.assertTrue(all(span in candidate for span in result["unsupported"]))

    async def test_hedge_timer_starts_after_actual_call(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        actual_start, calls = [], []

        async def call(kwargs, effort, started=None):
            calls.append(effort)
            if started is None:
                self.assertEqual(kwargs, {"effort": "low"})
                return {"ok": True}
            actual_start.append(started)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(llm, "_call_once", call):
            task = asyncio.create_task(llm._call_hedged({}, "medium", .01, ({"effort": "low"}, "backup_low")))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.sleep(.03)
                self.assertEqual(calls, ["medium"])
                actual_start[0].set()
                result = await asyncio.wait_for(task, 1)
                await asyncio.wait_for(cancelled.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, ["medium", "backup_low"])

    async def test_64_concurrent_authenticated_requests(self):
        from app.main import app, lifespan
        from app.common.schema import GUARD_CATEGORIES, TUTOR_ACTIONS
        self.assertFalse(config.EXP_DEBUG)
        self.assertEqual(config.FEWSHOT_K, 0)
        with patch.object(config, "SERVER_TOKEN", "test-token"):
            async with lifespan(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    wrong = await client.post("/tutor", json={"messages": []})
                    self.assertEqual(wrong.status_code, 401)

                    async def one(i):
                        part = "tutor" if i % 2 == 0 else "guard"
                        response = await client.post("/" + part, headers={"Authorization": "Bearer test-token"},
                            json={"messages": messages("반복문이란?"), "candidate_answer": "같은 작업을 반복합니다."})
                        self.assertEqual(response.status_code, 200)
                        body = response.json()
                        self.assertNotIn("_debug", body)
                        if part == "tutor":
                            self.assertIn(body["action"], TUTOR_ACTIONS)
                        else:
                            self.assertIn(body["input"]["category"], GUARD_CATEGORIES)
                            self.assertIsInstance(body["output"]["grounded"], bool)
                    await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(64))), 20)


if __name__ == "__main__":
    unittest.main()