import json

from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY, SCORING_MODEL, DRAFTING_MODEL

_client = Anthropic(api_key=ANTHROPIC_API_KEY)

SCORE_TOOL = {
    "name": "submit_score",
    "description": "Submit a fit score for a candidate against a job posting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fit_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "0-100 fit score between the resume and the job posting.",
            },
            "fit_reasoning": {
                "type": "string",
                "description": "2-3 sentence explanation of the score, naming the strongest matches and the biggest gaps.",
            },
            "requires_graduate_degree": {
                "type": "boolean",
                "description": (
                    "True if the posting requires the candidate to be pursuing or to hold a Master's, "
                    "PhD, or MBA (e.g. 'pursuing an MS or PhD', 'graduate students only'). "
                    "False if undergraduates / BS students are eligible, including postings open to "
                    "'undergraduate or graduate' students, or if no degree level is stated."
                ),
            },
            "is_research_role": {
                "type": "boolean",
                "description": "True if this is primarily a research position (research scientist/engineer/assistant, research intern, lab research).",
            },
        },
        "required": ["fit_score", "fit_reasoning", "requires_graduate_degree", "is_research_role"],
    },
}

DRAFT_TOOL = {
    "name": "submit_draft",
    "description": "Submit tailored application materials for a candidate applying to a job posting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "resume_bullets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-5 resume bullet points, rewritten/reordered from the candidate's real experience to best match this posting. Never invent experience the resume doesn't support.",
            },
            "cover_letter": {
                "type": "string",
                "description": "A concise (250-350 word) tailored cover letter for this posting, in the candidate's voice, based only on their real resume content.",
            },
        },
        "required": ["resume_bullets", "cover_letter"],
    },
}


def _job_summary(job: dict) -> str:
    return (
        f"Title: {job.get('title')}\n"
        f"Category: {job.get('category')}\n"
        f"Company: {job.get('company')}\n"
        f"Location: {job.get('location')}\n"
        f"Work model: {job.get('work_model')}\n"
        f"Salary: {job.get('salary')}\n"
        f"Grad time window: {job.get('grad_time') or job.get('grad_year')}\n"
        f"Company size/industry: {job.get('company_size')} / {job.get('company_industry')}\n"
        f"Qualifications: {job.get('qualifications')}\n"
    )


def score_job(resume_text: str, job: dict) -> dict:
    message = _client.messages.create(
        model=SCORING_MODEL,
        max_tokens=500,
        system=(
            "You screen internship postings against a candidate's resume. "
            "Be honest and calibrated: most postings are not a great fit. "
            "Score strictly on overlap between the resume's real skills/experience "
            "and the posting's stated qualifications. The candidate is a BS (undergraduate) "
            "student, so also flag whether the posting requires a graduate degree and whether it "
            "is a research role."
        ),
        tools=[SCORE_TOOL],
        tool_choice={"type": "tool", "name": "submit_score"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{_job_summary(job)}"
                ),
            }
        ],
    )
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_score":
            return block.input
    raise RuntimeError("Claude did not return a score tool call")


def draft_application(resume_text: str, job: dict) -> dict:
    message = _client.messages.create(
        model=DRAFTING_MODEL,
        max_tokens=1500,
        system=(
            "You write tailored internship application materials. Only use experience, "
            "skills, and projects that actually appear in the candidate's resume text. "
            "Never fabricate employers, titles, metrics, or skills."
        ),
        tools=[DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "submit_draft"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{_job_summary(job)}\n\n"
                    "Draft tailored resume bullets and a cover letter for this posting."
                ),
            }
        ],
    )
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_draft":
            return block.input
    raise RuntimeError("Claude did not return a draft tool call")
