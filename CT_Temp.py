import io
import re
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

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

    # 첫 번째 컬럼은 측정일시 컬럼
    timestamp_col = df_raw.columns[0]

    # 냉동CT 컬럼 탐색 및 CT명 표준화
    ct_rename = {}

    for col in df_raw.columns[1:]:
        ct_no = extract_ct_number(col)
        if ct_no is not None:
            ct_rename[col] = f"CT{ct_no}"

    if not ct_rename:
        raise ValueError("냉동CT 온도 컬럼을 찾지 못했습니다. CSV 컬럼명을 확인해 주세요.")

    df = df_raw.rename(columns={timestamp_col: "측정일시", **ct_rename})

    # 측정일시 변환
    df["측정일시"] = pd.to_datetime(df["측정일시"], errors="coerce")
    df = df.dropna(subset=["측정일시"]).copy()

    # CT 컬럼 숫자 기준 정렬
    ct_cols = sorted(ct_rename.values(), key=ct_sort_key)

    # 필요한 컬럼만 사용
    df = df[["측정일시"] + ct_cols].copy()

    # long-format 변환
    df_long = pd.melt(
        df,
        id_vars=["측정일시"],
        value_vars=ct_cols,
        var_name="컨테이너",
        value_name="온도"
    )

    # 온도 숫자 변환
    df_long["온도"] = pd.to_numeric(df_long["온도"], errors="coerce")

    # 이상값 판정: -50℃ 미만, 60℃ 초과
    abnormal_mask = df_long["온도"].lt(-50) | df_long["온도"].gt(60)

    df_abnormal = (
        df_long.loc[abnormal_mask, ["측정일시", "컨테이너", "온도"]]
        .copy()
        .sort_values(["측정일시", "컨테이너"])
        .reset_index(drop=True)
    )

    abnormal_count = int(abnormal_mask.sum())

    # 이상값은 결측치로 대체
    df_long.loc[abnormal_mask, "온도"] = np.nan

    # 결측치 수 확인
    missing_count_after_replace = int(df_long["온도"].isna().sum())

    # 분석용 데이터에서는 결측 제거
    df_long = df_long.dropna(subset=["온도"]).copy()

    # 분석용 파생 컬럼
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
      현재 기준은 유효 측정건수 중 온도 <= -15℃ 비율
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

        # 신규 지표: -15℃ 이하 유지율
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

    # 요일 한글 변환
    df_summary["요일"] = df_summary["요일"].map({
        "Monday": "월요일",
        "Tuesday": "화요일",
        "Wednesday": "수요일",
        "Thursday": "목요일",
        "Friday": "금요일",
        "Saturday": "토요일",
        "Sunday": "일요일"
    })

    # 타입 정리
    int_cols = ["측정면적", "목표면적", "냉동효율(%)", "측정건수"]

    for col in int_cols:
        df_summary[col] = df_summary[col].round(0).astype(int)

    df_summary["최저온도"] = df_summary["최저온도"].round(1)
    df_summary["평균누적온도"] = df_summary["평균누적온도"].round(1)
    df_summary["-15℃이하유지율(%)"] = df_summary["-15℃이하유지율(%)"].round(1)

    # CT 숫자 기준 정렬
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

    # 날짜 컬럼명 포맷 변경
    new_columns = []

    for col in df_final.columns:
        if isinstance(col, pd.Timestamp):
            new_columns.append(col.strftime("%m월 %d일"))
        else:
            new_columns.append(col)

    df_final.columns = new_columns

    # 정렬
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
# 그래프 생성
# =========================================================
def create_temperature_chart(plot_df, title, figsize=(10, 4)):
    """
    온도 추이 그래프 생성
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

    # 기준선
    ax.axhline(0, color="red", linestyle="--", linewidth=1, label="0°C")
    ax.axhline(-15, color="green", linestyle=":", linewidth=1, label="-15°C")
    ax.axhline(-18, color="blue", linestyle="--", linewidth=1, label="-18°C")

    # 기존 냉동효율 계산 기준 영역: 온도 < 0
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
            with st.expander("⚠️ 이상값 목록 확인 (-50℃ 미만 또는 60℃ 초과)"):
                st.dataframe(df_abnormal, use_container_width=True)

        # -------------------------------------------------
        # 요약표 출력
        # -------------------------------------------------
        st.subheader("📊 컨테이너별 날짜별 분석결과")
        st.dataframe(df_summary, use_container_width=True, height=500)

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
        # 그래프 출력
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
        # 날짜별 지표 요약 테이블
        # -------------------------------------------------
        st.subheader("📊 컨테이너별 최저온도 / 냉동효율 / -15℃ 이하 유지율 요약 테이블")
        st.dataframe(df_metric_table, use_container_width=True)

    except Exception as e:
        st.error("CSV 처리 중 오류가 발생했습니다.")
        st.exception(e)
