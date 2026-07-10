# =========================================================
# cold_chain_report_payload.py v2
#
# Streamlit 분석 결과 → Apps Script Webhook payload 생성 모듈.
#
# v2 변경 사항:
# 1. TEAM_MAP / get_team_by_ct 제거
#    - 팀 배정의 관리 지점을 Apps Script의 Report_Config 시트
#      한 곳으로 단일화한다. (v7.2 setupReportConfigSheetOnce 참고)
#    - payload의 team 필드는 스키마 호환을 위해 빈 값("")으로 유지하며,
#      Apps Script가 Report_Config 기준으로 팀을 판정해 채운다.
#    - CT 이동 시 이 파일은 수정할 필요가 없다.
# 2. 과냉주의 죽은 코드 정리
#    - Streamlit V4.4부터 종합상태에 '과냉주의'가 생성되지 않는다.
#      (과냉은 효율 120% 초과 참고지표로만 관리)
#    - summary의 overcool 키는 스키마 호환을 위해 유지하되 항상 0.
# 3. build_data_quality에 ct_col_count 옵션 추가
#    - CSV에 CT가 아닌 컬럼이 섞여 있을 때 raw_count 과대계산 방지.
#    - 미전달 시 기존 동작과 동일 (하위 호환).
#
# 유지 사항:
# - build_heatmap_rows의 전체 기간 순회는 의도적으로 유지한다.
#   (lookup 구축용이며, payload의 statuses는 period_dates만 포함)
# =========================================================
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import pandas as pd

SCHEMA_VERSION = "cold_chain_report_v1"
SOURCE = "streamlit"

DAILY_SUMMARY_COLUMNS = [
    "컨테이너",
    "측정일자",
    "요일",
    "최저온도",
    "평균누적온도",
    "측정면적",
    "목표면적",
    "냉동효율(%)",
    "-15℃이하유지율(%)",
    "측정건수",
]


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

    참고:
    - overcool 키는 스키마 호환을 위해 유지한다.
      V4.4부터 '과냉주의' 상태가 생성되지 않으므로 항상 0이다.
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
        + summary["risk"]
        + summary["emergency"]
        + summary["connection_issue"]
        + summary["no_data"]
    )

    return summary


def extract_ct_no(ct_label):
    """
    CT01, CT1, CT08 같은 문자열에서 숫자만 추출한다.
    """
    match = re.search(r"(\d+)", str(ct_label))
    return int(match.group(1)) if match else 9999


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

    참고:
    - team 필드는 Apps Script가 Report_Config 시트 기준으로 판정한다.
      payload에서는 스키마 호환용으로 빈 값을 보낸다.
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
            "team": "",
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
        "주의": 4,
        "데이터없음": 5,
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
            "team": "",
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
            "team": "",
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


def build_daily_summary_rows(df_summary):
    """
    공식 데이터셋 '데이터 다운로드' 시트에 적재할 일자별 CT 분석결과 payload를 생성한다.

    기준 컬럼:
    - 컨테이너
    - 측정일자
    - 요일
    - 최저온도
    - 평균누적온도
    - 측정면적
    - 목표면적
    - 냉동효율(%)
    - -15℃이하유지율(%)
    - 측정건수
    """
    if df_summary is None or df_summary.empty:
        return []

    missing_cols = [
        col for col in DAILY_SUMMARY_COLUMNS
        if col not in df_summary.columns
    ]
    if missing_cols:
        raise ValueError(
            "daily_summary_rows 생성 실패. 누락 컬럼: "
            + ", ".join(missing_cols)
        )

    df = df_summary[DAILY_SUMMARY_COLUMNS].copy()

    rows = []

    for _, row in df.iterrows():
        container = str(row.get("컨테이너", "")).strip()
        measure_date = format_date_string(row.get("측정일자"))

        # 핵심 키가 없는 행은 적재 대상에서 제외한다.
        if not container or not measure_date:
            continue

        rows.append({
            "컨테이너": container,
            "측정일자": measure_date,
            "요일": str(row.get("요일", "")).strip(),
            "최저온도": safe_float_or_none(row.get("최저온도")),
            "평균누적온도": safe_float_or_none(row.get("평균누적온도")),
            "측정면적": safe_int_or_none(row.get("측정면적")),
            "목표면적": safe_int_or_none(row.get("목표면적")),
            "냉동효율(%)": safe_float_or_none(row.get("냉동효율(%)")),
            "-15℃이하유지율(%)": safe_float_or_none(row.get("-15℃이하유지율(%)")),
            "측정건수": safe_int_or_none(row.get("측정건수")),
        })

    return rows


def build_data_quality(df_raw, df_long, abnormal_count, excluded_count, ct_col_count=None):
    """
    자동메일 하단 데이터 품질 참고용 payload를 생성한다.

    df_raw:
        원본 CSV 데이터프레임
    df_long:
        이상값/결측값 제외 후 분석 대상 long-form 데이터프레임
    abnormal_count:
        이상값 처리된 건수 (-50℃ 미만 또는 60℃ 초과)
    excluded_count:
        결측/이상값 처리 후 분석에서 제외된 전체 건수
    ct_col_count:
        (선택) 인식된 냉동CT 컬럼 수.
        전달하면 raw_count = 행 수 × CT 컬럼 수로 정확히 계산한다.
        CSV에 CT가 아닌 컬럼이 섞여 있을 때 과대계산을 방지한다.
        미전달 시 기존 방식(전체 컬럼 - 1)으로 계산한다 (하위 호환).
    """
    raw_count = 0
    if df_raw is not None and not df_raw.empty:
        if ct_col_count is not None and int(ct_col_count) > 0:
            raw_count = int(df_raw.shape[0] * int(ct_col_count))
        else:
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
    daily_summary_rows=None,
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
        "daily_summary_rows": daily_summary_rows or [],
    }

    # 실제 전송 시에만 token을 넣는다.
    # 화면 확인용 preview에서는 token을 노출하지 않기 위함.
    if token:
        payload["token"] = token
    else:
        payload["token"] = "__TOKEN_INJECTED_AT_SEND_TIME__"

    return payload
