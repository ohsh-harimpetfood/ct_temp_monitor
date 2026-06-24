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
