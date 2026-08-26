from __future__ import unicode_literals
import base64
import frappe
import requests
import json
import jwt
from frappe.utils import now_datetime, add_to_date, nowdate, getdate, formatdate
from requests.auth import HTTPBasicAuth
from urllib.parse import quote, urlencode
from frappe.model.document import Document
from frappe import _
from frappe.utils import get_defaults
import uuid  # Add this with your other imports
import hashlib
import socket
import datetime
import platform
from frappe.utils import get_site_name,get_host_name
import time
import ipaddress
import urllib.parse
from frappe.utils.password import set_encrypted_password
# from frappe.utils import hash


# HMRC OAuth 2.0 Configuration
# HMRC_AUTH_URL = "https://test-api.service.hmrc.gov.uk/oauth/authorize"
# HMRC_TOKEN_URL = "https://test-api.service.hmrc.gov.uk/oauth/token"
# HMRC_API_BASE_URL = "https://test-api.service.hmrc.gov.uk"


HMRC_AUTH_URL = "https://api.service.hmrc.gov.uk/oauth/authorize"
HMRC_TOKEN_URL = "https://api.service.hmrc.gov.uk/oauth/token"
HMRC_API_BASE_URL = "https://api.service.hmrc.gov.uk"

# @frappe.whitelist()
# def start_oauth_flow(docname):
#     doc = frappe.get_doc("VAT Settings", docname)
#     client_id = doc.client_id
#     redirect_uri = doc.redirect_url

#     auth_url = (
#         f"{HMRC_AUTH_URL}?response_type=code&"
#         f"client_id={client_id}&"
#         f"redirect_uri={redirect_uri}&"
#         f"scope=read:vat+write:vat&"  
#         f"state={docname}"  
#     )
#     return auth_url

# @frappe.whitelist(allow_guest=True)
# def oauth_callback():
#     code = frappe.form_dict.get("code")
#     state = frappe.form_dict.get("state")

#     if not code or not state:
#         frappe.throw("Authorization code or state not found in the callback URL.")

#     doc = frappe.get_doc("VAT Settings", state)
#     client_id = doc.client_id
#     client_secret = doc.get_password('client_secret')
#     redirect_uri = doc.redirect_url

#     payload = {
#         "grant_type": "authorization_code",
#         "code": code,
#         "redirect_uri": redirect_uri,
#         "client_id": client_id,  
#         "client_secret": client_secret
#     }
#     headers = {
#         "Content-Type": "application/x-www-form-urlencoded",
#     }

#     auth = HTTPBasicAuth(client_id, client_secret)

#     try:
#         response = requests.post(HMRC_TOKEN_URL, data=payload, headers=headers, auth=auth)

#         if response.status_code == 200:
#             token_data = response.json()
#             access_token = token_data["access_token"]
#             refresh_token = token_data["refresh_token"]
#             expires_in = token_data["expires_in"]

#             token_expiry = add_to_date(now_datetime(), seconds=expires_in)

#             doc.access_token = access_token
#             doc.refresh_token = refresh_token
#             doc.token_expiry = token_expiry
#             doc.status = "Authorized"
#             doc.save()
#             frappe.db.commit()

#             frappe.local.response["type"] = "redirect"
#             frappe.local.response["location"] = f"/app/vat-settings/{state}"

#         else:
#             frappe.throw(f"Error: {response.status_code}, {response.text}")

#     except requests.exceptions.RequestException as e:
#         frappe.throw(f"Request failed: {e}")

# Your registered redirect URI (without parameters)
HMRC_REGISTERED_REDIRECT_URI = "https://zikprotest.frappe.cloud/api/method/zikpro_uk_vat.api.oauth_callback"


@frappe.whitelist()
def start_oauth_flow(docname):
    
    doc = frappe.get_doc("VAT Settings", docname)
    client_id = doc.client_id

    redirect_uri = (
        f"{HMRC_REGISTERED_REDIRECT_URI}"
        # f"?docname={urllib.parse.quote(docname)}"
        f"?client_id={urllib.parse.quote(client_id)}"
        f"&user_site={urllib.parse.quote(frappe.local.site)}"
    )
    
    state = frappe.generate_hash(length=20)
    
    auth_url = (
        f"{HMRC_AUTH_URL}?response_type=code&"
        f"client_id={client_id}&"
        f"redirect_uri={urllib.parse.quote(redirect_uri)}&"
        f"scope=read:vat+write:vat&"
        f"state={state}"
    )
    
    return auth_url


@frappe.whitelist(allow_guest=True)
def oauth_callback():
    # docname = frappe.form_dict.get("docname")
    client_id = frappe.form_dict.get("client_id")
    user_site = frappe.form_dict.get("user_site")
    
    # These come from HMRC
    code = frappe.form_dict.get("code")
    state = frappe.form_dict.get("state")
    
    
    # if not all([docname, user_site, code]):
    #     frappe.throw("Missing required parameters")

    if not all([client_id, user_site, code]):
        frappe.throw("Missing required parameters")
    
    try:
        docname = frappe.db.get_value(
            "VAT Settings",
            {"client_id": client_id},
            "name"
        )

        if not docname:
            frappe.throw(f"VAT Settings not found for client_id: {client_id}")
        
        doc = frappe.get_doc("VAT Settings", docname)
        # client_id = doc.client_id
        client_secret = doc.get_password('client_secret')
        
        
    except Exception as e:
        frappe.log_error(f"[CALLBACK] VAT Settings not found: {str(e)}", "HMRC Flow")
        frappe.throw(f"VAT Settings not found")
    
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        # "redirect_uri": f"{HMRC_REGISTERED_REDIRECT_URI}?docname={docname}&user_site={user_site}",
        "redirect_uri": f"{HMRC_REGISTERED_REDIRECT_URI}?client_id={client_id}&user_site={user_site}",
        "client_id": client_id,
        "client_secret": client_secret
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth = HTTPBasicAuth(client_id, client_secret)

    response = requests.post(HMRC_TOKEN_URL, data=payload, headers=headers, auth=auth, timeout=30)

    if response.status_code != 200:
        frappe.throw(f"Token exchange failed: {response.status_code} - {response.text}")
    
    token_data = response.json()

    redirect_url = (
        f"https://{user_site}/api/method/zikpro_uk_vat.api.save_tokens?"
        # f"docname={docname}&"
        f"client_id={client_id}&"
        f"access_token={token_data['access_token']}&"
        f"refresh_token={token_data['refresh_token']}&"
        f"expires_in={token_data['expires_in']}"
    )
    
    
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = redirect_url


@frappe.whitelist()
def save_tokens():

    # docname = frappe.form_dict.get("docname")
    client_id = frappe.form_dict.get("client_id")
    access_token = frappe.form_dict.get("access_token")
    refresh_token = frappe.form_dict.get("refresh_token")
    expires_in = frappe.form_dict.get("expires_in")
    
    # if not all([docname, access_token, refresh_token, expires_in]):
    #     frappe.throw("Missing token data")

    if not all([client_id, access_token, refresh_token, expires_in]):
        frappe.throw("Missing token data")
    
    try:
        docname = frappe.db.get_value(
            "VAT Settings",
            {"client_id": client_id},
            "name"
        )

        if not docname:
            frappe.throw(f"VAT Settings not found for client_id: {client_id}")
            
        doc = frappe.get_doc("VAT Settings", docname)

        doc.access_token = access_token
        doc.refresh_token = refresh_token
        doc.token_expiry = add_to_date(now_datetime(), seconds=int(expires_in))
        doc.status = "Authorized"
        doc.save()
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(f"[SAVE] ERROR: {str(e)}", "HMRC Flow")
        frappe.throw(f"Failed to save tokens: {str(e)}")
    
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"/app/vat-settings/{docname}"


def make_hmrc_request(method, endpoint, docname, params=None, json_data=None, retry_count=0):
    """
    Helper function to make authenticated HMRC API requests with automatic token refresh
    """
    try:
        doc = frappe.get_doc("VAT Settings", docname)
        if not doc.access_token:
            return {"error": "Access token not found in VAT Settings", "success": False}

        access_token = doc.get_password("access_token")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.hmrc.1.0+json",
            "Content-Type": "application/json",
            **get_fraud_prevention_headers()  # Include all headers
        }
        
        url = f"{HMRC_API_BASE_URL}{endpoint}"
        
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=30
            )
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}", "success": False}

        log_msg = (
            f"HMRC API Request:\n"
            f"Method: {method}\n"
            f"URL: {url}\n"
            f"Headers: {headers}\n"
            f"Params: {params}\n"
            f"Status Code: {response.status_code}\n"
            f"Response: {response.text[:500]}"
        )
        frappe.log_error(title="HMRC API Debug", message=log_msg)

        # Handle token expiration (401) - attempt refresh once
        if response.status_code == 401 and retry_count == 0:
            if "expired" in response.text.lower() or "INVALID_CREDENTIALS" in response.text:
                frappe.log_error("Access token invalid/expired, attempting refresh...")
                refresh_result = refresh_access_token(docname)
                
                if not refresh_result.get("success"):
                    return {
                        "error": f"Token refresh failed: {refresh_result.get('error', 'Unknown error')}",
                        "success": False
                    }
                
                # Retry with new token
                return make_hmrc_request(
                    method, endpoint, docname, 
                    params, json_data, retry_count + 1
                )

        # Handle other error responses
        if response.status_code >= 400:
            try:
                error_data = response.json()
                error_msg = f"HMRC API Error ({response.status_code}): {error_data.get('message', 'No error message')}"
                if 'code' in error_data:
                    error_msg += f" (code: {error_data['code']})"
                return {
                    "error": error_msg,
                    "status_code": response.status_code,
                    "response_text": response.text,
                    "success": False
                }
            except ValueError:
                return {
                    "error": f"HMRC API Error ({response.status_code}): {response.text}",
                    "success": False
                }
        try:
            return {
                "status_code": response.status_code,
                "data": response.json() if response.content else None,
                "success": True
            }
        except ValueError:
            return {
                "error": f"Invalid JSON response: {response.text}",
                "success": False
            }

    except Exception as e:
        error_msg = f"Unexpected error in make_hmrc_request: {str(e)}"
        frappe.log_error("HMRC Request Error", error_msg)
        return {"error": error_msg, "success": False}

@frappe.whitelist()
def refresh_access_token(docname):
    """Refresh the access token using the refresh token"""
    try:
        doc = frappe.get_doc("VAT Settings", docname)
        if not doc.refresh_token:
            return {"success": False, "error": "Refresh token not found. Please re-authorize."}

        client_id = doc.client_id
        client_secret = doc.get_password('client_secret')
        refresh_token = doc.get_password('refresh_token')

        if not all([client_id, client_secret, refresh_token]):
            return {"success": False, "error": "Missing required credentials for token refresh"}

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret
        }
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        auth = HTTPBasicAuth(client_id, client_secret)

        response = requests.post(
            HMRC_TOKEN_URL,
            data=payload,
            headers=headers,
            auth=auth,
            timeout=30
        )

        frappe.log_error(
            "Token Refresh Response",
            f"Status: {response.status_code}\nResponse: {response.text}"
        )

        if response.status_code != 200:
            error_msg = f"Token refresh failed ({response.status_code})"
            try:
                error_data = response.json()
                error_msg += f": {error_data.get('error', 'Unknown error')}"
                if 'error_description' in error_data:
                    error_msg += f" - {error_data['error_description']}"
            except ValueError:
                error_msg += f": {response.text}"
            return {"success": False, "error": error_msg}

        token_data = response.json()
        if not all(k in token_data for k in ["access_token", "expires_in"]):
            return {"success": False, "error": "Invalid token response format"}

        doc.access_token = token_data["access_token"]
        if "refresh_token" in token_data:
            doc.refresh_token = token_data["refresh_token"]
        doc.token_expiry = add_to_date(now_datetime(), seconds=token_data["expires_in"])
        doc.save()
        frappe.db.commit()

        return {
            "success": True,
            "access_token": token_data["access_token"],
            "expires_in": token_data["expires_in"]
        }

    except requests.exceptions.RequestException as e:
        error_msg = f"Token refresh request failed: {str(e)}"
        frappe.log_error("Token Refresh Failed", error_msg)
        return {"success": False, "error": error_msg}
    except Exception as e:
        error_msg = f"Unexpected error during token refresh: {str(e)}"
        frappe.log_error("Token Refresh Error", error_msg)
        return {"success": False, "error": error_msg}

@frappe.whitelist()
def fetch_all_obligations(frequency, from_date=None, to_date=None):
    """Fetch obligations with proper date formatting for HMRC API"""
    try:
        default_company = frappe.db.get_single_value("Global Defaults", "default_company")
        if not default_company:
            frappe.throw("No default company set in Global Defaults.")
        
        # vrn = frappe.db.get_value("Company", default_company, "custom_uk_vat_registration_number")
        vrn = frappe.db.get_value("Company", default_company, "uk_vat_registration_number")
        if not vrn:
            frappe.throw(f"VRN not set for company {default_company}.")
        
        vat_settings = frappe.get_doc("VAT Settings", {"company": default_company})
        if not vat_settings:
            frappe.throw("VAT Settings not found for company.")

        if not vat_settings.get_password("access_token"):
            frappe.throw("Access token not found. Please authorize with HMRC first.")
        
        from_date = from_date or add_to_date(nowdate(), years=-1)
        to_date = to_date or nowdate()
        
        params = {
            "from": formatdate(from_date, "yyyy-MM-dd"),
            "to": formatdate(to_date, "yyyy-MM-dd")
        }
        
        response = make_hmrc_request(
            "GET",
            f"/organisations/vat/{vrn}/obligations",
            vat_settings.name,
            params=params
        )
        
        if not response.get("success"):
            error_msg = response.get("error", "Unknown error occurred")
            if "response_text" in response:
                error_msg += f"\nResponse: {response['response_text']}"
            frappe.throw(f"Failed to fetch obligations: {error_msg}")
        
        if not response.get("data"):
            frappe.throw("No data returned from HMRC API")
        
        obligations = response["data"].get("obligations", [])
        if not isinstance(obligations, list):
            frappe.throw(f"Unexpected obligations format: {type(obligations)}")
        
        processed_count = 0

        for obligation in obligations:
            try:
                if not all(key in obligation for key in ["start", "end", "due"]):
                    frappe.log_error("Invalid obligation format", str(obligation))
                    continue
                
                start = getdate(obligation["start"])
                end = getdate(obligation["end"])
                days_diff = (end - start).days
                
                # Frequency filtering
                if frequency == "Monthly" and days_diff > 31:
                    continue
                elif frequency == "Quarterly" and days_diff <= 31:
                    continue
                
                doc_data = {
                    "doctype": "UK MTD VAT Return",
                    "vrn": vrn,
                    "status": "Fulfilled" if obligation.get("status") == "F" else "Overdue",
                    "period_start_date": obligation["start"],
                    "period_end_date": obligation["end"],
                    "due_date": obligation["due"],
                    "reference_key": obligation.get("periodKey")
                }
                
                existing = frappe.get_all("UK MTD VAT Return",
                    filters={"reference_key": obligation.get("periodKey")},
                    limit=1)
                
                if existing:
                    doc = frappe.get_doc("UK MTD VAT Return", existing[0].name)
                    doc.update(doc_data)
                else:
                    doc = frappe.get_doc(doc_data)
                    doc.insert()
                
                processed_count += 1
            
            except Exception as e:
                frappe.log_error("Failed to process obligation", f"{str(e)}\nObligation: {obligation}")
                continue
        
        frappe.db.commit()
        return {
            "count": processed_count,
            "frequency": frequency,
            "message": f"Successfully processed {processed_count} obligations"
        }

    except Exception as e:
        error_msg = f"Failed to process obligations: {str(e)}"
        frappe.log_error("Obligation Processing Error", error_msg)
        frappe.throw(error_msg)

# @frappe.whitelist()
# def calculate_vat_boxes(docname):
#     """
#     Calculate UK VAT 9-box return using your field names
#     """
#     doc = frappe.get_doc("UK MTD VAT Return", docname)
    
#     if not doc.period_start_date or not doc.period_end_date:
#         frappe.throw("Please set period start and end dates first")
    
#     # 1. Calculate Box 1 (VAT Due on Sales)
#     sales_invoices = frappe.get_all("Sales Invoice",
#         filters={
#             "posting_date": ["between", [doc.period_start_date, doc.period_end_date]],
#             "docstatus": 1,
#             "is_return": 0  # Exclude credit notes
#         },
#         fields=["base_grand_total", "base_total_taxes_and_charges"]
#     )
#     doc.sales_vat_due_box1 = sum(inv.base_total_taxes_and_charges for inv in sales_invoices)
    
#     # 2. Calculate Box 6 (Net Sales)
#     doc.net_sales_box6 = sum(inv.base_grand_total - inv.base_total_taxes_and_charges 
#                            for inv in sales_invoices)
    
#     # 3. Calculate Box 4 (VAT Reclaimed on Purchases)
#     purchase_invoices = frappe.get_all("Purchase Invoice",
#         filters={
#             "posting_date": ["between", [doc.period_start_date, doc.period_end_date]],
#             "docstatus": 1,
#             "is_return": 0
#         },
#         fields=["base_grand_total", "base_total_taxes_and_charges"]
#     )
#     doc.purchase_vat_reclaimed_box4 = sum(inv.base_total_taxes_and_charges for inv in purchase_invoices)
    
#     # 4. Calculate Box 7 (Net Purchases)
#     doc.net_purchases_box7 = sum(inv.base_grand_total - inv.base_total_taxes_and_charges 
#                                for inv in purchase_invoices)
    
#     # 5. Calculate EU Transactions (Box 2, 8, 9)
#     eu_results = calculate_eu_transactions(doc.period_start_date, doc.period_end_date)
#     doc.eu_acquisition_vat_due_box2 = eu_results['box2']
#     doc.net_eu_supplies_box_8 = eu_results['box8']
#     doc.net_eu_acquisitions_box_9 = eu_results['box9']
    
#     # 6. Calculate Derived Boxes
#     doc.total_vat_due_box3 = doc.sales_vat_due_box1 + doc.eu_acquisition_vat_due_box2
#     doc.net_vat_due_box5 = doc.total_vat_due_box3 - doc.purchase_vat_reclaimed_box4

#     doc.formatted_sales_vat_due_box1 = format_currency(doc.sales_vat_due_box1)
#     doc.formatted_net_sales_box6 = format_currency(doc.net_sales_box6)
#     doc.formatted_purchase_vat_reclaimed_box4 = format_currency(doc.purchase_vat_reclaimed_box4)
#     doc.formatted_net_purchases_box7 = format_currency(doc.net_purchases_box7)
#     doc.formatted_eu_acquisition_vat_due_box2 = format_currency(doc.eu_acquisition_vat_due_box2)
#     doc.formatted_net_eu_supplies_box_8 = format_currency(doc.net_eu_supplies_box_8)
#     doc.formatted_net_eu_acquisitions_box_9 = format_currency(doc.net_eu_acquisitions_box_9)
#     doc.formatted_total_vat_due_box3 = format_currency(doc.total_vat_due_box3)
#     doc.formatted_net_vat_due_box5 = format_currency(doc.net_vat_due_box5)
    
#     doc.save()
#     frappe.db.commit()
    
#     return {
#         "status": "success",
#         "message": "VAT boxes calculated successfully",
#         "formatted_values": {
#             "sales_vat_due_box1": doc.formatted_sales_vat_due_box1,
#             "net_sales_box6": doc.formatted_net_sales_box6,
#             "purchase_vat_reclaimed_box4": doc.formatted_purchase_vat_reclaimed_box4,
#             "net_purchases_box7": doc.formatted_net_purchases_box7,
#             "eu_acquisition_vat_due_box2": doc.formatted_eu_acquisition_vat_due_box2,
#             "net_eu_supplies_box_8" : doc.formatted_net_eu_supplies_box_8,
#             "net_eu_acquisitions_box_9": doc.formatted_net_eu_acquisitions_box_9,
#             "total_vat_due_box3":doc.formatted_total_vat_due_box3,
#             "net_vat_due_box5":doc.formatted_net_vat_due_box5

#         }

#     }

@frappe.whitelist()
def calculate_vat_boxes(docname):
    doc = frappe.get_doc("UK MTD VAT Return", docname)
    
    if not doc.period_start_date or not doc.period_end_date:
        frappe.throw("Please set period start and end dates first")

    sales_invoices = frappe.get_all("Sales Invoice", filters={
        "posting_date": ["between", [doc.period_start_date, doc.period_end_date]],
        "docstatus": 1,
        "is_return": 0
    }, fields=["base_grand_total", "base_total_taxes_and_charges"],
    order_by="posting_date asc") # v16

    doc.sales_vat_due_box1 = sum(inv.base_total_taxes_and_charges for inv in sales_invoices)
    doc.net_sales_box6 = sum(inv.base_grand_total - inv.base_total_taxes_and_charges for inv in sales_invoices)

    purchase_invoices = frappe.get_all("Purchase Invoice", filters={
        "posting_date": ["between", [doc.period_start_date, doc.period_end_date]],
        "docstatus": 1,
        "is_return": 0
    }, fields=["base_grand_total", "base_total_taxes_and_charges"],
    order_by="posting_date asc") #v16

    doc.purchase_vat_reclaimed_box4 = sum(inv.base_total_taxes_and_charges for inv in purchase_invoices)
    doc.net_purchases_box7 = sum(inv.base_grand_total - inv.base_total_taxes_and_charges for inv in purchase_invoices)

    eu_results = calculate_eu_transactions(doc.period_start_date, doc.period_end_date)
    doc.eu_acquisition_vat_due_box2 = eu_results['box2']
    doc.net_eu_supplies_box_8 = eu_results['box8']
    doc.net_eu_acquisitions_box_9 = eu_results['box9']

    doc.total_vat_due_box3 = doc.sales_vat_due_box1 + doc.eu_acquisition_vat_due_box2
    doc.net_vat_due_box5 = doc.total_vat_due_box3 - doc.purchase_vat_reclaimed_box4

    # # Set formatted values
    # doc.formatted_sales_vat_due_box1 = format_currency(doc.sales_vat_due_box1)
    # doc.formatted_net_sales_box6 = format_currency(doc.net_sales_box6)
    # doc.formatted_purchase_vat_reclaimed_box4 = format_currency(doc.purchase_vat_reclaimed_box4)
    # doc.formatted_net_purchases_box7 = format_currency(doc.net_purchases_box7)
    # doc.formatted_eu_acquisition_vat_due_box2 = format_currency(doc.eu_acquisition_vat_due_box2)
    # doc.formatted_net_eu_supplies_box_8 = format_currency(doc.net_eu_supplies_box_8)
    # doc.formatted_net_eu_acquisitions_box_9 = format_currency(doc.net_eu_acquisitions_box_9)
    # doc.formatted_total_vat_due_box3 = format_currency(doc.total_vat_due_box3)
    # doc.formatted_net_vat_due_box5 = format_currency(doc.net_vat_due_box5)

    doc.save()
    frappe.db.commit()

    return {
        "status": "success",
        "message": "VAT boxes calculated successfully"
    }


def calculate_eu_transactions(start_date, end_date):
    """Calculate EU-specific boxes"""
    # Box 2: VAT due on EU acquisitions
    eu_purchases = frappe.get_all("Purchase Invoice",
        filters={
            "posting_date": ["between", [start_date, end_date]],
            "docstatus": 1,
            # "custom_is_eu_supplier": 1
            "is_eu_supplier": 1
        },
        fields=["base_grand_total", "base_total_taxes_and_charges"],
        order_by="posting_date asc" #v16
    )
    box2 = sum(inv.base_total_taxes_and_charges for inv in eu_purchases)
    
    # Box 8: Net EU supplies
    eu_sales = frappe.get_all("Sales Invoice",
        filters={
            "posting_date": ["between", [start_date, end_date]],
            "docstatus": 1,
            # "custom_is_eu_customer": 1
            "is_eu_customer": 1
        },
        fields=["base_grand_total", "base_total_taxes_and_charges"],
        order_by="posting_date asc" #v16
    )
    box8 = sum(inv.base_grand_total - inv.base_total_taxes_and_charges 
              for inv in eu_sales)
    
    # Box 9: Net EU acquisitions
    box9 = sum(inv.base_grand_total - inv.base_total_taxes_and_charges 
              for inv in eu_purchases)
    
    return {
        'box2': box2,
        'box8': box8,
        'box9': box9
    }

@frappe.whitelist()
def submit_vat_return_to_hmrc(docname):
    """Submit VAT return to HMRC with proper token handling and error management"""
    try:
        doc = frappe.get_doc("UK MTD VAT Return", docname)
        
        default_company = frappe.db.get_single_value("Global Defaults", "default_company")
        if not default_company:
            frappe.throw("No default company set in Global Defaults.")
            
        # vrn = frappe.db.get_value("Company", default_company, "custom_uk_vat_registration_number")
        vrn = frappe.db.get_value("Company", default_company, "uk_vat_registration_number")
        if not vrn:
            frappe.throw(f"VRN not set for company {default_company}.")

        vat_settings = frappe.get_doc("VAT Settings", {"company": default_company})
        if not vat_settings:
            frappe.throw("VAT Settings not found for company.")
        
        if not vat_settings.get_password("access_token"):
            frappe.throw("Access token not found. Please authorize with HMRC first.")

        original_doc = doc.as_dict().copy()
        
        try:
            submission_data = {
                "periodKey": doc.reference_key,
                "vatDueSales": float(doc.sales_vat_due_box1 or 0),
                "vatDueAcquisitions": float(doc.eu_acquisition_vat_due_box2 or 0),
                "totalVatDue": float(doc.total_vat_due_box3 or 0),
                "vatReclaimedCurrPeriod": float(doc.purchase_vat_reclaimed_box4 or 0),
                "netVatDue": float(doc.net_vat_due_box5 or 0),
                "totalValueSalesExVAT": int(round(float(doc.net_sales_box6 or 0), 0)),
                "totalValuePurchasesExVAT": int(round(float(doc.net_purchases_box7 or 0), 0)),
                "totalValueGoodsSuppliedExVAT": int(round(float(doc.net_eu_supplies_box_8 or 0), 0)),
                "totalAcquisitionsExVAT": int(round(float(doc.net_eu_acquisitions_box_9 or 0), 0)),
                "finalised": True
            }
        except (TypeError, ValueError) as e:
            frappe.throw(f"Invalid VAT box values: {str(e)}")

        response = make_hmrc_request(
            "POST",
            f"/organisations/vat/{vrn}/returns",
            vat_settings.name,
            json_data=submission_data
        )
        
        if not response.get("success"):
            error_msg = response.get("error", "Unknown error occurred")
            if "response_text" in response:
                error_msg += f"\nResponse: {response['response_text']}"
            frappe.throw(f"Failed to submit VAT return: {error_msg}")
            
        response_data = response.get("data", {})
        
        doc.status = "Fulfilled"
        doc.custom_processing_date = response_data.get("processingDate")
        doc.custom_form_bundle_number = response_data.get("formBundleNumber")
        doc.custom_receipt_id = response_data.get("chargeRefNumber")
        doc.custom_receipt_date = frappe.utils.now_datetime()

        # Save with version tracking
        doc.flags.ignore_version = False
        doc.save(ignore_permissions=True)
        create_proper_version_log(doc, original_doc)
        
        return {
            "status": "success",
            "message": "VAT return submitted successfully",
            "redirect_to": f"/app/uk-mtd-vat-return/{docname}",
            "response": response_data
        }

    except frappe.exceptions.ValidationError:
        raise  
    except Exception as e:
        error_msg = f"Failed to submit VAT return: {str(e)}"
        frappe.log_error("VAT Submission Failed", error_msg)
        frappe.throw(error_msg)

def create_proper_version_log(new_doc, old_doc_dict):
    """Create a version log that will definitely show in the UI"""
    try:
        meta = frappe.get_meta(new_doc.doctype)
        
        changes = []
        for field in ["status", "custom_processing_date", "custom_form_bundle_number",
                      "custom_receipt_id", "custom_receipt_date"]:
            old_value = old_doc_dict.get(field)
            new_value = new_doc.get(field)

            if old_value != new_value:
                field_meta = meta.get_field(field)
                changes.append({
                    "fieldname": field,
                    "old_value": old_value,
                    "new_value": new_value,
                    "fieldtype": field_meta.fieldtype if field_meta else "Data"
                })

        if not changes:
            frappe.logger().info("No changes detected; version log not created.")
            return False

        # Create version doc manually
        version_doc = frappe.new_doc("Version")
        version_doc.docname = new_doc.name
        version_doc.ref_doctype = new_doc.doctype
        version_doc.data = json.dumps({
            "changed": changes,
            "added": [],
            "removed": [],
            "row_changed": [],
            "doc": {}
        })  # NOTE: Leave "doc" as empty to avoid huge payloads unless needed
        
        version_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.logger().info(f"Version log created: {version_doc.name}")
        return True

        # Validate the version document exists and has data
        if not frappe.db.exists("Version", version_doc.name):
            frappe.log_error("Version document missing after insert", version_doc.name)
            return False

        return True

    except Exception as e:
        frappe.log_error("Failed to create version log", frappe.get_traceback())
        return False



@frappe.whitelist()
def fetch_liabilities(from_date=None, to_date=None):
    """
    Fetch VAT liabilities from HMRC API matching your doctype field names
    """
    try:
        # 1. Get company and VAT settings
        default_company = frappe.db.get_single_value("Global Defaults", "default_company")
        if not default_company:
            frappe.throw("No default company set in Global Defaults.")
            
        # vrn = frappe.db.get_value("Company", default_company, "custom_uk_vat_registration_number")
        vrn = frappe.db.get_value("Company", default_company, "uk_vat_registration_number")
        if not vrn:
            frappe.throw(f"VAT Registration Number not set for company {default_company}")

        vat_settings = frappe.get_doc("VAT Settings", {"company": default_company})
        if not vat_settings:
            frappe.throw("VAT Settings not found for company.")
            
        if not vat_settings.get_password("access_token"):
            frappe.throw("Access token not found. Please authorize with HMRC first.")

        # 2. Prepare API request with your field names
        params = {}
        if from_date:
            params["from"] = from_date  # Matches your from_date field
        if to_date:
            params["to"] = to_date  # Matches your to_date field
        
        response = make_hmrc_request(
            "GET",
            f"/organisations/vat/{vrn}/liabilities",
            vat_settings.name,
            params=params
        )
        
        if not response.get("success"):
            error_msg = response.get("error", "Unknown error")
            status_code = response.get("status_code", "unknown")

        if status_code == 404:
            frappe.throw("No liability data found for the specified period.")
        else:
            frappe.throw(f"HMRC API Error ({status_code}): {error_msg}")
            
        liabilities = response.get("data", {}).get("liabilities", [])
        today = nowdate()
        formatted_data = []
        
        for liability in liabilities:
            tax_period = liability.get("taxPeriod", {})
            outstanding = float(liability.get("outstandingAmount", 0))
            
            formatted_data.append({
                "name": f"LIABILITY-{tax_period.get('from')}-{liability.get('type')}",
                "type": liability.get("type"),  # Matches your type field
                "from_date": tax_period.get("from"),  # Matches your from_date field
                "to_date": tax_period.get("to"),  # Matches your to_date field
                "due_date": liability.get("due"),  # Matches your due_date field
                "original_amount": float(liability.get("originalAmount", 0)),  # Matches your field
                "outstanding_amount": outstanding
            })
            
        return formatted_data
        
    except Exception as e:
        frappe.log_error("VAT Liability Error", str(e))
        # frappe.throw("Failed to fetch liabilities. Please check error logs.")

@frappe.whitelist()
def fetch_payments(from_date=None, to_date=None):
    """
    Fetch VAT liabilities from HMRC API matching your doctype field names
    """
    try:
        # 1. Get company and VAT settings
        default_company = frappe.db.get_single_value("Global Defaults", "default_company")
        if not default_company:
            frappe.throw("No default company set in Global Defaults.")
            
        # vrn = frappe.db.get_value("Company", default_company, "custom_uk_vat_registration_number")
        vrn = frappe.db.get_value("Company", default_company, "uk_vat_registration_number")
        if not vrn:
            frappe.throw(f"VAT Registration Number not set for company {default_company}")

        vat_settings = frappe.get_doc("VAT Settings", {"company": default_company})
        if not vat_settings:
            frappe.throw("VAT Settings not found for company.")
            
        if not vat_settings.get_password("access_token"):
            frappe.throw("Access token not found. Please authorize with HMRC first.")

        # 2. Prepare API request with your field names
        params = {}
        if from_date:
            params["from"] = from_date  # Matches your from_date field
        if to_date:
            params["to"] = to_date  # Matches your to_date field
        
        response = make_hmrc_request(
            "GET",
            f"/organisations/vat/{vrn}/payments",
            vat_settings.name,
            params=params
        )
        
        if not response.get("success"):
            error_msg = response.get("error", "Unknown error")
            status_code = response.get("status_code", "unknown")

        if status_code == 404:
            frappe.throw("No Payment data found for the specified period.")
        else:
            frappe.throw(f"HMRC API Error ({status_code}): {error_msg}")
            
        payments = response.get("data", {}).get("payments", [])
        today = nowdate()
        formatted_data = []
        
        for payment in payments:
            
            formatted_data.append({
                "amount": float(payment.get("amount", 0)),
                "received_date": payment.get("received"),
            })
            
        return formatted_data
        
    except requests.exceptions.RequestException as e:
        frappe.log_error("HMRC API Connection Error", str(e))
        # frappe.throw("Failed to connect to HMRC API")
    except Exception as e:
        frappe.log_error("VAT Payment Error", str(e))
        # frappe.throw("Failed to fetch VAT payments")

def get_fraud_prevention_headers():
    """Generate HMRC Fraud Prevention Headers for WEB_APP_VIA_SERVER"""
    try:
        # Timestamp (used in multiple headers)
        timestamp = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        
        # 1. Device Identification
        device_id = get_device_id()
        
        # 2. Network Information
        public_ip = get_public_ip()
        public_port = get_client_port()
        
        # 3. User and System Information
        user_ids = get_user_ids()
        # screen_info = get_screen_info()
        # timezone = get_timezone()
        browser_user_agent = get_browser_user_agent()

        # 4. Multi-Factor Authentication
        mfa_header = get_mfa_header()

        # 5. Vendor Information
        vendor_public_ip = get_vendor_public_ip()  # Assuming same as client public IP for simple setups
        vendor_forwarded = get_vendor_forwarded()
        license_ids = get_license_ids()

        client_info = frappe.session.data.get('client_info', {})
        
        # Screen info
        screen_data = (
            f"width={client_info.get('screen_width', 1920)}&"
            f"height={client_info.get('screen_height', 1080)}&"
            f"scaling-factor={client_info.get('pixel_ratio', 1)}&"
            f"colour-depth={client_info.get('color_depth', 24)}"
        )
        
        # Timezone
        # offset = client_info.get('timezone_offset', 0)
        # timezone = f"UTC{'+' if offset >=0 else ''}{offset}:00"
        timezone = get_timezone()
        
        
        # Window size
        window_size = (
            f"width={client_info.get('width', 1920)}&"
            f"height={client_info.get('height', 1080)}"
        )

        headers = {
            # Required Headers
            "Gov-Client-Connection-Method": "WEB_APP_VIA_SERVER",
            "Gov-Client-Device-ID": device_id,
            "Gov-Client-Browser-JS-User-Agent": browser_user_agent,
            "Gov-Client-Public-IP": public_ip,
            "Gov-Client-Public-IP-Timestamp": timestamp,
            "Gov-Client-Public-Port": public_port,
            "Gov-Client-Screens": screen_data,
            "Gov-Client-Timezone": timezone,
            "Gov-Client-User-IDs": user_ids,
            "Gov-Client-Window-Size": window_size,
            
            # Multi-Factor Authentication
            "Gov-Client-Multi-Factor": mfa_header,
            
            # Vendor Headers
            "Gov-Vendor-Forwarded": vendor_forwarded,
            "Gov-Vendor-License-IDs": license_ids,
            "Gov-Vendor-Product-Name": "ERPNext",
            "Gov-Vendor-Public-IP": vendor_public_ip,
            "Gov-Vendor-Version": f"erpnext={quote(frappe.__version__)}"
        }
        
        return headers

    except Exception as e:
        frappe.log_error("Fraud Prevention Header Error", str(e))
        # return generate_compliant_fallback_headers()
        return {}

# ============== HELPER FUNCTIONS ==============

def get_device_id():
    """Get persistent device ID from cookies or generate new one"""
    try:
        device_id = frappe.local.request.cookies.get('hmrc_device_id')
        if not device_id:
            device_id = str(uuid.uuid4())
            frappe.local.response.cookies['hmrc_device_id'] = {
                'value': device_id,
                'expires': 10*365*24*60*60,  # 1 year expiration
                'httponly': True,
                'secure': True
            }
        return device_id
    except:
        return str(uuid.uuid4())

def get_public_ip():
    """Get the actual end-user's public IP with proper proxy handling"""
    try:
        # 1. Standard headers from proxies/load balancers
        ip = (
            frappe.local.request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or
            frappe.local.request.headers.get('X-Real-IP', '') or
            frappe.local.request.headers.get('CF-Connecting-IP', '')  # Cloudflare
        )
        
        # 2. Fallback to direct connection IP
        if not ip:
            ip = frappe.local.request.environ.get('REMOTE_ADDR', '')
        
        # 3. Validate the IP
        if ip and is_valid_ip(ip):
            return ip
            
        # 4. Final fallback for private networks/missing data
        return None  # Explicit None is better than test-net IP
        
    except Exception:
        return None

def is_valid_ip(ip):
    """Validate both IPv4 and IPv6 addresses"""
    try:
        socket.inet_pton(
            socket.AF_INET6 if ':' in ip else socket.AF_INET,
            ip
        )
        return not ip.startswith(('10.', '172.', '192.168.', 'fc00::/7'))
    except:
        return False

def get_client_port():
    """HMRC-compliant client port detection with proper fallbacks"""
    try:
        # 1. Try proxy headers first
        port = (
            frappe.local.request.headers.get('X-Client-Port') or  # Custom header you configure
            frappe.local.request.headers.get('X-Forwarded-Port')  # Common LB header
        )
        
        # 2. Fallback to direct connection
        if not port:
            port = frappe.local.request.environ.get('REMOTE_PORT')
        
        # 3. Validate port
        port = int(port or 0)
        if 1 <= port <= 65535 and port not in [80, 443]:
            return str(port)
            
        # 4. If invalid/missing, return empty (HMRC allows omission)
        return ""
        
    except Exception:
        return ""  # Omit entirely if undetectable

# def get_screen_info():
#     """Get screen info from client-side data or use defaults"""
#     try:
#         if frappe.session.data.get('screen_info'):
#             screen = frappe.session.data['screen_info']
#             return (
#                 f"width={screen.get('width', 1920)}&"
#                 f"height={screen.get('height', 1080)}&"
#                 f"scaling-factor={screen.get('scaling', 1)}&"
#                 f"colour-depth={screen.get('color_depth', 24)}"
#             )
#     except:
#         pass
#     return "width=1920&height=1080&scaling-factor=1&colour-depth=24"

# def get_window_size(screen_info):
#     """Extract window size from screen info"""
#     try:
#         # Parse screen_info to get width and height
#         parts = dict(pair.split('=') for pair in screen_info.split('&'))
#         return f"width={parts.get('width', 1920)}&height={parts.get('height', 1080)}"
#     except:
#         return "width=1920&height=1080"

# def get_timezone():
#     """Get timezone from client-side data or use server timezone"""
#     try:
#         if frappe.session.data.get('timezone_offset'):
#             offset = frappe.session.data['timezone_offset']
#             return f"UTC{'+' if offset >=0 else ''}{offset}:00"
#     except:
#         pass
    
#     # Server fallback
#     offset = -time.timezone // 3600
#     return f"UTC{'+' if offset >=0 else ''}{offset}:00"

def get_browser_user_agent():
    """Get browser user agent from request headers"""
    try:
        return frappe.local.request.headers.get('User-Agent', '')
    except:
        return ""

def get_user_ids():
    """HMRC-compliant user identification with multiple identifiers"""
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            frappe.msgprint(f"Current Session User: {frappe.session.user}")
            return "erpnext=system"

        # Fetch user document
        user_doc = frappe.get_doc("User", user)

        # Prepare identifiers
        identifiers = []

        if user_doc.full_name:
            identifiers.append(f"username={quote(user_doc.full_name)}")
        if user_doc.email:
            identifiers.append(f"email={quote(user_doc.email)}")
        if user_doc.name:
            identifiers.append(f"user_id={quote(user_doc.name)}")

        # Get roles safely
        try:
            roles = user_doc.get_roles()
            if roles:
                identifiers.append(f"roles={quote(','.join(roles))}")
        except Exception as role_err:
            frappe.log_error("Role Fetch Error", frappe.get_traceback())

        return "&".join(identifiers)

    except Exception as e:
        frappe.log_error("User ID Header Error", frappe.get_traceback())
        return "erpnext=system"

def get_mfa_header():
    """Generate HMRC-compliant MFA header"""
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            return ""

        # last_2fa = frappe.db.get_value("User", user, "last_2fa_login")
        # Get from our custom doctype instead of User
        last_2fa = frappe.db.get_value(
            "User MFA Timestamp",
            {"user": user},
            "last_login"
        )
        if not last_2fa:
            return ""

        method = frappe.db.get_single_value("System Settings", "two_factor_method") or "Email"
        mfa_type = "AUTH_CODE" if method == "Email" else "TOTP"
        email = frappe.db.get_value("User", user, "email")
        unique_ref = hashlib.sha256(f"zikpro_salt_{email}_{last_2fa}".encode()).hexdigest()[:32]
        timestamp = last_2fa.strftime("%Y-%m-%dT%H:%M:%SZ")

        return f"type={quote(mfa_type)}&timestamp={quote(timestamp)}&unique-reference={unique_ref}"
    except Exception as e:
        frappe.log_error("MFA Header Error", str(e))
        return ""

# def is_public_ip(ip):
#     """Check if IP is public (not RFC 1918)"""
#     try:
#         return not any(ip.startswith(prefix) for prefix in 
#                       ['10.', '172.', '192.168.', 'fc00::'])
#     except:
#         return False

# def get_vendor_forwarded(client_ip):
#     """Generate HMRC-compliant forwarded header with proxy chain"""
#     hops = []
    
#     try:
#         # Get all X-Forwarded-For IPs (reverse order)
#         forwarded_ips = frappe.local.request.headers.get('X-Forwarded-For', '')
#         proxy_chain = [ip.strip() for ip in forwarded_ips.split(',') if ip.strip()]
#         proxy_chain.reverse()  # Client IP is now first
        
#         # Add client IP if valid
#         if client_ip and is_public_ip(client_ip):
#             proxy_chain.insert(0, client_ip)
        
#         # Get server's public IP (skip private IPs)
#         server_ip = None
#         try:
#             server_ip = socket.gethostbyname(socket.gethostname())
#             if not is_public_ip(server_ip):
#                 server_ip = frappe.local.conf.get("public_ip")  # Fallback
#         except:
#             pass
        
#         # Build hop chain
#         for i in range(len(proxy_chain)):
#             if i == 0:  # First hop (client → first public proxy)
#                 if len(proxy_chain) > 1:
#                     hops.append(f"by={quote(proxy_chain[1])}&for={quote(proxy_chain[0])}")
#                 elif server_ip:
#                     hops.append(f"by={quote(server_ip)}&for={quote(proxy_chain[0])}")
#             else:  # Subsequent hops
#                 if i+1 < len(proxy_chain):
#                     hops.append(f"by={quote(proxy_chain[i+1])}&for={quote(proxy_chain[i])}")
#                 elif server_ip:
#                     hops.append(f"by={quote(server_ip)}&for={quote(proxy_chain[i])}")
        
#         # Add final hop (last proxy → your server)
#         if server_ip and hops:
#             hops.append(f"by={quote(server_ip)}&for={quote(proxy_chain[-1])}")
        
#         return ",".join(hops) if hops else ""
        
#     except Exception as e:
#         frappe.log_error("Vendor-Forwarded Header Error", str(e))
#         return ""

def is_public_ip(ip):
    """Return True if IP is global/public"""
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False

# def get_server_public_ip():
#     """
#     Get server's public IP.
#     Tries to detect via external service.
#     If not available, falls back to hostname resolution.
#     """
#     try:
#         # External service (fast & reliable)
#         return requests.get("https://api.ipify.org").text.strip()
#     except:
#         try:
#             # Fallback to hostname resolution
#             ip = socket.gethostbyname(socket.gethostname())
#             if is_public_ip(ip):
#                 return ip
#         except:
#             pass
#     return None

# def get_server_public_ip():
#     """
#     Get server's public IP - this should be YOUR infrastructure's public IP
#     Uses multiple external services for reliability
#     """
#     # Cache the result to avoid multiple external calls
#     if hasattr(frappe.local, 'cached_server_public_ip'):
#         return frappe.local.cached_server_public_ip
    
#     try:
#         # Try multiple external services for reliability
#         services = [
#             "https://api.ipify.org",
#             "https://checkip.amazonaws.com", 
#             "https://icanhazip.com",
#             "https://ident.me"
#         ]
        
#         for service in services:
#             try:
#                 response = requests.get(service, timeout=3)
#                 if response.status_code == 200:
#                     ip = response.text.strip()
#                     if ip and is_public_ip(ip):
#                         frappe.local.cached_server_public_ip = ip
#                         frappe.logger().debug(f"Detected server public IP from {service}: {ip}")
#                         return ip
#             except Exception as e:
#                 frappe.logger().debug(f"Failed to get IP from {service}: {str(e)}")
#                 continue
                
#         return None
#     except Exception as e:
#         frappe.logger().error(f"Error detecting server public IP: {str(e)}")
#         return None

# for single HOP
# def get_vendor_forwarded():
#     """
#     Build the Gov-Vendor-Forwarded header automatically.
#     """
#     try:
#         client_public_ip = get_public_ip() or frappe.local.request.headers.get('Gov-Client-Public-IP', '')
#         server_public_ip = get_vendor_public_ip()

#         if not (client_public_ip and is_public_ip(client_public_ip)):
#             return ""
#         if not (server_public_ip and is_public_ip(server_public_ip)):
#             return ""

#         # Get proxy chain but be more strict about validation
#         x_forwarded_for = frappe.local.request.headers.get('X-Forwarded-For', '')
#         proxy_chain = [ip.strip() for ip in x_forwarded_for.split(',') if ip.strip() and is_public_ip(ip.strip())]
        
#         # FILTER OUT CLIENT IP FROM PROXY CHAIN
#         proxy_chain = [ip for ip in proxy_chain if ip != client_public_ip]
        
#         hops = []
        
#         if proxy_chain and len(proxy_chain) >= 2:  # Only use if we have actual proxies
#             # Complex case with actual proxies
#             all_ips = [client_public_ip] + proxy_chain
            
#             for i in range(len(all_ips) - 1):
#                 if all_ips[i] != all_ips[i+1]:  # Avoid same-IP hops
#                     hop = f"by={quote(all_ips[i+1])}&for={quote(all_ips[i])}"
#                     hops.append(hop)
            
#             # Final hop to your server
#             if all_ips[-1] != server_public_ip:  # Avoid duplicate final hop
#                 final_hop = f"by={quote(server_public_ip)}&for={quote(all_ips[-1])}"
#                 hops.append(final_hop)
#         else:
#             # Simple direct connection
#             if client_public_ip != server_public_ip:  # Only add if different IPs
#                 hop = f"by={quote(server_public_ip)}&for={quote(client_public_ip)}"
#                 hops.append(hop)

#         return ",".join(hops) if hops else ""

#     except Exception as e:
#         frappe.logger().error(f"Vendor-Forwarded Error: {str(e)}")
#         return ""


def get_license_ids():
    """Generate HMRC-compliant license IDs with proper encoding"""
    try:
        # 1. Get actual license key(s) from your system
        license_key = frappe.db.get_value("System Settings", "System Settings", "license_key") or ""
        
        # 2. If no license exists (e.g., open-source), use site-specific hash
        if not license_key:
            site_hash = hashlib.sha256(frappe.local.site.encode()).hexdigest()
            return f"erpnext={quote(site_hash)}"
        
        # 3. For paid licenses, use the actual key (hashed)
        return f"erpnext={quote(hashlib.sha256(license_key.encode()).hexdigest())}"
        
    except Exception:
        return ""  # Omit entirely if data is unavailable

# for single HOP
# def get_vendor_public_ip():
#     """
#     Automatically determine the public IP of YOUR backend/server infrastructure.
#     PRIORITY: Server's actual public IP > Proxy headers
#     """
#     try:
#         # 1️⃣ FIRST PRIORITY: Get server's actual public IP via external service
#         server_public_ip = get_server_public_ip()
#         if server_public_ip and is_public_ip(server_public_ip):
#             frappe.logger().debug(f"Using server public IP: {server_public_ip}")
#             return server_public_ip

#         # 2️⃣ SECONDARY: Check if we're behind a proxy/load balancer
#         # Only use these if they represent YOUR infrastructure, not client IPs
        
#         # Try X-Forwarded-Server (usually contains load balancer hostname/IP)
#         x_forwarded_server = frappe.local.request.headers.get('X-Forwarded-Server', '')
#         if x_forwarded_server and is_public_ip(x_forwarded_server):
#             frappe.logger().debug(f"Using X-Forwarded-Server IP: {x_forwarded_server}")
#             return x_forwarded_server

#         # 3️⃣ LAST RESORT: Check last hop in X-Forwarded-For (closest to your server)
#         x_forwarded_for = frappe.local.request.headers.get('X-Forwarded-For', '')
#         if x_forwarded_for:
#             ips = [ip.strip() for ip in x_forwarded_for.split(',') if ip.strip()]
#             if ips:
#                 last_proxy_ip = ips[-1]
#                 if is_public_ip(last_proxy_ip):
#                     frappe.logger().debug(f"Using last X-Forwarded-For IP: {last_proxy_ip}")
#                     return last_proxy_ip

#         frappe.logger().warning("Could not determine vendor public IP")
#         return ""

#     except Exception as e:
#         frappe.logger().error(f"Error in get_vendor_public_ip: {str(e)}")
#         return ""



def get_timezone():
    """Get timezone from client-side data in HMRC-compliant format"""
    try:
        client_info = frappe.session.data.get('client_info', {})
        offset = client_info.get('timezone_offset', 0)
        
        # Convert decimal offset to whole hours (HMRC requirement)
        hours = int(offset)  # Get integer hours
        minutes = int((abs(offset) - abs(hours)) * 60)  # Convert decimal to minutes
        
        # Handle positive and negative offsets
        sign = '+' if offset >= 0 else '-'
        
        # Format as UTC±HH:MM (HMRC compliant)
        return f"UTC{sign}{abs(hours):02d}:{minutes:02d}"
        
    except Exception:
        # Fallback to server timezone
        server_offset = -time.timezone // 3600
        return f"UTC{'+' if server_offset >=0 else '-'}{abs(server_offset):02d}:00"
        

# def generate_compliant_fallback_headers():
#     """Generate minimum valid headers when all else fails"""
#     timestamp = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
#     return {
#         "Gov-Client-Connection-Method": "WEB_APP_VIA_SERVER",
#         "Gov-Client-Device-ID": str(uuid.uuid4()),
#         "Gov-Client-Browser-JS-User-Agent": "",
#         "Gov-Client-Public-IP": "203.0.113.1",
#         "Gov-Client-Public-IP-Timestamp": timestamp,
#         "Gov-Client-Public-Port": "49152",
#         "Gov-Client-Screens": "width=1920&height=1080&scaling-factor=1&colour-depth=24",
#         "Gov-Client-Timezone": "UTC+00:00",
#         "Gov-Client-User-IDs": "erpnext=system",
#         "Gov-Client-Window-Size": "width=1920&height=1080",
#         "Gov-Client-Multi-Factor": "",
#         "Gov-Vendor-Forwarded": "by=203.0.113.1&for=203.0.113.1",
#         "Gov-Vendor-License-IDs": "erpnext=0000000000000000000000000000000000000000000000000000000000000000",
#         "Gov-Vendor-Product-Name": "ERPNext",
#         "Gov-Vendor-Public-IP": "203.0.113.1",
#         "Gov-Vendor-Version": "erpnext=1.0"
#     }

# def is_valid_ip(ip):
#     """Validate IPv4 address format"""
#     try:
#         socket.inet_pton(socket.AF_INET, ip)
#         return True
#     except:
#         return False

# console
# from zikpro_uk_vat.api import validate_fraud_headers
# validate_fraud_headers()

# @frappe.whitelist()
# def validate_fraud_headers():
#     try:
#         default_company = frappe.db.get_single_value("Global Defaults", "default_company")
#         if not default_company:
#             return {"success": False, "message": "No default company set"}
            
#         vat_settings = frappe.get_all("VAT Settings", filters={"company": default_company}, limit=1)
#         if not vat_settings:
#             return {"success": False, "message": "No VAT Settings found"}
            
#         vat_settings = frappe.get_doc("VAT Settings", vat_settings[0].name)

#         access_token = vat_settings.get_password("access_token")
#         if not access_token:
#             return {"success": False, "message": "No access token found"}

#         headers = {
#             "Authorization": f"Bearer {access_token}",
#             **get_fraud_prevention_headers()
#         }
        
#         response = requests.get(
#             "https://test-api.service.hmrc.gov.uk/test/fraud-prevention-headers/validate",
#             headers=headers,
#             timeout=30
#         )

#         if response.status_code == 401: 
#             refresh_result = refresh_access_token(vat_settings.name)
#             if not refresh_result.get("success"):
#                 return refresh_result
            
#             return validate_fraud_headers()
            
#         return {
#             "success": response.status_code == 200,
#             "status_code": response.status_code,
#             "response": response.json() if response.content else response.text
#         }

#     except Exception as e:
#         frappe.log_error("Header Validation Error", str(e))
#         return {"success": False, "message": f"Validation failed: {str(e)}"}

# for multiple HOP

def get_vendor_public_ip():
    """
    RELIABLE vendor public IP detection that works for both simple and complex setups
    """
    try:
        # Cache to avoid multiple calls
        if hasattr(frappe.local, 'cached_vendor_ip'):
            return frappe.local.cached_vendor_ip

        # 1️⃣ FIRST: Always try server's actual public IP (most reliable)
        server_ip = get_server_public_ip()
        if server_ip and is_public_ip(server_ip):
            frappe.local.cached_vendor_ip = server_ip
            frappe.logger().info(f"Vendor IP: Using server public IP: {server_ip}")
            return server_ip

        # 2️⃣ SECOND: Check if we have proxy infrastructure
        x_forwarded_for = frappe.local.request.headers.get('X-Forwarded-For', '')
        if x_forwarded_for:
            ips = [ip.strip() for ip in x_forwarded_for.split(',') if ip.strip()]
            if ips:
                last_ip = ips[-1]
                if is_public_ip(last_ip):
                    frappe.local.cached_vendor_ip = last_ip
                    frappe.logger().info(f"Vendor IP: Using last proxy IP: {last_ip}")
                    return last_ip

        # 3️⃣ THIRD: Infrastructure headers
        infrastructure_headers = ['X-Forwarded-Server', 'X-Real-IP', 'CF-Connecting-IP']
        for header in infrastructure_headers:
            ip = frappe.local.request.headers.get(header, '').split(':')[0].strip()
            if ip and is_public_ip(ip):
                frappe.local.cached_vendor_ip = ip
                frappe.logger().info(f"Vendor IP: Using {header}: {ip}")
                return ip

        # ❌ FAILED: Return empty instead of wrong IP
        frappe.logger().warning("Could not determine vendor public IP")
        return ""

    except Exception as e:
        frappe.logger().error(f"Vendor Public IP Error: {str(e)}")
        return ""

def get_server_public_ip():
    """
    More robust server IP detection with multiple fallbacks
    """
    # Cache to avoid multiple calls
    if hasattr(frappe.local, 'cached_server_ip'):
        return frappe.local.cached_server_ip
    
    services = [
        "https://api.ipify.org",
        "https://checkip.amazonaws.com",
        "https://icanhazip.com",
        "https://ident.me"
    ]
    
    for service in services:
        try:
            response = requests.get(service, timeout=5)
            if response.status_code == 200:
                ip = response.text.strip()
                if ip and is_public_ip(ip):
                    frappe.local.cached_server_ip = ip
                    frappe.logger().info(f"Detected server IP from {service}: {ip}")
                    return ip
        except Exception as e:
            frappe.logger().debug(f"Failed {service}: {e}")
            continue
    
    frappe.logger().error("All server IP detection methods failed")
    return None

def get_vendor_forwarded():
    """
    COMPLETE Vendor-Forwarded for BOTH simple AND multiple hops
    """
    try:
        # Get client IP
        client_ip = get_public_ip() 
        if not client_ip:
            client_ip = frappe.local.request.headers.get('Gov-Client-Public-IP', '')
        
        # Get server IP
        server_ip = get_vendor_public_ip()
        
        frappe.logger().info(f"Building Vendor-Forwarded - Client: {client_ip}, Server: {server_ip}")

        # ✅ VALIDATION
        if not client_ip or not is_public_ip(client_ip):
            frappe.logger().warning(f"Invalid client IP: {client_ip}")
            return ""
            
        if not server_ip or not is_public_ip(server_ip):
            frappe.logger().warning(f"Invalid server IP: {server_ip}")
            return ""

        # 🎯 DETECT PROXY CHAIN FOR MULTIPLE HOPS
        x_forwarded_for = frappe.local.request.headers.get('X-Forwarded-For', '')
        proxy_chain = [ip.strip() for ip in x_forwarded_for.split(',') if ip.strip() and is_public_ip(ip.strip())]
        
        # Remove client IP if accidentally in chain
        proxy_chain = [ip for ip in proxy_chain if ip != client_ip]
        
        hops = []
        
        if proxy_chain:
            # 🏗️ MULTI-HOP ARCHITECTURE DETECTED
            frappe.logger().info(f"Multi-hop detected: {len(proxy_chain)} proxies")
            
            # Build complete chain: Client → Proxy1 → Proxy2 → Server
            all_ips = [client_ip] + proxy_chain
            
            # Create hops for each connection
            for i in range(len(all_ips) - 1):
                if all_ips[i] != all_ips[i + 1]:
                    hop = f"by={quote(all_ips[i + 1])}&for={quote(all_ips[i])}"
                    hops.append(hop)
                    frappe.logger().debug(f"Hop {i+1}: {all_ips[i]} → {all_ips[i + 1]}")
            
            # Final hop: Last proxy → Your server
            if all_ips[-1] != server_ip:
                final_hop = f"by={quote(server_ip)}&for={quote(all_ips[-1])}"
                hops.append(final_hop)
                frappe.logger().debug(f"Final hop: {all_ips[-1]} → {server_ip}")
                
        else:
            # 🏠 SIMPLE DIRECT CONNECTION
            frappe.logger().info("Simple direct connection")
            hop = f"by={quote(server_ip)}&for={quote(client_ip)}"
            hops.append(hop)

        result = ",".join(hops)
        frappe.logger().info(f"Vendor-Forwarded result: {result}")
        return result

    except Exception as e:
        frappe.logger().error(f"Vendor-Forwarded Error: {str(e)}")
        return ""    

@frappe.whitelist()
def validate_fraud_headers():
    try:
        default_company = frappe.db.get_single_value("Global Defaults", "default_company")
        if not default_company:
            return {"success": False, "message": "No default company set"}
            
        vat_settings = frappe.get_all("VAT Settings", filters={"company": default_company}, limit=1)
        if not vat_settings:
            return {"success": False, "message": "No VAT Settings found"}
            
        vat_settings = frappe.get_doc("VAT Settings", vat_settings[0].name)

        access_token = vat_settings.get_password("access_token")
        if not access_token:
            return {"success": False, "message": "No access token found"}

        headers = {
            "Authorization": f"Bearer {access_token}",
            **get_fraud_prevention_headers()
        }

        # ✅ Log headers to console for debugging (visible in browser when you return them)
        if frappe.conf.developer_mode:
            frappe.msgprint(f"Fraud Headers:<br><pre>{frappe.as_json(headers)}</pre>")

        response = requests.get(
            "https://test-api.service.hmrc.gov.uk/test/fraud-prevention-headers/validate",
            headers=headers,
            timeout=30
        )

        if response.status_code == 401: 
            refresh_result = refresh_access_token(vat_settings.name)
            if not refresh_result.get("success"):
                return refresh_result
            
            return validate_fraud_headers()

        return {
            "success": response.status_code == 200,
            "status_code": response.status_code,
            "headers": headers,  # ✅ Add headers to response for browser console
            "response": response.json() if response.content else response.text
        }

    except Exception as e:
        frappe.log_error("Header Validation Error", str(e))
        return {"success": False, "message": f"Validation failed: {str(e)}"}
