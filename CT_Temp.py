import io
import re
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import plotly.graph_objects as go
import cold_chain_report_payload as report_payload
import cold_chain_webhook as webhook_client

from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils import get_column_letter


# =========================================================
# Streamlit 기본 설정
# =========================================================
st.set_page_config(
    page_title="❄ 냉동 컨테이너 온도관리 플랫폼.V4",
    layout="wide"
)

st.title("❄ 냉동 컨테이너 온도관리 플랫폼.V4")
st.caption("신규 데이터로거 플랫폼 CSV 전용 | 이상값 기준: -50℃ 미만 또는 60℃ 초과 → 결측치 처리")


# =========================================================
# 공통 유틸 함수
# =========================================================
def extract_ct_number(col_name: str):
    match = re.search(r"(\d+)\s*번\s*냉동CT", str(col_name))
    return int(match.group(1)) if match else None


def ct_sort_key(ct_name: str) -> int:
    match = re.search(r"CT(\d+)", str(ct_name))
    return int(match.group(1)) if match else 9999


def integrate_trapezoid(y, x):
    if len(x) == 0:
        return 0

    if hasattr(np, "trapezoid"):
        return np.trapezoid(np.asarray(y), np.asarray(x))

    return np.trapz(np.asarray(y), np.asarray(x))


def auto_adjust_excel_column_width(worksheet):
    for col_cells in worksheet.iter_cols(min_row=1, max_row=worksheet.max_row):
        max_length = max(
            (len(str(cell.value)) for cell in col_cells if cell.value is not None),
            default=0
        )
        col_letter = get_column_letter(col_cells[0].column)
        worksheet.column_dimensions[col_letter].width = max_length + 6


def get_today_kst():
    return pd.Timestamp.now(tz="Asia/Seoul").date()


# =========================================================
# 신규 플랫폼 CSV 전처리
# =========================================================
def preprocess_new_platform_csv(uploaded_file):
    df_raw = pd.read_csv(
        uploaded_file,
        sep=";",
        encoding="utf-8-sig"
    )

    if df_raw.empty:
        raise ValueError("CSV 파일에 데이터가 없습니다.")

    timestamp_col = df_raw.columns[0]

    ct_rename = {}

    for col in df_raw.columns[1:]:
        ct_no = extract_ct_number(col)
        if ct_no is not None:
            ct_rename[col] = f"CT{ct_no}"

    if not ct_rename:
        raise ValueError("냉동CT 온도 컬럼을 찾지 못했습니다. CSV 컬럼명을 확인해 주세요.")

    df = df_raw.rename(columns={timestamp_col: "측정일시", **ct_rename})

    df["측정일시"] = pd.to_datetime(df["측정일시"], errors="coerce")
    df = df.dropna(subset=["측정일시"]).copy()

    ct_cols = sorted(ct_rename.values(), key=ct_sort_key)
    df = df[["측정일시"] + ct_cols].copy()

    df_long = pd.melt(
        df,
        id_vars=["측정일시"],
        value_vars=ct_cols,
        var_name="컨테이너",
        value_name="온도"
    )

    df_long["온도"] = pd.to_numeric(df_long["온도"], errors="coerce")

    abnormal_mask = df_long["온도"].lt(-50) | df_long["온도"].gt(60)

    df_abnormal = (
        df_long.loc[abnormal_mask, ["측정일시", "컨테이너", "온도"]]
        .copy()
        .sort_values(["측정일시", "컨테이너"])
        .reset_index(drop=True)
    )

    abnormal_count = int(abnormal_mask.sum())

    df_long.loc[abnormal_mask, "온도"] = np.nan

    excluded_count = int(df_long["온도"].isna().sum())

    df_long = df_long.dropna(subset=["온도"]).copy()

    df_long["측정일자"] = df_long["측정일시"].dt.date
    df_long["시각"] = df_long["측정일시"].dt.strftime("%H:%M:%S")
    df_long["요일"] = df_long["측정일시"].dt.day_name()
    df_long["시간(분)"] = (
        df_long["측정일시"].dt.hour * 60
        + df_long["측정일시"].dt.minute
    )

    df_long = (
        df_long
        .sort_values(["컨테이너", "측정일시"])
        .reset_index(drop=True)
    )

    return df_raw, df_long, df_abnormal, abnormal_count, excluded_count


# =========================================================
# 요약 계산
# =========================================================
@st.cache_data(show_spinner=False)
def calculate_summary(df_long):
    summary_list = []

    for (container, date), group in df_long.groupby(["컨테이너", "측정일자"], sort=False):
        group = group.dropna(subset=["온도"]).sort_values("측정일시")

        if group.empty:
            continue

        최저온도 = group["온도"].min()
        평균온도 = group["온도"].mean()
        전체시간 = group["시간(분)"].max() - group["시간(분)"].min()

        mask = group["온도"] < 0
        tmp = group.loc[mask, ["시간(분)", "온도"]].sort_values("시간(분)")

        x = tmp["시간(분)"]
        y = 0 - tmp["온도"]

        측정면적 = integrate_trapezoid(y, x) if not x.empty else 0
        목표면적 = 18 * 전체시간 if 전체시간 > 0 else 0
        냉동효율 = 측정면적 / 목표면적 if 목표면적 > 0 else 0

        영하15유지율 = (group["온도"] <= -15).mean() if len(group) > 0 else np.nan

        summary_list.append({
            "컨테이너": container,
            "측정일자": date,
            "요일": group["요일"].iloc[0],
            "최저온도": round(최저온도, 1),
            "평균누적온도": round(평균온도, 1),
            "측정면적": round(측정면적, 0),
            "목표면적": round(목표면적, 0),
            "냉동효율(%)": round(냉동효율 * 100, 1),
            "-15℃이하유지율(%)": round(영하15유지율 * 100, 1),
            "측정건수": len(group)
        })

    df_summary = pd.DataFrame(summary_list)

    if df_summary.empty:
        return df_summary

    df_summary["요일"] = df_summary["요일"].map({
        "Monday": "월요일",
        "Tuesday": "화요일",
        "Wednesday": "수요일",
        "Thursday": "목요일",
        "Friday": "금요일",
        "Saturday": "토요일",
        "Sunday": "일요일"
    })

    df_summary["측정면적"] = df_summary["측정면적"].round(0).astype(int)
    df_summary["목표면적"] = df_summary["목표면적"].round(0).astype(int)
    df_summary["측정건수"] = df_summary["측정건수"].round(0).astype(int)

    df_summary["최저온도"] = df_summary["최저온도"].round(1)
    df_summary["평균누적온도"] = df_summary["평균누적온도"].round(1)
    df_summary["냉동효율(%)"] = df_summary["냉동효율(%)"].round(1)
    df_summary["-15℃이하유지율(%)"] = df_summary["-15℃이하유지율(%)"].round(1)

    ct_order = sorted(df_summary["컨테이너"].unique(), key=ct_sort_key)
    df_summary["컨테이너"] = pd.Categorical(
        df_summary["컨테이너"],
        categories=ct_order,
        ordered=True
    )

    df_summary = (
        df_summary
        .sort_values(["컨테이너", "측정일자"])
        .reset_index(drop=True)
    )

    df_summary["컨테이너"] = df_summary["컨테이너"].astype(str)

    return df_summary


# =========================================================
# V4 상태 판정 로직
# =========================================================
def get_metric_flags(row):
    min_temp = row["최저온도"]
    eff = row["냉동효율(%)"]
    retention = row["-15℃이하유지율(%)"]

    temp_off = pd.notna(min_temp) and min_temp >= -16

    eff_low = pd.notna(eff) and eff < 70
    eff_high = pd.notna(eff) and eff > 120
    eff_off = eff_low or eff_high

    retention_off = pd.notna(retention) and retention <= 70
    eff_emergency = pd.notna(eff) and eff <= 60

    off_count = int(temp_off) + int(eff_off) + int(retention_off)

    issues = []

    if temp_off:
        issues.append(f"최저온도 {min_temp:.1f}℃")
    if eff_off:
        if eff_low:
            issues.append(f"냉동효율 저하 {eff:.1f}%")
        else:
            issues.append(f"냉동효율 과다 {eff:.1f}%")
    if retention_off:
        issues.append(f"-15℃ 유지율 저하 {retention:.1f}%")

    if not issues:
        issues.append("정상")

    return {
        "temp_off": temp_off,
        "eff_off": eff_off,
        "eff_low": eff_low,
        "eff_high": eff_high,
        "retention_off": retention_off,
        "eff_emergency": eff_emergency,
        "off_count": off_count,
        "issues": " / ".join(issues)
    }


def evaluate_daily_status(row):
    flags = get_metric_flags(row)

    if flags["off_count"] == 0:
        status = "정상"
        score = 0

    elif flags["eff_emergency"] or flags["off_count"] >= 2:
        status = "긴급점검"
        score = 3

    elif flags["off_count"] == 1 and flags["eff_high"]:
        status = "과냉주의"
        score = 1

    else:
        status = "주의"
        score = 1

    return status, score, flags["issues"], flags["off_count"], flags["eff_emergency"]


def calculate_deviation_score(row):
    score = 0

    min_temp = row.get("최저온도")
    eff = row.get("냉동효율(%)")
    retention = row.get("-15℃이하유지율(%)")

    if pd.notna(min_temp) and min_temp >= -16:
        score = max(score, min_temp - (-16))

    if pd.notna(eff) and eff < 70:
        score = max(score, 70 - eff)

    if pd.notna(eff) and eff > 120:
        score = max(score, eff - 120)

    if pd.notna(retention) and retention <= 70:
        score = max(score, 70 - retention)

    return round(float(score), 1)


@st.cache_data(show_spinner=False)
def build_status_tables(df_summary, today):
    daily_status_all = df_summary.copy()
    daily_status_all["측정일자_dt"] = pd.to_datetime(daily_status_all["측정일자"])

    status_results = daily_status_all.apply(evaluate_daily_status, axis=1)

    daily_status_all["상태"] = [result[0] for result in status_results]
    daily_status_all["상태점수"] = [result[1] for result in status_results]
    daily_status_all["이슈"] = [result[2] for result in status_results]
    daily_status_all["이탈수"] = [result[3] for result in status_results]
    daily_status_all["효율긴급"] = [result[4] for result in status_results]
    daily_status_all["이탈정도"] = daily_status_all.apply(calculate_deviation_score, axis=1)

    today_ts = pd.to_datetime(today)
    start_ts = today_ts - pd.Timedelta(days=2)

    recent_df = daily_status_all[
        (daily_status_all["측정일자_dt"] >= start_ts)
        & (daily_status_all["측정일자_dt"] <= today_ts)
    ].copy()

    all_containers = sorted(df_summary["컨테이너"].unique(), key=ct_sort_key)

    container_rows = []
    check_rows = []

    for ct in all_containers:
        ct_all = daily_status_all[daily_status_all["컨테이너"] == ct].copy()
        ct_recent = recent_df[recent_df["컨테이너"] == ct].copy()
        today_row = ct_recent[ct_recent["측정일자_dt"] == today_ts].copy()

        if today_row.empty:
            last_date = pd.to_datetime(ct_all["측정일자_dt"].max()).strftime("%Y-%m-%d") if not ct_all.empty else "-"
            status = "데이터 연결 이상"
            status_score = 4
            대표이슈 = f"오늘 데이터 없음 / 최근 측정일 {last_date}"
            today_off_count = np.nan
            min_eff = np.nan
            max_eff = np.nan
            min_retention = np.nan
            worst_min_temp = np.nan
            issue_days = int((ct_recent["이탈수"] > 0).sum()) if not ct_recent.empty else 0

            container_rows.append({
                "컨테이너": ct,
                "종합상태": status,
                "상태점수": status_score,
                "대표일자": str(today),
                "대표이슈": 대표이슈,
                "오늘이탈수": today_off_count,
                "최근3일이슈일": issue_days,
                "최악최저온도": worst_min_temp,
                "최저냉동효율": min_eff,
                "최고냉동효율": max_eff,
                "최저유지율": min_retention,
                "최근측정일": last_date
            })

            check_rows.append({
                "상태": status,
                "컨테이너": ct,
                "측정일자": str(today),
                "이슈": 대표이슈,
                "최저온도": np.nan,
                "냉동효율(%)": np.nan,
                "-15℃이하유지율(%)": np.nan,
                "우선순위": status_score,
                "이탈정도": 999
            })

            continue

        today_record = today_row.iloc[0]
        today_flags = get_metric_flags(today_record)
        today_off_count = today_flags["off_count"]

        recent_issue_days = int((ct_recent["이탈수"] > 0).sum())
        recent_total_off = int(ct_recent["이탈수"].sum())
        recent_any_all3 = bool((ct_recent["이탈수"] == 3).any())

        recent_two_days_start = today_ts - pd.Timedelta(days=1)
        recent_two = ct_recent[ct_recent["측정일자_dt"] >= recent_two_days_start]
        recent_two_ok = (
            len(recent_two) >= 2
            and int(recent_two["이탈수"].sum()) == 0
        )

        if today_flags["eff_emergency"] or today_off_count >= 2:
            status = "긴급점검"
            status_score = 3
            대표이슈 = today_flags["issues"]

        elif recent_any_all3:
            status = "위험"
            status_score = 2
            risk_row = ct_recent[ct_recent["이탈수"] == 3].sort_values("측정일자_dt", ascending=False).iloc[0]
            대표이슈 = risk_row["이슈"]

        elif today_off_count == 1 and today_flags["eff_high"]:
            status = "과냉주의"
            status_score = 1
            대표이슈 = today_flags["issues"]

        elif recent_total_off == 0:
            status = "정상"
            status_score = 0
            대표이슈 = "정상"

        elif recent_total_off == 1:
            status = "정상"
            status_score = 0
            issue_row = ct_recent[ct_recent["이탈수"] > 0].iloc[0]
            대표이슈 = f"단일 이탈 이력: {issue_row['이슈']}"

        elif recent_two_ok:
            status = "주의"
            status_score = 1
            대표이슈 = "최근 2일 정상 / 이전 이탈 이력 있음"

        else:
            status = "주의"
            status_score = 1
            issue_row = ct_recent.sort_values(["상태점수", "이탈정도"], ascending=[False, False]).iloc[0]
            대표이슈 = issue_row["이슈"]

        if ct_recent.empty:
            min_eff = np.nan
            max_eff = np.nan
            min_retention = np.nan
            worst_min_temp = np.nan
        else:
            min_eff = ct_recent["냉동효율(%)"].min()
            max_eff = ct_recent["냉동효율(%)"].max()
            min_retention = ct_recent["-15℃이하유지율(%)"].min()
            worst_min_temp = ct_recent["최저온도"].max()

        container_rows.append({
            "컨테이너": ct,
            "종합상태": status,
            "상태점수": status_score,
            "대표일자": str(today),
            "대표이슈": 대표이슈,
            "오늘이탈수": today_off_count,
            "최근3일이슈일": recent_issue_days,
            "최악최저온도": round(worst_min_temp, 1) if pd.notna(worst_min_temp) else np.nan,
            "최저냉동효율": round(min_eff, 1) if pd.notna(min_eff) else np.nan,
            "최고냉동효율": round(max_eff, 1) if pd.notna(max_eff) else np.nan,
            "최저유지율": round(min_retention, 1) if pd.notna(min_retention) else np.nan,
            "최근측정일": pd.to_datetime(ct_all["측정일자_dt"].max()).strftime("%Y-%m-%d") if not ct_all.empty else "-"
        })

        if status != "정상":
            check_source = today_record if status in ["긴급점검", "과냉주의"] else ct_recent.sort_values(["상태점수", "이탈정도"], ascending=[False, False]).iloc[0]

            check_rows.append({
                "상태": status,
                "컨테이너": ct,
                "측정일자": pd.to_datetime(check_source["측정일자"]).strftime("%Y-%m-%d"),
                "이슈": 대표이슈,
                "최저온도": check_source["최저온도"],
                "냉동효율(%)": check_source["냉동효율(%)"],
                "-15℃이하유지율(%)": check_source["-15℃이하유지율(%)"],
                "우선순위": status_score,
                "이탈정도": check_source["이탈정도"] if "이탈정도" in check_source else calculate_deviation_score(check_source)
            })

    container_status_df = pd.DataFrame(container_rows)
    container_status_df["컨테이너정렬키"] = container_status_df["컨테이너"].apply(ct_sort_key)
    container_status_df = (
        container_status_df
        .sort_values(["상태점수", "컨테이너정렬키"], ascending=[False, True])
        .drop(columns=["컨테이너정렬키"])
        .reset_index(drop=True)
    )

    check_list_df = pd.DataFrame(check_rows)

    if not check_list_df.empty:
        check_list_df = (
            check_list_df
            .sort_values(["우선순위", "이탈정도"], ascending=[False, False])
            .drop(columns=["우선순위", "이탈정도"])
            .reset_index(drop=True)
        )

    return daily_status_all, recent_df, container_status_df, check_list_df, start_ts.date(), today


# =========================================================
# V4 대시보드 렌더링
# =========================================================
def status_color(status):
    if status == "데이터 연결 이상":
        return {
            "bg": "#1f2937",
            "border": "#9ca3af",
            "text": "#e5e7eb",
            "badge": "#6b7280"
        }

    if status == "긴급점검":
        return {
            "bg": "#3f0a0a",
            "border": "#991b1b",
            "text": "#fee2e2",
            "badge": "#7f1d1d"
        }

    if status == "위험":
        return {
            "bg": "#3b1111",
            "border": "#ef4444",
            "text": "#fecaca",
            "badge": "#ef4444"
        }

    if status == "과냉주의":
        return {
            "bg": "#0b1f3a",
            "border": "#3b82f6",
            "text": "#dbeafe",
            "badge": "#2563eb"
        }

    if status == "주의":
        return {
            "bg": "#3a2a0a",
            "border": "#f59e0b",
            "text": "#fde68a",
            "badge": "#f59e0b"
        }

    return {
        "bg": "#10291a",
        "border": "#22c55e",
        "text": "#bbf7d0",
        "badge": "#22c55e"
    }


def render_status_cards(container_status_df):
    st.markdown("#### 🧊 컨테이너 상태 카드")

    if container_status_df.empty:
        st.info("컨테이너 상태 데이터가 없습니다.")
        return

    sorted_cards = container_status_df.copy()
    sorted_cards["컨테이너정렬키"] = sorted_cards["컨테이너"].apply(ct_sort_key)
    sorted_cards = sorted_cards.sort_values("컨테이너정렬키").drop(columns=["컨테이너정렬키"])

    cards_per_row = 7

    for start_idx in range(0, len(sorted_cards), cards_per_row):
        row_cards = sorted_cards.iloc[start_idx:start_idx + cards_per_row]
        cols = st.columns(cards_per_row)

        for idx, (_, row) in enumerate(row_cards.iterrows()):
            colors = status_color(row["종합상태"])

            eff_text = "-"
            if pd.notna(row["최저냉동효율"]) and pd.notna(row["최고냉동효율"]):
                eff_text = f"{row['최저냉동효율']:.1f}~{row['최고냉동효율']:.1f}%"

            retention_text = "-"
            if pd.notna(row["최저유지율"]):
                retention_text = f"{row['최저유지율']:.1f}%"

            today_off = "-"
            if pd.notna(row["오늘이탈수"]):
                today_off = f"{int(row['오늘이탈수'])}/3"

            html = f"""
            <div style="
                border: 1.2px solid {colors['border']};
                background: {colors['bg']};
                border-radius: 12px;
                padding: 10px 10px 9px 10px;
                min-height: 104px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.16);
            ">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <div style="font-size:19px; font-weight:850; color:white;">{row['컨테이너']}</div>
                    <div style="
                        background:{colors['badge']};
                        color:white;
                        border-radius:999px;
                        padding:2px 8px;
                        font-size:11px;
                        font-weight:800;
                    ">{row['종합상태']}</div>
                </div>
                <div style="color:{colors['text']}; font-size:11.5px; line-height:1.55;">
                    <b>오늘 이탈</b> {today_off}<br>
                    <b>최근3일 이슈</b> {row['최근3일이슈일']}일<br>
                    <b>효율</b> {eff_text}<br>
                    <b>유지율 최저</b> {retention_text}
                </div>
            </div>
            """

            with cols[idx]:
                st.markdown(html, unsafe_allow_html=True)


def render_v4_summary_dashboard(
    daily_status_df,
    recent_df,
    container_status_df,
    check_list_df,
    abnormal_count,
    excluded_count,
    dashboard_start,
    dashboard_today
):
    st.divider()
    st.header("🚦 V4 컨테이너 상태 대시보드")
    st.caption(
        f"상단 대시보드는 한국 날짜 기준 최근 3일({dashboard_start} ~ {dashboard_today})만 사용합니다. "
        "기존 상세 분석 영역은 전체 기간을 유지합니다."
    )

    total_ct = len(container_status_df)
    emergency_count = int((container_status_df["종합상태"] == "긴급점검").sum())
    danger_count = int((container_status_df["종합상태"] == "위험").sum())
    overcool_count = int((container_status_df["종합상태"] == "과냉주의").sum())
    caution_count = int((container_status_df["종합상태"] == "주의").sum())
    normal_count = int((container_status_df["종합상태"] == "정상").sum())
    connection_count = int((container_status_df["종합상태"] == "데이터 연결 이상").sum())

    if not check_list_df.empty:
        worst = check_list_df.iloc[0]
        worst_ct = worst["컨테이너"]
        worst_issue = worst["이슈"]
    else:
        worst_ct = "-"
        worst_issue = "점검 대상 없음"

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

    with c1:
        st.metric("전체", f"{total_ct}개")
    with c2:
        st.metric("정상", f"{normal_count}개")
    with c3:
        st.metric("주의", f"{caution_count}개")
    with c4:
        st.metric("과냉주의", f"{overcool_count}개")
    with c5:
        st.metric("위험", f"{danger_count}개")
    with c6:
        st.metric("긴급점검", f"{emergency_count}개")
    with c7:
        st.metric("연결 이상", f"{connection_count}개")

    st.caption(
        f"최우선 점검: {worst_ct} / 대표 이슈: {worst_issue} / "
        f"이상값 {abnormal_count:,}건 / 분석 제외 {excluded_count:,}건"
    )

    render_status_cards(container_status_df)

    st.markdown("#### 🔎 우선 점검 리스트")

    if check_list_df.empty:
        st.success("✅ 최근 3일 기준 우선 점검 대상이 없습니다.")
    else:
        st.dataframe(
            check_list_df,
            use_container_width=True,
            height=230
        )

    st.markdown("#### 🗓️ 컨테이너 × 일자 히트맵")

    heatmap_metric = st.radio(
        "히트맵 기준",
        ["종합상태", "최저온도", "냉동효율(%)", "-15℃이하유지율(%)"],
        horizontal=True
    )

    fig_heatmap = create_container_heatmap(daily_status_df, heatmap_metric)

    st.plotly_chart(
        fig_heatmap,
        use_container_width=True,
        config={
            "displayModeBar": True,
            "responsive": True
        }
    )


def create_container_heatmap(daily_status_df, heatmap_metric):
    df = daily_status_df.copy()
    df["측정일자_dt"] = pd.to_datetime(df["측정일자"])
    df["날짜"] = df["측정일자_dt"].dt.strftime("%m-%d")
    df["컨테이너정렬키"] = df["컨테이너"].apply(ct_sort_key)
    df = df.sort_values(["컨테이너정렬키", "측정일자_dt"])

    containers = sorted(df["컨테이너"].unique(), key=ct_sort_key)
    dates = sorted(df["날짜"].unique())

    if heatmap_metric == "종합상태":
        status_score = {
            "정상": 0,
            "주의": 1,
            "과냉주의": 2,
            "위험": 3,
            "긴급점검": 4,
            "데이터없음": 5
        }

        z_df = (
            df.pivot(index="컨테이너", columns="날짜", values="상태")
            .reindex(index=containers, columns=dates)
            .fillna("데이터없음")
        )

        issue_df = (
            df.pivot(index="컨테이너", columns="날짜", values="이슈")
            .reindex(index=containers, columns=dates)
            .fillna("데이터 없음")
        )

        z = z_df.replace(status_score).astype(float).values
        text = z_df.values
        issues = issue_df.values
        customdata = np.dstack([text, issues])

        fig = go.Figure(
            data=go.Heatmap(
                z=z,
                x=dates,
                y=containers,
                customdata=customdata,
                xgap=2,
                ygap=2,
                colorscale=[
                    [0.00, "#22c55e"],  # Normal
                    [0.16, "#22c55e"],
                    [0.17, "#f59e0b"],  # Caution
                    [0.32, "#f59e0b"],
                    [0.33, "#2563eb"],  # Overcool caution
                    [0.49, "#2563eb"],
                    [0.50, "#ef4444"],  # Risk
                    [0.66, "#ef4444"],
                    [0.67, "#7f1d1d"],  # Emergency
                    [0.83, "#7f1d1d"],
                    [0.84, "#6b7280"],  # No data
                    [1.00, "#6b7280"],
                ],
                zmin=0,
                zmax=5,
                colorbar=dict(
                    title="Status",
                    tickvals=[0, 1, 2, 3, 4, 5],
                    ticktext=["Normal", "Caution", "Overcool", "Risk", "Emergency", "No data"]
                ),
                hovertemplate=(
                    "Container: %{y}<br>"
                    "Date: %{x}<br>"
                    "Status: %{customdata[0]}<br>"
                    "Issue: %{customdata[1]}"
                    "<extra></extra>"
                )
            )
        )

        fig.update_layout(
            title="Container x Date Status Heatmap",
            height=430,
            margin=dict(l=50, r=30, t=60, b=40),
            plot_bgcolor="#000000",
            paper_bgcolor="rgba(0,0,0,0)"
        )

        return fig

    value_df = (
        df.pivot(index="컨테이너", columns="날짜", values=heatmap_metric)
        .reindex(index=containers, columns=dates)
    )

    issue_df = (
        df.pivot(index="컨테이너", columns="날짜", values="이슈")
        .reindex(index=containers, columns=dates)
        .fillna("데이터 없음")
    )

    z = value_df.astype(float).values
    issues = issue_df.values
    customdata = np.dstack([issues])

    metric_label_map = {
        "최저온도": "Min Temperature (°C)",
        "냉동효율(%)": "Freezing Efficiency (%)",
        "-15℃이하유지율(%)": "Below -15°C Retention (%)"
    }

    title = metric_label_map.get(heatmap_metric, heatmap_metric)

    if heatmap_metric == "최저온도":
        colorscale = [
            [0.0, "#1d4ed8"],
            [0.35, "#60a5fa"],
            [0.55, "#e0f2fe"],
            [0.72, "#facc15"],
            [1.0, "#dc2626"],
        ]
        zmin = -25
        zmax = -10

    elif heatmap_metric == "냉동효율(%)":
        colorscale = [
            [0.0, "#dc2626"],
            [0.35, "#facc15"],
            [0.50, "#22c55e"],
            [0.75, "#22c55e"],
            [1.0, "#2563eb"],
        ]
        zmin = 40
        zmax = 140

    else:
        colorscale = [
            [0.0, "#dc2626"],
            [0.35, "#facc15"],
            [0.60, "#22c55e"],
            [1.0, "#16a34a"],
        ]
        zmin = 0
        zmax = 100

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=dates,
            y=containers,
            customdata=customdata,
            xgap=2,
            ygap=2,
            colorscale=colorscale,
            zmin=zmin,
            zmax=zmax,
            colorbar=dict(title=title),
            hovertemplate=(
                "Container: %{y}<br>"
                "Date: %{x}<br>"
                + title + ": %{z:.1f}<br>"
                "Issue: %{customdata[0]}"
                "<extra></extra>"
            )
        )
    )

    fig.update_layout(
        title=f"Container x Date {title} Heatmap",
        height=430,
        margin=dict(l=50, r=30, t=60, b=40),
        plot_bgcolor="#000000",
        paper_bgcolor="rgba(0,0,0,0)"
    )

    return fig


# =========================================================
# 날짜별 지표 요약 테이블
# =========================================================
@st.cache_data(show_spinner=False)
def build_metric_table(df_summary):
    metrics = ["최저온도", "냉동효율(%)", "-15℃이하유지율(%)"]

    df_filtered = df_summary[
        ["컨테이너", "측정일자"] + metrics
    ].copy()

    for metric in metrics:
        df_filtered[metric] = df_filtered[metric].round(1)

    df_filtered["측정일자"] = pd.to_datetime(df_filtered["측정일자"])
    df_filtered.sort_values("측정일자", inplace=True)

    result_blocks = []

    for metric in metrics:
        pivot = df_filtered.pivot(
            index="측정일자",
            columns="컨테이너",
            values=metric
        )

        block = pivot.T.copy()
        block.insert(0, "지표", metric)
        block.insert(0, "컨테이너", block.index)
        result_blocks.append(block)

    df_final = pd.concat(result_blocks, axis=0)

    new_columns = []

    for col in df_final.columns:
        if isinstance(col, pd.Timestamp):
            new_columns.append(col.strftime("%m월 %d일"))
        else:
            new_columns.append(col)

    df_final.columns = new_columns

    df_final["컨테이너정렬키"] = df_final["컨테이너"].apply(ct_sort_key)

    metric_order = {
        "최저온도": 1,
        "냉동효율(%)": 2,
        "-15℃이하유지율(%)": 3
    }

    df_final["지표정렬키"] = df_final["지표"].map(metric_order)

    df_final = (
        df_final
        .sort_values(["컨테이너정렬키", "지표정렬키"])
        .drop(columns=["컨테이너정렬키", "지표정렬키"])
        .reset_index(drop=True)
    )

    date_cols = [col for col in df_final.columns if col not in ["컨테이너", "지표"]]

    for col in date_cols:
        df_final[col] = pd.to_numeric(df_final[col], errors="coerce").round(1)

    return df_final


def style_metric_table(df):
    def style_row(row):
        styles = [""] * len(row)
        metric = row.get("지표", "")

        for idx, col in enumerate(row.index):
            if col in ["컨테이너", "지표"]:
                continue

            value = row[col]

            if pd.isna(value):
                continue

            try:
                value = float(value)
            except Exception:
                continue

            if metric == "최저온도":
                if value >= -16:
                    styles[idx] = "color: #ff4b4b; font-weight: 700;"

            elif metric == "냉동효율(%)":
                if value < 70:
                    styles[idx] = "color: #ff4b4b; font-weight: 700;"
                elif value > 120:
                    styles[idx] = "color: #3b82f6; font-weight: 700;"

            elif metric == "-15℃이하유지율(%)":
                if value <= 70:
                    styles[idx] = "color: #ff4b4b; font-weight: 700;"

        return styles

    return df.style.apply(style_row, axis=1).format(
        formatter="{:.1f}",
        subset=[col for col in df.columns if col not in ["컨테이너", "지표"]]
    )


# =========================================================
# 그래프 생성
# =========================================================
def create_temperature_chart(plot_df, title, figsize=(6, 2.3)):
    fig, ax = plt.subplots(figsize=figsize)

    plot_df = plot_df.sort_values("측정일시")

    ax.plot(
        plot_df["측정일시"],
        plot_df["온도"],
        label="Temperature",
        color="orange",
        marker="o",
        markersize=0.5,
        linewidth=0.6
    )

    ax.axhline(0, color="red", linestyle="--", linewidth=0.6, label="0°C")
    ax.axhline(-15, color="green", linestyle=":", linewidth=0.6, label="-15°C")
    ax.axhline(-18, color="blue", linestyle="--", linewidth=0.6, label="-18°C")

    ax.fill_between(
        plot_df["측정일시"],
        plot_df["온도"],
        0,
        where=(plot_df["온도"] < 0),
        interpolate=True,
        color="skyblue",
        alpha=0.35,
        label="Area (Temp < 0°C)"
    )

    ax.set_ylim(-22, 36)
    ax.set_title(title, fontsize=7)
    ax.set_xlabel("Timestamp", fontsize=6)
    ax.set_ylabel("Temp (°C)", fontsize=6)
    ax.tick_params(axis="x", labelsize=5, rotation=25)
    ax.tick_params(axis="y", labelsize=5)
    ax.legend(loc="upper right", fontsize=5)
    ax.grid(True, linewidth=0.3, alpha=0.6)

    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    formatter = mdates.ConciseDateFormatter(locator)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)

    fig.tight_layout()

    return fig


def create_daily_metric_compare_chart(df_summary, selected_metric, selected_containers):
    metric_label_map = {
        "최저온도": "Min Temperature (°C)",
        "냉동효율(%)": "Freezing Efficiency (%)",
        "-15℃이하유지율(%)": "Below -15°C Retention (%)"
    }

    selected_metric_label = metric_label_map.get(selected_metric, selected_metric)

    plot_df = df_summary[
        df_summary["컨테이너"].isin(selected_containers)
    ].copy()

    plot_df["측정일자"] = pd.to_datetime(plot_df["측정일자"])
    plot_df = plot_df.sort_values(["컨테이너", "측정일자"])

    fig = go.Figure()

    for ct in sorted(selected_containers, key=ct_sort_key):
        ct_df = plot_df[plot_df["컨테이너"] == ct].copy()

        if ct_df.empty:
            continue

        fig.add_trace(
            go.Scatter(
                x=ct_df["측정일자"],
                y=ct_df[selected_metric],
                mode="lines+markers",
                name=ct,
                line=dict(width=2),
                marker=dict(size=6),
                hovertemplate=(
                    "<b>Container: " + ct + "</b><br>"
                    "Date: %{x|%Y-%m-%d}<br>"
                    + selected_metric_label + ": %{y:.1f}"
                    "<extra></extra>"
                )
            )
        )

    shapes = []

    if selected_metric == "-15℃이하유지율(%)":
        shapes.append({
            "type": "line",
            "xref": "paper",
            "x0": 0,
            "x1": 1,
            "yref": "y",
            "y0": 70,
            "y1": 70,
            "line": {
                "dash": "dash",
                "width": 1
            }
        })
        y_range = [0, 105]

    elif selected_metric == "냉동효율(%)":
        shapes.extend([
            {
                "type": "line",
                "xref": "paper",
                "x0": 0,
                "x1": 1,
                "yref": "y",
                "y0": 70,
                "y1": 70,
                "line": {
                    "dash": "dash",
                    "width": 1
                }
            },
            {
                "type": "line",
                "xref": "paper",
                "x0": 0,
                "x1": 1,
                "yref": "y",
                "y0": 120,
                "y1": 120,
                "line": {
                    "dash": "dash",
                    "width": 1
                }
            }
        ])
        y_range = None

    elif selected_metric == "최저온도":
        shapes.extend([
            {
                "type": "line",
                "xref": "paper",
                "x0": 0,
                "x1": 1,
                "yref": "y",
                "y0": -16,
                "y1": -16,
                "line": {
                    "dash": "dot",
                    "width": 1
                }
            },
            {
                "type": "line",
                "xref": "paper",
                "x0": 0,
                "x1": 1,
                "yref": "y",
                "y0": -18,
                "y1": -18,
                "line": {
                    "dash": "dash",
                    "width": 1
                }
            }
        ])
        y_range = None

    else:
        y_range = None

    fig.update_layout(
        title=f"Daily {selected_metric_label} Trend",
        xaxis_title="Date",
        yaxis_title=selected_metric_label,
        height=420,
        margin=dict(l=40, r=30, t=60, b=40),
        hovermode="closest",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10)
        ),
        shapes=shapes
    )

    fig.update_xaxes(
        tickformat="%m-%d",
        showgrid=True
    )

    fig.update_yaxes(
        showgrid=True
    )

    if y_range is not None:
        fig.update_yaxes(range=y_range)

    return fig


@st.cache_data(show_spinner=False)
def build_selected_metric_pivot_table(df_summary, selected_metric, selected_containers_tuple):
    selected_containers = list(selected_containers_tuple)

    table_df = df_summary[
        df_summary["컨테이너"].isin(selected_containers)
    ].copy()

    table_df["측정일자"] = pd.to_datetime(table_df["측정일자"])
    table_df[selected_metric] = table_df[selected_metric].round(1)

    pivot = table_df.pivot(
        index="측정일자",
        columns="컨테이너",
        values=selected_metric
    )

    selected_containers_sorted = sorted(selected_containers, key=ct_sort_key)
    pivot = pivot.reindex(columns=selected_containers_sorted)

    pivot = pivot.sort_index()
    pivot.index = pivot.index.strftime("%m월 %d일")

    pivot_reset = pivot.reset_index()
    pivot_reset.rename(columns={"측정일자": "측정일자"}, inplace=True)

    return pivot_reset


# =========================================================
# 엑셀 다운로드 생성
# =========================================================
def create_excel_download(
    df_summary,
    df_long,
    df_metric_table,
    df_abnormal,
    container_status_df=None,
    check_list_df=None,
    daily_status_df=None
):
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_summary.to_excel(writer, index=False, sheet_name="Summary")
        workbook = writer.book
        worksheet_summary = writer.sheets["Summary"]
        auto_adjust_excel_column_width(worksheet_summary)

        graph_ws = workbook.create_sheet(title="chart")
        row_offset = 2

        ct_list = sorted(df_long["컨테이너"].unique(), key=ct_sort_key)

        for ct in ct_list:
            plot_df = df_long[df_long["컨테이너"] == ct].copy()

            if plot_df.empty:
                continue

            fig = create_temperature_chart(
                plot_df=plot_df,
                title=f"{ct} Temperature Profile",
                figsize=(8, 3)
            )

            img_buf = io.BytesIO()
            fig.savefig(img_buf, format="png", bbox_inches="tight", dpi=150)
            plt.close(fig)
            img_buf.seek(0)

            img = XLImage(img_buf)
            img.anchor = f"B{row_offset}"
            graph_ws.add_image(img)

            row_offset += 18

        df_metric_table.to_excel(writer, index=False, sheet_name="table")
        worksheet_table = writer.sheets["table"]
        auto_adjust_excel_column_width(worksheet_table)

        if df_abnormal.empty:
            df_abnormal_export = pd.DataFrame({
                "메시지": ["이상값 없음"]
            })
        else:
            df_abnormal_export = df_abnormal.copy()

        df_abnormal_export.to_excel(writer, index=False, sheet_name="abnormal_values")
        worksheet_abnormal = writer.sheets["abnormal_values"]
        auto_adjust_excel_column_width(worksheet_abnormal)

        if container_status_df is not None:
            container_status_df.to_excel(writer, index=False, sheet_name="v4_container_status")
            auto_adjust_excel_column_width(writer.sheets["v4_container_status"])

        if check_list_df is not None:
            if check_list_df.empty:
                pd.DataFrame({"메시지": ["점검 대상 없음"]}).to_excel(
                    writer,
                    index=False,
                    sheet_name="v4_check_list"
                )
            else:
                check_list_df.to_excel(writer, index=False, sheet_name="v4_check_list")
            auto_adjust_excel_column_width(writer.sheets["v4_check_list"])

        if daily_status_df is not None:
            export_daily = daily_status_df.copy()
            if "측정일자_dt" in export_daily.columns:
                export_daily = export_daily.drop(columns=["측정일자_dt"])
            export_daily.to_excel(writer, index=False, sheet_name="v4_daily_status")
            auto_adjust_excel_column_width(writer.sheets["v4_daily_status"])

    output.seek(0)

    return output


# =========================================================
# Streamlit 실행부
# =========================================================
uploaded_file = st.file_uploader(
    "신규 플랫폼 CSV 파일을 업로드하세요",
    type="csv"
)

if uploaded_file is not None:
    try:
        df_raw, df_long, df_abnormal, abnormal_count, excluded_count = (
            preprocess_new_platform_csv(uploaded_file)
        )

        if df_long.empty:
            st.error("분석 가능한 온도 데이터가 없습니다. CSV 파일 또는 이상값 기준을 확인해 주세요.")
            st.stop()

        df_summary = calculate_summary(df_long)

        if df_summary.empty:
            st.error("요약 결과를 생성하지 못했습니다.")
            st.stop()

        df_metric_table = build_metric_table(df_summary)

        today = get_today_kst()

        daily_status_df, recent_df, container_status_df, check_list_df, dashboard_start, dashboard_today = (
            build_status_tables(df_summary, today)
        )

        period_dates = pd.date_range(
            start=dashboard_start,
            end=dashboard_today,
            freq="D"
        ).date.tolist()
        
        report_meta = report_payload.build_report_meta(
            report_date=dashboard_today,
            period_start=dashboard_start,
            period_end=dashboard_today,
            period_dates=period_dates,
            uploaded_filename=uploaded_file.name,
            dashboard_period_days=len(period_dates),
        )
        
        summary_payload = report_payload.build_summary(container_status_df)

        heatmap_rows_payload = report_payload.build_heatmap_rows(
            daily_status_df=daily_status_df,
            container_status_df=container_status_df,
            period_dates=period_dates,
        )

        check_list_payload = report_payload.build_check_list(
            check_list_df=check_list_df,
            container_status_df=container_status_df,
        )

        metrics_payload = report_payload.build_metrics(container_status_df)

        data_quality_payload = report_payload.build_data_quality(
            df_raw=df_raw,
            df_long=df_long,
            abnormal_count=abnormal_count,
            excluded_count=excluded_count,
        )

        report_payload_json = report_payload.build_report_payload(
            mode="draft",
            report_meta=report_meta,
            summary=summary_payload,
            heatmap_rows=heatmap_rows_payload,
            check_list=check_list_payload,
            metrics=metrics_payload,
            data_quality=data_quality_payload,
        )

        
        with st.expander("🧪 자동메일 payload 확인"):
            st.write("schema_version:", report_payload.SCHEMA_VERSION)
            st.write("source:", report_payload.SOURCE)
        
            st.markdown("##### report_meta")
            st.json(report_meta)
        
            st.markdown("##### summary")
            st.json(summary_payload)

            st.markdown("##### heatmap_rows")
            st.caption(f"heatmap_rows total: {len(heatmap_rows_payload)}")
            st.json(heatmap_rows_payload[:3])

            st.markdown("##### check_list")
            st.caption(f"check_list total: {len(check_list_payload)}")
            st.json(check_list_payload)

            st.markdown("##### metrics")
            st.caption(f"metrics total: {len(metrics_payload)}")
            st.json(metrics_payload[:3])

            st.markdown("##### data_quality")
            st.json(data_quality_payload)

            st.markdown("##### final payload preview")
            st.caption("실제 token은 화면에 노출하지 않고, 전송 직전에 Streamlit Secrets에서 주입할 예정입니다.")
            st.json(report_payload_json)

            st.markdown("#### 📡 Apps Script Webhook 연결 테스트")

            if st.button("Webhook ping 테스트", use_container_width=True):
                webhook_url = st.secrets.get("APPS_SCRIPT_WEBHOOK_URL", "")
                webhook_token = st.secrets.get("REPORT_WEBHOOK_TOKEN", "")
            
                ping_result = webhook_client.post_webhook_ping(
                    webhook_url=webhook_url,
                    webhook_token=webhook_token,
                )
            
                if ping_result.get("ok"):
                    st.success("✅ Apps Script Webhook ping 성공")
                else:
                    st.error("❌ Apps Script Webhook ping 실패")
            
                # token은 표시하지 않음
                st.json(ping_result)
        
        st.success("✅ 신규 플랫폼 CSV 데이터 불러오기 및 전처리 완료")

        render_v4_summary_dashboard(
            daily_status_df=daily_status_df,
            recent_df=recent_df,
            container_status_df=container_status_df,
            check_list_df=check_list_df,
            abnormal_count=abnormal_count,
            excluded_count=excluded_count,
            dashboard_start=dashboard_start,
            dashboard_today=dashboard_today
        )

        st.divider()
        st.header("📋 기존 상세 분석 영역")
        st.caption("기존 부서별 확인 방식은 아래에 그대로 유지했습니다.")

        min_date = df_long["측정일시"].min()
        max_date = df_long["측정일시"].max()
        ct_count = df_long["컨테이너"].nunique()
        valid_count = len(df_long)

        col_a, col_b, col_c, col_d = st.columns(4)

        with col_a:
            st.metric("측정 시작", min_date.strftime("%Y-%m-%d %H:%M"))

        with col_b:
            st.metric("측정 종료", max_date.strftime("%Y-%m-%d %H:%M"))

        with col_c:
            st.metric("컨테이너 수", f"{ct_count}개")

        with col_d:
            st.metric("유효 측정건수", f"{valid_count:,}건")

        col_e, col_f = st.columns(2)

        with col_e:
            st.metric("이상값 대체 건수", f"{abnormal_count:,}건")


        if abnormal_count > 0:
            with st.expander("⚠️ 이상값 목록 확인 (-50℃ 미만 또는 60℃ 초과)"):
                st.dataframe(
                    df_abnormal,
                    use_container_width=True,
                    height=220
                )

        st.subheader("📊 컨테이너별 날짜별 분석결과")
        st.dataframe(
            df_summary,
            use_container_width=True,
            height=350
        )

        excel_output = create_excel_download(
            df_summary=df_summary,
            df_long=df_long,
            df_metric_table=df_metric_table,
            df_abnormal=df_abnormal,
            container_status_df=container_status_df,
            check_list_df=check_list_df,
            daily_status_df=daily_status_df
        )

        start_date = df_long["측정일자"].min().strftime("%y%m%d")
        end_date = df_long["측정일자"].max().strftime("%y%m%d")
        filename = f"{start_date}_{end_date}_freezer_ct_dashboard_v4.xlsx"

        st.download_button(
            label="📥 분석결과 엑셀 다운로드 (V4 대시보드 + 기존 분석 포함)",
            data=excel_output,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        st.subheader("📈 온도 추이 그래프")

        col1, col2 = st.columns([1, 5])

        ct_list = sorted(df_summary["컨테이너"].unique(), key=ct_sort_key)

        with col1:
            selected_container = st.selectbox(
                "Select Container",
                ct_list
            )

            available_dates = (
                df_summary[df_summary["컨테이너"] == selected_container]["측정일자"]
                .sort_values()
                .unique()
                .tolist()
            )

            available_dates.insert(0, "전체")

            selected_date = st.selectbox(
                "Select Date",
                available_dates
            )

        if selected_date == "전체":
            plot_df = df_long[
                df_long["컨테이너"] == selected_container
            ].copy()
            title_suffix = "All Dates"
        else:
            plot_df = df_long[
                (df_long["컨테이너"] == selected_container)
                & (df_long["측정일자"] == selected_date)
            ].copy()
            title_suffix = str(selected_date)

        if plot_df.empty:
            st.warning("선택한 조건에 해당하는 그래프 데이터가 없습니다.")
        else:
            fig = create_temperature_chart(
                plot_df=plot_df,
                title=f"{selected_container} | {title_suffix} Temperature Profile",
                figsize=(6, 2.3)
            )

            with col2:
                st.pyplot(fig)

        st.subheader("📊 컨테이너별 최저온도 / 냉동효율 / -15℃ 이하 유지율 요약 테이블")
        st.caption(
            "표시 기준: 최저온도 -16℃ 이상 빨간색 / "
            "냉동효율 70% 미만 빨간색, 120% 초과 파란색 / "
            "-15℃ 이하 유지율 70% 이하 빨간색"
        )

        styled_metric_table = style_metric_table(df_metric_table)

        st.dataframe(
            styled_metric_table,
            use_container_width=True,
            height=320
        )

        st.divider()
        st.subheader("📈 일자별 지표 비교 그래프")
        st.caption("커서를 그래프의 선 또는 점에 올리면 해당 컨테이너 정보만 표시됩니다.")

        metric_options = [
            "최저온도",
            "냉동효율(%)",
            "-15℃이하유지율(%)"
        ]

        metric_short_label = {
            "최저온도": "최저온도",
            "냉동효율(%)": "냉동효율(%)",
            "-15℃이하유지율(%)": "-15℃ 유지율(%)"
        }

        ct_list_for_metric = sorted(
            df_summary["컨테이너"].unique(),
            key=ct_sort_key
        )

        if "daily_selected_metric" not in st.session_state:
            st.session_state.daily_selected_metric = "냉동효율(%)"

        if "daily_selected_containers" not in st.session_state:
            st.session_state.daily_selected_containers = ct_list_for_metric.copy()

        st.session_state.daily_selected_containers = [
            ct for ct in st.session_state.daily_selected_containers
            if ct in ct_list_for_metric
        ]

        selected_metric = st.session_state.daily_selected_metric

        st.markdown("#### 1) 지표 선택")

        metric_cols = st.columns(len(metric_options), gap="small")

        for idx, metric in enumerate(metric_options):
            is_selected = st.session_state.daily_selected_metric == metric
            button_label = f"✅ {metric_short_label[metric]}" if is_selected else metric_short_label[metric]

            with metric_cols[idx]:
                if st.button(
                    button_label,
                    key=f"metric_button_{metric}",
                    use_container_width=True
                ):
                    st.session_state.daily_selected_metric = metric

        selected_metric = st.session_state.daily_selected_metric

        st.markdown("#### 2) 컨테이너 선택")

        control_col1, control_col2, control_col3 = st.columns([1, 1, 4], gap="small")

        with control_col1:
            if st.button("전체 선택", use_container_width=True):
                st.session_state.daily_selected_containers = ct_list_for_metric.copy()

        with control_col2:
            if st.button("전체 해제", use_container_width=True):
                st.session_state.daily_selected_containers = []

        with control_col3:
            st.caption(
                f"Selected containers: {len(st.session_state.daily_selected_containers)} / {len(ct_list_for_metric)}"
            )

        ct_cols = st.columns(len(ct_list_for_metric), gap="small")

        for idx, ct in enumerate(ct_list_for_metric):
            is_selected = ct in st.session_state.daily_selected_containers
            button_label = f"{ct} ✓" if is_selected else ct

            with ct_cols[idx]:
                if st.button(
                    button_label,
                    key=f"ct_toggle_button_{ct}",
                    use_container_width=True
                ):
                    if ct in st.session_state.daily_selected_containers:
                        st.session_state.daily_selected_containers.remove(ct)
                    else:
                        st.session_state.daily_selected_containers.append(ct)

                    st.session_state.daily_selected_containers = sorted(
                        st.session_state.daily_selected_containers,
                        key=ct_sort_key
                    )

        selected_containers = st.session_state.daily_selected_containers

        if not selected_containers:
            st.warning("그래프를 표시하려면 컨테이너를 1개 이상 선택해 주세요.")
        else:
            fig_daily_metric = create_daily_metric_compare_chart(
                df_summary=df_summary,
                selected_metric=selected_metric,
                selected_containers=selected_containers
            )

            st.plotly_chart(
                fig_daily_metric,
                use_container_width=True,
                config={
                    "displayModeBar": True,
                    "responsive": True
                }
            )

            selected_metric_table = build_selected_metric_pivot_table(
                df_summary=df_summary,
                selected_metric=selected_metric,
                selected_containers_tuple=tuple(selected_containers)
            )

            st.markdown(f"#### 3) 선택 지표 테이블: {selected_metric}")
            st.dataframe(
                selected_metric_table,
                use_container_width=True,
                height=260
            )

    except Exception as e:
        st.error("CSV 처리 중 오류가 발생했습니다.")
        st.exception(e)
