#!/usr/bin/env python3
"""
Patient Responsibility Memo Agent

This script:
1. Gets updated patients from AdvancedMD API (last 24h)
2. Filters patients with insurance and appointments
3. Runs eligibility checks and Zapier webhooks
4. Calculates patient responsibility based on insurance type
5. Posts memos to AdvancedMD

Author: Auto-generated
"""

import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import time
import logging
import os
import uuid
import contextlib
import psycopg2
import psycopg2.extras
from sshtunnel import SSHTunnelForwarder
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


def memo_already_logged(patient_name: str, insurance_name: str, memo_text: str, lookback_days: int = 90) -> bool:
    """
    Returns True if an identical memo was already logged for this patient (success or skipped)
    within the lookback window. We match on the exact memo text + patient name.
    """
    # Patterns that match how we currently log messages
    success_msg = f"Patient: {patient_name} | Memo: {memo_text}"
    skipped_msg = f"Skipped due to posting rules. Patient: {patient_name} | Insurance: {insurance_name} | Memo preview: {memo_text}"

    sql = """
        SELECT 1
        FROM agent_run_logs
        WHERE agent_id = %s::uuid
          AND status IN ('success','skipped')
          AND start_time >= (NOW() AT TIME ZONE 'UTC' - (%s || ' days')::interval)
          AND (
                output_data->>'message' = %s
             OR output_data->>'message' = %s
          )
        LIMIT 1
    """
    args = (
        str(uuid.UUID(config.AGENT_ID)) if config.AGENT_ID else str(uuid.uuid4()),
        lookback_days,
        success_msg,
        skipped_msg,
    )
    try:
        with _pg_conn_via_ssh() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Failed duplicate-memo check: {e}", exc_info=True)
        # Be safe: if the check fails, do NOT block posting/logging
        return False

    

class AdvancedMDAPI:
    """AdvancedMD API client for patient and insurance management."""
    
    def __init__(self):
        self.base_url = config.AMD_CONFIG['base_url']
        self.api_base_url = config.AMD_CONFIG['api_base_url']
        self.username = config.AMD_CONFIG['username']
        self.password = config.AMD_CONFIG['password']
        self.office_code = config.AMD_CONFIG['office_code']
        self.app_name = config.AMD_CONFIG['app_name']
        self.token = None
        
    def authenticate(self) -> bool:
        """Authenticate with AdvancedMD and get session token."""
        payload = {
            "ppmdmsg": {
                "@action": "login",
                "@class": "login",
                "@msgtime": datetime.now().strftime("%m/%d/%Y %I:%M:%S %p"),
                "@username": self.username,
                "@psw": self.password,
                "@officecode": self.office_code,
                "@appname": self.app_name
            }
        }
        
        try:
            response = requests.post(
                self.base_url,
                headers={'Content-Type': 'application/json'},
                json=payload
            )
            response.raise_for_status()
            
            # Parse XML response to extract token
            root = ET.fromstring(response.text)
            usercontext = root.find('.//usercontext')
            if usercontext is not None and usercontext.text:
                self.token = usercontext.text.strip()
                logger.info("Successfully authenticated with AdvancedMD")
                return True
            else:
                logger.error("Failed to extract token from response")
                return False
                
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False
    
    def get_updated_patients(self, hours_back: int = 24) -> List[Dict]:
        """Get patients updated in the last N hours."""
        if not self.token:
            if not self.authenticate():
                return []
        
        # Calculate date 24 hours ago
        date_changed = (datetime.now() - timedelta(hours=hours_back)).strftime("%m/%d/%Y %I:%M:%S %p")
        
        payload = {
            "ppmdmsg": {
                "@action": "getupdatedpatients",
                "@class": "api",
                "@datechanged": date_changed,
                "@nocookie": "0",
                "patient": {
                    "@name": "Name",
                    "@ssn": "SSN",
                    "@changedat": "ChangedAt",
                    "@createdat": "CreatedAt",
                    "@hipaarelationship": "HipaaRelationship",
                    "@dob": "DOB",
                    "@sex": "Sex",
                    "@address1": "Address1",
                    "@address2": "Address2",
                    "@city": "City",
                    "@state": "State",
                    "@zipcode": "ZipCode"
                },
                "insurance": {
                    "@carname": "CarName",
                    "@carcode": "CarCode",
                    "@carcity": "CarCity",
                    "@changedat": "ChangedAt",
                    "@createdat": "CreatedAt",
                    "@active": "Active",
                    "@copaydollaramount": "CopayDollarAmount",
                    "@copaypercentageamount": "CopayPercentageAmount",
                    "@annualdeductible": "AnnualDeductible",
                    "@deductibleamountmet": "DeductibleAmountMet",
                    "@subscriberid": "SubscriberID",
                    "@subidnumber": "SubIdNumber"
                },
                "referralplan": {
                    "@reason": "Reason",
                    "@referraltype": "ReferralType",
                    "@defaultinchargeentry": "DefaultinChargeEntry",
                    "@byreferringproviderfid": "ByReferringProviderFID",
                    "@toreferringproviderfid": "ToReferringProviderFID"
                }
            }
        }
        
        try:
            response = requests.post(
                self.base_url,
                headers={
                    'Content-Type': 'application/json',
                    'Cookie': f'token={self.token}'
                },
                json=payload
            )
            response.raise_for_status()
            
            # Parse XML response
            root = ET.fromstring(response.text)
            patients = []
            
            for patient_elem in root.findall('.//patient'):
                # Check for required fields - skip patient if DOB or sex is missing
                dob = patient_elem.get('dob')
                sex = patient_elem.get('sex')
                
                if not dob or not sex:
                    logger.warning(f"Skipping the patient {patient_elem.get('name')} - missing DOB or sex (DOB: {dob}, Sex: {sex})")
                    continue
                
                patient_data = {
                    'id': patient_elem.get('id'),
                    'name': patient_elem.get('name', '').strip(),
                    'ssn': patient_elem.get('ssn', '').strip(),
                    'changedat': patient_elem.get('changedat'),
                    'createdat': patient_elem.get('createdat'),
                    'hipaarelationship': patient_elem.get('hipaarelationship'),
                    'updatestatus': patient_elem.get('updatestatus'),
                    'dob': dob.strip(),
                    'gender': sex.strip(),
                    'address1': patient_elem.get('address1', '').strip() if patient_elem.get('address1') else '',
                    'address2': patient_elem.get('address2', '').strip() if patient_elem.get('address2') else '',
                    'city': patient_elem.get('city', '').strip() if patient_elem.get('city') else 'Denver',  # Default city
                    'state': patient_elem.get('state', 'CO').strip() if patient_elem.get('state') else 'CO',  # Default state
                    'zipcode': patient_elem.get('zipcode', '').strip() if patient_elem.get('zipcode') else '',
                    'insurances': []
                }
                
                # Extract insurance information
                for insurance_elem in patient_elem.findall('.//insurance'):
                    insurance_data = {
                        'id': insurance_elem.get('id'),
                        'active': insurance_elem.get('active') == '1',
                        'carcode': insurance_elem.get('carcode'),
                        'carname': insurance_elem.get('carname'),
                        'carcity': insurance_elem.get('carcity'),
                        'copaydollaramount': float(insurance_elem.get('copaydollaramount', 0)),
                        'copaypercentageamount': float(insurance_elem.get('copaypercentageamount', 0)),
                        'annualdeductible': float(insurance_elem.get('annualdeductible', 0)),
                        'deductibleamountmet': float(insurance_elem.get('deductibleamountmet', 0)),
                        'createdat': insurance_elem.get('createdat'),
                        'changedat': insurance_elem.get('changedat'),
                        'subscriberid': insurance_elem.get('subscriberid', '').strip() if insurance_elem.get('subscriberid') else '',
                        'subidnumber': insurance_elem.get('subidnumber', '').strip() if insurance_elem.get('subidnumber') else ''
                    }
                    patient_data['insurances'].append(insurance_data)
                
                # Only include patients with at least one insurance
                if patient_data['insurances']:
                    patients.append(patient_data)
            
            logger.info(f"Retrieved {len(patients)} patients with insurance")
            return patients
            
        except Exception as e:
            logger.error(f"Failed to get updated patients: {e}")
            return []
    
    def has_appointments(self, patient_id: str) -> bool:
        """Check if patient has appointments using getpatientvisits API."""
        if not self.token:
            return False
            
        payload = {
            "ppmdmsg": {
                "@action": "getpatientvisits",
                "@patientid": patient_id,
                "@class": "api",
                "@nocookie": "0",
                "visit": {
                    "@color": "Color",
                    "@duration": "Duration",
                    "@refreason": "RefReason",
                    "@apptstatus": "ApptStatus",
                    "@ByRefProvMiddleName": "ByRefProvMiddleName",
                    "@ByRefProvFirstName": "ByRefProvFirstName",
                    "@ByRefProvLastName": "ByRefProvLastName",
                    "@ByReferringProviderFID": "ByReferringProviderFID",
                    "@columnheading": "ColumnHeading",
                    "@AppointmentType": "AppointmentType",
                    "@AppointmentTypeID": "AppointmentTypeID",
                    "@Visitstartdatetime": "VisitStartDateTime"
                },
                "patient": {
                    "@createdat": "CreatedAt",
                    "@changedat": "ChangedAt",
                    "@ssn": "SSN",
                    "@name": "Name"
                },
                "insurance": {
                    "@createdat": "CreatedAt",
                    "@changedat": "ChangedAt",
                    "@carcode": "CarCode",
                    "@carname": "CarName"
                }
            }
        }
            
        try:
            response = requests.post(
                self.base_url,
                headers={
                    'Cookie': f'token={self.token}',
                    'Content-Type': 'application/json'
                },
                json=payload
            )
            response.raise_for_status()
            
            # Parse XML response
            root = ET.fromstring(response.text)
            results = root.find('.//Results')
            
            if results is not None:
                visit_count = int(results.get('visitcount', '0'))
                has_appts = visit_count > 0
                logger.debug(f"Patient {patient_id} has {visit_count} visits/appointments: {has_appts}")
                return has_appts
            else:
                logger.warning(f"No results found in response for patient {patient_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error checking appointments for patient {patient_id}: {e}")
            return False
    
    def submit_eligibility_check(self, patient_id: str, insurance_coverage_id: str) -> Optional[str]:
        """Submit eligibility check request."""
        if not self.token:
            return None
            
        payload = {
            "ppmdmsg": {
                "@action": "submitdemandrequest",
                "@class": "atseligibility",
                "@msgtime": datetime.now().strftime("%m/%d/%Y %I:%M:%S %p"),
                "@eligibilitystc": "30",
                "@patientid": patient_id,
                "@insurancecoverageid": insurance_coverage_id
            }
        }
        
        try:
            response = requests.post(
                self.base_url,
                headers={
                    'Content-Type': 'application/json',
                    'Cookie': f'token={self.token}'
                },
                json=payload
            )
            response.raise_for_status()
            
            # Parse XML response to get eligibility ID
            root = ET.fromstring(response.text)
            results = root.find('.//Results')
            if results is not None:
                eligibility_id = results.get('eligibilityid')
                logger.debug(f"Submitted eligibility check for patient {patient_id}, eligibility_id: {eligibility_id}")
                return eligibility_id
            
        except Exception as e:
            logger.error(f"Failed to submit eligibility check for patient {patient_id}: {e}")
            
        return None
    
    
    
    
    def check_eligibility_response(self, eligibility_id: str) -> Dict:
        """Check eligibility response."""
        if not self.token:
            return {}
            
        payload = {
            "ppmdmsg": {
                "@action": "CheckEligibilityResponse",
                "@class": "eligibility",
                "@msgtime": datetime.now().strftime("%m/%d/%Y %I:%M:%S %p"),
                "@eligibilityid": eligibility_id
            }
        }
        
        try:
            response = requests.post(
                self.base_url,
                headers={
                    'Content-Type': 'application/json',
                    'Cookie': f'token={self.token}',
                    'Accept': 'application/json'
                },
                json=payload
            )
            response.raise_for_status()
            
            # Parse response
            if response.headers.get('content-type', '').startswith('application/json'):
                return response.json()
            else:
                # Parse XML if JSON not returned
                root = ET.fromstring(response.text)
                results = root.find('.//Results')
                if results is not None:
                    return {attr: results.get(attr) for attr in results.attrib}
            
        except Exception as e:
            logger.error(f"Failed to check eligibility response for {eligibility_id}: {e}")
            
        return {}
    
    def post_memo(self, patient_id: str, memo_text: str) -> bool:
        """Post a memo to patient record."""
        if not self.token:
            return False
            
        payload = {
            "ppmdmsg": {
                "@action": "savememo",
                "@class": "demographics",
                "@msgtime": datetime.now().strftime("%m/%d/%Y %I:%M:%S %p"),
                "@patientfid": patient_id,
                "@created": datetime.now().strftime("%m/%d/%Y"),
                "@case_memotext": memo_text,
                "@memotype": "d",
                "@expiredate": ""
            }
        }
        
        try:
            # ===== REAL API CALL (CURRENTLY ACTIVE) =====
            response = requests.post(
                self.base_url,
                headers={
                    'Content-Type': 'application/json',
                    'Cookie': f'token={self.token}'
                },
                json=payload
            )
            response.raise_for_status()
            
            # Parse XML response to check success
            root = ET.fromstring(response.text)
            results = root.find('.//Results')
            if results is not None and results.get('success') == '1':
                logger.info(f"Successfully posted memo for patient {patient_id}: {memo_text}")
                return True
            else:
                error = root.find('.//Error')
                error_msg = error.text if error is not None and error.text else "Unknown error"
                logger.error(f"Failed to post memo for patient {patient_id}: {error_msg}")
                return False
            # ===== END REAL API CALL =====
            
            # ===== SIMULATION MODE (CURRENTLY DISABLED) =====
            # Uncomment these lines to return to simulation mode:
            # logger.info(f"MEMO CREATED (simulated) for patient {patient_id}: {memo_text}")
            # logger.debug(f"Memo payload that would be sent: {payload}")
            # return True
            # ===== END SIMULATION MODE =====
                
        except Exception as e:
            logger.error(f"Failed to prepare memo for patient {patient_id}: {e}")
            return False


class PVerifyAPI:
    """PVerify API client for insurance eligibility verification."""
    
    def __init__(self):
        self.token_url = config.PVERIFY_CONFIG['token_url']
        self.discovery_url = config.PVERIFY_CONFIG['discovery_url']
        self.eligibility_url = config.PVERIFY_CONFIG['eligibility_url']
        self.client_id = config.PVERIFY_CONFIG['client_id']
        self.client_secret = config.PVERIFY_CONFIG['client_secret']
        self.provider_last_name = config.PVERIFY_CONFIG['provider_last_name']
        self.provider_npi = config.PVERIFY_CONFIG['provider_npi']
        self.access_token = None
        self.token_expires_at = None
    
    def get_access_token(self) -> bool:
        """Get or refresh PVerify access token."""
        # Check if current token is still valid (with 5 min buffer)
        if (self.access_token and self.token_expires_at and 
            datetime.now() < self.token_expires_at - timedelta(minutes=5)):
            return True
        
        payload = {
            'Client_Id': self.client_id,
            'Client_Secret': self.client_secret,
            'grant_type': 'client_credentials'
        }
        
        try:
            logger.debug(f"PVerify Token Request - URL: {self.token_url}")
            logger.debug(f"PVerify Token Request - Headers: {{'Content-Type': 'application/x-www-form-urlencoded'}}")
            logger.debug(f"PVerify Token Request - Payload: {{'Client_Id': '{self.client_id}', 'grant_type': 'client_credentials', 'Client_Secret': '[REDACTED]'}}")
            
            response = requests.post(
                self.token_url,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data=payload
            )
            response.raise_for_status()
            
            logger.debug(f"PVerify Token Response - Status: {response.status_code}")
            logger.debug(f"PVerify Token Response - Headers: {dict(response.headers)}")
            
            token_data = response.json()
            logger.debug(f"PVerify Token Response - Body: {{'access_token': '[REDACTED]', 'expires_in': {token_data.get('expires_in', 'N/A')}, 'token_type': '{token_data.get('token_type', 'N/A')}'}}")
            
            self.access_token = token_data['access_token']
            expires_in = token_data.get('expires_in', 3600)  # Default 1 hour
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info("Successfully obtained PVerify access token")
            return True
            
        except Exception as e:
            logger.error(f"Failed to get PVerify access token: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"PVerify Token Error Response - Status: {e.response.status_code}")
                logger.error(f"PVerify Token Error Response - Body: {e.response.text}")
            return False
    
    def match_insurance_name(self, amd_insurance_name: str, pverify_payer_name: str) -> bool:
        """Match insurance names between AMD and PVerify."""
        amd_name = amd_insurance_name.upper().strip()
        pverify_name = pverify_payer_name.upper().strip()
        
        # Direct match
        if amd_name == pverify_name:
            return True
        
        # Common abbreviation mappings
        abbreviations = {
            'BCBS': ['BLUE CROSS BLUE SHIELD', 'BLUECROSS', 'BC BS'],
            'UNITED': ['UNITED HEALTHCARE', 'UHC'],
            'ANTHEM': ['ANTHEM BCBS', 'ANTHEM BLUE CROSS'],
            'AETNA': ['AETNA HEALTH', 'AETNA INC'],
            'CIGNA': ['CIGNA HEALTH', 'CIGNA HEALTHCARE'],
            'HUMANA': ['HUMANA HEALTH', 'HUMANA INC'],
            'MEDICAID': ['MCD', 'HEALTH FIRST MEDICAID'],
            'MEDICARE': ['MEDICARE ADVANTAGE']
        }
        
        # Check if AMD name contains abbreviation and PVerify has full name
        for abbrev, full_names in abbreviations.items():
            if abbrev in amd_name:
                for full_name in full_names:
                    if full_name in pverify_name:
                        return True
        
        # Check if PVerify name contains abbreviation and AMD has full name  
        for abbrev, full_names in abbreviations.items():
            if abbrev in pverify_name:
                for full_name in full_names:
                    if full_name in amd_name:
                        return True
        
        # Partial match - check if key words overlap
        amd_words = set(amd_name.split())
        pverify_words = set(pverify_name.split())
        common_words = amd_words.intersection(pverify_words)
        
        # If they share significant words, consider it a match
        if len(common_words) >= 2 or any(len(word) > 5 for word in common_words):
            return True
        
        return False
    
    def get_location_and_state_id(self, patient: Dict) -> Tuple[str, int]:
        """Get location code and state ID from patient address."""
        state = patient.get('state', '').upper().strip()
        
        if state in ['CO', 'COLORADO']:
            return 'CO', config.STATE_IDS['CO']
        elif state in ['TX', 'TEXAS']:
            return 'TX', config.STATE_IDS['TX']
        else:
            # Default to CO if state is unclear
            logger.warning(f"Unknown state '{state}' for patient {patient.get('name')}, defaulting to CO")
            return 'CO', config.STATE_IDS['CO']
    
    def insurance_discovery(self, patient: Dict) -> Optional[Dict]:
        """Perform insurance discovery for a patient."""
        if not self.get_access_token():
            return None
        
        # Parse patient name
        name_parts = patient.get('name', '').split(',')
        if len(name_parts) < 2:
            logger.warning(f"Cannot parse patient name: {patient.get('name')}")
            return None
        
        last_name = name_parts[0].strip()
        first_name = name_parts[1].strip()
        
        location, state_id = self.get_location_and_state_id(patient)
        
        # Calculate service dates (today + 30 days)
        start_date = datetime.now().strftime("%m/%d/%Y")
        end_date = (datetime.now() + timedelta(days=30)).strftime("%m/%d/%Y")
        
        payload = {
            "patientFirstName": first_name,
            "patientLastName": last_name,
            "patientDOB": patient.get('dob'),  # DOB is required and validated
            "patientGender": patient.get('gender'),  # Gender is required and validated
            "patientStateId": state_id,
            "patientState": "",
            "patientSSN": patient.get('ssn', '').strip() if patient.get('ssn') else "",
            "notes": "",
            "doS_StartDate": start_date,
            "doS_EndDate": end_date,
            "location": location
        }
        
        try:
            logger.debug(f"PVerify Discovery Request - URL: {self.discovery_url}")
            logger.debug(f"PVerify Discovery Request - Headers: {{'Authorization': 'Bearer [REDACTED]', 'Client-API-Id': '{self.client_id}', 'Content-Type': 'application/json'}}")
            logger.debug(f"PVerify Discovery Request - Patient: {patient.get('name')} - Payload: {json.dumps(payload, indent=2)}")
            
            response = requests.post(
                self.discovery_url,
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Client-API-Id': self.client_id,
                    'Content-Type': 'application/json'
                },
                json=payload
            )
            response.raise_for_status()
            
            logger.debug(f"PVerify Discovery Response - Status: {response.status_code}")
            logger.debug(f"PVerify Discovery Response - Headers: {dict(response.headers)}")
            
            discovery_data = response.json()
            logger.debug(f"PVerify Discovery Response - Patient: {patient.get('name')} - Body: {json.dumps(discovery_data, indent=2)}")
            logger.debug(f"Insurance discovery for {patient.get('name')}: {discovery_data.get('PayerName', 'No payer found')}")
            return discovery_data
            
        except Exception as e:
            logger.error(f"Insurance discovery failed for {patient.get('name')}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"PVerify Discovery Error Response - Status: {e.response.status_code}")
                logger.error(f"PVerify Discovery Error Response - Body: {e.response.text}")
            return None
    
    def eligibility_check(self, patient: Dict, insurance: Dict, service_line: str = "NA") -> Dict:
        """Perform eligibility check for a patient and insurance."""
        if not self.get_access_token():
            return {}
        
        # Parse patient name
        name_parts = patient.get('name', '').split(',')
        if len(name_parts) < 2:
            logger.warning(f"Cannot parse patient name: {patient.get('name')}")
            return {}
        
        last_name = name_parts[0].strip()
        first_name = name_parts[1].strip()
        
        location, state_id = self.get_location_and_state_id(patient)
        
        # Calculate service dates (today + 30 days)
        start_date = datetime.now().strftime("%m/%d/%Y")
        end_date = (datetime.now() + timedelta(days=30)).strftime("%m/%d/%Y")
        
        # Determine service codes based on service line
        if service_line.upper() == "KAP":
            service_codes = ["98"]
        else:
            service_codes = ["30"]
        
        # Get member ID - prioritize subidnumber, fallback to subscriberid, then discovery
        subid_number = insurance.get('subidnumber')
        subscriber_id = insurance.get('subscriberid')
        member_id = ''
        payer_code = None
        
        # First priority: use subidnumber if available
        if subid_number and subid_number.strip():
            member_id = subid_number.strip()
            logger.debug(f"Using subIdNumber as member ID: {member_id}")
        # Second priority: use subscriberid if available
        elif subscriber_id and subscriber_id.strip():
            member_id = subscriber_id.strip()
            logger.debug(f"Using subscriberID as member ID: {member_id}")
        
        # Only run discovery if no member ID found from AMD data
        if not member_id:
            logger.debug(f"No member ID in AMD data, attempting discovery for {patient.get('name')} - {insurance.get('carname')}")
            # Try insurance discovery to find member ID
            discovery_result = self.insurance_discovery(patient)
            if discovery_result and discovery_result.get('PayerFound'):
                # Check if discovered insurance matches AMD insurance
                if self.match_insurance_name(insurance.get('carname', ''), discovery_result.get('PayerName', '')):
                    member_id = discovery_result.get('MemberID', '')
                    logger.debug(f"Found member ID via discovery: {member_id}")
                    # Extract payer code if available
                    if 'ComboPayerResponses' in discovery_result:
                        for payer_resp in discovery_result['ComboPayerResponses']:
                            if payer_resp.get('PayerCode'):
                                payer_code = payer_resp['PayerCode']
                                break
        
        if not member_id:
            logger.warning(f"No member ID found for {patient.get('name')} - {insurance.get('carname')}")
            return {}
        
        # Default payer code if not found
        if not payer_code:
            payer_code = "00192"  # Generic code
        
        payload = {
            "payerCode": payer_code,
            "provider": {
                "lastName": self.provider_last_name,
                "npi": self.provider_npi
            },
            "subscriber": {
                "firstName": first_name,
                "lastName": last_name,
                "dob": patient.get('dob'),  # DOB is required and validated
                "memberID": member_id
            },
            "isSubscriberPatient": "true",
            "doS_StartDate": start_date,
            "doS_EndDate": end_date,
            "serviceCodes": service_codes,
            "isHMOplan": False,
            "IncludeTextResponse": True,
            "Location": location,
            "InternalId": "",
            "CustomerID": ""
        }
        
        try:
            logger.debug(f"PVerify Eligibility Request - URL: {self.eligibility_url}")
            logger.debug(f"PVerify Eligibility Request - Headers: {{'Authorization': 'Bearer [REDACTED]', 'Client-API-Id': '{self.client_id}', 'Content-Type': 'application/json'}}")
            logger.debug(f"PVerify Eligibility Request - Patient: {patient.get('name')} - Insurance: {insurance.get('carname')} - Payload: {json.dumps(payload, indent=2)}")
            
            response = requests.post(
                self.eligibility_url,
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Client-API-Id': self.client_id,
                    'Content-Type': 'application/json'
                },
                json=payload
            )
            response.raise_for_status()
            
            logger.debug(f"PVerify Eligibility Response - Status: {response.status_code}")
            logger.debug(f"PVerify Eligibility Response - Headers: {dict(response.headers)}")
            
            eligibility_data = response.json()
            logger.debug(f"PVerify Eligibility Response - Patient: {patient.get('name')} - Insurance: {insurance.get('carname')} - Body: {json.dumps(eligibility_data, indent=2)}")
            logger.info(f"Eligibility check completed for {patient.get('name')} - Status: {eligibility_data.get('status', 'Unknown')}")
            return eligibility_data
            
        except Exception as e:
            logger.error(f"Eligibility check failed for {patient.get('name')}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"PVerify Eligibility Error Response - Status: {e.response.status_code}")
                logger.error(f"PVerify Eligibility Error Response - Body: {e.response.text}")
            return {}
    
    def extract_financial_data(self, eligibility_data: Dict) -> Dict:
        """Extract copay, coinsurance, and deductible from PVerify response."""
        financial_data = {
            'copay': 0.0,
            'coinsurance': 0.0,
            'deductible': 0.0,
            'deductible_remaining': 0.0,
            'annual_deductible': 0.0,
            'deductible_met': 0.0
        }
        
        # Check if eligibility status is inactive or null - return default values
        status = eligibility_data.get('status')
        if status is None:
            # Log additional error information if available
            error_code = eligibility_data.get('errorCode')
            error_description = eligibility_data.get('errorDescription')
            if error_code or error_description:
                logger.warning(f"PVerify eligibility error - Code: {error_code}, Description: {error_description}")
            logger.debug("Eligibility status is null (likely error response), returning default financial data")
            return financial_data
        elif status.lower() == 'inactive':
            logger.debug("Eligibility status is inactive, returning default financial data")
            return financial_data
        
        try:
            # Check networkSections for summary data
            network_sections = eligibility_data.get('networkSections', [])
            if network_sections:  # Add null check
                for section in network_sections:
                    if section and section.get('identifier') == 'Specialist':
                        in_network = section.get('inNetworkParameters', [])
                        if in_network:  # Add null check
                            for param in in_network:
                                if param:  # Add null check for individual param
                                    key = param.get('key', '').lower()
                                    value = param.get('value') or ''
                                    value = value.strip() if value else ''
                                    
                                    if 'co-pay' in key and value:
                                        try:
                                            financial_data['copay'] = float(value.replace('$', '').replace(',', ''))
                                        except ValueError:
                                            pass
                                    elif 'co-ins' in key and value:
                                        try:
                                            financial_data['coinsurance'] = float(value.replace('%', ''))
                                        except ValueError:
                                            pass
            
            # Check detailed service types for more comprehensive data
            service_types = eligibility_data.get('servicesTypes', [])
            if service_types:  # Add null check
                for service_type in service_types:
                    if service_type:  # Add null check
                        service_name = service_type.get('serviceTypeName', '')
                        
                        # Focus on relevant service types
                        if any(keyword in service_name.lower() for keyword in ['professional', 'physician', 'office']):
                            sections = service_type.get('serviceTypeSections', [])
                            
                            if sections:  # Add null check
                                for section in sections:
                                    if section:  # Add null check
                                        label = section.get('label', '')
                                        if 'in plan-network' in label.lower() or 'applies to' in label.lower():
                                            params = section.get('serviceParameters', [])
                                            
                                            if params:  # Add null check
                                                for param in params:
                                                    if param:  # Add null check for individual param
                                                        key = param.get('key', '').lower()
                                                        value = param.get('value') or ''
                                                        value = value.strip() if value else ''
                                                        
                                                        if 'co-payment' in key and value and '$' in value:
                                                            try:
                                                                copay_val = float(value.replace('$', '').replace(',', ''))
                                                                if copay_val > financial_data['copay']:
                                                                    financial_data['copay'] = copay_val
                                                            except ValueError:
                                                                pass
                                                        elif 'co-insurance' in key and value and '%' in value:
                                                            try:
                                                                coins_val = float(value.replace('%', ''))
                                                                if coins_val > financial_data['coinsurance']:
                                                                    financial_data['coinsurance'] = coins_val
                                                            except ValueError:
                                                                pass
                                                        elif 'deductible' in key and value and '$' in value:
                                                            try:
                                                                deduct_val = float(value.replace('$', '').replace(',', ''))
                                                                if 'remaining' in key or 'left' in key or 'balance' in key:
                                                                    if deduct_val > financial_data['deductible_remaining']:
                                                                        financial_data['deductible_remaining'] = deduct_val
                                                                elif 'met' in key or 'satisfied' in key:
                                                                    if deduct_val > financial_data['deductible_met']:
                                                                        financial_data['deductible_met'] = deduct_val
                                                                elif 'annual' in key or 'yearly' in key:
                                                                    if deduct_val > financial_data['annual_deductible']:
                                                                        financial_data['annual_deductible'] = deduct_val
                                                                else:
                                                                    # Generic deductible - could be annual or remaining
                                                                    if deduct_val > financial_data['deductible']:
                                                                        financial_data['deductible'] = deduct_val
                                                            except ValueError:
                                                                pass
            
            # Calculate deductible_remaining if we have annual and met amounts
            if financial_data['annual_deductible'] > 0 and financial_data['deductible_met'] >= 0:
                calculated_remaining = financial_data['annual_deductible'] - financial_data['deductible_met']
                if financial_data['deductible_remaining'] == 0 or calculated_remaining > 0:
                    financial_data['deductible_remaining'] = max(0, calculated_remaining)
            
            # If we only have a generic deductible value, assume it's remaining
            elif financial_data['deductible'] > 0 and financial_data['deductible_remaining'] == 0:
                financial_data['deductible_remaining'] = financial_data['deductible']
            
            logger.debug(f"Extracted financial data: {financial_data}")
            return financial_data
            
        except Exception as e:
            logger.error(f"Error extracting financial data: {e}")
            return financial_data


class ZapierWebhook:
    """Zapier webhook integration."""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send_patient_data(self, patient_name: str) -> Optional[str]:
        """Send patient data to Zapier webhook and get service line response."""
        payload = {"patient_name": patient_name}
        
        try:
            # Send the request to the webhook URL
            logger.info(f"Sending webhook request for {patient_name} to {self.webhook_url}")
            logger.debug(f"Payload: {payload}")
            
            response = requests.post(self.webhook_url, json=payload, timeout=30)
            response.raise_for_status()
            logger.info(f"Webhook response status: {response.status_code}")
            
            # Parse the actual response from Zapier
            result = response.json()
            logger.info(f"Zapier response for {patient_name}: {result}")
            
            service_line = result.get("Service Type")
            
            if not service_line or service_line.strip() == "":
                logger.warning(f"No service type returned for {patient_name} - skipping patient")
                return None
            
            logger.info(f"Received service line for {patient_name}: {service_line}")
            return service_line.strip()
            
        except Exception as e:
            logger.warning(f"Webhook request failed for {patient_name}: {e}")
            logger.info(f"Skipping patient due to webhook failure")
            # If webhook fails, return None to skip patient
            return None


def utc_now():
    # Return timezone-aware UTC for Postgres timestamptz
    return datetime.now(timezone.utc)

@contextlib.contextmanager
def _pg_conn_via_ssh():
    """
    Yields a psycopg2 connection to RDS through an SSH tunnel (or direct if USE_SSH=0).
    SSL is enforced (sslmode=require).
    """
    if not config.SSH_CONFIG['use_ssh']:
        conn = psycopg2.connect(
            host=config.DB_CONFIG['host'],
            port=config.DB_CONFIG['port'],
            dbname=config.DB_CONFIG['database'],
            user=config.DB_CONFIG['username'],
            password=config.DB_CONFIG['password'],
            sslmode=config.DB_CONFIG['sslmode'],
        )
        try:
            yield conn
        finally:
            conn.close()
        return

    # Check if SSH key file exists and is readable
    ssh_key_path = config.SSH_CONFIG['private_key_path']
    if not os.path.isfile(ssh_key_path):
        raise FileNotFoundError(f"SSH private key file not found: {ssh_key_path}")
    
    # Log SSH connection details (without sensitive info)
    logger.info(f"Establishing SSH tunnel to {config.SSH_CONFIG['bastion_host']}:{config.SSH_CONFIG['bastion_port']} as {config.SSH_CONFIG['bastion_user']}")
    
    server = SSHTunnelForwarder(
        (config.SSH_CONFIG['bastion_host'], config.SSH_CONFIG['bastion_port']),
        ssh_username=config.SSH_CONFIG['bastion_user'],
        ssh_pkey=ssh_key_path,
        remote_bind_address=(config.DB_CONFIG['host'], config.DB_CONFIG['port']),
        set_keepalive=60.0,
    )
    server.start()
    try:
        conn = psycopg2.connect(
            host="127.0.0.1",
            port=server.local_bind_port,
            dbname=config.DB_CONFIG['database'],
            user=config.DB_CONFIG['username'],
            password=config.DB_CONFIG['password'],
            sslmode=config.DB_CONFIG['sslmode'],
        )
        try:
            yield conn
        finally:
            conn.close()
    finally:
        server.stop()


def _is_financial_data_empty(fin: Dict) -> bool:
    return all((fin.get(k, 0) or 0) == 0 for k in [
        'copay','coinsurance','deductible','deductible_remaining',
        'annual_deductible','deductible_met'
    ])


def log_agent_run_success(patient_memo: str, started_at_utc: datetime, ended_at_utc: datetime, documents_processed: int = 1):
    """
    Inserts a success row into agent_run_logs with explicit casts to match DB types.
    """
    if not patient_memo:
        patient_memo = ""

    output_payload = psycopg2.extras.Json({"message": patient_memo})

    sql = """
        INSERT INTO agent_run_logs 
          (agent_id, service_request_id, documents_processed, status,
           output_data, start_time, end_time, call_id, vapi_listen_url,
           vapi_control_url, manual_trigger)
        VALUES
          (%s::uuid, NULL::int, %s::int, %s, %s, %s::timestamptz, %s::timestamptz, NULL, NULL, NULL, %s)
    """
    args = (
        str(uuid.UUID(config.AGENT_ID)) if config.AGENT_ID else str(uuid.uuid4()),  # ensure UUID type
        int(documents_processed),                 # int
        "success",                                # text
        output_payload,
        started_at_utc,                           # timestamptz
        ended_at_utc,                             # timestamptz
        False,                                    # bool
    )

    try:
        with _pg_conn_via_ssh() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, args)
            conn.commit()
        logger.info("agent_run_logs: success row inserted")
    except Exception as e:
        logger.error(f"Failed to write agent_run_logs: {e}", exc_info=True)


def log_agent_run_skipped(reason: str, started_at_utc: datetime, ended_at_utc: datetime, documents_processed: int = 0):
    """
    Inserts a 'skipped' row into agent_run_logs (e.g., filtered by posting rules).
    """
    output_payload = psycopg2.extras.Json({"message": reason})

    sql = """
        INSERT INTO agent_run_logs 
          (agent_id, service_request_id, documents_processed, status,
           output_data, start_time, end_time, call_id, vapi_listen_url,
           vapi_control_url, manual_trigger)
        VALUES
          (%s::uuid, NULL::int, %s::int, %s, %s, %s::timestamptz, %s::timestamptz, NULL, NULL, NULL, %s)
    """
    args = (
        str(uuid.UUID(config.AGENT_ID)) if config.AGENT_ID else str(uuid.uuid4()),
        int(documents_processed),
        "skipped",                               # <-- status
        output_payload,
        started_at_utc,
        ended_at_utc,
        False,
    )

    try:
        with _pg_conn_via_ssh() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, args)
            conn.commit()
        logger.info("agent_run_logs: skipped row inserted")
    except Exception as e:
        logger.error(f"Failed to write agent_run_logs skipped: {e}", exc_info=True)



def log_agent_run_error(error_message: str, started_at_utc: datetime, ended_at_utc: datetime):
    """
    Inserts an error row into agent_run_logs.
    """
    output_payload = psycopg2.extras.Json({"message": error_message})

    sql = """
        INSERT INTO agent_run_logs 
          (agent_id, service_request_id, documents_processed, status,
           output_data, start_time, end_time, call_id, vapi_listen_url,
           vapi_control_url, manual_trigger)
        VALUES
          (%s::uuid, NULL::int, %s::int, %s, %s, %s::timestamptz, %s::timestamptz, NULL, NULL, NULL, %s)
    """
    args = (
        str(uuid.UUID(config.AGENT_ID)) if config.AGENT_ID else str(uuid.uuid4()),  # ensure UUID type
        0,                                        # int - no documents processed on error
        "error",                                  # text
        output_payload,
        started_at_utc,                           # timestamptz
        ended_at_utc,                             # timestamptz
        False,                                    # bool
    )

    try:
        with _pg_conn_via_ssh() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, args)
            conn.commit()
        logger.info("agent_run_logs: error row inserted")
    except Exception as e:
        logger.error(f"Failed to write agent_run_logs error: {e}", exc_info=True)

class PatientResponsibilityAgent:
    """Main agent class that orchestrates the entire workflow."""
    
    def __init__(self, zapier_webhook_url: str):
        self.amd_api = AdvancedMDAPI()
        self.pverify_api = PVerifyAPI()
        self.zapier = ZapierWebhook(zapier_webhook_url)
        self.final_patients = []
        self.run_started = None
        self.documents_processed = 0
        
        # Initialize data structures for enhanced calculations
        self._init_service_line_mappings()
        self._init_payer_mappings()
        self._init_allowed_amounts()
    
    def _init_service_line_mappings(self):
        """Initialize service line to CPT code mappings."""
        self.service_line_cpt_mapping = {
            'KAP': ['90791', '90837', '96130', '96130-59'],
            'Spravato': ['G2082', 'G2083', 'S0013-84'],
            'Med Management (Psych E/M)': ['99204', '99204-25', '99205-25', '99205-95', '99214', '99215', '99215-25', '99417', 'G2212'],
            'IM ketamine': ['96372', '96372-59', 'J3490', 'J3490-GA']
        }
    
    def _init_payer_mappings(self):
        """Initialize insurance name to payer code mappings."""
        self.payer_mappings = {
            # Health First Medicaid
            'HFM': ['HEALTH FIRST MEDICAID', 'HFM', 'MEDICAID'],
            # Texas Blue Shield  
            'TBS': ['TEXAS BLUE SHIELD', 'TBS', 'BLUE CROSS BLUE SHIELD OF TEXAS', 'BCBS TEXAS'],
            # Anthem BCBS
            'ANT': ['ANTHEM', 'ANTHEM BLUE CROSS', 'ANTHEM BCBS', 'ANTHEM BLUE CROSS BLUE SHIELD'],
            # Colorado Access
            'COA': ['COLORADO ACCESS', 'COA'],
            # United Healthcare
            'UHC': ['UNITED HEALTHCARE', 'UHC', 'UNITED', 'UNITEDHEALTHCARE'],
            # Medicare
            'MCR': ['MEDICARE', 'MCR', 'MEDICARE ADVANTAGE']
        }
    
    def _init_allowed_amounts(self):
        """Initialize allowed amounts lookup table."""
        self.allowed_amounts = {
            'HFM': {
                '99215': 150.45, 'G2083': 1258.52, '99417-4': 176.42, '99417-2': 87.16,
                '90837': 137.67, '99417-3': 131.17, '96372-59': 29.72, '99215-25': 158.99,
                '99214': 108.00, 'J3490': 3.48, 'G2212': 94.08, '99417-5': 221.25,
                '99417': 63.98, 'J3490-GA': 4.22, '96130': 101.74, '99205-25': 186.21,
                'G2082': 857.80, '99204-25': 137.32, '90791': 160.21, '99417-6': 265.33,
                '96372': 16.65, '99205-95': 188.38, '99204': 134.08, '96130-59': 34.59
            },
            'TBS': {
                '99215': 132.07, 'G2083': 1005.74, '99417-4': 113.02, '99417-2': 59.35,
                '90837': 105.12, '99417-3': 87.10, '96372-59': 27.94, '99215-25': 123.91,
                '99214': 116.90, 'J3490': 9.95, 'G2212': 69.07, '99417-5': 138.14,
                '99417': 54.88, 'J3490-GA': 5.67, '96130': 134.18, '99205-25': 220.84,
                '99204-25': 180.38, '90791': 132.92, '99417-6': 206.10, '96372': 24.70,
                'S0013-84': 1417.14, '99204': 124.34, '96130-59': 106.68
            },
            'ANT': {
                '99215': 180.23, 'G2083': 1243.50, '99417-4': 130.95, '99417-2': 68.18,
                '90837': 90.66, '99417-3': 98.83, '96372-59': 29.55, '99215-25': 177.63,
                '99214': 133.80, 'J3490': 8.22, 'G2212': 426.67, '99417-5': 191.29,
                '99417': 62.64, 'J3490-GA': 7.27, '96130': 129.49, '99205-25': 250.94,
                'G2082': 892.97, '99204-25': 164.06, '90791': 95.01, '99417-6': 246.64,
                '96372': 21.13, '99205-95': 260.29, '99204': 177.44, '96130-59': 128.82
            },
            'COA': {
                '99215': 149.29, 'G2083': 1246.12, '99417-4': 174.28, '99417-2': 87.14,
                '90837': 131.30, '99417-3': 130.71, '96372-59': 22.34, '99214': 108.68,
                '99417': 55.71, '96130': 134.81, '99205-25': 186.02, '99204-25': 136.94,
                '90791': 161.37, '96372': 14.78, '99205-95': 188.01, '99204': 136.94,
                '96130-59': 150.00
            },
            'UHC': {
                '99215': 197.63, '99417-4': 252.10, '99417-2': 59.52, '90837': 138.46,
                '99417-3': 62.25, '96372-59': 34.60, '99215-25': 188.13, '99214': 104.41,
                'J3490': 5.77, 'G2212': 173.99, '99417-5': 400.00, '99417': 53.13,
                'J3490-GA': 10.26, '96130': 135.75, '99205-25': 200.80, '99204-25': 142.27,
                '90791': 161.20, '99417-6': 360.00, '96372': 19.53, '99204': 125.32,
                '96130-59': 109.55
            },
            'MCR': {
                '99215': 179.34, '90837': 156.60, '99214': 127.92, 'J3490': 0.20,
                'G2212': 31.52, '96130': 118.96, '90791': 169.43, '96372': 14.33
            }
        }

    def get_payer_code(self, insurance_name: str) -> Optional[str]:
        """Map insurance name to payer code for allowed amounts lookup."""
        insurance_upper = insurance_name.upper().strip()
        
        # Check each payer mapping
        for payer_code, name_variants in self.payer_mappings.items():
            for variant in name_variants:
                if variant in insurance_upper:
                    return payer_code
        
        # Check for common patterns not in explicit mappings
        if any(term in insurance_upper for term in ['BLUE CROSS', 'BCBS', 'BLUE SHIELD']):
            if 'TEXAS' in insurance_upper:
                return 'TBS'
            elif 'ANTHEM' in insurance_upper:
                return 'ANT'
            else:
                return 'TBS'  # Default to TBS for other BCBS variants
        
        # Return None if no match found - will use average calculation
        return None
    
    def get_average_allowed_amount(self, cpt_code: str) -> float:
        """Calculate average allowed amount across all payers for a CPT code."""
        amounts = []
        for payer_code, payer_amounts in self.allowed_amounts.items():
            if cpt_code in payer_amounts:
                amounts.append(payer_amounts[cpt_code])
        
        return sum(amounts) / len(amounts) if amounts else 0.0

    def is_medicaid_insurance(self, insurance: Dict) -> bool:
        """Check if insurance is Medicaid based on carcode or carname."""
        carcode = insurance.get('carcode', '').upper()
        carname = insurance.get('carname', '').upper()
        
        return any(indicator in carcode or indicator in carname for indicator in config.MEDICAID_INDICATORS)
    
    def is_medicare_advantage(self, carname: str) -> bool:
        """Determine if insurance is Medicare Advantage based on comprehensive rules."""
        name_lower = carname.lower()
        
        # Strong positive indicators
        positive_strong = [
            "medicare advantage", "part c", "ma-pd", "mapd",
            "medicare advantage prescription drug",
            "dual special needs plan", "dual complete", "d-snp", "dsnp",
            "chronic condition special needs", "c-snp", "csnp",
            "institutional special needs", "i-snp", "isnp"
        ]
        
        # Check for strong indicators
        if any(indicator in name_lower for indicator in positive_strong):
            return True
        
        # Check for contract ID pattern (H####-###)
        import re
        if re.search(r'\bh\d{4}-\d{3}\b', name_lower):
            return True
        
        # Positive when paired with "medicare" nearby
        positive_with_medicare = [
            "hmo", "ppo", "pffs", "msa",
            "complete", "choice", "gold plus", "prime", "select", "plus", "complete care",
            "senior advantage"
        ]
        
        if "medicare" in name_lower:
            if any(indicator in name_lower for indicator in positive_with_medicare):
                return True
        
        # Texas-specific MA brands
        tx_brands = [
            "aetna medicare", "humana", "humana gold plus", "humanachoice",
            "aarp medicare advantage", "unitedhealthcare medicare", "uhc medicare",
            "blue cross medicare advantage", "bcbstx medicare advantage",
            "kelseycare advantage", "cigna true choice", "cigna preferred",
            "wellcare medicare", "allwell",
            "scott and white medicare", "baylor scott & white medicare",
            "superior healthplan medicare"
        ]
        
        # Colorado-specific MA brands
        co_brands = [
            "anthem mediblue", "anthem medicare advantage",
            "kaiser permanente senior advantage",
            "aetna medicare", "humana", "aarp medicare advantage", "unitedhealthcare medicare",
            "wellcare medicare",
            "denver health elevate medicare", "elevate medicare advantage",
            "rocky mountain health plans medicare", "rmhp medicare"
        ]
        
        # Check brand-specific indicators
        all_brands = tx_brands + co_brands
        if any(brand in name_lower for brand in all_brands):
            return True
        
        # Common negatives (NOT Medicare Advantage)
        negatives = [
            "medicare supplement", "medigap", "plan g", "plan n", "plan f",
            "pdp", "prescription drug plan", "part d only", "rx only",
            "blue advantage hmo",  # TX marketplace plan (commercial), not MA
            "original medicare", "fee-for-service (original)", "msp", "qmb", "slmb", "qi",
            "medicaid only"
        ]
        
        # Exclude if negatives are present
        if any(negative in name_lower for negative in negatives):
            return False
        
        return False
    
    def get_medicaid_rae(self, insurance: Dict) -> str:
        """Get Medicaid RAE information for behavioral services."""
        carname = insurance.get('carname', '').upper()
        
        if 'COLORADO COMMUNITY HEALTH ALLIANCE' in carname or 'CCHA' in carname:
            return 'CCHA (Colorado Community Health Alliance)'
        elif 'COLORADO ACCESS' in carname:
            return 'Colorado Access'
        else:
            return 'Unknown RAE'
    
    def get_payer_type(self, insurance: Dict) -> str:
        """Determine payer type based on insurance information."""
        carcode = insurance.get('carcode', '').upper()
        carname = insurance.get('carname', '').upper()
        
        # Check if Medicaid
        if any(indicator in carcode or indicator in carname for indicator in config.MEDICAID_INDICATORS):
            return 'Medicaid'
        
        # Check if Self-Pay (typically no insurance or specific codes)
        if 'SELF' in carname or 'CASH' in carname:
            return 'Self-Pay'
        
        # Check if Medicare Advantage using comprehensive rules
        if self.is_medicare_advantage(carname):
            return 'Medicare Advantage'
        
        # Default to Commercial
        return 'Commercial'
    
    def calculate_service_line_responsibility_enhanced(self, insurance: Dict, pverify_data: Dict, service_line: str) -> float:
        """Calculate patient responsibility using deductible and coinsurance with allowed amounts."""
        payer_type = self.get_payer_type(insurance)

        amd_copay = float(insurance.get('copaydollaramount') or 0)
        amd_coins_pct = float(insurance.get('copaypercentageamount') or 0)
        amd_annual = float(insurance.get('annualdeductible') or 0)
        amd_met = float(insurance.get('deductibleamountmet') or 0)

        
        # Medicaid overrides - return 0 for specific service lines
        if payer_type == 'Medicaid':
            if service_line in ['IM ketamine', 'KAP']:
                return 0.0
            # For Spravato and Med Management, continue with calculation
        
        # Self-Pay overrides - return fixed amounts
        if payer_type == 'Self-Pay':
            if service_line == 'IM ketamine':
                return 399.0
            elif service_line == 'Spravato':
                return 949.0
            # For KAP and Med Management, continue with calculation (no explicit amounts)
        
        # Get financial data
        pverify_data = pverify_data or {}
        pverify_financial = pverify_data.get('financial_data', {}) or {}
        pverify_status = (pverify_data.get('eligibility_data') or {}).get('status')

        # --- NEW: detect "Per Elig / zeros" fallback condition ---
        fallback_needed = (
            (pverify_status is None) or
            (isinstance(pverify_status, str) and pverify_status.lower() == 'inactive') or
            _is_financial_data_empty(pverify_financial)
        )
        
        # Get copay, coinsurance, and deductible data (PVerify priority, AMD fallback)
        copay_amount = pverify_financial.get('copay', 0) or insurance.get('copaydollaramount', 0)
        coinsurance_pct = pverify_financial.get('coinsurance', 0) or insurance.get('copaypercentageamount', 0)
        deductible_remaining = pverify_financial.get('deductible_remaining', 0)
        annual_deductible = pverify_financial.get('annual_deductible', 0) or insurance.get('annualdeductible', 0)
        deductible_met = pverify_financial.get('deductible_met', 0) or insurance.get('deductibleamountmet', 0)
        
        # Calculate remaining deductible if not available from PVerify
        if deductible_remaining == 0 and annual_deductible > 0:
            deductible_remaining = max(0, annual_deductible - deductible_met)
        
        # --- NEW: apply fallback if everything came back "Per Elig"/Inactive/zeros ---
        if fallback_needed:
            # keep AMD copay if present; otherwise estimate using coinsurance + AMD deductible
            if copay_amount <= 0:
                # use AMD coins%, else a safe default (20%) from config if present
                default_coins = getattr(config, 'DEFAULT_COINSURANCE_PCT', 20.0)
                coinsurance_pct = coinsurance_pct or default_coins
            # recompute deductible remaining purely from AMD (if available)
            deductible_remaining = max(0.0, amd_annual - amd_met) if (amd_annual or amd_met) else 0.0



    
        # If we have a copay, use it (traditional copay plan)
        if copay_amount > 0:
            return copay_amount
        
        # Get CPT codes for this service line
        cpt_codes = self.service_line_cpt_mapping.get(service_line, [])
        if not cpt_codes:
            return 0.0
        
        # Get payer code for allowed amounts lookup
        insurance_name = insurance.get('carname', '')
        payer_code = self.get_payer_code(insurance_name)
        
        # Calculate total allowed amount for all CPT codes in this service line
        total_allowed = 0.0
        for cpt_code in cpt_codes:
            if payer_code and cpt_code in self.allowed_amounts.get(payer_code, {}):
                allowed_amount = self.allowed_amounts[payer_code][cpt_code]
            else:
                # Use average if payer not found or CPT not available for payer
                allowed_amount = self.get_average_allowed_amount(cpt_code)
            total_allowed += allowed_amount
        
        if total_allowed == 0:
            return 0.0
        
        # Calculate patient responsibility
        patient_responsibility = 0.0
        
        # Apply deductible first
        if deductible_remaining > 0:
            deductible_portion = min(total_allowed, deductible_remaining)
            patient_responsibility += deductible_portion
            remaining_after_deductible = total_allowed - deductible_portion
        else:
            remaining_after_deductible = total_allowed
        
        # Apply coinsurance to remaining amount
        if coinsurance_pct > 0 and remaining_after_deductible > 0:
            coinsurance_portion = remaining_after_deductible * (coinsurance_pct / 100.0)
            patient_responsibility += coinsurance_portion
        
        return round(patient_responsibility, 2)
    
    def calculate_service_line_responsibility(self, insurance: Dict, pverify_data: Dict, service_line: str) -> str:
        """Calculate patient responsibility for a specific service line."""
        # Use enhanced calculation to get dollar amount
        enhanced_amount = self.calculate_service_line_responsibility_enhanced(insurance, pverify_data, service_line)
        
        payer_type = self.get_payer_type(insurance)
        
        # Handle special Medicaid cases that return text instead of dollar amounts
        if payer_type == 'Medicaid':
            if service_line == 'Med Management (Psych E/M)':
                if enhanced_amount == 0.0:
                    return 'Typically $0 if eligible (Medicaid balances should be $0). Verify under the medical service type (drill to 01 = Medical Care) when checking E/M.'
                else:
                    return f'${enhanced_amount:.2f} patient responsibility'
            elif service_line == 'Spravato':
                if enhanced_amount == 0.0:
                    return 'Copay/coinsurance/deductible per eligibility'
                else:
                    return f'${enhanced_amount:.2f} patient responsibility'
        
        # Handle Self-Pay special text cases
        elif payer_type == 'Self-Pay':
            if service_line == 'IM ketamine':
                return '$399 at first visit ("Self-Pay Item: Ketamine Induction")'
            elif service_line == 'KAP':
                if enhanced_amount == 0.0:
                    return 'No explicit amount documented in KB'
                else:
                    return f'${enhanced_amount:.2f} patient responsibility'
            elif service_line == 'Spravato':
                return '$949 self-pay Spravato induction'
            elif service_line == 'Med Management (Psych E/M)':
                if enhanced_amount == 0.0:
                    return 'No self-pay policy'
                else:
                    return f'${enhanced_amount:.2f} patient responsibility'
        
        # For all other cases, return calculated dollar amount or fallback text
        if enhanced_amount > 0:
            return f'${enhanced_amount:.2f} patient responsibility'
        else:
            return 'Copay/coinsurance/deductible per eligibility'
    
    def should_post_memo(self, insurance: Dict, pverify_data: Dict) -> bool:
        """
        Determine if memo should be posted based on filtering rules:
        - Do NOT post if memo has no dollar amounts and only "Per Elig" everywhere
        - Do NOT post if memo has a mix of "Per Elig" and $0 amounts
        - DO post if memo has AT LEAST one non-zero dollar amount, OR no "Per Elig" at all
        """
        service_lines = ['IM ketamine', 'KAP', 'Spravato', 'Med Management (Psych E/M)']
        
        has_per_elig = False
        has_non_zero_dollar = False
        has_zero_dollar = False
        has_other_values = False

        name_upper = (insurance.get('carname') or '').upper()

        # 🚫 Skip Medicaid & RAEs — no PR to post
        if any(tag in name_upper for tag in [
            'MEDICAID',
            'HEALTH FIRST MEDICAID',
            'CO ACCESS',
            'COLORADO ACCESS',
            'CCHA',
            'COLORADO COMMUNITY HEALTH ALLIANCE'
        ]):
            logger.debug(f"Skipping memo: Medicaid/RAE plan [{name_upper}] — no PR to post")
            return False
        
        for service_line in service_lines:
            # Get the formatted responsibility text (what appears in memo)
            responsibility = self.calculate_service_line_responsibility(insurance, pverify_data, service_line)
            resp_abbrev = self.get_responsibility_abbreviation(responsibility)
            
            # Check what type of value this is
            if resp_abbrev == 'Per Elig':
                has_per_elig = True
            elif resp_abbrev == '$0' or resp_abbrev == '$0.00':
                has_zero_dollar = True
            elif resp_abbrev.startswith('$') and resp_abbrev not in ['$0', '$0.00']:
                # Extract dollar amount to check if non-zero
                import re
                dollar_match = re.search(r'\$(\d+(?:\.\d{2})?)', resp_abbrev)
                if dollar_match:
                    amount = float(dollar_match.group(1))
                    if amount > 0:
                        has_non_zero_dollar = True
                    else:
                        has_zero_dollar = True
            elif resp_abbrev.endswith('%') or resp_abbrev in ['No Policy', 'TBD']:
                has_other_values = True
        
        # Apply filtering rules:
        
        # Rule 1: Do NOT post if only "Per Elig" everywhere
        if has_per_elig and not has_non_zero_dollar and not has_zero_dollar and not has_other_values:
            logger.debug(f"Filtering out memo: only 'Per Elig' values found")
            return False
        
        # Rule 2: Do NOT post if mix of "Per Elig" and $0 amounts (no positive amounts or other values)
        if has_per_elig and has_zero_dollar and not has_non_zero_dollar and not has_other_values:
            logger.debug(f"Filtering out memo: mix of 'Per Elig' and $0 amounts only")
            return False
        
        # Rule 3: DO post if AT LEAST one non-zero dollar amount
        if has_non_zero_dollar:
            logger.debug(f"Posting memo: has non-zero dollar amount")
            return True
        
        # Rule 4: DO post if no "Per Elig" at all (even if all $0 or other values)
        if not has_per_elig:
            logger.debug(f"Posting memo: no 'Per Elig' values found")
            return True
        
        # Default: do not post
        logger.debug(f"Filtering out memo: does not meet posting criteria")
        return False
    
    def get_payer_abbreviation(self, payer_name: str) -> str:
        """Get abbreviated payer name for memo."""
        payer_upper = payer_name.upper()
        
        # Common payer abbreviations
        abbreviations = {
            'UNITED HEALTHCARE': 'UHC',
            'BLUE CROSS BLUE SHIELD': 'BCBS',
            'ANTHEM': 'ANTHEM',
            'AETNA': 'AETNA',
            'CIGNA': 'CIGNA',
            'HUMANA': 'HUMANA',
            'KAISER': 'KAISER',
            'MEDICAID': 'MEDICAID',
            'MEDICARE': 'MEDICARE',
            'HEALTH FIRST MEDICAID': 'MEDICAID',
            'COLORADO COMMUNITY HEALTH ALLIANCE': 'CCHA',
            'COLORADO ACCESS': 'CO ACCESS',
            'BRAVO CIGNA': 'CIGNA',
            'CITY OF AURORA': 'AURORA',
            'AARP': 'AARP'
        }
        
        # Check for exact matches first
        for full_name, abbrev in abbreviations.items():
            if full_name in payer_upper:
                return abbrev
        
        # If no match, take first 8 characters
        return payer_name[:8].upper()
    
    def get_service_abbreviation(self, service_line: str) -> str:
        """Get abbreviated service line name."""
        abbreviations = {
            'IM ketamine': 'IM',
            'KAP': 'KAP',
            'Spravato': 'SPR',
            'Med Management (Psych E/M)': 'MM'
        }
        return abbreviations.get(service_line, service_line[:3])
    
    def get_responsibility_abbreviation(self, responsibility: str) -> str:
        """Get abbreviated responsibility text."""
        # Extract dollar amounts
        if '$' in responsibility:
            import re
            dollar_match = re.search(r'\$(\d+(?:\.\d{2})?)', responsibility)
            if dollar_match:
                return f"${dollar_match.group(1)}"
        
        # Extract percentages
        if '%' in responsibility:
            import re
            percent_match = re.search(r'(\d+)%', responsibility)
            if percent_match:
                return f"{percent_match.group(1)}%"
        
        # Common abbreviations for text
        if 'copay/coinsurance/deductible per eligibility' in responsibility.lower():
            return 'Per Elig'
        elif 'typically $0 if eligible' in responsibility.lower():
            return '$0'
        elif 'no self-pay policy' in responsibility.lower():
            return 'No Policy'
        elif 'no explicit amount' in responsibility.lower():
            return 'TBD'
        
        # Default to first 10 characters
        return responsibility[:10]
    
    def generate_comprehensive_memo(self, patient: Dict, insurance: Dict, pverify_data: Dict) -> str:
        """Generate compact memo under 50 characters."""
        payer_name = insurance.get('carname', 'Unknown')
        payer_abbrev = self.get_payer_abbreviation(payer_name)
        
        # Service lines to include
        service_lines = ['IM ketamine', 'KAP', 'Spravato', 'Med Management (Psych E/M)']
        
        # Create compact memo lines
        memo_lines = [f"{payer_abbrev} PR:"]
        
        # Add each service line responsibility in compact format
        for service_line in service_lines:
            responsibility = self.calculate_service_line_responsibility(insurance, pverify_data, service_line)
            service_abbrev = self.get_service_abbreviation(service_line)
            resp_abbrev = self.get_responsibility_abbreviation(responsibility)
            memo_lines.append(f"{service_abbrev}:{resp_abbrev}")
        
        return " ".join(memo_lines)
    
    def process_patients(self):
        """Main processing workflow."""
        self.run_started = utc_now()
        logger.info("Starting patient responsibility processing...")
        
        # Step 1: Authenticate with AdvancedMD
        if not self.amd_api.authenticate():
            logger.error("Failed to authenticate with AdvancedMD")
            return
        
        # Step 2: Get updated patients from last 24h with insurance
        logger.info("Fetching updated patients...")
        patients = self.amd_api.get_updated_patients(config.PROCESSING_CONFIG['hours_back'])
        
        if not patients:
            logger.warning("No patients found")
            return
        
        # Step 3: Filter patients WITHOUT appointments
        logger.info("Filtering patients without appointments...")
        patients_without_appointments = []
        
        for patient in patients:
            if not self.amd_api.has_appointments(patient['id']):
                patients_without_appointments.append(patient)
        
        logger.info(f"Found {len(patients_without_appointments)} patients without appointments")
        self.final_patients = patients_without_appointments
        
        # Step 4: Process each patient
        for patient in self.final_patients:
            logger.info(f"Processing patient: {patient['name']} (ID: {patient['id']})")
            
            try:
                # Step 4a: Run PVerify eligibility check for each active insurance
                pverify_results = {}
                for insurance in patient['insurances']:
                    if insurance['active']:
                        logger.info(f"Running PVerify eligibility for {patient['name']} - {insurance.get('carname')}")
                        
                        # Run PVerify eligibility check (using default service code 30)
                        eligibility_data = self.pverify_api.eligibility_check(patient, insurance, 'General')
                        
                        if eligibility_data:
                            # Extract financial data from PVerify response
                            financial_data = self.pverify_api.extract_financial_data(eligibility_data)
                            pverify_results[insurance['id']] = {
                                'eligibility_data': eligibility_data,
                                'financial_data': financial_data
                            }
                            logger.debug(f"PVerify financial data for {insurance.get('carname')}: {financial_data}")
                        else:
                            logger.warning(f"No PVerify data for {patient['name']} - {insurance.get('carname')}")
                
                # Step 4b: Generate and post comprehensive memo for each active insurance
                for insurance in patient['insurances']:
                    if insurance['active']:
                        # Get PVerify data for this insurance
                        pverify_data = pverify_results.get(insurance['id'], {})
                        
                        memo_text = ""
                        
                        # Generate comprehensive memo with all service lines
                        memo_text = self.generate_comprehensive_memo(patient, insurance, pverify_data)

                        # 🚫 De-dupe: if we already logged this exact memo for this patient, skip everything
                        if memo_already_logged(patient['name'], insurance.get('carname',''), memo_text):
                            logger.info(f"Duplicate memo detected — skipping post & DB log for {patient['name']} - {insurance.get('carname')}")
                            continue
                        
                        # Check if memo should be posted based on filtering rules
                        if not self.should_post_memo(insurance, pverify_data):
                            logger.info(f"Skipping memo for {patient['name']} - {insurance.get('carname')} (filtered out per posting rules)")
                            skip_time = utc_now()
                            log_agent_run_skipped(
                                reason=f"Skipped due to posting rules. Patient: {patient['name']} | Insurance: {insurance.get('carname')} | Memo preview: {memo_text}",
                                started_at_utc=skip_time,
                                ended_at_utc=skip_time,
                                documents_processed=0
                            )
                            continue
                        
                        
                        # Post memo to AMD
                        success = self.amd_api.post_memo(patient['id'], memo_text)
                        
                        if success:
                            logger.info(f"Successfully posted comprehensive memo for {patient['name']} - {insurance.get('carname')}")
                            logger.debug(f"Memo content:\n{memo_text}")
                            self.documents_processed += 1
                            
                            # Log success to database with patient name and memo content
                            memo_success_time = utc_now()
                            log_agent_run_success(
                                f"Patient: {patient['name']} | Memo: {memo_text}",
                                memo_success_time,
                                memo_success_time,
                                1
                            )
                        else:
                            logger.error(f"Failed to post memo for {patient['name']} - {insurance.get('carname')}")
                            
                            # Log error to database
                            memo_error_time = utc_now()
                            log_agent_run_error(
                                f"Failed to post memo for patient {patient['name']} - {insurance.get('carname')}",
                                memo_error_time,
                                memo_error_time
                            )
                
            except Exception as e:
                logger.error(f"Error processing patient {patient['name']}: {e}")
                
                # Log processing error to database
                process_error_time = utc_now()
                log_agent_run_error(
                    f"Error processing patient {patient['name']}: {str(e)}",
                    process_error_time,
                    process_error_time
                )
                continue
        
        logger.info("Patient responsibility processing completed")
    
    def get_summary(self) -> Dict:
        """Get processing summary."""
        return {
            'total_patients_processed': len(self.final_patients),
            'patients': [
                {
                    'name': p['name'],
                    'id': p['id'],
                    'insurance_count': len(p['insurances']),
                    'service_line': p.get('service_line', 'NA')
                }
                for p in self.final_patients
            ]
        }


def main():
    """Main execution function."""
    # Initialize and run agent
    agent = PatientResponsibilityAgent(config.ZAPIER_WEBHOOK_URL)
    run_started = utc_now()
    
    try:
        agent.process_patients()
        
        # Print summary
        summary = agent.get_summary()
        logger.info("Processing Summary:")
        logger.info(f"Total patients processed: {summary['total_patients_processed']}")
        
        for patient in summary['patients']:
            logger.info(f"  - {patient['name']} (ID: {patient['id']}) - "
                       f"{patient['insurance_count']} insurances - "
                       f"Service line: {patient['service_line']}")
    
    except KeyboardInterrupt:
        logger.info("Processing interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
