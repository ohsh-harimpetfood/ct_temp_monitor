import copy
import requests


def post_webhook_ping(webhook_url, webhook_token, timeout=20):
    """
    Apps Script Webhook 연결 확인용 ping 요청.

    주의:
    - token은 Streamlit Secrets에서 받아서 전달한다.
    - token 원문은 화면이나 로그에 노출하지 않는다.
    """

    if not webhook_url:
        return {
            "ok": False,
            "error": "APPS_SCRIPT_WEBHOOK_URL이 비어 있습니다.",
        }

    if not webhook_token:
        return {
            "ok": False,
            "error": "REPORT_WEBHOOK_TOKEN이 비어 있습니다.",
        }

    payload = {
        "source": "streamlit",
        "mode": "ping",
        "token": webhook_token,
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=timeout,
        )

        try:
            response_json = response.json()
        except Exception:
            response_json = {
                "raw_text": response.text[:1000]
            }

        return {
            "ok": response.ok and response_json.get("ok") is True,
            "status_code": response.status_code,
            "response": response_json,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }


def post_report_payload(webhook_url, webhook_token, payload, timeout=30):
    """
    Apps Script Webhook으로 냉동 CT 보고서 payload 전체를 전송한다.

    주의:
    - token은 Streamlit Secrets에서 받아 전송 직전에만 주입한다.
    - 화면이나 로그에는 token 원문을 노출하지 않는다.
    """

    if not webhook_url:
        return {
            "ok": False,
            "error": "APPS_SCRIPT_WEBHOOK_URL이 비어 있습니다.",
        }

    if not webhook_token:
        return {
            "ok": False,
            "error": "REPORT_WEBHOOK_TOKEN이 비어 있습니다.",
        }

    if not payload:
        return {
            "ok": False,
            "error": "전송할 payload가 없습니다.",
        }

    send_payload = dict(payload)
    send_payload["token"] = webhook_token
    send_payload["mode"] = "draft"

    try:
        response = requests.post(
            webhook_url,
            json=send_payload,
            timeout=timeout,
        )

        try:
            response_json = response.json()
        except Exception:
            response_json = {
                "raw_text": response.text[:1000]
            }

        return {
            "ok": response.ok and response_json.get("ok") is True,
            "status_code": response.status_code,
            "response": response_json,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }

def post_report_preview(webhook_url, webhook_token, payload, timeout=30):
    """
    Apps Script Webhook으로 보고서 payload를 보내고
    Gmail 초안 생성 없이 HTML 미리보기만 받아온다.
    """

    if not webhook_url:
        return {
            "ok": False,
            "error": "APPS_SCRIPT_WEBHOOK_URL이 비어 있습니다.",
        }

    if not webhook_token:
        return {
            "ok": False,
            "error": "REPORT_WEBHOOK_TOKEN이 비어 있습니다.",
        }

    if not payload:
        return {
            "ok": False,
            "error": "전송할 payload가 없습니다.",
        }

    send_payload = dict(payload)
    send_payload["token"] = webhook_token
    send_payload["mode"] = "preview"

    try:
        response = requests.post(
            webhook_url,
            json=send_payload,
            timeout=timeout,
        )

        try:
            response_json = response.json()
        except Exception:
            response_json = {
                "raw_text": response.text[:1000]
            }

        return {
            "ok": response.ok and response_json.get("ok") is True,
            "status_code": response.status_code,
            "response": response_json,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }

def post_report_send(webhook_url, webhook_token, payload, timeout=60):
    """
    Apps Script Webhook으로 실제 메일 발송 요청을 보낸다.
    mode = send
    """

    if not webhook_url:
        return {
            "ok": False,
            "error": "APPS_SCRIPT_WEBHOOK_URL이 설정되지 않았습니다."
        }

    if not webhook_token:
        return {
            "ok": False,
            "error": "REPORT_WEBHOOK_TOKEN이 설정되지 않았습니다."
        }

    if payload is None:
        return {
            "ok": False,
            "error": "전송할 report payload가 없습니다."
        }

    try:
        import requests

        send_payload = dict(payload)
        send_payload["token"] = webhook_token
        send_payload["mode"] = "send"

        response = requests.post(
            webhook_url,
            json=send_payload,
            timeout=timeout,
        )

        try:
            response_json = response.json()
        except Exception:
            response_json = {
                "raw_text": response.text
            }

        return {
            "ok": response.ok and bool(response_json.get("ok")),
            "status_code": response.status_code,
            "response": response_json,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }

def post_report_store(
    webhook_url,
    webhook_token,
    payload,
    timeout=60,
):
    """
    Apps Script Webhook store mode 호출.

    목적:
    - report payload 안의 daily_summary_rows를
      Google Sheet "데이터 다운로드" 시트에 upsert 적재한다.
    - 메일 미리보기/초안/발송은 수행하지 않는다.
    """

    if not webhook_url:
        return {
            "ok": False,
            "error": "APPS_SCRIPT_WEBHOOK_URL이 설정되지 않았습니다.",
            "response": {},
        }

    if not webhook_token:
        return {
            "ok": False,
            "error": "REPORT_WEBHOOK_TOKEN이 설정되지 않았습니다.",
            "response": {},
        }

    if payload is None:
        return {
            "ok": False,
            "error": "전송할 report payload가 없습니다.",
            "response": {},
        }

    body = copy.deepcopy(payload)
    body["mode"] = "store"
    body["token"] = webhook_token

    try:
        res = requests.post(
            webhook_url,
            json=body,
            timeout=timeout,
        )

        try:
            response_json = res.json()
        except Exception:
            response_json = {
                "raw_text": res.text,
            }

        return {
            "ok": bool(response_json.get("ok")) and 200 <= res.status_code < 300,
            "status_code": res.status_code,
            "response": response_json,
        }

    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": str(exc),
            "response": {},
        }
