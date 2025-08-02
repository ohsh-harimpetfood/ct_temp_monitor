# CT_Temp Monitor

Analyze temperature data from cold storage containers with automated metrics:

- 📊 Average & minimum temperature analysis
- ❄️ Freezing efficiency based on integration
- 📈 Time-series plots and summary tables
- 📂 CSV upload → 📊 Auto-analysis → 📤 Export results

---

## 🔧 Features

- Upload temperature CSV data from container loggers
- Auto preprocessing (date parsing, format fix)
- Calculate:
  - Average & minimum temperature per container
  - Freezing efficiency (integration over threshold)
- Visualize:
  - Time-series line chart
  - Daily summaries and efficiency table
- Export results to Excel

---

## 📁 File Structure

| File | Description |
|------|-------------|
| `CT_Temp.py` | Main script for analysis |
| `requirements.txt` | Required Python packages |

---

## ▶️ How to Use

1. Prepare the environment:
- Python 3.9 or higher is recommended.
- Install required packages.

2. Install requirements:
> 아래 명령어를 터미널 또는 명령 프롬프트에 입력하세요.

    pip install -r requirements.txt

3. Run the script:
> 아래 명령어를 실행하여 스크립트를 구동합니다.

    python CT_Temp.py

--------------------------------------------------

📌 To-Do
--------

□ Streamlit app version for user upload and interactive dashboard  
□ Alert for containers not reaching target freezing range  
□ Integration with Google Sheets or Drive

--------------------------------------------------

🧠 Author
---------

Maintained by ohsh-harimpetfood
(https://github.com/ohsh-harimpetfood)
