#!/usr/bin/env python3
"""
FastAPI server for Patient Responsibility Memo Agent

This server provides:
- Healthcheck endpoint
- Trigger endpoint to run the patient responsibility processing
"""

# Load environment variables from .env file before importing config
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
import logging
import traceback
from typing import Dict, Optional
from patient_responsibility_agent import PatientResponsibilityAgent, utc_now
import config

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('patient_responsibility.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Patient Responsibility Agent API",
    description="API for triggering patient responsibility memo processing",
    version="1.0.0"
)


@app.get("/health")
async def healthcheck():
    """
    Health check endpoint to verify the server is running.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "patient-responsibility-agent"
    }


@app.post("/trigger")
async def trigger_processing():
    """
    Trigger endpoint that runs the main patient responsibility processing function.
    
    This endpoint:
    1. Initializes the PatientResponsibilityAgent
    2. Runs the process_patients workflow
    3. Returns a summary of the processing results
    """
    run_started = utc_now()
    
    try:
        logger.info("Trigger endpoint called - starting patient responsibility processing...")
        
        
        agent = PatientResponsibilityAgent()
        
        # Run processing
        agent.process_patients()
        
        # Get summary
        summary = agent.get_summary()
        run_ended = utc_now()
        
        response_data = {
            "status": "success",
            "started_at": run_started.isoformat(),
            "ended_at": run_ended.isoformat(),
            "summary": summary
        }
        
        logger.info(f"Processing completed successfully. Processed {summary['total_patients_processed']} patients.")
        
        return JSONResponse(content=response_data, status_code=200)
        
    except KeyboardInterrupt:
        logger.warning("Processing interrupted by user")
        raise HTTPException(status_code=500, detail="Processing interrupted")
        
    except Exception as e:
        run_ended = utc_now()
        error_message = str(e)
        error_traceback = traceback.format_exc()
        
        logger.error(f"Error during processing: {error_message}")
        logger.error(f"Traceback: {error_traceback}")
        
        # Log error to database if possible
        try:
            from patient_responsibility_agent import log_agent_run_error
            log_agent_run_error(error_message, run_started, run_ended)
        except Exception as log_error:
            logger.error(f"Failed to log error to database: {log_error}")
        
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "message": error_message,
                "started_at": run_started.isoformat(),
                "ended_at": run_ended.isoformat()
            }
        )


@app.get("/")
async def root():
    """
    Root endpoint with API information.
    """
    return {
        "service": "Patient Responsibility Agent API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "trigger": "/trigger (POST)"
        }
    }

