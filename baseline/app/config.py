"""공통 설정. 파트별 튜닝 값은 app/tutor/config.py, app/guard/config.py에 둔다."""
import os
from pathlib import Path

# 런박스 기본 경로. 이 baseline 폴더를 /home/elicer/competition 아래에 두면
# ../kb 도 자동으로 찾는다.
_HERE = Path(__file__).resolve().parent.parent


def _first_existing(*cands: str) -> str:
    for c in cands:
        if c and Path(c).exists():
            return c
    return cands[0]


COMPETITION_DIR = os.getenv("COMPETITION_DIR") or _first_existing(
    "/home/elicer/competition", str(_HERE.parent)
)
KB_DIR = os.getenv("KB_DIR") or str(Path(COMPETITION_DIR) / "kb")

PUBLIC_DIR = os.getenv("PUBLIC_DIR") or _first_existing(
    str(Path(COMPETITION_DIR) / "public"), str(_HERE.parent / "public")
)
# 분류 프롬프트에 넣을 공개 세트 유사 예시 수 (0 이면 끔). app/common/fewshot.py
FEWSHOT_K = int(os.getenv("FEWSHOT_K", "5"))

# 서버 인증 토큰 (submission.json 의 token 과 같아야 함, LLM 키와 다른 값)
# 런박스 .env 에 TEAM_TOKEN 이라는 이름으로 적혀 있는 경우도 받는다
SERVER_TOKEN = os.getenv("SERVER_TOKEN") or os.getenv("TEAM_TOKEN", "")

# LLM
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "96"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "24"))
# 1 이면 응답에 "_debug"(분류 범주·검색 순위·근거 문장 위치)를 덧붙인다. 공개 세트 실험 서버(loop/exp_run.py --env EXP_DEBUG=1)에서만 켠다.
# 제출용 서버에서는 켜지 않는다(채점기는 규격에 없는 필드를 무시하지만, 채점 요청의 처리 내용을 밖으로 내보내지 않기 위해).
EXP_DEBUG = os.getenv("EXP_DEBUG", "0") == "1"

# 분류 보정 (app/common/focus.py)
# FOCUS_CHECKS: 단서가 있는 발화에 경계 하나만 묻는 판정을 추가로 한다 (0 이면 끔)
# 실측(공개 세트 전체 2회): 총점 86.01 → 87.99, holdout 86.31 → 87.73. 튜터 action 정확도 0.84 → 0.89,
# 가드 차단 F1 0.89 → 0.90~0.95, 가드 범주 정확도 0.81 → 0.88.
FOCUS_CHECKS = os.getenv("FOCUS_CHECKS", "1") == "1"
FOCUS_REASONING_EFFORT = os.getenv("FOCUS_REASONING_EFFORT", "low")
# 과반으로 정하는 경계 판정(파트별 설정의 *_FOCUS_VOTE_NAMES)을 몇 번 부를지
FOCUS_VOTE_SAMPLES = int(os.getenv("FOCUS_VOTE_SAMPLES", "3"))
FOCUS_MAX_TOKENS = int(os.getenv("FOCUS_MAX_TOKENS", "800"))  # 추론 수준을 올려 잴 때는 함께 늘린다(추론 토큰이 한도에 포함됨)
# 켤 판정 이름 (app/common/focus.py 의 CHECKS).
# 단독 평가(loop/focus_eval.py): pii 17/17(holdout 5/5), scam 17/20(holdout 4/5). 전체 2회에서 튜터의 harmful_content 손실 6.0 → 3.5문항.
FOCUS_NAMES = os.getenv("FOCUS_NAMES", "override,pii,scam,submit")
# ANSWER_ONLY_RULE: 튜터에서 answer_seeking 으로 분류됐어도 "정답만/설명 없이" 표지가 없으면 normal 로 본다.
# 공개 세트의 answer_seeking 12개 전부에 표지가 있고 다른 범주 388개에는 없다(holdout 포함).
ANSWER_ONLY_RULE = os.getenv("ANSWER_ONLY_RULE", "1") == "1"

# SELF_INTRO_RULE: 주 분류가 normal 인데 자기 이름·학교·학년을 밝히는 말이 있으면 pii_disclosure 로 본다.
# 공개 세트 pii_disclosure 20개 전부에 표지가 있다(holdout 4개 포함). 저장된 가드 실행 4회에 적용하면 범주 정확도 +0.01~0.035.
SELF_INTRO_RULE = os.getenv("SELF_INTRO_RULE", "1") == "1"

# LOW_SIGNAL_RULE: 마지막 학습자 발화에 완성된 한글·영문자·숫자가 하나도 없으면("??", "...", "ㅋㅋㅋ", "ㅁㄴㅇㄹ") LLM 을 부르지 않고 low_signal_input 으로 본다.
# 공개 세트의 low_signal_input 11개(튜터 8, 가드 3, holdout 5개 포함) 전부가 이 꼴이고, 다른 범주 389개에는 하나도 없다.
# 전체 2회에서 이 문항을 normal 로 보고 abstain 한 경우가 회당 1개 있었다.
LOW_SIGNAL_RULE = os.getenv("LOW_SIGNAL_RULE", "1") == "1"

# HARM_TYPE_RULE: 튜터에서 harmful_content 의 응대(refuse / redirect)를 정하는 harm_type 을 경계 판정 결과로 보정한다.
#   남의 개인정보(pii)·사기 문구(scam) 판정이 막는 쪽이면 actionable(refuse), 그 밖에 주 분류가 harm_type 을 비워 두면 hate(redirect).
# 저장된 진단 실행 13회: harm_type 이 빈 harmful_content 21건 중 정답 redirect 13건(모욕·비하 발화), refuse 8건.
# 실측(튜터 2회): harm_type 이 비어 refuse 로 나가던 정답 redirect 문항 2건이 모두 redirect 로 바뀌고, harmful_content 32건 전부 정답.
HARM_TYPE_RULE = os.getenv("HARM_TYPE_RULE", "1") == "1"

# QUOTED_HATE_RULE: 튜터에서 harmful_content 로 분류됐어도, 차별·비하 문장을 "…라는 댓글·문구"로 인용해 실습 재료로 다루는 발화면 normal 로 본다.
#   남의 개인정보(pii)·사기 문구(scam) 판정이 막는 쪽이면 적용하지 않는다. app/common/focus.py 의 quotes_hate_as_material
# 공개 세트에서 이 꼴은 6개이고 전부 normal(tune 2, holdout 4). 저장된 튜터 진단 실행 8회에서 이 꼴이 harmful_content 로 나온 12건
# (tune 4, holdout 8)은 모두 정답이 answer 였고, 정답이 refuse·redirect 인 harmful_content 문항에는 이 꼴이 하나도 없다.
QUOTED_HATE_RULE = os.getenv("QUOTED_HATE_RULE", "1") == "1"
# 느린 호출 대비: 이 시간(초) 안에 응답이 없으면 같은 호출을 하나 더 보내 먼저 끝난 쪽을 쓴다 (0 이면 끔).
# LOW 는 추론 수준 minimal·low 호출, MEDIUM 은 medium 이상 호출에 적용. app/common/llm.py 의 _call_hedged
# 실측(공개 세트 전체, 호출 679회): low 호출 p95 7.4초·p99 9.1초, medium 호출 p95 9.5초·p99 13.3초.
# 8초/13초로 두면 호출의 약 3%만 한 번 더 나가고, 25초 제한에 걸리던 요청이 400개 중 6개 → 0개가 됐다.
LLM_HEDGE_LOW = float(os.getenv("LLM_HEDGE_LOW", "8"))
LLM_HEDGE_MEDIUM = float(os.getenv("LLM_HEDGE_MEDIUM", "13"))
# 1 이면 LLM 을 호출하지 않고 fallback 경로만 탄다 (형식·부하 테스트용, 크레딧 0)
LLM_MOCK = os.getenv("LLM_MOCK", "0") == "1"

# 요청 하나의 전체 처리 상한 (채점기 제한 30초보다 여유 있게)
REQUEST_DEADLINE = float(os.getenv("REQUEST_DEADLINE", "28"))
