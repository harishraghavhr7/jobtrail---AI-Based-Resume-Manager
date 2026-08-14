import logging
from celery import shared_task
from django.shortcuts import get_object_or_404
from django.db import transaction
from .models import Resume, Application, MatchAnalysis
from .services import extract_text_from_file, extract_resume_skills, analyze_match

logger = logging.getLogger(__name__)

@shared_task
def extract_resume_skills_task(resume_id):
    """
    Background task to extract text from a resume file and query
    Groq to parse technical and soft skills.
    """
    logger.info(f"Starting resume skill extraction for Resume ID: {resume_id}")
    try:
        resume = Resume.objects.get(pk=resume_id)
        
        # 1. Extract raw text from file
        file_path = resume.file.path
        raw_text = extract_text_from_file(resume.file)
        
        # 2. Extract skills using Groq
        skills = extract_resume_skills(raw_text)
        
        # 3. Update the resume object in a transaction
        with transaction.atomic():
            resume.raw_text = raw_text
            resume.parsed_skills = skills
            resume.save()
            
        logger.info(f"Successfully extracted {len(skills)} skills for Resume ID: {resume_id}")
        return {
            "status": "success",
            "skills_count": len(skills),
            "skills": skills
        }
        
    except Resume.DoesNotExist:
        logger.error(f"Resume with ID {resume_id} does not exist.")
        return {"status": "error", "message": f"Resume {resume_id} not found"}
    except Exception as e:
        logger.error(f"Failed to parse resume {resume_id}: {str(e)}")
        return {"status": "error", "message": str(e)}


@shared_task
def analyze_match_task(application_id):
    """
    Background task to perform match analysis between a linked resume's parsed skills
    and the pasted Job Description (JD).
    """
    logger.info(f"Starting match analysis for Application ID: {application_id}")
    try:
        application = Application.objects.get(pk=application_id)
        
        if not application.resume:
            logger.warning(f"Application ID {application_id} has no linked resume.")
            return {"status": "error", "message": "No resume linked to this application"}
            
        resume = application.resume
        
        # Check if resume skills have been parsed. If not, trigger it synchronously or wait.
        if not resume.parsed_skills and resume.raw_text:
            logger.info("Resume has text but no parsed skills. Parsing skills synchronously first.")
            resume.parsed_skills = extract_resume_skills(resume.raw_text)
            resume.save()
        elif not resume.parsed_skills and not resume.raw_text:
            logger.info("Resume text not yet extracted. Extracting text and parsing skills synchronously first.")
            raw_text = extract_text_from_file(resume.file)
            resume.raw_text = raw_text
            resume.parsed_skills = extract_resume_skills(raw_text)
            resume.save()
            
        # Perform JD Match Analysis
        analysis_result = analyze_match(resume.parsed_skills, application.jd_text)
        
        # Save or update MatchAnalysis record
        with transaction.atomic():
            match_analysis, created = MatchAnalysis.objects.get_or_create(
                application=application
            )
            match_analysis.match_percentage = analysis_result.get('match_percentage', 0)
            match_analysis.strong_matches = analysis_result.get('strong_matches', [])
            match_analysis.missing_skills = analysis_result.get('missing_skills', [])
            match_analysis.ai_summary = analysis_result.get('ai_summary', '')
            match_analysis.raw_response = analysis_result.get('raw_response', {})
            match_analysis.save()
            
        logger.info(f"Successfully completed match analysis for Application ID {application_id} (Score: {match_analysis.match_percentage}%)")
        return {
            "status": "success",
            "match_percentage": match_analysis.match_percentage,
            "strong_matches_count": len(match_analysis.strong_matches),
            "missing_skills_count": len(match_analysis.missing_skills)
        }
        
    except Application.DoesNotExist:
        logger.error(f"Application with ID {application_id} does not exist.")
        return {"status": "error", "message": f"Application {application_id} not found"}
    except Exception as e:
        logger.error(f"Failed to analyze match for application {application_id}: {str(e)}")
        return {"status": "error", "message": str(e)}
