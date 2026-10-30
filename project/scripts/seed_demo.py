"""Reset this folder's data to the ApplySmart demo state, for recording a video.

Loads the sample persona (Alex Tan's resume), saves one sample posting, and
sets example job requirements. It deletes everything under data/, so it runs
only in a folder marked as a demo copy by a file named DEMO_COPY.

Run from the demo folder:  python scripts/seed_demo.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import resume  # noqa: E402
from storage import Store  # noqa: E402
from tools import normalize_posted_date  # noqa: E402

SAMPLES = ROOT / "samples"

# Alex Tan's resume as the parser would produce it, written out so the demo
# starts the same way every time without a model call.
RESUME_DRAFT = {
    "items": [
        {"category": "education", "text": "Master of IT in Business (Analytics)", "organization": "Singapore Management University",
         "dates": "Aug 2025 – Dec 2026", "skills": [], "details": [
             {"text": "GPA 3.7/4.0", "skills": []},
             {"text": "Coursework: Machine Learning, Human-AI Interaction, Data Visualisation",
              "skills": ["Machine Learning", "Data Visualisation"]},
         ]},
        {"category": "education", "text": "BSc Statistics, Second Upper Honours", "organization": "Example University",
         "dates": "2017 – 2021", "skills": ["Statistics"], "details": []},
        {"category": "work", "text": "Data Analyst", "organization": "Example Bank, Singapore",
         "dates": "Jul 2022 – Present", "skills": [], "details": [
             {"text": "Built weekly SQL reports for the retail lending team, cutting manual prep time by 30%", "skills": ["SQL"]},
             {"text": "Helped migrate 40+ dashboards from Excel to Tableau", "skills": ["Tableau", "Excel"]},
             {"text": "Presented monthly loan performance findings to the product team", "skills": []},
         ]},
        {"category": "work", "text": "Business Analyst Intern", "organization": "RetailCo",
         "dates": "May 2021 – Dec 2021", "skills": [], "details": [
             {"text": "Cleaned and analysed point-of-sale data in Python and pandas", "skills": ["Python", "pandas"]},
             {"text": "Supported the pricing team with weekly competitor price tracking", "skills": []},
         ]},
        {"category": "project", "text": "Customer Churn Prediction", "organization": "SMU Data Hackathon",
         "dates": "Mar 2026", "skills": [], "details": [
             {"text": "Participated in a 4-person team that built a churn model in Python (XGBoost)", "skills": ["Python", "XGBoost"]},
             {"text": "Placed 3rd of 25 teams", "skills": []},
         ]},
        {"category": "leadership", "text": "Vice-President", "organization": "SMU Analytics Club",
         "dates": "2025 – 2026", "skills": [], "details": [
             {"text": "Organised 6 workshops on SQL and Tableau for 200+ students", "skills": ["SQL", "Tableau"]},
         ]},
    ],
    "skills": ["Python", "SQL", "R", "Tableau", "Power BI", "Excel", "Git", "English", "Mandarin"],
}

# The Harbour Bank posting (sample 1) as the assistant extracts it. Pasting
# sample 2 during the demo then shows the duplicate check against it.
POSTING_FIELDS = {
    "title": "Data Analyst, Consumer Banking Group",
    "company": "Harbour Bank Ltd",
    "department": "Consumer Banking Group",
    "location": "Singapore",
    "employment_type": "Full-time · Hybrid",
    "source": "LinkedIn",
    "requirements": [
        {"text": "2+ years of experience in SQL", "type": "required"},
        {"text": "Proficiency in Python or R", "type": "required"},
        {"text": "Experience with Tableau or Power BI", "type": "required"},
        {"text": "Experience leading analytics projects end to end", "type": "required"},
        {"text": "Banking domain knowledge", "type": "preferred"},
    ],
    "responsibilities": [
        "Build dashboards that track loan portfolio performance",
        "Partner with product teams to define and monitor KPIs",
        "Prepare monthly performance reviews for senior management",
    ],
    "about_role": "Harbour Bank's Consumer Banking Group is looking for a Data Analyst to turn lending data into decisions.",
    "products": ["Consumer lending", "Loan portfolio"],
    "partner_teams": ["Product teams", "Senior management"],
}

# Against JOB-0001 ("Full-time · Hybrid", no salary) these give one of each
# result in the demo: job type meets, work arrangement doesn't, salary not stated.
REQUIREMENTS = {
    "min_monthly_salary": 6000,
    "hours": ["full-time"],
    "contract_types": [],
    "work_arrangements": ["remote"],
    "dealbreakers": ["No shift or weekend work"],
}


def main() -> int:
    if not (ROOT / "DEMO_COPY").is_file():
        print(f"Refusing to run: {ROOT} is not marked as a demo copy (no DEMO_COPY file).")
        print("This script deletes all saved data, so it only runs in the demo folder.")
        return 1
    shutil.rmtree(ROOT / "data", ignore_errors=True)
    store = Store(ROOT)
    store.ensure_layout()

    resume_file = SAMPLES / "resume_alex_tan.txt"
    imported = store.import_resume_data(
        resume.flatten_draft(resume.normalize_draft(RESUME_DRAFT)), resume_file.name, resume_file.read_bytes(),
    )

    date_text = "2 weeks ago"
    date_posted, estimate = normalize_posted_date(date_text)
    posting = store.create_posting(
        {**POSTING_FIELDS, "date_posted": date_posted, "date_posted_is_estimate": estimate, "date_posted_text": date_text},
        (SAMPLES / "posting_1_linkedin_data_analyst.txt").read_text(encoding="utf-8"),
    )
    store.write_job_preferences(REQUIREMENTS)

    print(f"Demo data ready in {ROOT / 'data'}:")
    print(f"  {len(imported['entries'])} experience entries from {resume_file.name}")
    print(f"  {posting['id']} {posting['title']} ({posting['company']})")
    print("  job requirements: S$6,000 minimum, full-time, remote only, no shift work")
    print("  empty interaction log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
