import pandas as pd
import streamlit as st
from datetime import datetime
import re
import numpy as np
import matplotlib.pyplot as plt
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils import get_column_letter
from PIL import Image
import io
import streamlit as st



st.set_page_config(page_title="❄냉동 컨테이너 온도데이터 처리 프로그램.V2", layout="wide")

# Step 1: 파일 업로드
st.title("❄ 냉동 컨테이너 온도 데이터 처리 프로그램.V2")
uploaded_file = st.file_uploader("CSV 파일을 업로드하세요", type="csv")

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    st.success("✅ 원본 데이터 불러오기 완료")

    # Step 2: 0~6행 제거
    df = df.iloc[7:].reset_index(drop=True)

    # ✅ Step 3: 날짜 변환 함수 (사용자 규칙 완전 반영)
    def reverse_parse_date_custom(date_str):
        """
        날짜 문자열을 다음 규칙으로 변환:
        - 무조건 '일/월/연' 순서로 역전
        - 연도는 마지막 2자리만 추출해서 20YY로 해석
        예: '2021-07-25' → 2025-07-21
        """
        try:
            parts = re.split(r'[/\-\.]', str(date_str).strip())
            if len(parts) != 3:
                return None

            day = parts[0][-2:].zfill(2)
            month = parts[1].zfill(2)
            year_suffix = parts[2][-2:].zfill(2)

            return datetime(int(f"20{year_suffix}"), int(month), int(day))
        except:
            return None

    # Step 4: 날짜/시각 컬럼 생성
    df["측정일자"] = df["위치"].astype(str).apply(reverse_parse_date_custom)
    df["측정시각"] = df["Unnamed: 1"]

    # Step 5: 결측치 제거 (온도값이 모두 결측인 행 제거)
    temp_cols = [col for col in df.columns if "°C" in col]
    df_cleaned = df.dropna(subset=temp_cols, how='all').reset_index(drop=True)

    # Step 6: 시간 제거 (날짜만 표시)
    if "측정일자" in df_cleaned.columns and pd.api.types.is_datetime64_any_dtype(df_cleaned["측정일자"]):
        df_cleaned["측정일자"] = df_cleaned["측정일자"].dt.date

    # Step 7: 컬럼명 축약
    rename_dict = {}
    for col in df_cleaned.columns:
        if "냉동CT" in col:
            match = re.search(r"(\d+)번냉동CT", col)
            if match:
                rename_dict[col] = f"CT{match.group(1)}"
        elif col == "측정일자":
            rename_dict[col] = "일자"
        elif col == "측정시각":
            rename_dict[col] = "시각"

    df_cleaned.rename(columns=rename_dict, inplace=True)

    # Step 8: 컬럼 순서 재정렬
    current_cols = df_cleaned.columns.tolist()
    temp_cols_short = [col for col in current_cols if col.startswith("CT")]
    ordered_cols = ["일자", "시각"] + temp_cols_short
    df_cleaned = df_cleaned[ordered_cols]

    # ✅ 최종 출력 (숨김)
    #st.subheader("✅ 전처리된 데이터 (축약 보기)")
    #st.dataframe(df_cleaned.head(10))

    # ✅ 현재 DataFrame의 컬럼 확인 (숨김)
    #st.subheader("🔍 현재 컬럼 목록")
    #st.write(df.columns.tolist())
    
    # 3단계 long-format으로 변환 
    df_long = pd.melt(
        df_cleaned,
        id_vars=["일자", "시각"],
        value_vars=[col for col in df_cleaned.columns if col.startswith("CT")],
        var_name="컨테이너",
        value_name="온도"
    )
    # 3단계 -1 데이터로거 신규추가에 따른 에러 제거 : ### → NaN
    df_long['온도'] = pd.to_numeric(df_long['온도'], errors='coerce')
    df_long["측정일시"] = pd.to_datetime(df_long["일자"].astype(str) + " " + df_long["시각"])
    # 결측치 개수 확인(숨기)
    #st.write("결측치 수:", df_long["온도"].isna().sum())
    # 결측치 처리
    df_long = df_long.dropna(subset=["온도"])
    # 처리결과(숨김)
    #st.dataframe(df_long.head(10))
    # 4단계 데이터 분석결과 반환

    # ✅ 현재 정제 데이터의 컬럼 확인 (숨김)
    #st.subheader("🔍 현재 정제 데이터 컬럼 목록")
    #st.write(df_long.columns.tolist())

    # 데이터 분석결과 반환
        # 1. 측정 시간 컬럼 생성 (분 단위)
    df_long["시간(분)"] = df_long["측정일시"].dt.hour * 60 + df_long["측정일시"].dt.minute
    
    # 2. 측정일자 컬럼 생성 (datetime.date 객체)
    df_long["측정일자"] = df_long["측정일시"].dt.date
    
    # 3. 요일 컬럼 생성
    df_long["요일"] = df_long["측정일시"].dt.day_name()
    
    # 4. 요약 집계 테이블 생성
    summary_list = []
    
    for (container, date), group in df_long.groupby(["컨테이너", "측정일자"]):
        group = group.dropna(subset=["온도"])  # 결측치 제거
        if group.empty:
            continue
    
        최저온도 = group["온도"].min()
        평균온도 = group["온도"].mean()
        전체시간 = group["시간(분)"].max() - group["시간(분)"].min()
        
        # 적분값 계산 (0도 이하만 적분)
        mask = group["온도"] < 0

        # (안정성) 시간순 정렬 후 적분
        tmp = group.loc[mask, ["시간(분)", "온도"]].sort_values("시간(분)")

        x = tmp["시간(분)"]
        y = 0 - tmp["온도"]

        측정면적 = np.trapezoid(np.asarray(y), np.asarray(x)) if not x.empty else 0

    
        목표면적 = 18 * 전체시간 if 전체시간 > 0 else 0
        냉동효율 = 측정면적 / 목표면적 if 목표면적 > 0 else 0
    
        요약 = {
            "컨테이너": container,
            "측정일자": date,
            "요일": group["요일"].iloc[0],
            "최저온도": round(최저온도, 1),
            "평균누적온도": round(평균온도, 1),
            "측정면적": round(측정면적, 0),
            "목표면적": round(목표면적, 0),
            "냉동효율(%)": round(냉동효율 * 100, 0),
            "측정건수": len(group)
        }
    
        summary_list.append(요약)

    # 5. 요약 데이터프레임 생성
    df_summary = pd.DataFrame(summary_list)

    df_summary['요일'] = df_summary['요일'].map({
        'Monday': '월요일',
        'Tuesday': '화요일',
        'Wednesday': '수요일',
        'Thursday': '목요일',
        'Friday': '금요일',
        'Saturday': '토요일',
        'Sunday': '일요일'
    })
    df_summary["측정면적"] = df_summary["측정면적"].round(0).astype(int)
    df_summary["목표면적"] = df_summary["목표면적"].round(0).astype(int)
    df_summary["냉동효율(%)"] = df_summary["냉동효율(%)"].round(0).astype(int)
    df_summary["최저온도"] = df_summary["최저온도"].round(1)
    df_summary["평균누적온도"] = df_summary["평균누적온도"].round(1)

    # 요약표 출력
    st.subheader("📊 컨테이너별 날짜별 분석결과")
    st.dataframe(df_summary, use_container_width=False, height=500)

    # 엑셀 다운로드 기능    
    # 메모리에 엑셀 저장
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # ✅ 시트1: Summary
        df_summary.to_excel(writer, index=False, sheet_name='Summary')
        workbook = writer.book
        worksheet_summary = writer.sheets['Summary']
    
        # 열 너비 자동 조정 (Summary 시트)
        for col_cells in worksheet_summary.iter_cols(min_row=1, max_row=worksheet_summary.max_row):
            max_length = max((len(str(cell.value)) for cell in col_cells if cell.value), default=0)
            col_letter = get_column_letter(col_cells[0].column)
            worksheet_summary.column_dimensions[col_letter].width = max_length + 6
    
        # ✅ 시트2: Chart
        graph_ws = workbook.create_sheet(title='chart')
        row_offset = 2
    
        for ct in df_long["컨테이너"].unique():
            plot_df = df_long[df_long["컨테이너"] == ct]
            fig, ax = plt.subplots(figsize=(10, 4))
    
            ax.plot(
                plot_df["측정일시"], plot_df["온도"],
                label="Temperature", color="orange",
                marker="o", markersize=1, linewidth=1
            )
            ax.axhline(0, color="red", linestyle="--", linewidth=1, label="0°C")
            ax.axhline(-18, color="blue", linestyle="--", linewidth=1, label="-18°C")
            ax.fill_between(
                plot_df["측정일시"], plot_df["온도"], 0,
                where=(plot_df["온도"] < 0), interpolate=True,
                color="skyblue", alpha=0.4, label="Integration Area (Temp < 0°C)"
            )
    
            ax.set_ylim(-22, 36)
            ax.set_title(f"{ct} Temperature Profile", fontsize=11)
            ax.set_xlabel("Timestamp", fontsize=9)
            ax.set_ylabel("Temperature (°C)", fontsize=9)
            ax.tick_params(axis='x', labelsize=8)
            ax.tick_params(axis='y', labelsize=8)
            ax.legend(loc="upper right", fontsize=8)
            ax.grid(True)
    
            # 메모리 저장 후 삽입
            img_buf = io.BytesIO()
            fig.savefig(img_buf, format='png', bbox_inches='tight')
            plt.close(fig)
            img_buf.seek(0)
    
            img = XLImage(img_buf)
            img.anchor = f'B{row_offset}'
            graph_ws.add_image(img)
    
            row_offset += 18  # 이미지마다 간격 확보

        
        # ✅ 시트3: Table
        df_filtered = df_summary[['컨테이너', '측정일자', '최저온도', '냉동효율(%)']].copy()
        df_filtered['측정일자'] = pd.to_datetime(df_filtered['측정일자'])
        df_filtered.sort_values('측정일자', inplace=True)
    
        pivot_temp = df_filtered.pivot(index='측정일자', columns='컨테이너', values='최저온도')
        pivot_eff = df_filtered.pivot(index='측정일자', columns='컨테이너', values='냉동효율(%)')
    
        df_temp = pivot_temp.T
        df_temp.index = [f'최저온도_{ct}' for ct in df_temp.index]
        df_eff = pivot_eff.T
        df_eff.index = [f'냉동효율_{ct}' for ct in df_eff.index]
    
        df_combined = pd.concat([df_temp, df_eff])
        df_combined.columns = [col.strftime('%m월 %d일') for col in df_combined.columns]
    
        df_combined_reset = df_combined.copy()
        df_combined_reset.insert(0, '구분', df_combined.index)
        df_combined_reset['지표'] = df_combined_reset['구분'].str.extract(r'(최저온도|냉동효율)')
        df_combined_reset['컨테이너'] = df_combined_reset['구분'].str.extract(r'_(CT\d+)$')
    
        지표_순서 = pd.CategoricalDtype(['최저온도', '냉동효율'], ordered=True)
        df_combined_reset['지표'] = df_combined_reset['지표'].astype(지표_순서)
    
        cols_order = ['컨테이너', '지표'] + list(df_combined.columns)
        df_final = df_combined_reset[cols_order].sort_values(by=['컨테이너', '지표']).reset_index(drop=True)
    
        df_final.to_excel(writer, index=False, sheet_name='table')
        worksheet_table = writer.sheets['table']
    
        # 열 너비 자동 조정 (Table 시트)
        for col_cells in worksheet_table.iter_cols(min_row=1, max_row=worksheet_table.max_row):
            max_length = max((len(str(cell.value)) for cell in col_cells if cell.value), default=0)
            col_letter = get_column_letter(col_cells[0].column)
            worksheet_table.column_dimensions[col_letter].width = max_length + 6
    
    # ✅ 파일명 생성
    # long-format 데이터에서 날짜 범위 추출

    start_date = df_long["측정일자"].min().strftime("%y%m%d")  # 예: 250722
    end_date = df_long["측정일자"].max().strftime("%y%m%d")    # 예: 250723
    filename = f"{start_date}_{end_date}_data_chart.xlsx"
        
    
    # 다운로드 버튼
    output.seek(0)
    st.download_button(
        label="📥 분석결과 엑셀 다운로드 (전체 그래프 포함)",
        data=output,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    
    
    # 시각화 자료 출력

    # 1. 컬럼 나누기: 좌측(선택), 우측(그래프)
    col1, col2 = st.columns([1, 5])
    
    with col1:
        # ① 컨테이너 선택
        selected_container = st.selectbox("Select Container", df_summary["컨테이너"].unique())
    
        # ② 날짜 선택 + "전체" 옵션 포함
        available_dates = df_summary[df_summary["컨테이너"] == selected_container]["측정일자"].unique()
        available_dates = sorted(available_dates)
        available_dates.insert(0, "전체")  # 맨 앞에 "전체" 추가
        selected_date = st.selectbox("Select Date", available_dates)
    
    # 2. 데이터 필터링
    if selected_date == "전체":
        plot_df = df_long[df_long["컨테이너"] == selected_container].copy()
        title_suffix = "All Dates"
    else:
        plot_df = df_long[
            (df_long["컨테이너"] == selected_container) &
            (df_long["측정일자"] == selected_date)
        ].copy()
        title_suffix = str(selected_date)
    
    # 3. 그래프 생성
    fig, ax = plt.subplots(figsize=(6, 2.5))
    
    ax.plot(
        plot_df["측정일시"],
        plot_df["온도"],
        label="Temperature",
        color="orange",
        marker="o",
        markersize=0.5,
        linewidth=0.5
    )
    
    # 기준선
    ax.axhline(0, color="red", linestyle="--", linewidth=0.3, label="0°C")
    ax.axhline(-18, color="blue", linestyle="--", linewidth=0.3, label="-18°C")
    
    # 면적 강조: 온도 < 0
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

    # ✅ Y축 온도 범위 고정
    ax.set_ylim(-22, 36)
    
    # 라벨 스타일
    ax.set_title(f"{selected_container} | {title_suffix} Temperature Profile", fontsize=9)
    ax.set_xlabel("Timestamp", fontsize=7)
    ax.set_ylabel("Temperature (°C)", fontsize=7)
    ax.tick_params(axis='x', labelsize=6)
    ax.tick_params(axis='y', labelsize=6)
    ax.legend(loc="upper right", fontsize=6)
    ax.grid(True)
    
    with col2:
        st.pyplot(fig)

    #----------부가 기능 ------------
    
    # --- 1. 필요한 컬럼만 필터링 및 정렬 ---
    df_filtered = (
        df_summary[['컨테이너', '측정일자', '최저온도', '냉동효율(%)']]
        .copy()
    )
    df_filtered['측정일자'] = pd.to_datetime(df_filtered['측정일자'])
    df_filtered.sort_values('측정일자', inplace=True)
    
    # --- 2. 피벗 테이블 생성 ---
    pivot_temp = df_filtered.pivot(index='측정일자', columns='컨테이너', values='최저온도')
    pivot_eff = df_filtered.pivot(index='측정일자', columns='컨테이너', values='냉동효율(%)')
    
    # --- 3. 전치 + 이름 변경 (지표명을 포함시킴) ---
    df_temp = pivot_temp.T
    df_temp.index = [f'최저온도_{ct}' for ct in df_temp.index]
    
    df_eff = pivot_eff.T
    df_eff.index = [f'냉동효율_{ct}' for ct in df_eff.index]
    
    # --- 4. 결합 및 컬럼 날짜 포맷 변경 ---
    df_combined = pd.concat([df_temp, df_eff])
    df_combined.columns = [col.strftime('%m월 %d일') for col in df_combined.columns]
    
    # --- 5. 인덱스를 컬럼으로 변환하여 분해 ---
    df_combined_reset = df_combined.copy()
    df_combined_reset.insert(0, '구분', df_combined.index)
    
    df_combined_reset['지표'] = df_combined_reset['구분'].str.extract(r'(최저온도|냉동효율)')
    df_combined_reset['컨테이너'] = df_combined_reset['구분'].str.extract(r'_(CT\d+)$')
    
    # --- ✅ 사용자 정의 순서로 지표 정렬을 위한 Categorical 설정 ---
    지표_순서 = pd.CategoricalDtype(['최저온도', '냉동효율'], ordered=True)
    df_combined_reset['지표'] = df_combined_reset['지표'].astype(지표_순서)
    
    # --- 6. 컬럼 순서 지정 및 정렬 ---
    cols_order = ['컨테이너', '지표'] + list(df_combined.columns)
    df_final = df_combined_reset[cols_order].sort_values(by=['컨테이너', '지표']).reset_index(drop=True)
    
    # --- 7. Streamlit 출력 ---
    st.subheader("📊 컨테이너별 최저온도 및 냉동효율 요약 테이블(정렬)")
    st.dataframe(df_final, use_container_width=True)
