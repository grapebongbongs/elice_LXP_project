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


def log_error(where: str, exc: BaseException) -> None:
    # 예외 메시지에 본문이 섞일 수 있으므로 '오류 이름'만 남긴다
    logger.info(f"error where={where} name={type(exc).__name__}")


def log_llm(tag: str, elapsed_ms: float, outcome: str, attempt: int = 1) -> None:
    # LLM 호출 하나의 소요 시간과 결과 이름만 남긴다 (tag 는 코드에 박힌 고정 문자열, 본문 아님)
    logger.info(f"llm tag={tag or '-'} attempt={attempt} ms={elapsed_ms:.0f} outcome={outcome}")
