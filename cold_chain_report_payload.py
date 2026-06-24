from datetime import datetime
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "cold_chain_report_v1"
SOURCE = "streamlit"


def build_report_meta(
    report_date,
    period_start,
    period_end,
    period_dates,
    uploaded_filename=None,
    dashboard_period_days=3,
):
    """
    Apps Script 히트맵형 메일 생성을 위한 보고서 기준정보 생성 함수.

    report_date:
        보고 기준일. 보통 분석 데이터의 최신일자.

    period_start:
        분석 시작일.

    period_end:
        분석 종료일.

    period_dates:
        히트맵 컬럼으로 사용할 날짜 배열.
        현재는 3일, 향후 7일 확장 가능.

    uploaded_filename:
        사용자가 업로드한 CSV 파일명.

    dashboard_period_days:
        히트맵 기준 일수. 현재 3일.
    """

    generated_at = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "report_date": str(report_date),
        "period_start": str(period_start),
        "period_end": str(period_end),
        "period_dates": [str(d) for d in period_dates],
        "generated_at": generated_at,
        "uploaded_filename": uploaded_filename or "",
        "dashboard_period_days": int(dashboard_period_days),
    }


def build_summary(container_status_df):
    """
    컨테이너별 최종 상태표(container_status_df)를 기준으로
    메일 상단 요약 카드용 summary payload를 생성한다.
    """

    summary = {
        "total_ct": 0,
        "normal": 0,
        "caution": 0,
        "overcool": 0,
        "risk": 0,
        "emergency": 0,
        "connection_issue": 0,
        "no_data": 0,
        "issue_total": 0,
    }

    if container_status_df is None or container_status_df.empty:
        return summary

    status_key_map = {
        "정상": "normal",
        "주의": "caution",
        "과냉주의": "overcool",
        "위험": "risk",
        "긴급점검": "emergency",
        "데이터 연결 이상": "connection_issue",
        "데이터없음": "no_data",
    }

    summary["total_ct"] = int(len(container_status_df))

    for status in container_status_df["종합상태"].astype(str).tolist():
        key = status_key_map.get(status)

        if key:
            summary[key] += 1
        else:
            summary["no_data"] += 1

    summary["issue_total"] = (
        summary["caution"]
        + summary["overcool"]
        + summary["risk"]
        + summary["emergency"]
        + summary["connection_issue"]
        + summary["no_data"]
    )

    return summary
