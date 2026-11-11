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

# 서버 인증 토큰 (submission.json 의 token 과 같아야 함, LLM 키와 다른 값)
SERVER_TOKEN = os.getenv("SERVER_TOKEN", "")

# LLM
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "64"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))
# 1 이면 LLM 을 호출하지 않고 fallback 경로만 탄다 (형식·부하 테스트용, 크레딧 0)
LLM_MOCK = os.getenv("LLM_MOCK", "0") == "1"

# 임베딩: 런박스가 주소와 모델명을 미리 주입한다. API 키만 .env 에 둔다.
# KB 벡터는 서버 시작 시 한 번 만들고, 요청별 질문 벡터는 메모리에서만 잠깐 쓴다.
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_TIMEOUT = float(os.getenv("EMBEDDING_TIMEOUT", "12"))
EMBEDDING_MAX_CONCURRENCY = int(os.getenv("EMBEDDING_MAX_CONCURRENCY", "16"))

# 요청 하나의 전체 처리 상한 (채점기 제한 30초보다 여유 있게)
REQUEST_DEADLINE = float(os.getenv("REQUEST_DEADLINE", "25"))
