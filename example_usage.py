#!/usr/bin/env python3
"""
Example usage of the Patient Responsibility Memo Agent

This script demonstrates how to use the agent with custom configuration.
"""

from patient_responsibility_agent import PatientResponsibilityAgent
import logging

# Configure logging for this example
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def main():
    """Example usage of the Patient Responsibility Agent."""
    
    # Initialize the agent
    agent = PatientResponsibilityAgent()
    
    try:
        print("Starting Patient Responsibility Memo Agent...")
        
        # Run the full processing workflow
        agent.process_patients()
        
        # Get and display summary
        summary = agent.get_summary()
        print(f"\n=== Processing Summary ===")
        print(f"Total patients processed: {summary['total_patients_processed']}")
        
        if summary['patients']:
            print("\nPatient Details:")
            for patient in summary['patients']:
                print(f"  • {patient['name']} (ID: {patient['id']})")
                print(f"    - Insurances: {patient['insurance_count']}")
                print(f"    - Service Line: {patient['service_line']}")
        else:
            print("No patients were processed.")
            
    except KeyboardInterrupt:
        print("\nProcessing interrupted by user.")
    except Exception as e:
        print(f"Error during processing: {e}")
        logging.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
