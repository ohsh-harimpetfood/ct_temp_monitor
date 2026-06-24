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


