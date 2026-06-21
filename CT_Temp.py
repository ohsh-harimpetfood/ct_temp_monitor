import io
import re
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import plotly.graph_objects as go

from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils import get_column_letter


# =========================================================
# Streamlit 기본 설정
# =========================================================
st.set_page_config(
    page_title="❄ 냉동 컨테이너 온도데이터 처리 프로그램.V3",
    layout="wide"
)

st.title("❄ 냉동 컨테이너 온도 데이터 처리 프로그램.V3")
st.caption("신규 데이터로거 플랫폼 CSV 전용 | 이상값 기준: -50℃ 미만 또는 60℃ 초과 → 결측치 처리")


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
    - missing_count_after_replace: 결측 + 이상값 대체 후 결측 건수
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

    missing_count_after_replace = int(df_long["온도"].isna().sum())

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

    return df_raw, df_long, df_abnormal, abnormal_count, missing_count_after_replace


# =========================================================
# 요약 계산
# =========================================================
@st.cache_data(show_spinner=False)
def calculate_summary(df_long):
    """
    컨테이너별 / 날짜별 분석결과 생성

    기존 유지:
    - 최저온도
    - 평균누적온도
    - 측정면적
    - 목표면적
    - 냉동효율(%)

    신규 추가:
    - -15℃ 이하 유지율(%)
      유효 측정건수 중 온도 <= -15℃ 비율
    """

    summary_list = []

    for (container, date), group in df_long.groupby(["컨테이너", "측정일자"], sort=False):
        group = group.dropna(subset=["온도"]).sort_values("측정일시")

        if group.empty:
            continue

        최저온도 = group["온도"].min()
        평균온도 = group["온도"].mean()

        전체시간 = group["시간(분)"].max() - group["시간(분)"].min()

        # 기존 냉동효율 계산식 유지
        # 0℃ 이하만 적분
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
            "냉동효율(%)": round(냉동효율 * 100, 0),
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

    int_cols = ["측정면적", "목표면적", "냉동효율(%)", "측정건수"]

    for col in int_cols:
        df_summary[col] = df_summary[col].round(0).astype(int)

    df_summary["최저온도"] = df_summary["최저온도"].round(1)
    df_summary["평균누적온도"] = df_summary["평균누적온도"].round(1)
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
# 날짜별 지표 요약 테이블 생성
# =========================================================
@st.cache_data(show_spinner=False)
def build_metric_table(df_summary):
    """
    컨테이너별 날짜별 주요 지표 피벗 테이블 생성

    포함 지표:
    - 최저온도
    - 냉동효율(%)
    - -15℃이하유지율(%)
    """

    metrics = ["최저온도", "냉동효율(%)", "-15℃이하유지율(%)"]

    df_filtered = df_summary[
        ["컨테이너", "측정일자"] + metrics
    ].copy()

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

    return df_final


# =========================================================
# 온도 추이 그래프 생성 - 엑셀 삽입 및 단일 CT 확인용
# =========================================================
def create_temperature_chart(plot_df, title, figsize=(10, 4)):
    """
    온도 추이 그래프 생성
    그래프 내부 문구는 영문으로 표시
    """

    fig, ax = plt.subplots(figsize=figsize)

    plot_df = plot_df.sort_values("측정일시")

    ax.plot(
        plot_df["측정일시"],
        plot_df["온도"],
        label="Temperature",
        color="orange",
        marker="o",
        markersize=1,
        linewidth=1
    )

    ax.axhline(0, color="red", linestyle="--", linewidth=1, label="0°C")
    ax.axhline(-15, color="green", linestyle=":", linewidth=1, label="-15°C")
    ax.axhline(-18, color="blue", linestyle="--", linewidth=1, label="-18°C")

    ax.fill_between(
        plot_df["측정일시"],
        plot_df["온도"],
        0,
        where=(plot_df["온도"] < 0),
        interpolate=True,
        color="skyblue",
        alpha=0.4,
        label="Integration Area (Temp < 0°C)"
    )

    ax.set_ylim(-22, 36)
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("Timestamp", fontsize=9)
    ax.set_ylabel("Temperature (°C)", fontsize=9)
    ax.tick_params(axis="x", labelsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True)

    return fig


# =========================================================
# 일자별 지표 비교 - 상태 판정 / 요약 / 스타일 함수
# =========================================================
def get_metric_status(selected_metric, value):
    """
    선택 지표별 상태 판정

    기준:
    - 최저온도: -15℃ 초과 시 Check
    - 냉동효율(%): 70% 미만 Low, 120% 초과 High
    - -15℃이하유지율(%): 90% 미만 Check
    """

    if pd.isna(value):
        return "Missing"

    if selected_metric == "최저온도":
        if value > -15:
            return "Check"
        return "OK"

    if selected_metric == "냉동효율(%)":
        if value < 70:
            return "Low"
        if value > 120:
            return "High"
        return "OK"

    if selected_metric == "-15℃이하유지율(%)":
        if value < 90:
            return "Check"
        return "OK"

    return "OK"


def get_metric_warning_df(df_summary, selected_metric, selected_containers):
    """
    선택 지표 기준 주의 데이터만 추출
    """

    warning_df = df_summary[
        df_summary["컨테이너"].isin(selected_containers)
    ].copy()

    warning_df["측정일자"] = pd.to_datetime(warning_df["측정일자"])
    warning_df["상태"] = warning_df[selected_metric].apply(
        lambda x: get_metric_status(selected_metric, x)
    )

    warning_df = warning_df[warning_df["상태"] != "OK"].copy()

    if warning_df.empty:
        return warning_df

    warning_df = warning_df[
        ["컨테이너", "측정일자", selected_metric, "상태"]
    ].copy()

    if selected_metric == "최저온도":
        warning_df = warning_df.sort_values(selected_metric, ascending=False)
    elif selected_metric == "냉동효율(%)":
        warning_df["범위이탈정도"] = warning_df[selected_metric].apply(
            lambda x: 70 - x if x < 70 else x - 120
        )
        warning_df = warning_df.sort_values("범위이탈정도", ascending=False)
        warning_df = warning_df.drop(columns=["범위이탈정도"])
    elif selected_metric == "-15℃이하유지율(%)":
        warning_df = warning_df.sort_values(selected_metric, ascending=True)
    else:
        warning_df = warning_df.sort_values(["컨테이너", "측정일자"])

    warning_df["측정일자"] = warning_df["측정일자"].dt.strftime("%Y-%m-%d")

    return warning_df.reset_index(drop=True)


def style_selected_metric_table(df, selected_metric):
    """
    선택 지표 피벗 테이블 스타일 적용
    - 주의값은 글자색 강조 + 굵게
    """

    def style_cell(value):
        if not isinstance(value, (int, float, np.integer, np.floating)):
            return ""

        status = get_metric_status(selected_metric, value)

        if status == "OK":
            return ""

        if status == "Low":
            return "color: #d97706; font-weight: 700;"

        if status == "High":
            return "color: #9333ea; font-weight: 700;"

        if status == "Check":
            return "color: #dc2626; font-weight: 700;"

        return ""

    return df.style.map(style_cell)


# =========================================================
# 일자별 지표 비교 그래프 생성 - Plotly 반응형 버전
# =========================================================
def create_daily_metric_compare_chart(df_summary, selected_metric, selected_containers):
    """
    선택한 지표를 기준으로 컨테이너별 일자 추이 Plotly 그래프 생성

    X축: 측정일자
    Y축: 선택 지표
    라인: 선택된 컨테이너

    그래프 내부 문구는 한글 깨짐 방지를 위해 영문으로 표시
    기준 이탈 포인트는 별도 마커로 강조
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

        ct_df["Status"] = ct_df[selected_metric].apply(
            lambda x: get_metric_status(selected_metric, x)
        )

        fig.add_trace(
            go.Scatter(
                x=ct_df["측정일자"],
                y=ct_df[selected_metric],
                mode="lines+markers",
                name=ct,
                line=dict(width=2),
                marker=dict(size=6),
                customdata=np.stack(
                    [
                        ct_df["컨테이너"],
                        ct_df["Status"]
                    ],
                    axis=-1
                ),
                hovertemplate=(
                    "<b>Container: %{customdata[0]}</b><br>"
                    "Date: %{x|%Y-%m-%d}<br>"
                    + selected_metric_label + ": %{y}<br>"
                    "Status: %{customdata[1]}"
                    "<extra></extra>"
                )
            )
        )

        check_df = ct_df[ct_df["Status"] != "OK"].copy()

        if not check_df.empty:
            fig.add_trace(
                go.Scatter(
                    x=check_df["측정일자"],
                    y=check_df[selected_metric],
                    mode="markers",
                    name=f"{ct} Check",
                    marker=dict(
                        size=12,
                        symbol="x",
                        line=dict(width=2)
                    ),
                    showlegend=False,
                    customdata=np.stack(
                        [
                            check_df["컨테이너"],
                            check_df["Status"]
                        ],
                        axis=-1
                    ),
                    hovertemplate=(
                        "<b>CHECK POINT</b><br>"
                        "Container: %{customdata[0]}<br>"
                        "Date: %{x|%Y-%m-%d}<br>"
                        + selected_metric_label + ": %{y}<br>"
                        "Status: %{customdata[1]}"
                        "<extra></extra>"
                    )
                )
            )

    shapes = []
    annotations = []

    if selected_metric == "-15℃이하유지율(%)":
        shapes.append({
            "type": "line",
            "xref": "paper",
            "x0": 0,
            "x1": 1,
            "yref": "y",
            "y0": 90,
            "y1": 90,
            "line": {
                "dash": "dash",
                "width": 1
            }
        })
        annotations.append({
            "xref": "paper",
            "x": 1,
            "yref": "y",
            "y": 90,
            "text": "90% threshold",
            "showarrow": False,
            "xanchor": "left",
            "font": {"size": 11}
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
        annotations.extend([
            {
                "xref": "paper",
                "x": 1,
                "yref": "y",
                "y": 70,
                "text": "70% lower limit",
                "showarrow": False,
                "xanchor": "left",
                "font": {"size": 11}
            },
            {
                "xref": "paper",
                "x": 1,
                "yref": "y",
                "y": 120,
                "text": "120% upper limit",
                "showarrow": False,
                "xanchor": "left",
                "font": {"size": 11}
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
                "y0": -15,
                "y1": -15,
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
        annotations.extend([
            {
                "xref": "paper",
                "x": 1,
                "yref": "y",
                "y": -15,
                "text": "-15°C threshold",
                "showarrow": False,
                "xanchor": "left",
                "font": {"size": 11}
            },
            {
                "xref": "paper",
                "x": 1,
                "yref": "y",
                "y": -18,
                "text": "-18°C target",
                "showarrow": False,
                "xanchor": "left",
                "font": {"size": 11}
            }
        ])
        y_range = None

    else:
        y_range = None

    fig.update_layout(
        title=f"Daily {selected_metric_label} Trend",
        xaxis_title="Date",
        yaxis_title=selected_metric_label,
        height=460,
        margin=dict(l=40, r=110, t=70, b=40),
        hovermode="closest",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        shapes=shapes,
        annotations=annotations
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
def create_excel_download(df_summary, df_long, df_metric_table, df_abnormal):
    """
    엑셀 다운로드 파일 생성

    시트 구성:
    - Summary
    - chart
    - table
    - abnormal_values
    """

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # Sheet 1: Summary
        df_summary.to_excel(writer, index=False, sheet_name="Summary")
        workbook = writer.book
        worksheet_summary = writer.sheets["Summary"]
        auto_adjust_excel_column_width(worksheet_summary)

        # Sheet 2: Chart
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
                figsize=(10, 4)
            )

            img_buf = io.BytesIO()
            fig.savefig(img_buf, format="png", bbox_inches="tight")
            plt.close(fig)
            img_buf.seek(0)

            img = XLImage(img_buf)
            img.anchor = f"B{row_offset}"
            graph_ws.add_image(img)

            row_offset += 18

        # Sheet 3: Table
        df_metric_table.to_excel(writer, index=False, sheet_name="table")
        worksheet_table = writer.sheets["table"]
        auto_adjust_excel_column_width(worksheet_table)

        # Sheet 4: Abnormal values
        if df_abnormal.empty:
            df_abnormal_export = pd.DataFrame({
                "메시지": ["이상값 없음"]
            })
        else:
            df_abnormal_export = df_abnormal.copy()

        df_abnormal_export.to_excel(writer, index=False, sheet_name="abnormal_values")
        worksheet_abnormal = writer.sheets["abnormal_values"]
        auto_adjust_excel_column_width(worksheet_abnormal)

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
        df_raw, df_long, df_abnormal, abnormal_count, missing_count_after_replace = (
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

        # -------------------------------------------------
        # 파일 처리 결과 요약
        # -------------------------------------------------
        st.success("✅ 신규 플랫폼 CSV 데이터 불러오기 및 전처리 완료")

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
            st.metric("결측 포함 총 제외 건수", f"{missing_count_after_replace:,}건")

        if abnormal_count > 0:
            with st.expander("⚠️ 이상값 목록 확인 (-50℃ 미만 또는 60℃ 초과)", expanded=False):
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
            height=420
        )

        # -------------------------------------------------
        # 엑셀 다운로드
        # -------------------------------------------------
        excel_output = create_excel_download(
            df_summary=df_summary,
            df_long=df_long,
            df_metric_table=df_metric_table,
            df_abnormal=df_abnormal
        )

        start_date = df_long["측정일자"].min().strftime("%y%m%d")
        end_date = df_long["측정일자"].max().strftime("%y%m%d")
        filename = f"{start_date}_{end_date}_freezer_ct_data_chart.xlsx"

        st.download_button(
            label="📥 분석결과 엑셀 다운로드 (요약표 + 그래프 + 이상값 목록 포함)",
            data=excel_output,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # -------------------------------------------------
        # 단일 컨테이너 온도 추이 그래프
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
                figsize=(6, 2.5)
            )

            with col2:
                st.pyplot(fig)

        # -------------------------------------------------
        # 전체 지표 피벗 테이블은 접어서 표시
        # -------------------------------------------------
        with st.expander("📊 컨테이너별 최저온도 / 냉동효율 / -15℃ 이하 유지율 요약 테이블 보기", expanded=False):
            st.dataframe(
                df_metric_table,
                use_container_width=True,
                height=280
            )

        # -------------------------------------------------
        # 일자별 지표 비교 그래프 - compact button + Plotly
        # -------------------------------------------------
        st.divider()
        st.subheader("📈 일자별 지표 비교 그래프")
        st.caption("지표와 컨테이너를 선택하면 일자별 추이 그래프가 반영됩니다. 그래프 내부 표기는 한글 깨짐 방지를 위해 영문으로 표시됩니다.")

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

        # session_state 초기화
        if "daily_selected_metric" not in st.session_state:
            st.session_state.daily_selected_metric = "냉동효율(%)"

        if "daily_selected_containers" not in st.session_state:
            st.session_state.daily_selected_containers = ct_list_for_metric.copy()

        # 현재 업로드 파일에 없는 컨테이너 제거
        st.session_state.daily_selected_containers = [
            ct for ct in st.session_state.daily_selected_containers
            if ct in ct_list_for_metric
        ]

        selected_metric = st.session_state.daily_selected_metric

        # -----------------------------
        # 지표 선택 버튼
        # -----------------------------
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

        # -----------------------------
        # 컨테이너 선택 버튼
        # -----------------------------
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

        # CT1~CT13 한 줄 compact 배치
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

        # -----------------------------
        # 그래프 및 테이블 출력
        # -----------------------------
        if not selected_containers:
            st.warning("그래프를 표시하려면 컨테이너를 1개 이상 선택해 주세요.")
        else:
            warning_df = get_metric_warning_df(
                df_summary=df_summary,
                selected_metric=selected_metric,
                selected_containers=selected_containers
            )

            # -----------------------------
            # 주의 데이터 요약 카드
            # -----------------------------
            st.markdown("#### 3) 주의 데이터 요약")

            if warning_df.empty:
                st.success("✅ 선택한 지표 기준으로 주의 데이터가 없습니다.")
            else:
                warning_count = len(warning_df)
                warning_ct_count = warning_df["컨테이너"].nunique()

                if selected_metric == "최저온도":
                    worst_row = warning_df.sort_values(selected_metric, ascending=False).iloc[0]
                    worst_label = "Highest min temp"

                elif selected_metric == "냉동효율(%)":
                    tmp_warning = warning_df.copy()
                    tmp_warning["범위이탈정도"] = tmp_warning[selected_metric].apply(
                        lambda x: 70 - x if x < 70 else x - 120
                    )
                    worst_row = tmp_warning.sort_values("범위이탈정도", ascending=False).iloc[0]
                    worst_label = "Largest efficiency deviation"

                elif selected_metric == "-15℃이하유지율(%)":
                    worst_row = warning_df.sort_values(selected_metric, ascending=True).iloc[0]
                    worst_label = "Lowest retention"

                else:
                    worst_row = warning_df.iloc[0]
                    worst_label = "Worst point"

                card1, card2, card3, card4 = st.columns(4)

                with card1:
                    st.metric("주의 데이터", f"{warning_count:,}건")

                with card2:
                    st.metric("주의 컨테이너", f"{warning_ct_count:,}개")

                with card3:
                    st.metric("대표 컨테이너", str(worst_row["컨테이너"]))

                with card4:
                    st.metric(worst_label, f"{worst_row[selected_metric]}")

                st.caption(
                    f"대표 일자: {worst_row['측정일자']} / 상태: {worst_row['상태']}"
                )

                st.dataframe(
                    warning_df,
                    use_container_width=True,
                    height=180
                )

            # -----------------------------
            # Plotly 그래프
            # -----------------------------
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

            # -----------------------------
            # 전체 피벗 테이블은 접어서 표시
            # -----------------------------
            selected_metric_table = build_selected_metric_pivot_table(
                df_summary=df_summary,
                selected_metric=selected_metric,
                selected_containers_tuple=tuple(selected_containers)
            )

            with st.expander(f"전체 피벗 테이블 보기: {selected_metric}", expanded=False):
                styled_metric_table = style_selected_metric_table(
                    selected_metric_table,
                    selected_metric
                )

                st.dataframe(
                    styled_metric_table,
                    use_container_width=True,
                    height=260
                )

    except Exception as e:
        st.error("CSV 처리 중 오류가 발생했습니다.")
        st.exception(e)
