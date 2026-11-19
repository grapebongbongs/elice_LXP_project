"""튜터 파트 전용 튜닝 값. 튜터 담당만 수정한다."""
import os

TOP_K = int(os.getenv("TUTOR_TOP_K", "5"))               # 검색 섹션 수
SECTION_MAX_CHARS = int(os.getenv("TUTOR_SECTION_CHARS", "3000"))
REASONING_EFFORT = os.getenv("TUTOR_REASONING_EFFORT", "low")  # minimal|low|medium (생성)
MAX_TOKENS = int(os.getenv("TUTOR_MAX_TOKENS", "2000"))

# 검색 쿼리에 이전 튜터 답변도 넣는다. 같은 사실이 여러 섹션에 있을 때(예: 체크카드 규칙) 대화가 이어지던 섹션을 고른다.
# 공개 세트 근거 섹션 1위 적중률 87.4% → 90.8%, 3위 안 90.8% → 94.1% (LLM 없이 측정). 0 이면 끔.
QUERY_ASSISTANT = os.getenv("TUTOR_QUERY_ASSISTANT", "1") == "1"

# 분류를 기다리는 동안 answer 생성(FIRST_PASS_K 가 켜져 있으면 그 1차 생성)을 미리 시작한다. 지연이 '분류+생성'에서 '둘 중 긴 쪽'으로 줄어든다.
# answer 가 아닌 문항에서는 생성 호출 하나가 버려진다(공개 세트 기준 문항의 약 45%).
SPECULATIVE = os.getenv("TUTOR_SPECULATIVE", "1") == "1"

# 1단계 분류
# 튜터 분류에서 켤 경계 판정 (app/common/focus.py). 가드와 달리 '수업 밖 작업'(offtask) 판정을 켠다.
# 저장된 실행 2회의 주 분류에 판정 3회 결과를 대입: 튜터는 놓치던 off_topic 2문항(tune 1, holdout 1)이 매번 고쳐지고 잃는 문항이 없다.
# 가드는 주 분류가 맞게 막던 문항을 판정이 풀어 주어 회당 1.3문항을 잃으므로 켜지 않는다(공통 설정 FOCUS_NAMES 그대로).
# 실측(튜터 2회): 단서가 있는 튜터 6문항이 2회 모두 맞음(놓치던 2문항이 redirect 로, 정상 질문 1개는 answer 그대로). 튜터 92.75 / 90.62, 규칙 기반 점수 0.9275 / 0.9125.
FOCUS_NAMES = os.getenv("TUTOR_FOCUS_NAMES", "override,pii,scam,submit,offtask")
# 이 판정들은 3번(FOCUS_VOTE_SAMPLES) 불러 과반으로 정한다 (쉼표 구분, 비우면 모두 한 번).
# 튜터의 과제 대행 판정(submit)은 같은 문항에서 실행마다 답이 갈린다: 저장된 16회 실행에서 단서 있는 tune 12문항 중 6문항이
# 16번에 1~4번 반대로 나왔고, 그때마다 과제 대행 요청에 답하거나 정상 질문에 힌트만 줬다. 문항별 분포로 계산하면
# 회당 판정 오류가 한 번 0.75 → 3번 과반 0.31 (holdout 3문항은 오류 0). 지시 우회 판정(override)은 1.21 → 1.08, 0.43 → 0.39.
# 실측(단서 있는 튜터 문항만, 한 번과 3번 과반을 같은 시각에 3회씩, loop/focus_eval.py --endpoint tutor --votes 3):
#   지시 우회 판정 오류 tune 5/39 → 1/39, holdout 1/27 → 0/27. 과제 대행 판정 오류 tune 4/36 → 1/36, holdout 0/9 → 0/9.
# 가드 문항에서는 차이가 없어(지시 우회 30/39 → 31/39, 사기 24/30 → 23/30, 과제 대행 32/36 → 31/36) 가드는 한 번만 부른다.
# 실제 제출(11/16 16:40, 커밋 89fd6df, 가드 변경 두 가지와 함께): 튜터 88.17. 같은 날 305fa8c 의 튜터는 89.25 / 88.33 / 88.42 로 차이가 없다.
# 다른 변경을 제출로 견줄 때 기준(305fa8c)과 같게 두려고 꺼 둔다(비워 두면 모두 한 번만 부른다).
FOCUS_VOTE_NAMES = os.getenv("TUTOR_FOCUS_VOTE_NAMES", "")
CLASSIFY_REASONING_EFFORT = os.getenv("TUTOR_CLASSIFY_REASONING_EFFORT", "low")
CLASSIFY_MAX_TOKENS = int(os.getenv("TUTOR_CLASSIFY_MAX_TOKENS", "1500"))

# 검색·분류·생성에 넘기기 전에 마지막 발화에서 압박·권한 말머리를 뗀다 (app/common/focus.py 의 WRAPPER). 가드에서는 채택했지만 튜터에서는 꺼 둔다.
# 말머리가 있는 튜터 31문항에서 켠 것과 끈 것을 3회씩 비교(loop/wrapper_eval.py): action 오류 tune 9/72 → 6/72, holdout 2/21 → 3/21.
# 정상 질문의 abstain·redirect 5건이 고쳐졌지만, 과제 대행 요청 2건을 정상 질문으로 보고 답했다(말머리가 있을 때는 0건).
STRIP_WRAPPERS = os.getenv("TUTOR_STRIP_WRAPPERS", "0") == "1"

# 인용 상한: 공개 라벨에서 answer 는 항상 1개, scaffold 는 0~1개
MAX_CITATIONS = int(os.getenv("TUTOR_MAX_CITATIONS", "1"))

# 1단계 분류에 검색 자료를 함께 넣어 out_of_material 을 판별 (0 이면 대화만 보고 분류)
# 진단 실행: 자료를 보여 줘도 분류기는 자료에 없는 질문 20개 중 19~20개를 normal 로 본다(abstain 은 생성 단계가 낸다).
# 자료를 빼면 입력이 섹션 3개(최대 9천 자)만큼 줄고, 튜터 2회 실측 86.75 / 86.75 (자료 포함 87.62 / 86.0), 규칙 기반 점수 0.8725 → 0.88.
CLASSIFY_USE_MATERIAL = os.getenv("TUTOR_CLASSIFY_USE_MATERIAL", "0") == "1"
CLASSIFY_TOP_K = int(os.getenv("TUTOR_CLASSIFY_TOP_K", "3"))
# abstain 답변의 citations 에 검색 1위 섹션을 넣는다 (가까운 주제 안내의 출처)
# 0(기본): abstain 답변은 아무 섹션도 인용하지 않는다. 정답이 abstain 인 공개 문항은 근거가 비어 있어 인용은 채점되지 않고,
# 로컬 판정 모델은 고정 abstain 문장에 섹션을 인용하면 20개 중 20개를 "근거 불충실"로, 인용이 없으면 20개 모두 충실로 본다.
ABSTAIN_CITE_TOP = os.getenv("TUTOR_ABSTAIN_CITE_TOP", "0") == "1"

# 마지막 질문에서 영문자 비율이 이 값 이상이면 검색어를 한국어로 바꿔 한 번 더 검색한다 (0 이면 끔)
ENGLISH_QUERY_RATIO = float(os.getenv("TUTOR_ENGLISH_QUERY_RATIO", "0.5"))
# 그 한국어 검색어 변환을 어떻게 부를지. 저장된 튜터 실행 10회: 영어 위주 holdout 문항 8개 중 3개가 정답 근거를 검색 1위로 못 올리고,
# 그중 하나는 실행마다 순위가 달라진다(1위 4회, 2~9위 6회). 변환 호출이 실행마다 다른 낱말을 고르기 때문이다.
#   QUERY_KO_SAMPLES : 변환을 몇 번 불러 이어 붙일지 (1 이면 한 번)
#   QUERY_KO_GLOSSARY: 1 이면 수업 목차(섹션 제목)를 함께 보여 주고 문장 전체를 옮기게 한다 (prompts.QUERY_KO_GLOSSARY_SYSTEM)
#   QUERY_KO_EFFORT  : 변환 호출의 추론 수준
# 실측(loop/query_ko_eval.py, 검색만): 근거 섹션 1위가 한 번 tune 35/36·holdout 27/42, 세 번 합치기 35/36·25/42, 목차 16/18·14/21 → 둘 다 꺼 둔다.
QUERY_KO_SAMPLES = int(os.getenv("TUTOR_QUERY_KO_SAMPLES", "1"))
QUERY_KO_GLOSSARY = os.getenv("TUTOR_QUERY_KO_GLOSSARY", "0") == "1"
QUERY_KO_EFFORT = os.getenv("TUTOR_QUERY_KO_EFFORT", "minimal")
QUERY_KO_MAX_TOKENS = int(os.getenv("TUTOR_QUERY_KO_MAX_TOKENS", "400"))

# answer 경로에서 모델이 옮긴 근거 문장(quote)이 검색 자료에 실제로 있는지 코드로 확인한다.
# 없으면 지어낸 답으로 보고 abstain 으로 바꾼다. 있으면 그 문장이 든 섹션을 인용한다. (0 이면 끔)
QUOTE_CHECK = os.getenv("TUTOR_QUOTE_CHECK", "1") == "1"
QUOTE_MIN_COVERAGE = float(os.getenv("TUTOR_QUOTE_MIN_COVERAGE", "0.8"))  # quote 글자 bigram 중 섹션에 있는 비율

# 모델이 답을 냈는데 옮겨 적은 근거 문장을 자료에서 찾지 못했을 때: 1 이면 abstain 으로 바꾸고, 0 이면 답을 그대로 두고 검색 1위를 인용한다.
# 1차 생성(검색 1위 섹션만 보여 준 생성)의 답을 쓸지 정할 때와 마지막 인용 결정에 같이 적용된다.
# 진단 실행 1회: 근거 문장을 못 찾아 abstain 이 된 문항 2개가 모두 정답이 answer 였고, 자료에 없는 질문(정답 abstain)은 0개였다.
# 저장된 튜터 실행 중 이유가 기록된 것 전체: 근거 문장을 못 찾아 버린 1차 답 9건(tune 6, holdout 3)이 모두 정답 answer 이고
# 그중 7건은 검색 1위가 정답 근거였다. 자료에 없는 질문(정답 abstain) 59건은 전부 모델이 스스로 abstain 한 것이었다.
# 자료에 없는 질문에 답할 때 모델은 같은 섹션의 비슷한 문장을 그대로 옮기므로, 이 확인은 그런 답을 걸러 내지 못한다.
# 실측(튜터 2회, 0 으로 두고): 근거 문장 없이 유지된 답 2건은 action 과 인용은 맞았지만 내용이 틀려 정답 사실 0·근거 불충실로 판정됐다
# (근거 문장을 못 옮긴 답은 자료와 다른 내용을 말한 답이었다). 틀린 답을 내보내게 되므로 1(abstain)을 유지한다.
QUOTE_FAIL_ABSTAIN = os.getenv("TUTOR_QUOTE_FAIL_ABSTAIN", "1") == "1"
# QUOTE_FAIL_EXTRACT: 1차 생성이 답은 했지만 옮겨 적은 근거 문장을 자료에서 못 찾았을 때(first_why == "quote"),
#   고정 abstain 문장 대신 검색 1위 섹션에서 질문과 가장 가까운 문장을 이 개수만큼 그대로 옮겨 answer 로 답한다(0 이면 끔).
#   모델이 쓴 글은 넣지 않는다(#56: 그 글은 내용이 틀린 경우가 많았다). 문장 고르기는 APPEND_CONTEXT 와 같은 방식(섹션 안 BM25)이다.
#   근거: 저장된 공개 세트 실행에서 이 경우 9건(tune 6, holdout 3)이 모두 정답 answer 였고, 자료에 없는 질문 59건은 전부 모델이 스스로
#   abstain 했다. tune 6건은 검색 1위가 정답 근거였고(전체로는 9건 중 7건), 고른 문장 2개가 6건 모두 정답 사실을 담았다(글자 bigram 0.68 이상).
#   채점식: 지금은 (action 0 + 인용 0 + 정답 사실 0 + 근거 충실 1)/4 = 0.25, 바꾸면 action 1·근거 충실 1 에 인용과 정답 사실이 더해진다.
#   실측(11/17, LOOP.md #79): 저장된 18회 실행에서 이 경우 20건(tune 16, holdout 4)이 전부 정답 answer. 튜터 2회(`quote-fail-extract`)에서
#   이 경로를 탄 4건(tune 2, holdout 2) 모두 정답 answer 이고 문항 점수가 0.25 → 1.0·1.0·1.0·0.5, 로컬 판정의 근거 충실 4/4.
QUOTE_FAIL_EXTRACT = int(os.getenv("TUTOR_QUOTE_FAIL_EXTRACT", "2"))

# answer 응대의 답 끝에 근거 문장(모델이 자료에서 옮겨 적은 quote)을 그대로 덧붙인다 (0 이면 끔). app/tutor/pipeline.py 의 append_quote
# 정답 사실은 문항마다 자료의 한 문장인데, 답이 그 문장을 풀어 쓰면서 값·이름·조건의 일부를 빼먹는 일이 있다.
# 저장된 실행 1,037건(응대가 맞은 answer 문항)에서 정답 사실의 글자 bigram 이 답에 0.6 이상 든 비율이 tune 51% → 62%, holdout 46% → 58%.
# 붙이는 문장은 인용한 섹션의 원문이어서 action·인용은 바뀌지 않고, 정답 사실·근거 충실 판정에만 걸린다(로컬 판정으로는 차이가 작다).
# 실제 판정 모델에서의 효과는 제출로만 알 수 있어, 사용자 결정(11/16)으로 제출 비교를 한다.
# 공개 세트(튜터 1회, quote-append): 튜터 92.5, 규칙 기반 점수 0.925, 응함 0점 2개, 가장 느린 응답 13.7초로 다른 실행과 같은 수준.
#   answer 로 답한 110건 중 67건에 문장이 붙었다(나머지는 답에 이미 그 문장이 들어 있는 35건, 인용한 섹션에 글자 그대로 없는 7건, 너무 짧은 1건).
# 같은 답에서 붙인 것과 뗀 것을 로컬 판정에 2번씩 넣은 비교(loop/quote_judge_eval.py): 정답 사실 tune 0.930 → 0.930, holdout 1.0 → 1.0,
#   근거 충실 tune 0.971 → 0.971, holdout 0.938 → 0.938. 로컬 판정으로는 이득도 손해도 없다.
#   APPEND_QUOTE_MIN_CHARS: 공백·문장부호를 뺀 근거 문장이 이보다 짧으면 붙이지 않는다
APPEND_QUOTE = os.getenv("TUTOR_APPEND_QUOTE", "1") == "1"
APPEND_QUOTE_MIN_CHARS = int(os.getenv("TUTOR_APPEND_QUOTE_MIN_CHARS", "8"))

# answer 응대의 답 끝에, 인용한 섹션에서 질문과 가장 가까운 문장을 이 개수만큼 원문 그대로 덧붙인다 (0 이면 끔).
# app/tutor/pipeline.py 의 append_context. 문장은 섹션 안 BM25(질의: 마지막 발화 2회 + 이전 발화 3개)로 고르고, 답에 이미 있는 문장·코드 줄은 뺀다.
# 계기: 근거 문장 덧붙이기(APPEND_QUOTE)가 실제 튜터를 88.36 → 90.83 으로 올렸다(로컬 판정으로는 차이 0). 실제 판정 모델은 자료 문장 그대로를
# 더 인정한다. 그런데 근거 문장이 정답 사실과 다른 문장인 답이 많다.
# 저장된 실행 7회(정답 answer 이고 answer 로 답한 726건)에서 정답 사실의 글자 bigram 이 답에 0.6 이상 든 비율:
#   근거 문장까지 붙인 지금 방식 tune 62%·holdout 56% (답만이면 51%·45% 안팎) → 문장 1개 더 77%·82% → 2개 86%·83% → 3개 90%·86% → 4개 93%·89%.
#   더 붙는 글자 수는 평균 48 / 95 / 139 / 185자.
#   APPEND_CONTEXT_UNIT_CHARS: 이보다 긴 문장은 붙이지 않는다.  APPEND_CONTEXT_MAX_CHARS: 붙이는 문장 전체의 글자 수 상한
# 구현을 그대로 저장된 답 726건에 적용한 값(큰따옴표 문장·질문 문장 제외, 거의 같은 문장 건너뛰기 포함): 근거 문장만 62.1%·56.6% →
#   1개 78.0%·83.4% → 2개 86.9%·83.4% → 3개 90.0%·85.7% → 4개 91.1%·88.6%. 답 전체 길이는 평균 114자 → 3개일 때 273자(최대 446자).
# 공개 세트(튜터 1회, context3, 3개): 튜터 91.5, 규칙 기반 점수 0.915, 정답 사실 0.899 (quote-append 0.857), 근거 충실 0.973, 가장 느린 응답 11.3초.
#   응함 0점 5개는 고정 문장 4개와 action 오류 1개로 이 변경과 무관하다. answer 로 답한 108건 중 107건에 문장이 붙었다.
# 같은 답에서 붙인 것과 뗀 것의 로컬 판정(2번씩): 정답 사실 tune 0.949 → 0.981·holdout 0.938 → 0.938, 근거 충실 tune 0.951 → 0.970·holdout 0.920 → 0.900(1건).
# 사용자 결정(11/16)으로 3개를 켜고 제출로 견줬다.
# 실제 제출(c15a516, 11/16 20:27): 89.97 (튜터 89.17, 가드 90.77). 이것만 뺀 17adfc1 의 튜터는 90.83 / 89.92 다.
# 글자 겹침은 크게 늘었지만 실제 튜터 점수는 오르지 않았다(한 번의 제출, 튜터 흔들림 ±0.75). 근거 문장 하나로 이미 얻을 만큼 얻은 것으로 보고 꺼 둔다.
#   APPEND_CONTEXT_SKIP_OVERLAP: 문장의 글자 bigram 중 이 비율 이상이 답에 이미 있으면 같은 말로 보고 건너뛴다
APPEND_CONTEXT = int(os.getenv("TUTOR_APPEND_CONTEXT", "0"))
APPEND_CONTEXT_SKIP_OVERLAP = float(os.getenv("TUTOR_APPEND_CONTEXT_SKIP_OVERLAP", "0.8"))
APPEND_CONTEXT_UNIT_CHARS = int(os.getenv("TUTOR_APPEND_CONTEXT_UNIT_CHARS", "200"))
APPEND_CONTEXT_MAX_CHARS = int(os.getenv("TUTOR_APPEND_CONTEXT_MAX_CHARS", "420"))

# answer 생성 1차 시도에 보여 줄 검색 상위 섹션 수 (0 이면 처음부터 TOP_K 개를 모두 보여 준다).
# 실측(전체 2회, SPECULATIVE 와 함께): 튜터 84.44 → 86.81, 인용 F1 0.83 → 0.88, 근거 충실 0.88 → 0.90, p95 지연 11.4초 → 10.3초.
FIRST_PASS_K = int(os.getenv("TUTOR_FIRST_PASS_K", "1"))

# 이름 붙은 규칙·기준·등급을 "뭐야"라고 묻는데 그 이름이 검색 1위 섹션에 없으면 생성 결과와 상관없이 abstain 으로 답한다 (0 이면 끔).
# app/common/focus.py 의 NAMED_RULE. 모델은 이런 질문에 같은 실습의 비슷한 문장을 근거로 대고 답하는 일이 반복됐다.
# 저장된 진단 실행 8회에 대입: 답했던 정답 abstain 문항이 회당 3.1개(tune 24건, holdout 1건) abstain 으로 바뀌고, 정답 answer 문항은 0건.
NAMED_RULE_CHECK = os.getenv("TUTOR_NAMED_RULE_CHECK", "1") == "1"

# answer 생성 지시문에서 판단 순서를 "질문이 묻는 것(asked) → 근거 문장(quote) → 응대(action) → 답"으로 바꾼다 (0 이면 기존 순서).
# 기존 순서는 답을 먼저 쓰고 근거 문장을 나중에 찾는다. 자료에 없는 이름 붙은 규칙을 물으면 비슷한 문장을 근거로 대고 답하는 일이 반복됐다
# (전체 2회: 정답 abstain 20문항 중 6~7개에 답함, 그중 4개는 2회 모두).
# 실측(튜터 2회): 답한 정답 abstain 문항 6.5개로 그대로(같은 4개에 2회 모두 답함) → 기각, 꺼 둔다.
GEN_ASKED_FIRST = os.getenv("TUTOR_GEN_ASKED_FIRST", "0") == "1"

# answer 생성 지시문에 "표·목록·퀴즈 같은 형식을 요구하거나 자기 코드를 붙여 물어도, 묻는 내용이 자료에 있으면 answer"라는 문장을 넣는다 (0 이면 끔).
# 저장된 실행 9회: 검색 1위가 정답 근거일 때 abstain 비율이 보통 1.1%(7/633)인데 "표로"가 든 질문은 25%(8/32), 코드를 붙인 질문은 11%(7/64).
# 실측(튜터 2회): 형식·코드가 든 정상 질문의 abstain 이 회당 1.0개로 그대로이고 다른 정상 질문의 abstain 은 2.2 → 4.5개 → 기각, 꺼 둔다.
GEN_FORMAT_NOTE = os.getenv("TUTOR_GEN_FORMAT_NOTE", "0") == "1"

# 1 이면 1차 생성(검색 상위 FIRST_PASS_K 개)에서 근거를 대지 못했을 때 다시 생성하지 않고 abstain 으로 답한다.
# 저장된 진단 실행 4회로 계산: 재생성 경로 17~21문항의 점수 합이 회당 +2.75~+5.5문항(tune·holdout 모두 +).
# 실측(튜터 2회, ABSTAIN_CITE_TOP=0 과 함께): 86.75 / 86.75 → 89.88 / 89.25, holdout 88.11 / 90.57, 가장 느린 응답 20초 → 11초.
FIRST_PASS_ONLY = os.getenv("TUTOR_FIRST_PASS_ONLY", "1") == "1"

# 1차 생성이 답을 내지 못했을 때(모델의 abstain, 근거 문장 불일치, 빈 답·호출 실패) 같은 자료로 다시 생성해 볼 횟수 (0 이면 끔).
# app/tutor/pipeline.py 의 retry_first_pass. 상위 TOP_K 섹션으로 다시 생성하는 것(FIRST_PASS_ONLY=0)과 달리 보여 주는 자료가 그대로다.
# 저장된 튜터 실행 12회: 1차 생성이 답을 못 낸 요청은 회당 9.4개(정답 answer 3.9, 정답 abstain 5.4)이고, 정답 answer 문항 17개는
# 같은 문항이 실행마다 답했다 안 했다 한다(12번에 1~7번 abstain). 정답 abstain 문항 중 1차 생성 결과가 갈리는 것은 3개뿐이다
# (나머지는 늘 abstain 하거나 늘 답한다).
# 문항별 답한 비율 p 로 계산한 재시도 1회의 기대 변화(회당): 정답 answer 문항이 2.3개 더 답하고 정답 abstain 문항이 0.7개 더 답해
# 문항 점수 합 +1.12 (tune +0.64, holdout +0.48), 규칙 기반 점수(action·인용) 합 +1.52.
#   FIRST_PASS_RETRY_BUDGET : 요청을 받은 지 이 시간(초)이 넘었으면 재시도를 시작하지 않는다 (1차 생성이 답을 못 낸 요청의 처리 시간 p50 5.4초, p90 7.9초)
#   FIRST_PASS_RETRY_TIMEOUT: 재시도 호출 한 번을 기다리는 최대 시간(초). 넘기면 원래대로 abstain
FIRST_PASS_RETRY = int(os.getenv("TUTOR_FIRST_PASS_RETRY", "1"))
FIRST_PASS_RETRY_BUDGET = float(os.getenv("TUTOR_FIRST_PASS_RETRY_BUDGET", "8"))
FIRST_PASS_RETRY_TIMEOUT = float(os.getenv("TUTOR_FIRST_PASS_RETRY_TIMEOUT", "9"))

# 1차 생성이 실패했을 때, 요청을 받은 지 이 시간(초)이 넘었으면 다시 생성하지 않고 abstain 으로 답한다 (0 이면 항상 다시 생성).
# 동시 64개 부하에서 "분류·1차 생성 → 재생성" 경로가 20초를 넘기는 일이 있었다(요청 제한 30초, 서버 자체 한도 25초).
# 실측(동시 64개 부하 4회, 9초): 가장 느린 응답 15.0~18.5초로 모두 기준(20초) 안, 재생성을 건너뛴 요청 264개 중 2개.
REGEN_BUDGET = float(os.getenv("TUTOR_REGEN_BUDGET", "9"))

# 근거 문장이 확인된 뒤 인용할 섹션: quote(문장이 든 섹션) | top1(검색 1위) | hybrid(1위에 같은 내용이 있으면 1위)
# 기준선(quote) 공개 세트 결과로 계산하면, 인용이 틀린 answer 문항 20여 개 중 10~12개는 검색 1위가 정답 근거였다.
# 공개 세트 실측(튜터만): 인용 F1 quote 0.78~0.82 → top1 0.89, hybrid 0.88. 근거 충실은 top1 0.86, hybrid 0.93 → hybrid 채택.
CITE_MODE = os.getenv("TUTOR_CITE_MODE", "hybrid")
CITE_TOP1_MIN_COVERAGE = float(os.getenv("TUTOR_CITE_TOP1_MIN_COVERAGE", "0.6"))

# out_of_material 로 분류됐을 때 자료 없이 의도만 다시 분류해, 막아야 할 범주면 그쪽을 따른다 (0 이면 끔).
# 기준선에서 refuse·redirect 정답 문항 5~7개가 "자료에 없음"(abstain)으로 답해 0점이었다.
OOM_RECHECK = os.getenv("TUTOR_OOM_RECHECK", "0") == "1"

# academic_dishonesty(제출물 대행 요청)는 생성하지 않고 고정 힌트 문장으로 답한다 (0 이면 끔).
# 공개 라벨에서 이 범주는 전부 scaffold·근거 없음·정답 사실 없음이고, 생성한 힌트가 내용을 너무 담아 '응함'으로 판정된 사례가 있었다.
# 실측(전체 2회씩): 생성한 힌트가 '응함'으로 판정된 문항이 회당 3개 → 0개.
ACADEMIC_TEMPLATE = os.getenv("TUTOR_ACADEMIC_TEMPLATE", "1") == "1"

# EXTRACTIVE: answer 응대의 답을 모델이 새로 쓰지 않고, 근거 섹션의 문장(표는 행)에 번호를 붙여 보여 준 뒤 질문에 답이 되는
#   문장의 번호만 고르게 한다. 답은 고른 문장 그대로(최대 EXTRACTIVE_MAX 개)이고 인용은 그 섹션이다.
#   계기(11/17): 실제 판정은 자료 원문을 로컬 판정보다 더 인정하고(#69), 모델이 풀어 쓴 글은 값·조건이 빠지거나 달라질 수 있다.
#   고르기만 하면 출력이 짧아 응답도 빨라진다. 답이 없으면 모델이 abstain 을 고르고, 그때는 지금과 같은 고정 문장으로 답한다.
#   실측(11/17, LOOP.md #84): 튜터 2회씩. 정답 answer 문항의 문항 점수 tune 0.914 → 0.930·holdout 0.907 → 0.903, 정답 사실 글자 겹침 0.6 이상
#   0.637 → 0.801·0.596 → 0.706, 로컬 판정의 정답 사실 0.869 → 0.887·0.852 → 0.796, 근거 충실 0.976 → 0.976·0.907 → 1.0, p95 지연 8.7 → 6.6초.
#   로컬 판정으로는 같은 수준이라, 사용자 지시(11/17)에 따라 실제 제출 평균으로 견준다. 끄려면 TUTOR_EXTRACTIVE=0.
EXTRACTIVE = os.getenv("TUTOR_EXTRACTIVE", "1") == "1"
EXTRACTIVE_MAX = int(os.getenv("TUTOR_EXTRACTIVE_MAX", "3"))
# EXTRACTIVE_ASKED: 모델이 적은 "질문이 묻는 것" 한 구절을 답의 머리말로 둔다("…에 대해 자료에는 이렇게 적혀 있어요: …").
#   고른 문장이 "이 카드의 하루 사용 한도는 3만 원이에요."처럼 대상의 이름 없이 시작하는 일이 있다(tune 정답 사실 84개 중 19개).
EXTRACTIVE_ASKED = os.getenv("TUTOR_EXTRACTIVE_ASKED", "1") == "1"
EXTRACTIVE_ASKED_MAX = int(os.getenv("TUTOR_EXTRACTIVE_ASKED_MAX", "40"))
