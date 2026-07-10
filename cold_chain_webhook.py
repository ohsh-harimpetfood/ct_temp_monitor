# =========================================================
# cold_chain_webhook.py v2
#
# Apps Script Webhook 호출 모듈.
#
# v2 변경 사항:
# 1. 공통 헬퍼 _post_webhook() 통합 — 5개 함수의 중복 제거
# 2. ok 판정 기준 통일: HTTP 2xx AND 응답 JSON의 ok가 True
# 3. 에러 반환 형태 통일: {"ok": False, "error": ..., "response": {}}
# 4. raw_text 자르기 통일 ([:1000])
# 5. post_report_send에 force_send 파라미터 추가
#    (Apps Script v7.2 중복 발송 가드 재발송용)
#
# 공개 함수 시그니처는 기존과 호환됨 — CT_Temp.py 수정 불필요.
#
# 보안 원칙:
# - token은 Streamlit Secrets에서 받아 전송 직전에만 주입한다.
# - token 원문은 반환값, 화면, 로그에 노출하지 않는다.
# =========================================================
import requests


def _error_result(message):
    """검증 실패 / 예외 시 통일된 에러 반환 형태."""
    return {
        "ok": False,
        "error": message,
        "response": {},
    }


def _post_webhook(webhook_url, webhook_token, payload, mode, timeout, extra_fields=None):
    """
    Apps Script Webhook 공통 호출 헬퍼.

    처리 순서:
    1. URL / token / payload 검증
    2. payload 복사 후 mode, token 주입 (원본 payload는 변경하지 않음)
    3. POST 요청
    4. 응답 JSON 파싱 (실패 시 raw_text 1000자 보존)
    5. ok 판정: HTTP 2xx AND 응답 JSON의 ok가 True

    주의:
    - token은 요청 body에만 넣고 반환값에는 포함하지 않는다.
    - 최상위 키만 교체하므로 shallow copy(dict())로 충분하다.
    """
    if not webhook_url:
        return _error_result("APPS_SCRIPT_WEBHOOK_URL이 설정되지 않았습니다.")

    if not webhook_token:
        return _error_result("REPORT_WEBHOOK_TOKEN이 설정되지 않았습니다.")

    if payload is None:
        return _error_result("전송할 payload가 없습니다.")

    body = dict(payload)
    body["mode"] = mode
    body["token"] = webhook_token

    if extra_fields:
        body.update(extra_fields)

    try:
        response = requests.post(
            webhook_url,
            json=body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return _error_result(str(exc))

    try:
        response_json = response.json()
    except Exception:
        response_json = {
            "raw_text": response.text[:1000],
        }

    return {
        "ok": (200 <= response.status_code < 300) and response_json.get("ok") is True,
        "status_code": response.status_code,
        "response": response_json,
    }


def post_webhook_ping(webhook_url, webhook_token, timeout=20):
    """
    Apps Script Webhook 연결 확인용 ping 요청.
    """
    return _post_webhook(
        webhook_url=webhook_url,
        webhook_token=webhook_token,
        payload={"source": "streamlit"},
        mode="ping",
        timeout=timeout,
    )


def post_report_payload(webhook_url, webhook_token, payload, timeout=30):
    """
    보고서 payload를 전송해 Gmail 초안(draft)을 생성한다.
    mode = draft
    """
    return _post_webhook(
        webhook_url=webhook_url,
        webhook_token=webhook_token,
        payload=payload,
        mode="draft",
        timeout=timeout,
    )


def post_report_preview(webhook_url, webhook_token, payload, timeout=30):
    """
    보고서 payload를 전송해 Gmail 초안 생성 없이
    HTML 미리보기만 받아온다.
    mode = preview
    """
    return _post_webhook(
        webhook_url=webhook_url,
        webhook_token=webhook_token,
        payload=payload,
        mode="preview",
        timeout=timeout,
    )


def post_report_send(webhook_url, webhook_token, payload, timeout=60, force_send=False):
    """
    실제 메일 발송 요청.
    mode = send

    force_send:
        Apps Script v7.2는 동일 기준일(report_date)로 이미 발송된 이력이 있으면
        발송을 차단한다. force_send=True를 전달하면 가드를 우회해 재발송한다.
        (Streamlit에서 '강제 재발송' 기능을 붙일 때 사용)
    """
    extra_fields = {"force_send": True} if force_send else None

    return _post_webhook(
        webhook_url=webhook_url,
        webhook_token=webhook_token,
        payload=payload,
        mode="send",
        timeout=timeout,
        extra_fields=extra_fields,
    )


def post_report_store(webhook_url, webhook_token, payload, timeout=60):
    """
    report payload 안의 daily_summary_rows를
    Google Sheet "데이터 다운로드" 시트에 upsert 적재한다.
    메일 미리보기/초안/발송은 수행하지 않는다.
    mode = store
    """
    return _post_webhook(
        webhook_url=webhook_url,
        webhook_token=webhook_token,
        payload=payload,
        mode="store",
        timeout=timeout,
    )
