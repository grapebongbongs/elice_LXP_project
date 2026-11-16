"""대회 규정: 로그에는 시간·길이·상태 코드·오류명만 남긴다. 요청/답변 본문 금지."""
import logging
import sys

logger = logging.getLogger("server")
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


def log_request(path: str, req_len: int, status: int, elapsed_ms: float, error: str = "") -> None:
    logger.info(f"path={path} req_len={req_len} status={status} ms={elapsed_ms:.0f} err={error}")


def log_llm(effort: str, elapsed_ms: float, error: str = "") -> None:
    # LLM 호출 한 번의 추론 수준·걸린 시간·오류 이름 (프롬프트·응답 본문은 남기지 않는다)
    logger.info(f"llm effort={effort} ms={elapsed_ms:.0f} err={error}")


def log_error(where: str, exc: BaseException) -> None:
    # 예외 메시지에 본문이 섞일 수 있으므로 '오류 이름'만 남긴다
    logger.info(f"error where={where} name={type(exc).__name__}")
