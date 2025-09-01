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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import time
import logging
from config import AMD_CONFIG, ZAPIER_WEBHOOK_URL, PROCESSING_CONFIG, MEDICAID_INDICATORS, PVERIFY_CONFIG, STATE_IDS

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

class AdvancedMDAPI:
    """AdvancedMD API client for patient and insurance management."""
    
    def __init__(self):
        self.base_url = AMD_CONFIG['base_url']
        self.api_base_url = AMD_CONFIG['api_base_url']
        self.username = AMD_CONFIG['username']
        self.password = AMD_CONFIG['password']
        self.office_code = AMD_CONFIG['office_code']
        self.app_name = AMD_CONFIG['app_name']
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
                    "@subscriberid": "SubscriberID"
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
                    logger.warning(f"Skipping patient {patient_elem.get('name')} - missing DOB or sex (DOB: {dob}, Sex: {sex})")
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
                        'subscriberid': insurance_elem.get('subscriberid', '').strip() if insurance_elem.get('subscriberid') else ''
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
        """Check if patient has appointments."""
        if not self.token:
            return False
            
        try:
            url = f"{self.api_base_url}/scheduler/Appointments"
            params = {
                'forView': 'patient',
                'patientId': patient_id
            }
            
            response = requests.get(
                url,
                headers={
                    'Cookie': f'token={self.token}',
                    'Authorization': f'Bearer {self.token}'
                },
                params=params
            )
            
            if response.status_code == 200:
                appointments = response.json()
                has_appts = len(appointments) > 0 if isinstance(appointments, list) else bool(appointments)
                logger.debug(f"Patient {patient_id} has appointments: {has_appts}")
                return has_appts
            else:
                logger.warning(f"Failed to get appointments for patient {patient_id}: {response.status_code}")
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
        self.token_url = PVERIFY_CONFIG['token_url']
        self.discovery_url = PVERIFY_CONFIG['discovery_url']
        self.eligibility_url = PVERIFY_CONFIG['eligibility_url']
        self.client_id = PVERIFY_CONFIG['client_id']
        self.client_secret = PVERIFY_CONFIG['client_secret']
        self.provider_last_name = PVERIFY_CONFIG['provider_last_name']
        self.provider_npi = PVERIFY_CONFIG['provider_npi']
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
            response = requests.post(
                self.token_url,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data=payload
            )
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            expires_in = token_data.get('expires_in', 3600)  # Default 1 hour
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info("Successfully obtained PVerify access token")
            return True
            
        except Exception as e:
            logger.error(f"Failed to get PVerify access token: {e}")
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
            return 'CO', STATE_IDS['CO']
        elif state in ['TX', 'TEXAS']:
            return 'TX', STATE_IDS['TX']
        else:
            # Default to CO if state is unclear
            logger.warning(f"Unknown state '{state}' for patient {patient.get('name')}, defaulting to CO")
            return 'CO', STATE_IDS['CO']
    
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
            
            discovery_data = response.json()
            logger.debug(f"Insurance discovery for {patient.get('name')}: {discovery_data.get('PayerName', 'No payer found')}")
            return discovery_data
            
        except Exception as e:
            logger.error(f"Insurance discovery failed for {patient.get('name')}: {e}")
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
        
        # Get member ID - use subscriber ID from insurance or try discovery
        subscriber_id = insurance.get('subscriberid')
        member_id = subscriber_id.strip() if subscriber_id else ''
        payer_code = None
        
        if not member_id:
            # Try insurance discovery to find member ID
            discovery_result = self.insurance_discovery(patient)
            if discovery_result and discovery_result.get('PayerFound'):
                # Check if discovered insurance matches AMD insurance
                if self.match_insurance_name(insurance.get('carname', ''), discovery_result.get('PayerName', '')):
                    member_id = discovery_result.get('MemberID', '')
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
            
            eligibility_data = response.json()
            logger.info(f"Eligibility check completed for {patient.get('name')} - Status: {eligibility_data.get('status', 'Unknown')}")
            return eligibility_data
            
        except Exception as e:
            logger.error(f"Eligibility check failed for {patient.get('name')}: {e}")
            return {}
    
    def extract_financial_data(self, eligibility_data: Dict) -> Dict:
        """Extract copay, coinsurance, and deductible from PVerify response."""
        financial_data = {
            'copay': 0.0,
            'coinsurance': 0.0,
            'deductible': 0.0
        }
        
        try:
            # Check networkSections for summary data
            network_sections = eligibility_data.get('networkSections', [])
            for section in network_sections:
                if section.get('identifier') == 'Specialist':
                    in_network = section.get('inNetworkParameters', [])
                    for param in in_network:
                        key = param.get('key', '').lower()
                        value = param.get('value', '').strip()
                        
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
            for service_type in service_types:
                service_name = service_type.get('serviceTypeName', '')
                
                # Focus on relevant service types
                if any(keyword in service_name.lower() for keyword in ['professional', 'physician', 'office']):
                    sections = service_type.get('serviceTypeSections', [])
                    
                    for section in sections:
                        label = section.get('label', '')
                        if 'in plan-network' in label.lower() or 'applies to' in label.lower():
                            params = section.get('serviceParameters', [])
                            
                            for param in params:
                                key = param.get('key', '').lower()
                                value = param.get('value', '').strip()
                                
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
                                        if deduct_val > financial_data['deductible']:
                                            financial_data['deductible'] = deduct_val
                                    except ValueError:
                                        pass
            
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


class PatientResponsibilityAgent:
    """Main agent class that orchestrates the entire workflow."""
    
    def __init__(self, zapier_webhook_url: str):
        self.amd_api = AdvancedMDAPI()
        self.pverify_api = PVerifyAPI()
        self.zapier = ZapierWebhook(zapier_webhook_url)
        self.final_patients = []
    
    def is_medicaid_insurance(self, insurance: Dict) -> bool:
        """Check if insurance is Medicaid based on carcode or carname."""
        carcode = insurance.get('carcode', '').upper()
        carname = insurance.get('carname', '').upper()
        
        return any(indicator in carcode or indicator in carname for indicator in MEDICAID_INDICATORS)
    
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
        if any(indicator in carcode or indicator in carname for indicator in MEDICAID_INDICATORS):
            return 'Medicaid'
        
        # Check if Self-Pay (typically no insurance or specific codes)
        if 'SELF' in carname or 'CASH' in carname:
            return 'Self-Pay'
        
        # Check if Medicare Advantage using comprehensive rules
        if self.is_medicare_advantage(carname):
            return 'Medicare Advantage'
        
        # Default to Commercial
        return 'Commercial'
    
    def calculate_service_line_responsibility(self, insurance: Dict, pverify_data: Dict, service_line: str) -> str:
        """Calculate patient responsibility for a specific service line."""
        payer_type = self.get_payer_type(insurance)
        pverify_financial = pverify_data.get('financial_data', {})
        
        # Get copay and coinsurance data (PVerify priority, AMD fallback)
        copay_amount = pverify_financial.get('copay', 0) or insurance.get('copaydollaramount', 0)
        coinsurance_pct = pverify_financial.get('coinsurance', 0) or insurance.get('copaypercentageamount', 0)
        
        # Apply rules based on payer type and service line
        if payer_type == 'Medicaid':
            if service_line == 'IM ketamine':
                return '$0 patient responsibility'
            elif service_line == 'KAP':
                return '$0 patient responsibility'
            elif service_line == 'Spravato':
                return 'Copay/coinsurance/deductible per eligibility'
            elif service_line == 'Med Management (Psych E/M)':
                return 'Typically $0 if eligible (Medicaid balances should be $0). Verify under the medical service type (drill to 01 = Medical Care) when checking E/M.'
        
        elif payer_type == 'Commercial' or payer_type == 'Medicare Advantage':
            # For commercial payers, return copay/coinsurance/deductible info
            if copay_amount > 0:
                return f'${copay_amount:.2f} copay'
            elif coinsurance_pct > 0:
                return f'{coinsurance_pct:.0f}% coinsurance'
            else:
                return 'Copay/coinsurance/deductible per eligibility'
        
        elif payer_type == 'Self-Pay':
            if service_line == 'IM ketamine':
                return '$399 at first visit ("Self-Pay Item: Ketamine Induction")'
            elif service_line == 'KAP':
                return 'No explicit amount documented in KB'
            elif service_line == 'Spravato':
                return '$949 self-pay Spravato induction'
            elif service_line == 'Med Management (Psych E/M)':
                return 'No self-pay policy'
        
        # Default fallback
        return 'Copay/coinsurance/deductible per eligibility'
    
    def has_dollar_values(self, insurance: Dict, pverify_data: Dict) -> bool:
        """Check if any service line has specific dollar values (including $0)."""
        service_lines = ['IM ketamine', 'KAP', 'Spravato', 'Med Management (Psych E/M)']
        
        for service_line in service_lines:
            responsibility = self.calculate_service_line_responsibility(insurance, pverify_data, service_line)
            
            # Check if the responsibility contains dollar amounts (including $0) or percentages
            if '$' in responsibility:
                return True
            if '%' in responsibility:
                return True
        
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
        logger.info("Starting patient responsibility processing...")
        
        # Step 1: Authenticate with AdvancedMD
        if not self.amd_api.authenticate():
            logger.error("Failed to authenticate with AdvancedMD")
            return
        
        # Step 2: Get updated patients from last 24h with insurance
        logger.info("Fetching updated patients...")
        patients = self.amd_api.get_updated_patients(PROCESSING_CONFIG['hours_back'])
        
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
                        
                        # Check if memo has any specific dollar values or percentages
                        if not self.has_dollar_values(insurance, pverify_data):
                            logger.info(f"Skipping memo for {patient['name']} - {insurance.get('carname')} (no specific dollar values)")
                            continue
                        
                        # Generate comprehensive memo with all service lines
                        memo_text = self.generate_comprehensive_memo(patient, insurance, pverify_data)
                        
                        # Post memo to AMD
                        success = self.amd_api.post_memo(patient['id'], memo_text)
                        
                        if success:
                            logger.info(f"Successfully posted comprehensive memo for {patient['name']} - {insurance.get('carname')}")
                            logger.debug(f"Memo content:\n{memo_text}")
                        else:
                            logger.error(f"Failed to post memo for {patient['name']} - {insurance.get('carname')}")
                
            except Exception as e:
                logger.error(f"Error processing patient {patient['name']}: {e}")
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
    agent = PatientResponsibilityAgent(ZAPIER_WEBHOOK_URL)
    
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
