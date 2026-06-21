import io
import re
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import plotly.graph_objects as go

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
st.caption(
    "신규 데이터로거 플랫폼 CSV 전용 | 이상값 기준: -50℃ 미만 또는 60℃ 초과 → 결측치 처리"
)


# =========================================================
# 공통 유틸 함수
# =========================================================
def extract_ct_number(col_name: str):
    """
    신규 CSV 컬럼명 예:
    - 8번냉동CT: 온도 (°C)
    - 13번냉동CT: 온도 (°C)

    반환:
    - 8, 13 등 숫자
    """
    match = re.search(r"(\d+)\s*번\s*냉동CT", str(col_name))
    return int(match.group(1)) if match else None


def ct_sort_key(ct_name: str) -> int:
    """
    CT1, CT2, CT10 정렬 꼬임 방지용 숫자 정렬 키
    """
    match = re.search(r"CT(\d+)", str(ct_name))
    return int(match.group(1)) if match else 9999


def integrate_trapezoid(y, x):
    """
    numpy 버전 호환용 적분 함수
    - numpy 2.x: np.trapezoid
    - numpy 1.x: np.trapz
    """
    if len(x) == 0:
        return 0

    if hasattr(np, "trapezoid"):
        return np.trapezoid(np.asarray(y), np.asarray(x))

    return np.trapz(np.asarray(y), np.asarray(x))


def auto_adjust_excel_column_width(worksheet):
    """
    엑셀 시트 열 너비 자동 조정
    """
    for col_cells in worksheet.iter_cols(min_row=1, max_row=worksheet.max_row):
        max_length = max(
            (len(str(cell.value)) for cell in col_cells if cell.value is not None),
            default=0
        )
        col_letter = get_column_letter(col_cells[0].column)
        worksheet.column_dimensions[col_letter].width = max_length + 6


# =========================================================
# 신규 플랫폼 CSV 전처리
# =========================================================
def preprocess_new_platform_csv(uploaded_file):
    """
    신규 플랫폼 CSV 전용 전처리

    입력 CSV 구조:
    - 구분자: ;
    - 첫 번째 컬럼: Asia/Seoul GMT+9 (UTC +09:00)
    - 이후 컬럼: n번냉동CT: 온도 (°C)

    반환:
    - df_raw: 원본 wide 데이터
    - df_long: 분석용 long-format 데이터
    - df_abnormal: 이상값 목록
    - abnormal_count: 이상값 건수
    - excluded_count: 결측 또는 이상값으로 분석에서 제외된 건수
    """

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
    """
    컨테이너별 / 날짜별 분석결과 생성
    """

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
# 상태 판정 로직
# =========================================================
def evaluate_daily_status(row):
    """
    일자별 컨테이너 상태 판정

    위험:
    - 최저온도 >= -16℃
    - 냉동효율 < 70%
    - -15℃ 이하 유지율 <= 70%

    주의:
    - 냉동효율 > 120%

    정상:
    - 위 조건 없음
    """

    issues = []
    severity = "정상"
    severity_score = 0

    min_temp = row["최저온도"]
    eff = row["냉동효율(%)"]
    retention = row["-15℃이하유지율(%)"]

    if pd.notna(min_temp) and min_temp >= -16:
        issues.append(f"최저온도 {min_temp:.1f}℃")
        severity = "위험"
        severity_score = max(severity_score, 2)

    if pd.notna(eff) and eff < 70:
        issues.append(f"냉동효율 저하 {eff:.1f}%")
        severity = "위험"
        severity_score = max(severity_score, 2)

    if pd.notna(retention) and retention <= 70:
        issues.append(f"-15℃ 유지율 저하 {retention:.1f}%")
        severity = "위험"
        severity_score = max(severity_score, 2)

    if pd.notna(eff) and eff > 120:
        issues.append(f"냉동효율 과다 {eff:.1f}%")
        if severity != "위험":
            severity = "주의"
        severity_score = max(severity_score, 1)

    if not issues:
        issues.append("정상")

    return severity, severity_score, " / ".join(issues)


@st.cache_data(show_spinner=False)
def build_status_tables(df_summary):
    """
    V4 대시보드용 상태 테이블 생성
    - daily_status_df: 컨테이너 × 일자 상태
    - container_status_df: 컨테이너별 종합 상태
    - check_list_df: 우선 점검 리스트
    """

    daily_status_df = df_summary.copy()
    daily_status_df["측정일자_dt"] = pd.to_datetime(daily_status_df["측정일자"])

    status_results = daily_status_df.apply(evaluate_daily_status, axis=1)
    daily_status_df["상태"] = [result[0] for result in status_results]
    daily_status_df["상태점수"] = [result[1] for result in status_results]
    daily_status_df["이슈"] = [result[2] for result in status_results]

    # 우선 점검 리스트
    check_list_df = daily_status_df[daily_status_df["상태"] != "정상"].copy()

    if not check_list_df.empty:
        check_list_df["이탈정도"] = check_list_df.apply(calculate_deviation_score, axis=1)
        check_list_df = (
            check_list_df
            .sort_values(["상태점수", "이탈정도", "측정일자_dt"], ascending=[False, False, False])
            .reset_index(drop=True)
        )

        check_list_df["측정일자"] = check_list_df["측정일자_dt"].dt.strftime("%Y-%m-%d")
        check_list_df = check_list_df[
            [
                "상태",
                "컨테이너",
                "측정일자",
                "이슈",
                "최저온도",
                "냉동효율(%)",
                "-15℃이하유지율(%)",
                "측정건수"
            ]
        ]

    # 컨테이너별 종합 상태
    container_rows = []

    for ct, group in daily_status_df.groupby("컨테이너", sort=False):
        group = group.sort_values("측정일자_dt")

        max_score = group["상태점수"].max()

        if max_score >= 2:
            status = "위험"
        elif max_score == 1:
            status = "주의"
        else:
            status = "정상"

        issue_group = group[group["상태점수"] == max_score].copy()

        if issue_group.empty:
            representative = group.iloc[-1]
        else:
            issue_group["이탈정도"] = issue_group.apply(calculate_deviation_score, axis=1)
            representative = issue_group.sort_values("이탈정도", ascending=False).iloc[0]

        container_rows.append({
            "컨테이너": ct,
            "종합상태": status,
            "상태점수": int(max_score),
            "대표일자": pd.to_datetime(representative["측정일자"]).strftime("%Y-%m-%d"),
            "대표이슈": representative["이슈"],
            "최악최저온도": group["최저온도"].max().round(1),
            "최저냉동효율": group["냉동효율(%)"].min().round(1),
            "최고냉동효율": group["냉동효율(%)"].max().round(1),
            "최저유지율": group["-15℃이하유지율(%)"].min().round(1),
            "이슈건수": int((group["상태"] != "정상").sum())
        })

    container_status_df = pd.DataFrame(container_rows)

    if not container_status_df.empty:
        container_status_df["컨테이너정렬키"] = container_status_df["컨테이너"].apply(ct_sort_key)
        container_status_df = (
            container_status_df
            .sort_values(["상태점수", "컨테이너정렬키"], ascending=[False, True])
            .drop(columns=["컨테이너정렬키"])
            .reset_index(drop=True)
        )

    return daily_status_df, container_status_df, check_list_df


def calculate_deviation_score(row):
    """
    우선순위 정렬용 이탈 점수
    """

    score = 0

    min_temp = row["최저온도"]
    eff = row["냉동효율(%)"]
    retention = row["-15℃이하유지율(%)"]

    if pd.notna(min_temp) and min_temp >= -16:
        score = max(score, min_temp - (-16))

    if pd.notna(eff) and eff < 70:
        score = max(score, 70 - eff)

    if pd.notna(eff) and eff > 120:
        score = max(score, eff - 120)

    if pd.notna(retention) and retention <= 70:
        score = max(score, 70 - retention)

    return round(float(score), 1)


# =========================================================
# V4 대시보드 렌더링 함수
# =========================================================
def status_color(status):
    if status == "위험":
        return {
            "bg": "#3b1111",
            "border": "#ef4444",
            "text": "#fecaca",
            "badge": "#ef4444"
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
    """
    컨테이너 상태 카드 그리드
    """

    st.markdown("#### 🧊 컨테이너 상태 카드")

    if container_status_df.empty:
        st.info("컨테이너 상태 데이터가 없습니다.")
        return

    sorted_cards = container_status_df.copy()
    sorted_cards["컨테이너정렬키"] = sorted_cards["컨테이너"].apply(ct_sort_key)
    sorted_cards = sorted_cards.sort_values("컨테이너정렬키").drop(columns=["컨테이너정렬키"])

    cards_per_row = 4

    for start_idx in range(0, len(sorted_cards), cards_per_row):
        row_cards = sorted_cards.iloc[start_idx:start_idx + cards_per_row]
        cols = st.columns(cards_per_row)

        for idx, (_, row) in enumerate(row_cards.iterrows()):
            colors = status_color(row["종합상태"])

            html = f"""
            <div style="
                border: 1.5px solid {colors['border']};
                background: {colors['bg']};
                border-radius: 14px;
                padding: 14px 14px 12px 14px;
                min-height: 150px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.18);
            ">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <div style="font-size:22px; font-weight:800; color:white;">{row['컨테이너']}</div>
                    <div style="
                        background:{colors['badge']};
                        color:white;
                        border-radius:999px;
                        padding:3px 10px;
                        font-size:13px;
                        font-weight:700;
                    ">{row['종합상태']}</div>
                </div>
                <div style="color:{colors['text']}; font-size:13px; line-height:1.7;">
                    <b>대표일자</b> {row['대표일자']}<br>
                    <b>최악 최저온도</b> {row['최악최저온도']:.1f}℃<br>
                    <b>냉동효율</b> {row['최저냉동효율']:.1f}% ~ {row['최고냉동효율']:.1f}%<br>
                    <b>최저 유지율</b> {row['최저유지율']:.1f}%<br>
                    <b>이슈</b> {row['이슈건수']}건
                </div>
            </div>
            """

            with cols[idx]:
                st.markdown(html, unsafe_allow_html=True)


def render_v4_summary_dashboard(
    df_summary,
    daily_status_df,
    container_status_df,
    check_list_df,
    abnormal_count,
    excluded_count
):
    """
    V4 상단 요약 대시보드
    """

    st.divider()
    st.header("🚦 V4 컨테이너 상태 대시보드")
    st.caption("기존 분석표를 보기 전에, 먼저 정상/주의/위험 컨테이너와 점검 우선순위를 확인합니다.")

    total_ct = len(container_status_df)
    danger_count = int((container_status_df["종합상태"] == "위험").sum())
    caution_count = int((container_status_df["종합상태"] == "주의").sum())
    normal_count = int((container_status_df["종합상태"] == "정상").sum())

    if not check_list_df.empty:
        worst = check_list_df.iloc[0]
        worst_ct = worst["컨테이너"]
        worst_issue = worst["이슈"]
    else:
        worst_ct = "-"
        worst_issue = "주의 데이터 없음"

    c1, c2, c3, c4, c5, c6 = st.columns(6)

    with c1:
        st.metric("전체 컨테이너", f"{total_ct}개")

    with c2:
        st.metric("정상", f"{normal_count}개")

    with c3:
        st.metric("주의", f"{caution_count}개")

    with c4:
        st.metric("위험", f"{danger_count}개")

    with c5:
        st.metric("최우선 점검", worst_ct)

    with c6:
        st.metric("이상값", f"{abnormal_count}건")

    st.caption(f"대표 이슈: {worst_issue} / 분석 제외 건수: {excluded_count:,}건")

    render_status_cards(container_status_df)

    st.markdown("#### 🔎 우선 점검 리스트")

    if check_list_df.empty:
        st.success("✅ 기준 이탈 데이터가 없습니다. 전체 컨테이너 상태가 정상입니다.")
    else:
        st.dataframe(
            check_list_df,
            use_container_width=True,
            height=230
        )

    st.markdown("#### 🗓️ 컨테이너 × 일자 상태 히트맵")

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
    """
    컨테이너 × 일자 히트맵
    """

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
            "위험": 2
        }

        z_df = df.pivot(index="컨테이너", columns="날짜", values="상태").reindex(index=containers, columns=dates)
        issue_df = df.pivot(index="컨테이너", columns="날짜", values="이슈").reindex(index=containers, columns=dates)

        z = z_df.replace(status_score).astype(float).values
        text = z_df.fillna("").values
        issues = issue_df.fillna("").values

        customdata = np.dstack([text, issues])

        fig = go.Figure(
            data=go.Heatmap(
                z=z,
                x=dates,
                y=containers,
                customdata=customdata,
                colorscale=[
                    [0.0, "#22c55e"],
                    [0.49, "#22c55e"],
                    [0.50, "#f59e0b"],
                    [0.74, "#f59e0b"],
                    [0.75, "#ef4444"],
                    [1.0, "#ef4444"],
                ],
                zmin=0,
                zmax=2,
                colorbar=dict(
                    title="Status",
                    tickvals=[0, 1, 2],
                    ticktext=["Normal", "Caution", "Risk"]
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
            margin=dict(l=50, r=30, t=60, b=40)
        )

        return fig

    value_df = df.pivot(index="컨테이너", columns="날짜", values=heatmap_metric).reindex(index=containers, columns=dates)
    issue_df = df.pivot(index="컨테이너", columns="날짜", values="이슈").reindex(index=containers, columns=dates)

    z = value_df.astype(float).values
    issues = issue_df.fillna("").values

    customdata = np.dstack([issues])

    metric_label_map = {
        "최저온도": "Min Temperature (°C)",
        "냉동효율(%)": "Freezing Efficiency (%)",
        "-15℃이하유지율(%)": "Below -15°C Retention (%)"
    }

    title = metric_label_map.get(heatmap_metric, heatmap_metric)

    if heatmap_metric == "최저온도":
        colorscale = "RdBu"
    elif heatmap_metric == "냉동효율(%)":
        colorscale = "RdYlGn"
    else:
        colorscale = "RdYlGn"

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=dates,
            y=containers,
            customdata=customdata,
            colorscale=colorscale,
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
        margin=dict(l=50, r=30, t=60, b=40)
    )

    return fig


# =========================================================
# 날짜별 지표 요약 테이블 생성
# =========================================================
@st.cache_data(show_spinner=False)
def build_metric_table(df_summary):
    """
    컨테이너별 날짜별 주요 지표 피벗 테이블 생성
    """

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


# =========================================================
# 요약 피벗 테이블 스타일
# =========================================================
def style_metric_table(df):
    """
    표시 기준:
    1) 최저온도: -16도 이상 빨간색 글씨
    2) 냉동효율(%): 70% 미만 빨간색 글씨, 120% 초과 파란색 글씨
    3) -15℃ 이하 유지율(%): 70% 이하 빨간색 글씨
    """

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
# 온도 추이 그래프 생성
# =========================================================
def create_temperature_chart(plot_df, title, figsize=(6, 2.3)):
    """
    온도 추이 그래프 생성
    - 그래프 내부 글자 크기 축소
    - 날짜 라벨 겹침 완화
    """

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


# =========================================================
# 일자별 지표 비교 그래프 생성 - Plotly
# =========================================================
def create_daily_metric_compare_chart(df_summary, selected_metric, selected_containers):
    """
    선택한 지표를 기준으로 컨테이너별 일자 추이 Plotly 그래프 생성
    """

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
    """
    선택한 컨테이너와 지표 기준의 일자별 피벗 테이블 생성
    """

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
    """
    엑셀 다운로드 파일 생성

    시트 구성:
    - Summary
    - chart
    - table
    - abnormal_values
    - v4_container_status
    - v4_check_list
    - v4_daily_status
    """

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
        daily_status_df, container_status_df, check_list_df = build_status_tables(df_summary)

        st.success("✅ 신규 플랫폼 CSV 데이터 불러오기 및 전처리 완료")

        # -------------------------------------------------
        # V4 상단 대시보드
        # -------------------------------------------------
        render_v4_summary_dashboard(
            df_summary=df_summary,
            daily_status_df=daily_status_df,
            container_status_df=container_status_df,
            check_list_df=check_list_df,
            abnormal_count=abnormal_count,
            excluded_count=excluded_count
        )

        # -------------------------------------------------
        # 기존 상세 분석 영역
        # -------------------------------------------------
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

        with col_f:
            st.metric("분석 제외 건수", f"{excluded_count:,}건")

        if abnormal_count > 0:
            with st.expander("⚠️ 이상값 목록 확인 (-50℃ 미만 또는 60℃ 초과)"):
                st.dataframe(
                    df_abnormal,
                    use_container_width=True,
                    height=220
                )

        # -------------------------------------------------
        # 요약표 출력
        # -------------------------------------------------
        st.subheader("📊 컨테이너별 날짜별 분석결과")
        st.dataframe(
            df_summary,
            use_container_width=True,
            height=350
        )

        # -------------------------------------------------
        # 엑셀 다운로드
        # -------------------------------------------------
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

        # -------------------------------------------------
        # 온도 추이 그래프
        # -------------------------------------------------
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

        # -------------------------------------------------
        # 날짜별 지표 요약 테이블
        # -------------------------------------------------
        st.subheader("📊 컨테이너별 최저온도 / 냉동효율 / -15℃ 이하 유지율 요약 테이블")
        st.caption("표시 기준: 최저온도 -16℃ 이상 빨간색 / 냉동효율 70% 미만 빨간색, 120% 초과 파란색 / -15℃ 이하 유지율 70% 이하 빨간색")

        styled_metric_table = style_metric_table(df_metric_table)

        st.dataframe(
            styled_metric_table,
            use_container_width=True,
            height=320
        )

        # -------------------------------------------------
        # 일자별 지표 비교 그래프
        # -------------------------------------------------
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
