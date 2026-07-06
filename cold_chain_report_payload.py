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
    # 2026년 7월 1주차 운영 위치 변경 반영
    # CT2: 생산1팀 → 물류지원팀 / 20FT
    # CT3: 물류지원팀 → 생산1팀 / 40FT
    "생산1팀": [3, 4, 5, 13],
    "생산2팀": [6, 7, 8, 9],
    "물류지원팀": [1, 2, 11, 12],
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
        source_ct_label = str(row["컨테이너"])
        ct_no = extract_ct_no(source_ct_label)
        ct_label = f"CT{ct_no:02d}"

        statuses = []

        for date_str in period_date_strings:
            status_item = daily_lookup.get(
                (source_ct_label, date_str),
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

def build_check_list(check_list_df, container_status_df=None):
    """
    우선점검 리스트(check_list_df)를 Apps Script 메일 payload용으로 변환한다.

    check_list_df 필요 컬럼:
    - 상태
    - 컨테이너
    - 측정일자
    - 이슈
    - 최저온도
    - 냉동효율(%)
    - -15℃이하유지율(%)

    container_status_df 선택 컬럼:
    - 컨테이너
    - 오늘이탈수
    - 최근3일이슈일
    """

    if check_list_df is None or check_list_df.empty:
        return []

    status_priority = {
        "긴급점검": 1,
        "위험": 2,
        "데이터 연결 이상": 3,
        "과냉주의": 4,
        "주의": 5,
        "데이터없음": 6,
        "정상": 99,
    }

    status_lookup = {}

    if container_status_df is not None and not container_status_df.empty:
        for _, row in container_status_df.iterrows():
            source_ct_label = str(row.get("컨테이너", ""))
            status_lookup[source_ct_label] = {
                "today_deviation_count": safe_int_or_none(row.get("오늘이탈수")),
                "issue_days_3d": safe_int_or_none(row.get("최근3일이슈일")),
            }

    rows = []

    for _, row in check_list_df.iterrows():
        source_ct_label = str(row.get("컨테이너", ""))
        ct_no = extract_ct_no(source_ct_label)
        ct_label = f"CT{ct_no:02d}"

        status = str(row.get("상태", "데이터없음"))
        issue = str(row.get("이슈", ""))

        extra = status_lookup.get(source_ct_label, {})

        item = {
            "priority": status_priority.get(status, 99),
            "team": get_team_by_ct(source_ct_label),
            "ct_no": ct_no,
            "ct_label": ct_label,
            "status": status,
            "date": format_date_string(row.get("측정일자")),
            "issue": issue,
            "metrics": {
                "min_temp": safe_float_or_none(row.get("최저온도")),
                "cooling_efficiency": safe_float_or_none(row.get("냉동효율(%)")),
                "under_minus15_rate": safe_float_or_none(row.get("-15℃이하유지율(%)")),
                "today_deviation_count": extra.get("today_deviation_count"),
                "issue_days_3d": extra.get("issue_days_3d"),
            },
            "request": build_default_request(status, issue),
        }

        rows.append(item)

    rows = sorted(
        rows,
        key=lambda x: (x["priority"], x["ct_no"])
    )

    return rows


def safe_float_or_none(value):
    """
    NaN, None 값을 JSON 전송 가능한 None으로 변환한다.
    """
    if pd.isna(value):
        return None

    try:
        return round(float(value), 1)
    except Exception:
        return None


def safe_int_or_none(value):
    """
    NaN, None 값을 JSON 전송 가능한 None으로 변환한다.
    """
    if pd.isna(value):
        return None

    try:
        return int(value)
    except Exception:
        return None


def format_date_string(value):
    """
    날짜 값을 YYYY-MM-DD 문자열로 변환한다.
    """
    if pd.isna(value):
        return ""

    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def build_default_request(status, issue):
    """
    상태값 기준 기본 요청사항 문구를 생성한다.
    이후 운영하면서 문구는 조정 가능.
    """

    if status == "긴급점검":
        return "장비 운전상태, 문 닫힘 상태, 센서 위치, 냉동기 작동 여부를 우선 확인 바랍니다."

    if status == "위험":
        return "최근 이탈 이력과 현장 운전상태를 확인하고 필요 시 장비 점검을 진행 바랍니다."

    if status == "데이터 연결 이상":
        return "최근 데이터 수집 여부, 데이터로거 전원, 통신 상태를 우선 확인 바랍니다."

    if status == "과냉주의":
        return "설정온도, 운전조건, 과냉 운전 여부를 확인 바랍니다."

    if status == "주의":
        return "해당 CT의 온도 추이와 현장 상태를 확인 바랍니다."

    return "필요 시 현장 상태를 확인 바랍니다."


def build_metrics(container_status_df):
    """
    CT별 종합 상태표(container_status_df)를 기준으로
    LLM 분석 및 메일 상세 참고용 metrics payload를 생성한다.

    container_status_df 필요 컬럼:
    - 컨테이너
    - 종합상태
    - 대표이슈
    - 오늘이탈수
    - 최근3일이슈일
    - 최악최저온도
    - 최저냉동효율
    - 최고냉동효율
    - 최저유지율
    - 최근측정일
    """

    if container_status_df is None or container_status_df.empty:
        return []

    df = container_status_df.copy()
    df["ct_no"] = df["컨테이너"].apply(extract_ct_no)
    df = df.sort_values("ct_no")

    rows = []

    for _, row in df.iterrows():
        source_ct_label = str(row.get("컨테이너", ""))
        ct_no = extract_ct_no(source_ct_label)
        ct_label = f"CT{ct_no:02d}"

        rows.append({
            "team": get_team_by_ct(source_ct_label),
            "ct_no": ct_no,
            "ct_label": ct_label,
            "final_status": str(row.get("종합상태", "데이터없음")),
            "main_issue": str(row.get("대표이슈", "")),
            "worst_min_temp": safe_float_or_none(row.get("최악최저온도")),
            "cooling_efficiency_min": safe_float_or_none(row.get("최저냉동효율")),
            "cooling_efficiency_max": safe_float_or_none(row.get("최고냉동효율")),
            "under_minus15_rate_min": safe_float_or_none(row.get("최저유지율")),
            "today_deviation_count": safe_int_or_none(row.get("오늘이탈수")),
            "issue_days_3d": safe_int_or_none(row.get("최근3일이슈일")),
            "latest_measured_date": format_date_string(row.get("최근측정일")),
        })

    return rows


def build_data_quality(df_raw, df_long, abnormal_count, excluded_count):
    """
    자동메일 하단 데이터 품질 참고용 payload를 생성한다.

    df_raw:
        원본 CSV 데이터프레임

    df_long:
        이상값/결측값 제외 후 분석 대상 long-form 데이터프레임

    abnormal_count:
        -50℃ 미만 또는 60℃ 초과로 이상값 처리된 건수

    excluded_count:
        결측/이상값 처리 후 분석에서 제외된 전체 건수
    """

    raw_count = 0

    if df_raw is not None and not df_raw.empty:
        # 첫 번째 컬럼은 측정일시이므로 제외하고 온도 측정 셀 수만 계산
        raw_count = int(df_raw.shape[0] * max(df_raw.shape[1] - 1, 0))

    valid_count = int(len(df_long)) if df_long is not None else 0
    invalid_count = int(excluded_count or 0)
    abnormal_replaced_count = int(abnormal_count or 0)

    # raw_count가 계산되지 않는 예외 상황 대비
    if raw_count == 0:
        raw_count = valid_count + invalid_count

    return {
        "raw_count": raw_count,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "abnormal_replaced_count": abnormal_replaced_count,
        "missing_or_invalid_count": invalid_count,
        "note": "이상값 및 결측값은 분석 대상에서 제외함",
    }

def build_report_payload(
    mode,
    report_meta,
    summary,
    heatmap_rows,
    check_list,
    metrics,
    data_quality,
    token=None,
):
    """
    Streamlit → Apps Script로 보낼 최종 payload를 생성한다.

    주의:
    - Gemini API Key는 절대 payload에 넣지 않는다.
    - Gmail 권한 정보도 절대 payload에 넣지 않는다.
    - token은 Webhook 호출 직전에 Streamlit Secrets에서 주입한다.
    """

    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "mode": mode,
        "report_meta": report_meta,
        "summary": summary,
        "heatmap_rows": heatmap_rows,
        "check_list": check_list,
        "metrics": metrics,
        "data_quality": data_quality,
    }

    # 실제 전송 시에만 token을 넣는다.
    # 화면 확인용 preview에서는 token을 노출하지 않기 위함.
    if token:
        payload["token"] = token
    else:
        payload["token"] = "__TOKEN_INJECTED_AT_SEND_TIME__"

    return payload
