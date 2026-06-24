from datetime import datetime
from zoneinfo import ZoneInfo
import re
import pandas as pd


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

TEAM_MAP = {
    "생산1팀": [2, 4, 5, 13],
    "생산2팀": [6, 7, 8, 9],
    "물류지원팀": [1, 3, 11, 12],
    "예비": [10],
}


def extract_ct_no(ct_label):
    """
    CT01, CT1, CT08 같은 문자열에서 숫자만 추출한다.
    """
    match = re.search(r"(\d+)", str(ct_label))
    return int(match.group(1)) if match else 9999


def get_team_by_ct(ct_label):
    """
    CT 번호 기준으로 담당팀을 반환한다.
    """
    ct_no = extract_ct_no(ct_label)

    for team, ct_list in TEAM_MAP.items():
        if ct_no in ct_list:
            return team

    return "미지정"


def build_heatmap_rows(daily_status_df, container_status_df, period_dates):
    """
    CT별 최근 N일 상태 히트맵용 payload를 생성한다.

    daily_status_df:
        일자별 CT 상태 데이터.
        필요 컬럼: 컨테이너, 측정일자, 상태, 이슈

    container_status_df:
        CT별 최종 종합상태 데이터.
        필요 컬럼: 컨테이너, 종합상태, 대표이슈

    period_dates:
        히트맵 컬럼으로 사용할 날짜 배열.
        현재는 3일, 향후 7일 확장 가능.
    """

    if daily_status_df is None or daily_status_df.empty:
        return []

    if container_status_df is None or container_status_df.empty:
        return []

    period_date_strings = [
        pd.to_datetime(d).strftime("%Y-%m-%d")
        for d in period_dates
    ]

    daily = daily_status_df.copy()
    daily["date_str"] = pd.to_datetime(daily["측정일자"]).dt.strftime("%Y-%m-%d")
    daily["ct_label"] = daily["컨테이너"].astype(str)

    daily_lookup = {}

    for _, row in daily.iterrows():
        key = (row["ct_label"], row["date_str"])
        daily_lookup[key] = {
            "status": str(row.get("상태", "데이터없음")),
            "issue": str(row.get("이슈", "데이터 없음")),
        }

    status_df = container_status_df.copy()
    status_df["ct_no"] = status_df["컨테이너"].apply(extract_ct_no)
    status_df = status_df.sort_values("ct_no")

    heatmap_rows = []

    for _, row in status_df.iterrows():
        ct_label = str(row["컨테이너"])
        ct_no = extract_ct_no(ct_label)

        statuses = []

        for date_str in period_date_strings:
            status_item = daily_lookup.get(
                (ct_label, date_str),
                {
                    "status": "데이터없음",
                    "issue": "데이터 없음",
                }
            )

            statuses.append({
                "date": date_str,
                "status": status_item["status"],
                "issue": status_item["issue"],
            })

        heatmap_rows.append({
            "team": get_team_by_ct(ct_label),
            "ct_no": ct_no,
            "ct_label": ct_label,
            "statuses": statuses,
            "final_status": str(row.get("종합상태", "데이터없음")),
            "remark": str(row.get("대표이슈", "")),
        })

    return heatmap_rows
