from functools import lru_cache

from pypdf import PdfReader

from app.config import RESUME_PATH


@lru_cache(maxsize=1)
def get_resume_text() -> str:
    reader = PdfReader(str(RESUME_PATH))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
