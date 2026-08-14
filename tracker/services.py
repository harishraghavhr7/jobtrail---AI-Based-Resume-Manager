import os
import json
import logging
import re
import pdfplumber
import docx
from django.conf import settings
from groq import Groq

logger = logging.getLogger(__name__)

def extract_text_from_file(file):
    """
    Extracts text from an uploaded file (PDF, DOCX, or text).
    Accepts a Django UploadedFile or file-like object.
    """
    filename = getattr(file, 'name', '').lower()
    text = ""
    
    try:
        # Re-seek file just in case it has been read before
        if hasattr(file, 'seek'):
            file.seek(0)

        if filename.endswith('.pdf'):
            with pdfplumber.open(file) as pdf:
                pages_text = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        pages_text.append(page_text)
                text = "\n".join(pages_text)
                
        elif filename.endswith('.docx'):
            doc = docx.Document(file)
            paragraphs = [p.text for p in doc.paragraphs]
            text = "\n".join(paragraphs)
            
        else:
            # Fallback to reading as text
            content = file.read()
            if isinstance(content, bytes):
                text = content.decode('utf-8', errors='ignore')
            else:
                text = content
                
    except Exception as e:
        logger.error(f"Error extracting text from file {filename}: {str(e)}")
        raise e

    return text.strip()


def clean_and_parse_json(text):
    """
    Cleans markdown code block wraps and parses JSON.
    """
    cleaned = text.strip()
    
    # Remove markdown code fences if present
    if cleaned.startswith("```"):
        # Match starting fence (e.g. ```json or ```)
        cleaned = re.sub(r'^```[a-zA-Z]*\s*', '', cleaned)
        # Match ending fence
        cleaned = re.sub(r'\s*```$', '', cleaned)
    
    cleaned = cleaned.strip()
    
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Attempt simple regex clean-ups
        # If it returned some text prefixing a JSON, try to search for the JSON part
        array_match = re.search(r'\[\s*".*"\s*\]', cleaned, re.DOTALL)
        if array_match:
            try:
                return json.loads(array_match.group(0))
            except json.JSONDecodeError:
                pass
                
        obj_match = re.search(r'\{\s*".*"\s*\}', cleaned, re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group(0))
            except json.JSONDecodeError:
                pass
                
        raise e


def get_groq_client():
    """
    Returns an initialized Groq client if the API key is configured.
    Otherwise returns None to signify mock mode.
    """
    api_key = getattr(settings, 'GROQ_API_KEY', '')
    # Check if the API key is set to a placeholder
    if not api_key or api_key.startswith('gsk_your_default') or api_key.startswith('your_groq_api_key'):
        return None
    try:
        return Groq(api_key=api_key)
    except Exception as e:
        logger.warning(f"Failed to initialize Groq client: {str(e)}")
        return None


def extract_resume_skills(raw_text):
    """
    Uses the Groq API to extract skills from resume text.
    Falls back to mock extraction if the key is not set.
    """
    client = get_groq_client()
    
    if not client:
        logger.info("Using mock resume skill extraction.")
        # Return mock skills
        return ["Python", "Django", "Django REST Framework", "JavaScript", "React", "PostgreSQL", "HTML", "CSS", "Git", "REST APIs", "Celery", "Redis", "Docker"]

    prompt = (
        "Extract technical skills, tools, frameworks, and notable soft skills from this resume text.\n"
        "Return ONLY a JSON array of strings, no other text, no markdown formatting.\n\n"
        f"Resume text:\n{raw_text}"
    )

    def run_api_call():
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=settings.GROQ_MODEL,
            temperature=0.0,
        )
        return chat_completion.choices[0].message.content

    try:
        response_text = run_api_call()
        try:
            return clean_and_parse_json(response_text)
        except json.JSONDecodeError:
            # Retry once
            logger.warning("JSON parsing failed, retrying once...")
            response_text = run_api_call()
            return clean_and_parse_json(response_text)
    except Exception as e:
        logger.error(f"Error in extract_resume_skills Groq API call: {str(e)}")
        raise e


def analyze_match(resume_skills_list, jd_text):
    """
    Uses the Groq API to compare resume skills with a Job Description.
    Falls back to mock analysis if the key is not set.
    """
    client = get_groq_client()
    
    if not client:
        logger.info("Using mock match analysis.")
        # Generate generic mock response
        jd_lower = jd_text.lower()
        skills_lower = [s.lower() for s in resume_skills_list]
        
        strong_matches = []
        missing_skills = []
        
        # Simple keywords to match
        common_keywords = ["python", "django", "javascript", "react", "postgresql", "docker", "aws", "kubernetes", "celery", "redis", "html", "css", "git"]
        for keyword in common_keywords:
            if keyword in jd_lower:
                # Find matching skill in user's resume
                matched_skill = None
                for user_skill in resume_skills_list:
                    if user_skill.lower() == keyword or user_skill.lower().startswith(keyword):
                        matched_skill = user_skill
                        break
                
                if matched_skill:
                    strong_matches.append(matched_skill)
                else:
                    missing_skills.append(keyword.capitalize())

        # Match percentage calculation
        total_keywords = len(strong_matches) + len(missing_skills)
        if total_keywords > 0:
            match_percentage = int((len(strong_matches) / total_keywords) * 100)
        else:
            match_percentage = 50

        # Adjust score
        if "python" in skills_lower and "django" in skills_lower:
            match_percentage = max(70, match_percentage)
            ai_summary = "Excellent matching candidate for the Django backend stack with a solid base in Python."
        else:
            match_percentage = min(50, match_percentage)
            ai_summary = "Candidate lacks the core backend framework skills (Django/Python) required in the JD."
            
        mock_response = {
            "match_percentage": match_percentage,
            "strong_matches": strong_matches,
            "missing_skills": missing_skills,
            "ai_summary": ai_summary,
            "raw_response": {"mode": "mock", "info": "No Groq API key set, generated locally"}
        }
        return mock_response

    resume_skills = ", ".join(resume_skills_list)
    prompt = (
        "Compare the candidate's skills to this job description and assess fit.\n\n"
        f"Candidate skills: {resume_skills}\n\n"
        f"Job description:\n{jd_text}\n\n"
        "Return ONLY valid JSON in this exact structure (no markdown fences, no extra text):\n"
        "{\n"
        "  \"match_percentage\": <integer 0-100>,\n"
        "  \"strong_matches\": [<skills present in both resume and JD>],\n"
        "  \"missing_skills\": [<important skills in JD but absent from resume>],\n"
        "  \"ai_summary\": \"<one sentence overall assessment>\"\n"
        "}"
    )

    def run_api_call():
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=settings.GROQ_MODEL,
            temperature=0.0,
        )
        return chat_completion.choices[0].message.content

    try:
        response_text = run_api_call()
        parsed_data = None
        try:
            parsed_data = clean_and_parse_json(response_text)
        except json.JSONDecodeError:
            # Retry once
            logger.warning("JD Match Analysis JSON parsing failed, retrying once...")
            response_text = run_api_call()
            parsed_data = clean_and_parse_json(response_text)
        
        # Attach raw response for debugging/records
        parsed_data['raw_response'] = {
            "model": settings.GROQ_MODEL,
            "response": response_text
        }
        return parsed_data
    except Exception as e:
        logger.error(f"Error in analyze_match Groq API call: {str(e)}")
        # If API failed, return an error block rather than crashing the save flow
        return {
            "match_percentage": 0,
            "strong_matches": [],
            "missing_skills": [],
            "ai_summary": "Couldn't analyze — try again",
            "raw_response": {"error": str(e)}
        }
