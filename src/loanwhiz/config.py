"""Central config — GCP project, model names, dataset URLs."""

GCP_PROJECT = "loanwhiz"
GCP_LOCATION = "us-central1"

# Gemini 2.5 Flash for orchestration/planning (fast, 1M context)
# Gemini 2.5 Pro for extraction tasks (highest quality)
MODEL_FLASH = "gemini-2.5-flash"
MODEL_PRO = "gemini-2.5-pro"

HF_BASE = "https://huggingface.co/datasets/Algoritmica/green-lion-2026/resolve/main/Hackathon_Data"

GREEN_LION = {
    "deal_name": "Green Lion 2026-1 B.V.",
    "prospectus_url": f"{HF_BASE}/green-lion-2026-1-prospectus.pdf",
    "tape_urls": [
        {"date": "2026-02-28", "url": f"{HF_BASE}/green_lion_202602_1_synthetic_loan_tape.csv"},
        {"date": "2026-03-31", "url": f"{HF_BASE}/green_lion_202603_1_synthetic_loan_tape.csv"},
        {"date": "2026-04-30", "url": f"{HF_BASE}/green_lion_2026_1_synthetic_loan_tape.csv"},
    ],
    "investor_report_urls": [
        {"period": "February 2026", "url": f"{HF_BASE}/monthly-investor-report-green-lion-2026-1-february-2026.pdf"},
        {"period": "March 2026",    "url": f"{HF_BASE}/monthly-investor-report-green-lion-2026-1-march-2026.pdf"},
        {"period": "April 2026",    "url": f"{HF_BASE}/monthly-investor-report-green-lion-2026-1-april-2026.pdf"},
    ],
}
