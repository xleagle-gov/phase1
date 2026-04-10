import os
import requests
import json
import hashlib
import re
import time
from datetime import datetime

# Gemini API Configuration
# Multiple API keys from different Google Cloud projects

GEMINI_API_KEYS = [k.strip() for k in os.getenv('GEMINI_API_KEYS', '').split(',') if k.strip()]

# Old API keys (kept for reference)
# GEMINI_API_KEYS = [
#     "AIzaSyA05v1DtlGfVSkhkfyFOULoNYksSHm-fWw",
#     "AIzaSyAnA4U9EN63VhKr0AY-JWW0RWr_ZJoQRHE",
#     "AIzaSyB1gsVVKyUj3WGUCpuwkLRds10xyqM0Bmg",
#     "AIzaSyAcLEp4GZu00-ARY4s8CSqUYiAgst-fCEg",
#     "AIzaSyDYDqeef4y4FKXOnqlapjkfIwh2kICEfwA",
#     "AIzaSyB47JkWxTgFg_6hUtMAGRJkOOWqB3UgWyI",
#     "AIzaSyCacYEgC1OKS05FG0SLn0bLE9C-LDOLHw8",
#     "AIzaSyDKRW9tPPfYt64uK5zEhYNPMgXZbsUs8VY",
#     "AIzaSyDlRAIBbzEF8cDCwDX4qQoNgEMTDnYSr-4",
#     "AIzaSyCGkKH3sAPUMYgYiSPkSbv9sBAh_0NPQPM",
# ]

# Current key index for rotation
current_key_index = 0

# Track usage per key (for monitoring)
key_usage = {}

def load_api_keys_from_file(filename="api_keys.txt"):
    """Load API keys from a text file (one key per line)."""
    global GEMINI_API_KEYS
    try:
        with open(filename, 'r') as f:
            keys = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            if keys:
                GEMINI_API_KEYS = keys
                print(f"Loaded {len(keys)} API keys from {filename}")
                initialize_usage_tracking()
            else:
                print(f"No valid API keys found in {filename}")
    except FileNotFoundError:
        print(f"API keys file {filename} not found. Using default configuration.")

def initialize_usage_tracking():
    """Initialize usage tracking for all API keys."""
    global key_usage
    key_usage = {i: 0 for i in range(len(GEMINI_API_KEYS))}

def get_current_api_key():
    """Get the current API key, track usage, and rotate proactively for even distribution."""
    global current_key_index, key_usage
    key = GEMINI_API_KEYS[current_key_index]
    
    # Track usage
    if current_key_index not in key_usage:
        key_usage[current_key_index] = 0
    key_usage[current_key_index] += 1
    
    # Proactive round-robin: rotate after every call so requests spread evenly
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    
    return key

def rotate_api_key():
    """Rotate to the next API key."""
    global current_key_index
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    print(f"Rotated to API key {current_key_index + 1}/{len(GEMINI_API_KEYS)}")

def get_usage_stats():
    """Get usage statistics for all API keys."""
    total_usage = sum(key_usage.values())
    print(f"\nAPI Key Usage Statistics:")
    print(f"Total requests made: {total_usage}")
    for i, usage in key_usage.items():
        percentage = (usage / total_usage * 100) if total_usage > 0 else 0
        print(f"Key {i+1}: {usage} requests ({percentage:.1f}%)")
    print()

def add_api_key(new_key):
    """Add a new API key to the rotation."""
    global GEMINI_API_KEYS, key_usage
    GEMINI_API_KEYS.append(new_key)
    key_usage[len(GEMINI_API_KEYS) - 1] = 0
    print(f"Added new API key. Total keys: {len(GEMINI_API_KEYS)}")

# Initialize usage tracking
initialize_usage_tracking()

# Try to load keys from file on startup
load_api_keys_from_file()

MODEL = "gemini-3-flash-preview"  # Official model name
TEMPERATURE = 0.3
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# OpenAI fallback config
OPENAI_FALLBACK_MODEL = "gpt-5-mini"
MAX_429_BEFORE_FALLBACK = 2


def _call_openai_fallback(prompt_text, temperature=0.3):
    """Fallback to OpenAI gpt-5-mini when Gemini keys are exhausted (no web search)."""
    try:
        import openai
        from config import OPENAI_API_KEY

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        print(f"⚡ Falling back to OpenAI {OPENAI_FALLBACK_MODEL} (no web search)...")

        response = client.chat.completions.create(
            model=OPENAI_FALLBACK_MODEL,
            messages=[{"role": "user", "content": prompt_text}],
        )

        result = response.choices[0].message.content
        print(f"✅ OpenAI fallback returned {len(result)} chars")
        return result
    except Exception as e:
        print(f"❌ OpenAI fallback also failed: {e}")
        return None


def call_llm(prompt_text, temperature=TEMPERATURE, timeout=60,
             response_mime_type=None, max_retries=3,
             max_429_rotations=MAX_429_BEFORE_FALLBACK):
    """
    Centralized LLM call: tries Gemini first, falls back to OpenAI gpt-5-mini
    after max_429_rotations rate-limit (429) hits.

    Returns the raw text response, or None on complete failure.
    """
    headers = {"Content-Type": "application/json"}
    gen_config = {"temperature": temperature}
    if response_mime_type:
        gen_config["responseMimeType"] = response_mime_type

    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": gen_config,
    }

    rotation_count = 0

    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{BASE_URL}/{MODEL}:generateContent",
                params={"key": get_current_api_key()},
                headers=headers,
                json=payload,
                timeout=timeout,
            )

            if response.status_code == 200:
                response_json = response.json()
                return response_json["candidates"][0]["content"]["parts"][0]["text"]

            elif response.status_code == 429:
                rotation_count += 1
                print(f"Rate limit hit (429 #{rotation_count}). Rotating key...")
                rotate_api_key()
                if rotation_count >= max_429_rotations:
                    print(f"Hit {rotation_count} rate limits — switching to OpenAI fallback...")
                    return _call_openai_fallback(prompt_text, temperature)
                time.sleep(2)
                continue

            elif response.status_code in (500, 502, 503):
                rotation_count += 1
                print(f"Gemini server error ({response.status_code}). Rotating key...")
                rotate_api_key()
                if rotation_count >= max_429_rotations:
                    return _call_openai_fallback(prompt_text, temperature)
                time.sleep(2)
                continue

            else:
                print(f"Gemini API Error (Status {response.status_code}): {response.text}")
                return None

        except requests.exceptions.Timeout:
            rotation_count += 1
            print(f"Gemini timeout (#{rotation_count}). Rotating key...")
            rotate_api_key()
            if rotation_count >= max_429_rotations:
                return _call_openai_fallback(prompt_text, temperature)
            time.sleep(2)
            continue

        except requests.exceptions.RequestException as e:
            print(f"Request error: {e}")
            return _call_openai_fallback(prompt_text, temperature)

        except Exception as e:
            print(f"Unexpected error: {e}")
            return None

    print("All Gemini retries exhausted — trying OpenAI fallback...")
    return _call_openai_fallback(prompt_text, temperature)

# Default prompt for analyzing contract opportunities
DEFAULT_SYSTEM_PROMPT = """
You are an expert in government contracts and procurement analysis. Your task is to rigorously analyze government contract opportunities and determine their realistic feasibility specifically for a small, highly skilled 2-person LLC.

Our LLC consists of only two highly experienced professionals capable of independently managing detailed technical work, project management, and administrative tasks. However, we do not have internal heavy equipment, specialized vehicles, extensive labor force, or large infrastructure. While strategic subcontracting of limited portions is possible, reliance on extensive subcontracting or heavy resource mobilization should be considered infeasible.

Contracts should be marked feasible only if they:

Can be primarily executed by two skilled individuals without excessive external dependencies.

Require no significant investment in heavy equipment, specialized machinery, or extensive staffing.

Do not heavily depend on geographic proximity unless explicitly advantageous. They should also be set aside for small businesses. We are also neither veterans nor service disabled veterans or women owned small businesses.

Allow for strategic, limited subcontracting clearly within the logistical and financial capabilities of a small LLC.

contracts are not feasible if they :
1. Require a large investment in heavy equipment, specialized machinery, or extensive staffing.
2 They are not explicitly set aside for small businesses.



Evaluate the contract strictly against these criteria, erring on the side of caution. Your output must be structured as follows:

{
  "is_feasible": true/false,
  "reasoning": "explanation of the reasoning for the is_feasible value"
  
}
"""

DEFAULT_SYSTEM_PROMPTV2 = """
Role
 You are an expert in U.S. government contracting and procurement analysis with live web-search access.
 You advise a two-person Total Small Business Set-Aside team:
 • Avinash Nayak, Ph.D. – semiconductor,hardware compute, quality & product-reliability (Micron, AMD, Intel; 8D RCA, Lean/Six Sigma, supplier audits; AWS & Azure customer interface).
 • Abhiram Koganti – cloud-native & Gen-AI software engineer (serverless/Kubernetes; Go & Python back-end, React front-end; AWS & GCP DevOps; OpenAI/Whisper integrations).

Inputs you will receive
The contract-opportunity page text (e.g., SAM.gov) and downloadable files.

Analysis Tasks
You need to determine if a contract is feasible or not based on the ease of subcontracting and if its close to the team's expertise. The exact rules are given below:

Feasibility for Our Team  following these rules:
- Automatic NO if the opportunity is reserved for SDVOSB, WOSB, HUBZone, 8(a), etc., or if it is full-and-open (not small-business set-aside).
- Otherwise weigh:
  - Core fit with Avinash/Abhiram's expertise.
  - Ease of subcontracting (simple services like janitorial or COTS supplies vs. specialized tasks like replacing a ship rudder).
Evaluate the contract strictly against these criteria Your output must be structured as follows:

{
  "is_feasible": true/false,
  "reasoning": "explanation of the reasoning for the is_feasible value"
  
}
"""





# Email drafting system prompt
DRAFT_EMAIL_SYSTEM_PROMPT = """
You are an AI assistant that has over a decade of experience in government contract communications and have been successful in obtaining governmental contracts. Your task is to draft a professional introductory email expressing interest in a specific contract opportunity.
You will receive:
1.  Contract details extracted from its UI page (Title, Text Content).
2.  Extracted text content from relevant associated files (like PDFs, DOCX, TXT). This text might be truncated.
3.  Optionally, a target Point of Contact email address.
Instructions:
- Use the provided Contract Title and UI Page Text, along with the *extracted text from associated files*, for context about the opportunity.
- Maintain a professional and courteous tone throughout. Make it short and to the point. Don't be too verbose.
- *Output ONLY the complete email draft text.* Do not include introductory phrases like "Here is the email draft:". Start directly with the  the salutation.
- I have provided you what a draft should look like.


The link(you have to use this link in the email) to the solicitation is: <link>
You are an AI assistant that has over a decade of experience in government contract communications and have been successful in obtaining governmental contracts. Your task is to draft a professional introductory email expressing interest in a specific contract opportunity.
You will receive:
1.  Contract details extracted from its UI page (Title, Text Content).
2.  Extracted text content from relevant associated files (like PDFs, DOCX, TXT). This text might be truncated.
3.  Optionally, a target Point of Contact email address.
Instructions:
- Use the provided Contract Title and UI Page Text, along with the *extracted text from associated files*, for context about the opportunity.
- Maintain a professional and courteous tone throughout. Make it short and to the point. Don't be too verbose.
- *Output ONLY the complete email draft text.* Do not include introductory phrases like "Here is the email draft:". Start directly with the  the salutation.
- Dont make the email too long. Try to be concise and get to the point.
- I have provided you what a draft should look like. You need to ask a clarification question if you think one is needed. THe example only show where the clarification question would be asked.

[Upload content from the solicitation and the pdf/documents for .each prompt]
Subject: Submission of Interest – [Solicitation Number or Title] – XL Eagle
Hello [Primary Point of Contact],
I hope this message finds you well.
My name is Avinash Nayak, and I'm the Chief Operating Officer of XL Eagle, a small, family-owned LLC based in Austin, Texas. We are writing to express our interest in the government contract listed at the following link:
 [Insert full SAM.gov or official solicitation link here]
Based on the details provided in the solicitation, we believe XL Eagle is well-positioned to deliver the required services. We have reviewed the scope and requirements outlined in the solicitation documents and are preparing our response accordingly.
To help us move forward effectively, we would appreciate clarification on the following points:
[Clarification questions here]
Thank you for your time and for making this opportunity available. We look forward to the possibility of supporting your team on this contract and are happy to provide any additional documentation upon request.
Warm regards,
Avinash Nayak
Chief Operating Officer
XL Eagle
📧 avinash@xleagle.com / info@xleagle.com
📞 832-380-5845
UEI Code: K6SYWSWZAMY9


Here is some info about the company:
Avinash Nayak - full name COO - Chief Operating Officer XL Eagle - Company Name avinash@xleagle.com/info@xleagle.com 832-380-5845 - Phone Number UEI Code - K6SYWSWZAMY9
"""

def analyze_contract_text(text_content, custom_prompt=None, use_cache=True, max_retries=10):
    """
    Analyzes contract text using Gemini API.
    
    Args:
        text_content: The text from the contract opportunity UI page
        custom_prompt: Optional custom prompt to use instead of the default
        use_cache: Parameter kept for compatibility but not used
        max_retries: Maximum number of API key rotations to try
        
    Returns:
        The AI analysis response or error message
    """
    if not GEMINI_API_KEYS:
        return {"error": "No Gemini API keys configured."}
    
    if not text_content or len(text_content.strip()) < 10:
        return {"error": "Insufficient text content to analyze."}
    
    # Use custom prompt if provided, otherwise use default
    system_prompt = custom_prompt if custom_prompt else DEFAULT_SYSTEM_PROMPTV2
    
    # Truncate text if needed
    max_content_chars = 30000  # Adjustment for Gemini token limits
    truncated_text = text_content
    
    # Prepare the prompt
    full_prompt = f"{system_prompt}\n\nHere is the contract opportunity text to analyze:\n\n{truncated_text}"
    
    result_text = call_llm(full_prompt, temperature=TEMPERATURE,
                           response_mime_type="application/json", max_retries=max_retries)
    if result_text:
        try:
            output = json.loads(result_text)
            print(output)
            return output
        except json.JSONDecodeError as e:
            return {"error": f"Failed to parse response: {e}"}
    return {"error": "All API attempts exhausted (Gemini + OpenAI fallback)."}

def draft_contract_email(ui_data, extracted_files_text, target_email=None, uiLink=None):
    """
    Drafts an email using Gemini based on contract UI data and extracted text from files.

    Args:
        ui_data (dict): Dictionary containing 'title' and 'text_content'.
        extracted_files_text (str): Concatenated text extracted from associated files.
        target_email (str, optional): The email address of the point of contact.
        uiLink (str, optional): Link to the solicitation UI.

    Returns:
        str: The drafted email text or an error message starting with "Error:".
    """
    if uiLink is None:
        return "Error: UI Link is not provided."
        
    if not GEMINI_API_KEYS: # Changed from GEMINI_API_KEY to GEMINI_API_KEYS
        return "Error: No Gemini API keys found or configured."
    
    if not ui_data or 'title' not in ui_data or 'text_content' not in ui_data:
        return "Error: Insufficient UI data provided (missing title or text_content)."

    # Prepare the user prompt content for the email draft
    prompt_content = f"Contract Title: {ui_data.get('title', 'N/A')}\n\n"

    if target_email:
        prompt_content += f"Target Point of Contact Email: {target_email}\n\n"
    else:
        prompt_content += "No specific Point of Contact email provided.\n\n"

    # Include the UI page text (limit its size if necessary)
    max_ui_chars = 1000000  # Limit UI text separately if needed
    context_text = ui_data['text_content'][:max_ui_chars]
    if len(ui_data['text_content']) > max_ui_chars:
        context_text += "\n[... UI text truncated ...]"
    prompt_content += f"Contract UI Page Text (for context):\n---\n{context_text}\n---\n\n"

    # Include the extracted text from files
    if extracted_files_text:
        prompt_content += f"Extracted Text from Associated Files:\n---\n{extracted_files_text}\n---\n\n"
    else:
        prompt_content += "No text extracted from associated files (or no supported files found).\n\n"

    prompt_content += "Please draft the email now based on the system instructions, using context from both the UI page text and the extracted file text."
    
    # Replace the placeholder link in the prompt
    actual_prompt = DRAFT_EMAIL_SYSTEM_PROMPT.replace("<link>", uiLink)
    # print(actual_prompt)
    
    # Combined prompt for Gemini
    full_prompt = f"{actual_prompt}\n\n{prompt_content}"
    
    print("Sending request for email draft...")
    result_text = call_llm(full_prompt, temperature=0.5, timeout=120, max_retries=3)
    if result_text:
        print("Response received successfully.")
        return result_text.strip()
    return "Error: All API attempts exhausted (Gemini + OpenAI fallback)."

# def analyze_awarded_contract(ui_data, extracted_files_text, company_name, contract_title, uiLink):
#     """
#     Analyzes an awarded contract using Gemini API.
    
#     Args:
#         ui_data (dict): Dictionary containing 'title' and 'text_content'.   


    
    


def draft_contract_proposal(ui_data, extracted_files_text, company_name, contract_title, uiLink):
    """
    Use AI to draft a proposal for a contract opportunity.
    
    Args:
        ui_data (dict): Data extracted from the UI, contains title and text_content
        extracted_files_text (str): Text extracted from contract documents
        company_name (str): Name of the company submitting the proposal
        contract_title (str): Title of the contract
        uiLink (str): Link to the contract opportunity
        
    Returns:
        str: The generated proposal text
    """
    try:
        # Create prompt for the model
        prompt = f"""
You are an expert business proposal writer with experience in government contracts.
Draft a comprehensive proposal for the following contract opportunity.

CONTRACT TITLE: {contract_title}
OPPORTUNITY LINK: {uiLink}
COMPANY NAME: {company_name}

CONTRACT DESCRIPTION AND DETAILS:
{ui_data.get('text_content', 'No content available')}

ADDITIONAL DOCUMENT CONTENT:
{extracted_files_text or 'No additional documents available'}

Your task is to create a professional and persuasive proposal that addresses:

1. Executive Summary - Brief overview of what you're proposing and why {company_name} is the best choice
2. Understanding of Requirements - Show clear understanding of what the contract requires
3. Proposed Solution - Detailed description of how {company_name} will meet these requirements
4. Company Capabilities - Why {company_name} is qualified to deliver this work (experience, expertise, etc.)
5. Timeline and Implementation Plan - How and when the work will be accomplished
6. Budget or Cost Structure - General pricing approach (if appropriate)
7. Conclusion - Final pitch for why {company_name} should be awarded this contract

Make the proposal specific to this opportunity and highlight how {company_name} addresses the specific needs mentioned in the contract documents.
Ensure the proposal is well-structured with clear headings for each section.
"""

        # Call your AI model - I'm assuming you have a function like this in your gemini module
        # response = generate_response(prompt)
        file1=open("proposal.txt","w")
        file1.write(prompt)
        file1.close()
        return "Proposal saved to proposal.txt"
        # Return the generated response
        return response
    
    except Exception as e:
        return f"Error: Failed to generate proposal: {str(e)}"

# You may need to implement or modify this function depending on your gemini module structure
def generate_response(prompt):
    """Call the Gemini model to generate a response based on the prompt."""
    # This is a placeholder - you should replace with your actual implementation
    # that calls your preferred AI model (Gemini/OpenAI/etc.)
    
    # Example with Gemini:
    from google.generativeai import configure, generate_text
    
    # Configure with your API key
    configure(api_key="YOUR_GEMINI_API_KEY")
    
    # Generate response
    result = generate_text(prompt=prompt)
    
    # Return the generated text
    return result.text

def analyze_parts_procurement(text_content, ui_link=None, custom_prompt=None, use_cache=True):
    """
    Analyzes contract text to identify parts procurement needs and find suppliers/pricing.
    Uses Gemini API with web search to ground responses with online information.
    
    Args:
        text_content: The text from the contract opportunity UI page and documents
        ui_link: Optional link to the contract opportunity
        custom_prompt: Optional custom prompt to use instead of the default
        use_cache: Whether to use cached responses
        
    Returns:
        String containing the complete JSON response
    """
    if not GEMINI_API_KEYS: # Changed from GEMINI_API_KEY to GEMINI_API_KEYS
        return {"error": "No Gemini API keys configured."}
    
    if not text_content or len(text_content.strip()) < 10:
        return {"error": "Insufficient text content to analyze."}
    
    # Procurement analysis system prompt
    PROCUREMENT_SYSTEM_PROMPT = """
**Contract Type Determination**  
   First identify if this is:  
   - (Goods) Requires physical products/equipment procurement  
   - (Services) Requires labor/specialized service providers  
  
  2. if it required a labour/serivce, return a list of suppliers that can provide the service(preferably small businesses include their website and contact)
  3. if it required a physical product, return a list of suppliers that can provide the product(include website and contact). For the product,Also return a quote if you can find it online
  4. if it required a combination of both, return a  list of suppliers for both
  the output should be in the following format:
  {"suppliers":[
    {
      "supplier_name": "Supplier Name",
      "supplier_website": "Supplier Website",
      "supplier_contact": "Supplier Contact",
      "product_quote": "Product Quote"
    }
  ],"products(if any)":[
    {
      "product_name": "Product Name",
      "product_quote": "Product Quote"
    }
  ],"services(if any)":[
    {
      "service_name": "Service Name",
      "service_quote": "Service Quote"
    }
  ]}


"""
    
    # Use custom prompt if provided, otherwise use default
    system_prompt = PROCUREMENT_SYSTEM_PROMPT
    
    # Truncate text if needed
    max_content_chars = 30000  # Adjustment for Gemini token limits
    truncated_text = text_content
    
    # Add the UI link to the prompt if available
    link_text = f"\nContract UI Link: {ui_link}" if ui_link else ""
    
    # Prepare the prompt
    full_prompt = f"{system_prompt}\n\nHere is the contract opportunity text to analyze:{link_text}\n\n{truncated_text}"
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{
                "text": full_prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.2
        },
        "tools": [{
            "google_search": {}  # Correct syntax for enabling Google Search grounding
        }]
    }
    
    file1=open("prompt.txt","w")
    file1.write(full_prompt)
    file1.close()
    
    try:
        print("Sending procurement analysis request to Gemini API with web search...")
        response = requests.post(
            f"{BASE_URL}/{MODEL}:generateContent",
                        params={"key": get_current_api_key()},
            headers=headers,
            json=payload
        )
        
        if response.status_code == 200:
            response_json = response.json()
            
            # Extract the result text
            # result_text = response_json['candidates'][0]['content']['parts'][0]['text']for for final text
            finalText=""
            for i in response_json['candidates']:
                # finalText+=i['content']['parts'][0]['text']
                for part in i['content']['parts']:
                    # print(part['text'])
                    try:
                        output=json.loads(part['text'])
                        finalText+=output
                    except:
                        finalText+=part['text']

            
            # Simply return the full response text
            print(finalText)
            return finalText
        else:
            error_msg = f"Gemini API Error (Status {response.status_code}): {response.text}"
            print(error_msg)
            return {"error": error_msg}
            
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        return {"error": f"Failed to connect to Gemini API: {str(e)}"}
    
    except Exception as e:
        print(f"Unexpected error: {e}")
        return {"error": f"Unexpected error occurred: {str(e)}"}


def classify_fsc_5330_item(nsn: str, use_cache: bool = False, max_retries: int = 5) -> dict:
    """
    Classify an NSN under FSC 5330 and return both the gasket/seal determination and
    the overall material composition including whether it is metallic or non-metallic.

    Args:
        nsn: National Stock Number string (with or without dashes)
        use_cache: If True, read/write a simple JSON cache under `cache/fsc5330_classification.json`
        max_retries: Number of API attempts with key rotation on 429

    Returns:
        A dictionary with the schema:
        {
          "item_type": "gasket" | "seal" | "unknown",
          "material_composition": "string (e.g., PTFE, Buna-N rubber, silicone)",
          "is_metallic": true | false | null,
          "reasoning": "brief justification",
          "source_urls": ["url1", ...]
        }
    """
    if not nsn or len(nsn.strip()) == 0:
        return {
            "item_type": "unknown",
            "material_composition": "unknown",
            "is_metallic": None,
            "reasoning": "no nsn provided",
            "source_urls": []
        }

    normalized_nsn = nsn.strip()

    # Simple JSON cache
    cache_dir = os.path.join(os.getcwd(), "cache")
    cache_file = os.path.join(cache_dir, "fsc5330_classification.json")
    cache_data = {}

    if use_cache:
        try:
            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as cf:
                    cache_data = json.load(cf)
                if normalized_nsn in cache_data:
                    cached_value = cache_data[normalized_nsn]
                    # Backward compatibility: older cache stored a string like "non-metallic gasket"
                    if isinstance(cached_value, str):
                        cached_lower = cached_value.lower()
                        item_type = (
                            "gasket" if "gasket" in cached_lower else (
                                "seal" if "seal" in cached_lower else "unknown"
                            )
                        )
                        is_metallic = (False if "non-metallic" in cached_lower else (True if "metallic" in cached_lower else None))
                        return {
                            "item_type": item_type,
                            "material_composition": "unknown",
                            "is_metallic": is_metallic,
                            "reasoning": "returned from legacy cache string",
                            "source_urls": []
                        }
                    if isinstance(cached_value, dict):
                        # Ensure required keys exist
                        return {
                            "item_type": str(cached_value.get("item_type", "unknown")).lower(),
                            "material_composition": str(cached_value.get("material_composition", "unknown")),
                            "is_metallic": cached_value.get("is_metallic", None),
                            "reasoning": str(cached_value.get("reasoning", "cached result")),
                            "source_urls": list(cached_value.get("source_urls", []))
                        }
        except Exception:
            # Ignore cache errors
            cache_data = {}

    if not GEMINI_API_KEYS:
        return {
            "item_type": "unknown",
            "material_composition": "unknown",
            "is_metallic": None,
            "reasoning": "no api keys configured",
            "source_urls": []
        }

    system_prompt = (
        "You are classifying an NSN from FSC 5330 (Packing and Gasket Materials). "
        "Using web search, determine whether the specific NSN is a 'gasket' or a 'seal'. "
        "Also determine the overall material composition (e.g., PTFE, Buna-N rubber, silicone, cork, copper) "
        "and whether the item is metallic or non-metallic. If neither type is clearly supported by sources, return 'unknown'.\n\n"
        "Guidelines:\n"
        "- Search trusted sources (DLA/DIBBS, NSN catalogs, vendor listings).\n"
        "- Prefer descriptions that explicitly say gasket vs seal and indicate materials.\n"
        "- Treat rubber, elastomer, PTFE, silicone, Buna-N, neoprene, etc., as non-metallic.\n"
        "- Treat steel, copper, aluminum, nickel, and other metals/alloys as metallic.\n"
        "- Output strictly the following JSON schema.\n\n"
        "{\n  \"item_type\": \"gasket\" | \"seal\" | \"unknown\",\n"
        "  \"material_composition\": \"string\",\n"
        "  \"is_metallic\": true | false | null,\n"
        "  \"reasoning\": \"brief justification\",\n  \"source_urls\": [\"url1\", \"url2\"]\n}"
    )

    user_prompt = f"NSN: {normalized_nsn}\nFSC: 5330"

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [
                {"text": system_prompt},
                {"text": user_prompt}
            ]
        }],
        "generationConfig": {
            "temperature": 0.1
        },
        "tools": [
            {"google_search": {}}
        ]
    }

    allowed_types = {"gasket", "seal", "unknown"}

    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{BASE_URL}/{MODEL}:generateContent",
                        params={"key": get_current_api_key()},
                headers=headers,
                json=payload,
                timeout=60
            )

            if response.status_code == 200:
                response_json = response.json()
                try:
                    result_text = response_json["candidates"][0]["content"]["parts"][0]["text"]
                except Exception:
                    return {
                        "item_type": "unknown",
                        "material_composition": "unknown",
                        "is_metallic": None,
                        "reasoning": "no text in response",
                        "source_urls": []
                    }

                # Parse JSON
                parsed: dict
                try:
                    parsed = json.loads(result_text)
                except Exception:
                    # Try to find a JSON substring
                    try:
                        start = result_text.find("{")
                        end = result_text.rfind("}")
                        parsed = json.loads(result_text[start:end+1]) if start != -1 and end != -1 else {}
                    except Exception:
                        parsed = {}

                item_type = str(parsed.get("item_type", "unknown")).strip().lower()
                if item_type not in allowed_types:
                    item_type = "unknown"

                material = str(parsed.get("material_composition", "unknown")).strip() or "unknown"
                is_metallic_val = parsed.get("is_metallic", None)
                # Normalize is_metallic to True/False/None
                if isinstance(is_metallic_val, str):
                    lowered = is_metallic_val.strip().lower()
                    if lowered in {"true", "yes", "metal", "metallic"}:
                        is_metallic_val = True
                    elif lowered in {"false", "no", "non-metal", "nonmetal", "non-metallic"}:
                        is_metallic_val = False
                    else:
                        is_metallic_val = None
                elif not isinstance(is_metallic_val, bool):
                    is_metallic_val = None

                result_obj = {
                    "item_type": item_type,
                    "material_composition": material,
                    "is_metallic": is_metallic_val,
                    "reasoning": str(parsed.get("reasoning", "")).strip(),
                    "source_urls": list(parsed.get("source_urls", [])) if isinstance(parsed.get("source_urls", []), list) else []
                }

                # Write-through cache
                if use_cache:
                    try:
                        if not os.path.isdir(cache_dir):
                            os.makedirs(cache_dir, exist_ok=True)
                        cache_data[normalized_nsn] = result_obj
                        with open(cache_file, "w", encoding="utf-8") as cf:
                            json.dump(cache_data, cf, indent=2)
                    except Exception:
                        pass

                return result_obj

            elif response.status_code == 429:
                print(f"Rate limit hit on API key {current_key_index + 1}. Rotating to next key...")
                rotate_api_key()
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2)
                continue
            else:
                print(f"Gemini API Error (Status {response.status_code}): {response.text}")
                return {
                    "item_type": "unknown",
                    "material_composition": "unknown",
                    "is_metallic": None,
                    "reasoning": f"api error {response.status_code}",
                    "source_urls": []
                }
        except requests.exceptions.RequestException as e:
            print(f"Request error during FSC 5330 classification: {e}")
            return {
                "item_type": "unknown",
                "material_composition": "unknown",
                "is_metallic": None,
                "reasoning": "request exception",
                "source_urls": []
            }
        except Exception as e:
            print(f"Unexpected error during FSC 5330 classification: {e}")
            return {
                "item_type": "unknown",
                "material_composition": "unknown",
                "is_metallic": None,
                "reasoning": "unexpected exception",
                "source_urls": []
            }

    return {
        "item_type": "unknown",
        "material_composition": "unknown",
        "is_metallic": None,
        "reasoning": "exhausted retries",
        "source_urls": []
    }

def isFeasible(textContent):
    prompt='''determine has been answered as is_feasible: true or false your output
    should be in the following format:
    {
      "is_feasible": true/false,
      "reasoning": "explanation of the reasoning for the is_feasible value"
      
    }
    '''
    prompt=prompt+textContent
    # response=generate_response(prompt)
    payload={
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": TEMPERATURE,
            "responseMimeType": "application/json"
        }
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(
        f"{BASE_URL}/{MODEL}:generateContent",
                        params={"key": get_current_api_key()},
        headers=headers,
        json=payload,
        timeout=60
    )
    # print(response.text)
    output=response.json()
    isFeasibleOutput=(output['candidates'][0]['content']['parts'][0]['text'])
    # print(isFeasibleOutput)
    isFeasible=json.loads(isFeasibleOutput)['is_feasible']
    return isFeasible
# output=isFeasible('''What the Government Wants  
# The 763d Enterprise Sourcing Squadron (Air Force) needs twenty-five (25) replacement "USB-C to ODU" cables that are 18 inches long.  These cables connect modern USB-C devices to rugged ODU military-style circular connectors and are used by the 20th Air Support Operations Squadron for communications and data transfer during training in garrison as well as at forward operating locations.  The purchase will be made under Simplified Acquisition Procedures (FAR Part 13) and is solicited as a commercial-item buy.  Offerors must:  
# • Complete a one-page Pricing Worksheet showing unit and total price, and proposed delivery lead time (in days).  
# • Fill in the standard FAR/DFARS representations and certifications contained in Attachment 2.  
# • Provide, on company letterhead, a cover letter that (a) keeps the offer valid until 30 May 2025 and (b) includes technical product specifications demonstrating that the offered cable meets the government's specification in Attachment 3 (electrical pin-out, shielding, durability, etc.).  
# Proposals are due by e-mail NLT 30 April 2025, 1200 CST.  Place of performance / delivery is Fort Drum, NY 13602.  The requirement is open only to small-business concerns; no other socioeconomic program is cited.  Award will be a single fixed-price purchase order.  [1]

# Feasibility for Our Team – YES  
# Rationale:  
# • Set-aside status: The memorandum is addressed to "ALL SMALL BUSINESS CONCERNS" with no SDVOSB, HUBZone, 8(a), or WOSB restriction, so our Total Small Business qualifies. [1]  
# • Core fit: Although Avinash and Abhiram's main specialties are semiconductors and cloud software, supplying finished COTS or build-to-print cable assemblies is straightforward and requires no unique facility clearance or specialized labor.  
# • Ease of subcontracting: Custom USB-C↔ODU assemblies are commercially available from ODU and several authorized cable houses (e.g., MilesTek, PEI-Genesis); we can buy and resell or have them drop-ship within typical 4-6 week lead times. [2] This is much simpler than complex depot-level hardware work.  ''')
# print(output)
# analyze_contract_text("Sample contract text for analysis")

def filter_vendor_relevant_content(complete_text, max_retries=3):
    """
    Filters complete contract text to retain only information relevant to vendors for outreach.
    Uses Gemini API to intelligently extract vendor-focused content.
    
    Args:
        complete_text (str): The complete raw text from all contract files
        max_retries (int): Maximum number of API key rotations to try
        
    Returns:
        str: Filtered text containing only vendor-relevant information, or original text if filtering fails
    """
    if not GEMINI_API_KEYS:
        print(f"WARNING: No Gemini API keys configured. Returning original text")
        return complete_text
    
    if not complete_text or len(complete_text.strip()) < 50:
        print(f"WARNING: Text too short to filter. Returning original.")
        return complete_text
    
    # Vendor-focused filter prompt
    VENDOR_FOCUSED_FILTER_PROMPT = """
You are an expert in government contracting who specializes in identifying information that vendors need for business development and outreach purposes.

Your task is to filter contract documents to extract ONLY information that would be valuable to a vendor who wants to:
1. Understand the opportunity and requirements
2. Reach out to the contracting officer or prime contractor
3. Determine if they can provide the goods/services needed
4. Prepare for potential partnerships or subcontracting

INCLUDE but not limited to:
- Contract title and solicitation number
- What goods/services are being procured (brief description)
- Quantities and specifications (key requirements only)
- Key dates (proposal due dates, performance periods)
- Primary contact information (contracting officer, points of contact)
- Prime contractor information (if this is a subcontracting opportunity)
- Set-aside information (small business, SDVOSB, etc.)
- Performance location
- Contract value/budget (if mentioned)
- Key technical requirements that vendors need to know
- Vendor qualification requirements
- Security clearance requirements (if any)

EXCLUDE:
- Detailed legal boilerplate and FAR clauses
- Extensive administrative procedures
- Detailed proposal formatting requirements
- Repetitive compliance language
- Internal government processes
- Excessive technical specifications beyond what's needed for initial vendor assessment
- Long lists of standard certifications
- Detailed evaluation criteria beyond the basics

IMPORTANT: Focus on information a vendor would need to:
- Quickly assess if this is relevant to their capabilities
- Contact the right people
- Understand the basic scope and timeline
- Determine partnership opportunities

Keep the output  comprehensive for vendor outreach purposes.IT is very important to include all the information and not miss any information.
"""
    
    # Truncate text if too long for API
    max_content_chars = 900000
    if len(complete_text) > max_content_chars:
        truncated_text = complete_text[:max_content_chars] + "\n[... content truncated for processing ...]"
        print(f"WARNING: Content truncated from {len(complete_text)} to {max_content_chars} characters")
    else:
        truncated_text = complete_text
    
    # Prepare the full prompt
    full_prompt = f"{VENDOR_FOCUSED_FILTER_PROMPT}\n\nCONTRACT DOCUMENTS TO FILTER FOR VENDOR OUTREACH:\n\n{truncated_text}"
    
    print(f"Filtering content for vendor outreach...")
    filtered_text = call_llm(full_prompt, temperature=0.1, timeout=120, max_retries=max_retries)
    
    if filtered_text:
        filtered_text = filtered_text.strip()
        if len(filtered_text) < 20:
            print(f"WARNING: Filtered content too short. Using original text.")
            return complete_text
        print(f"SUCCESS: Filtered for vendor outreach from {len(complete_text)} to {len(filtered_text)} characters")
        return filtered_text
    
    print(f"WARNING: Failed to filter for vendor outreach after {max_retries} attempts. Returning original text.")
    return complete_text

def extract_sam_metadata(ui_text, max_retries=10):
    """
    Use Gemini to extract key metadata from SAM UI text.
    Returns a dict with extracted information and deadline flags.
    Enhanced with retry mechanism for transient errors like 503.
    """
    result = {
        "notice_id": None,
        "department": None,
        "due_datetime_iso": None,
        "is_past_deadline": False,
        "is_due_within_3_days": False,
        "is_dla": False,
        "is_dibbs": False,
    }

    if not GEMINI_API_KEYS:
        print("No Gemini API keys configured.")
        return result
    # print(ui_text)
    if not ui_text or len(ui_text.strip()) < 10:
        print("Insufficient text content to analyze.")
        return result

    # Gemini system prompt for metadata extraction
    system_prompt = """
Extract the following information from this SAM.gov contract opportunity text:

1. Notice ID
2. Department/Agency name
3. Date Offers Due (or Inactive Dates as fallback)
4. Whether this is a DLA (Defense Logistics Agency) contract
5. Whether this mentions DIBBS (DLA Internet Bid Board System)
6. whether there are controlled attachments (only if there are files whose Access is not public must be explicit)

For the due date, determine:
- Is it past the current date/time?
- Is it due within 3 days from now?
 the current date  is -cuurentDate-
Return ONLY a JSON object with this exact structure:
{
  "notice_id": "string or null",
  "department": "string or null", 
  "due_datetime_iso": "ISO datetime string or null",
  "is_past_deadline": true/false,
  "is_due_within_3_days": true/false,
  "is_dla": true/false,
  "is_dibbs": true/false,
  "explaination for the values": "string or null",
  "controlled_attachments": true/false
}
"""

    # Truncate text if needed
    currentDate = datetime.now().strftime('%Y-%m-%d')
    system_prompt = system_prompt.replace("-cuurentDate-", currentDate)
    max_content_chars = 300000
    truncated_text = ui_text[:max_content_chars] if len(ui_text) > max_content_chars else ui_text

    full_prompt = f"{system_prompt}\n\nSAM.gov Contract Text:\n{truncated_text}"

    print("Extracting SAM metadata...")
    result_text = call_llm(full_prompt, temperature=TEMPERATURE,
                           response_mime_type="application/json", max_retries=max_retries)
    if result_text:
        try:
            parsed_result = json.loads(result_text)
            print(parsed_result)
            result.update(parsed_result)
            return result
        except json.JSONDecodeError as e:
            print(f"Failed to parse SAM metadata response: {e}")
            return result
    return result

def filter_important_content(filename, extracted_text, max_retries=3):
    """
    Filters extracted text from contract files to retain only important information.
    Uses Gemini API to intelligently extract and preserve all critical content.
    
    Args:
        filename (str): Name of the file being processed
        extracted_text (str): The raw extracted text from the file
        max_retries (int): Maximum number of API key rotations to try
        
    Returns:
        str: Filtered text containing only important information, or original text if filtering fails
    """
    if not GEMINI_API_KEYS:
        print(f"WARNING: No Gemini API keys configured. Returning original text for {filename}")
        return extracted_text
    
    if not extracted_text or len(extracted_text.strip()) < 50:
        print(f"WARNING: Text too short to filter for {filename}. Returning original.")
        return extracted_text
    
    # System prompt that emphasizes preserving ALL important information
    CONTENT_FILTER_PROMPT = """
You are an expert document analyzer specializing in government contract documents. Your task is to filter and extract ONLY the important information from contract-related documents while ensuring NO CRITICAL INFORMATION IS LOST.

CRITICAL REQUIREMENT: You must preserve ALL important information. It's better to include too much than to miss something critical.

Important information includes:
- Contract requirements, specifications, and deliverables
- Technical specifications and performance criteria  
- Pricing information, quantities, and financial details
- Deadlines, timelines, and delivery requirements
- Contact information and points of contact
- Vendor/contractor requirements and qualifications
- Compliance requirements and certifications needed
- Scope of work and statement of work details
- Terms and conditions
- Submission requirements and procedures
- Evaluation criteria
- Security clearance requirements
- Geographic or location-specific requirements
- Quality standards and testing requirements
- Warranty and maintenance requirements

Information to EXCLUDE (only if clearly not relevant):
- Repetitive boilerplate legal text that doesn't add specific requirements
- Generic FAR clauses that don't specify unique requirements for this contract
- Excessive formatting artifacts or OCR errors
- Duplicate information that appears multiple times identically

INSTRUCTIONS:
1. Analyze the document content carefully
2. Extract and preserve ALL information that could be relevant to understanding or responding to the contract
3. Maintain the logical flow and organization of information
4. If unsure whether something is important, INCLUDE IT
5. Return the filtered content in a clear, organized format
6. Do not add commentary or explanations - just return the filtered content

Remember: Missing important information could cost the opportunity. Be comprehensive in what you preserve.
"""
    
    # Prepare the full prompt
    full_prompt = f"{CONTENT_FILTER_PROMPT}\n\nFILE NAME: {filename}\n\nDOCUMENT CONTENT TO FILTER:\n\n{extracted_text}"
    
    print(f"Filtering content for {filename}...")
    filtered_text = call_llm(full_prompt, temperature=0.1, timeout=120, max_retries=max_retries)
    
    if filtered_text:
        filtered_text = filtered_text.strip()
        if len(filtered_text) < 20:
            print(f"WARNING: Filtered content too short for {filename}. Using original text.")
            return extracted_text
        print(f"SUCCESS: Filtered {filename} from {len(extracted_text)} to {len(filtered_text)} characters")
        return filtered_text
    
    print(f"WARNING: Failed to filter {filename}. Returning original text.")
    return extracted_text

def draft_manufacturer_quote_request(nsn_code, part_number, manufacturer_name, quantity, lead_time):
    """
    Drafts a professional email to request a quote from a manufacturer.
    
    Args:
        nsn_code (str): The NSN code of the item
        part_number (str): The part number of the item
        manufacturer_name (str): The name of the manufacturer
        quantity (str): The quantity required
        lead_time (str): The required lead time
        
    Returns:
        str: The drafted email text or an error message starting with "Error:"
    """
    if not GEMINI_API_KEYS: # Changed from GEMINI_API_KEY to GEMINI_API_KEYS
        return "Error: No Gemini API keys found or configured."
    
    prompt = f"""
    You are an expert in government procurement and manufacturer communications.
    Draft a concise, professional email to request a quote for the following item:
    
    NSN Code: {nsn_code}
    Part Number: {part_number}
    Manufacturer: {manufacturer_name}
    Quantity: {quantity}
    Lead Time: {lead_time}
    
    Requirements:
    1. Keep the email brief and to the point
    2. Include all provided details
    3. Request best price and delivery timeline
    4. Subject line should include NSN and Part Number
    5. make the lead tighter by 30% but dont mention it in the email
    6. start the email with Hello,
    6. End the email with the following signature:
    
    Best regards,
    Avinash Nayak, PhD
    XL Eagle LLC
    info@xleagle.com
    (832) 380-5845
    cage code: 11R42
    
    Format the response as:
    Subject: [Subject line]
    
    [Email body]
    """
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.3
        }
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/{MODEL}:generateContent",
                        params={"key": get_current_api_key()},
            headers=headers,
            json=payload,
            timeout=120
        )
        
        if response.status_code == 200:
            response_json = response.json()
            print(response_json)
            return response_json['candidates'][0]['content']['parts'][0]['text'].strip()
        else:
            error_msg = f"Gemini API Error (Status {response.status_code}): {response.text}"
            print(error_msg)
            return f"Error: {error_msg}"
            
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        return f"Error: Failed to connect to Gemini API: {str(e)}"
    
    except Exception as e:
        print(f"Unexpected error: {e}")
        return f"Error: Unexpected error occurred: {str(e)}"

def format_email_draft_with_signature(draft_body, sender_email=None, max_retries=3):
    """
    Format an email draft to ensure it has the proper signature and opt-out line.
    Returns HTML formatted output.
    
    Args:
        draft_body (str): The current draft body text
        sender_email (str): The email address of the sender (to determine which signature to use)
        max_retries (int): Maximum number of API retries
        
    Returns:
        dict: {'success': bool, 'formatted_html': str, 'error': str}
    """
    if not GEMINI_API_KEYS:
        return {
            'success': False,
            'formatted_html': None,
            'error': 'No Gemini API keys configured'
        }
    
    if not draft_body or len(draft_body.strip()) < 10:
        return {
            'success': False,
            'formatted_html': None,
            'error': 'Draft body too short'
        }
    
    # Determine which signature to use based on sender email domain
    is_nexan = sender_email and 'nexan' in sender_email.lower()
    
    if is_nexan:
        signature_block = """Thanks,

Avinash Nayak
Chief Operating Officer
Nexan
info@thenexan.com
2021 Guadalupe St, Suite 260, Austin, TX 78705
www.thenexan.com"""
    else:
        signature_block = """Thanks,

Avinash Nayak
Chief Operating Officer
XL Eagle
info@xleagle.com
2021 Guadalupe St, Suite 260, Austin, TX 78705
www.xleaglegov.com"""
    
    # System prompt for draft formatting
    system_prompt = f"""You are an expert email formatter and editor. Your job is to rewrite and format the email draft for clarity, grammar, and professionalism while preserving every detail, fact, and meaning from the original draft.

                    RULES:

                    Do NOT delete, omit, or summarize any content.

                    Do NOT add any new ideas or facts not present in the original draft.

                    You ARE allowed to rewrite poorly written or awkward sentences to improve clarity, flow, and professionalism.

                    Keep all original information, intent, and details exactly the same.

                    Format the result in clean, professional HTML using <p> and <br> tags.

                    The final email MUST include:

                    Opt-out line (insert only if missing):
                    "If these sorts of requests aren't a fit, reply and we'll remove you from future quote requests."

                    Signature block in this exact format:

                    {signature_block}


                    If an opt-out line already exists (even slightly different), keep it.

                    If a signature exists but does not match the required format, replace it with the correct block.

                    YOUR TASK:

                    Take the original email draft exactly as provided.

                    Rewrite sentences ONLY for clarity, grammar, and professionalism.

                    Preserve ALL details and meaning.

                    Add the required opt-out line and signature if missing.

                    Return ONLY the final HTML email — no explanations.
"""

    full_prompt = f"{system_prompt}\n\nEMAIL DRAFT TO FORMAT:\n\n{draft_body}"
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{
                "text": full_prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.2
        }
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{BASE_URL}/{MODEL}:generateContent",
                        params={"key": get_current_api_key()},
                headers=headers,
                json=payload,
                timeout=60
            )
            # print(response.text)
            
            if response.status_code == 200:
                response_json = response.json()
                formatted_html = response_json['candidates'][0]['content']['parts'][0]['text'].strip()
                
                return {
                    'success': True,
                    'formatted_html': formatted_html,
                    'error': None
                }
                
            elif response.status_code == 429:  # Rate limit
                print(f"Rate limit hit. Rotating API key...")
                rotate_api_key()
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2)
                continue
                
            else:
                error_msg = f"Gemini API Error (Status {response.status_code})"
                print(error_msg)
                return {
                    'success': False,
                    'formatted_html': None,
                    'error': error_msg
                }
                
        except requests.exceptions.Timeout:
            print(f"Request timeout (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                continue
            else:
                return {
                    'success': False,
                    'formatted_html': None,
                    'error': 'Request timeout'
                }
                
        except Exception as e:
            print(f"Error formatting draft: {e}")
            return {
                'success': False,
                'formatted_html': None,
                'error': str(e)
            }
    
    return {
        'success': False,
        'formatted_html': None,
        'error': 'Max retries exceeded'
    }

def has_site_visit(solicitation_text, max_retries=3, check_in_person_submission=False):
    """
    Determines if a solicitation requires a site visit or mandatory pre-proposal conference,
    whether it has controlled (non-public) attachments, optionally whether it only accepts
    in-person submission, and whether the actual bid documents are missing from what we have.

    Args:
        solicitation_text (str): Combined text from the SAM.gov page and downloaded files
        max_retries (int): Maximum number of API key rotations to try
        check_in_person_submission (bool): If True, also check for in-person-only submission and
                                           missing bid documents (used for local contracts)

    Returns:
        dict: {
            "has_site_visit": bool,
            "has_controlled_attachments": bool,
            "requires_in_person_submission": bool,
            "missing_bid_documents": bool,
            "external_documents_url": str or None,
            "reasoning": str
        }
    """
    default_result = {
        "has_site_visit": False,
        "has_controlled_attachments": False,
        "requires_in_person_submission": False,
        "missing_bid_documents": False,
        "external_documents_url": None,
        "reasoning": ""
    }

    if not GEMINI_API_KEYS:
        default_result["reasoning"] = "No API keys configured, defaulting to not skip"
        return default_result

    if not solicitation_text or len(solicitation_text.strip()) < 50:
        default_result["reasoning"] = "Insufficient text to analyze"
        return default_result

    in_person_section = ""
    in_person_json_fields = ""
    if check_in_person_submission:
        in_person_section = """
3. Does this contract ONLY accept IN-PERSON / PHYSICAL submission of proposals or bids?

**IN-PERSON SUBMISSION ONLY indicators** - look for phrases like:
- "hand deliver", "hand-deliver", "hand delivery"
- "deliver in person", "submitted in person", "physical delivery only"
- "sealed bids must be delivered to", "deliver sealed proposals to"
- "no electronic submissions", "no email submissions"
- "must be received at the front desk", "drop off at"
- Submission instructions that ONLY list a physical address with no email or electronic portal option
Only mark true if the solicitation EXCLUSIVELY requires in-person/physical delivery with NO electronic submission option. If both electronic and physical delivery are accepted, mark false.

4. Are the ACTUAL BID DOCUMENTS (RFP, IFB, scope of work, specifications, pricing sheets, etc.) MISSING from the text provided below?

The text below includes the solicitation page content AND any downloaded attachment files. Given ALL of that:
- Mark missing_bid_documents as TRUE only if the substantive bid documents are NOT present in the text below AND the solicitation directs you to an external URL/portal to get them.
- Mark missing_bid_documents as FALSE if the actual RFP/IFB/scope of work content IS already present in the text below, even if an external URL is also mentioned as an alternative source.
- If the downloaded files contain the full bid package, mark FALSE.
- If the text is just a brief notice/summary pointing to another site for the real documents, mark TRUE.
- If missing_bid_documents is true, include the external URL in external_documents_url (or null if no URL found).
"""
        in_person_json_fields = """
    "requires_in_person_submission": true/false,
    "missing_bid_documents": true/false,
    "external_documents_url": "URL string or null","""

    SITE_VISIT_PROMPT = f"""
You are an expert in government procurement. Analyze the following solicitation and determine:

1. Does this contract mention a SITE VISIT or PRE-PROPOSAL CONFERENCE of any kind (mandatory or optional)?
2. Does this contract have CONTROLLED ATTACHMENTS (non-public access files)?
{in_person_section}
**SITE VISIT / PRE-PROPOSAL CONFERENCE indicators** - look for phrases like:
- "site visit", "site tour", "site walk", "walk-through", "walkthrough"
- "pre-proposal conference", "pre-bid conference", "pre-solicitation conference"
- "job walk", "facility tour", "on-site inspection"

**CONTROLLED ATTACHMENTS indicators** - look for phrases like:
- "Controlled Unclassified Information", "CUI"
- "Controlled" access level on attached files
- Restricted distribution statements (Distribution B/C/D/E/F)
- "password-protected", "FOUO" (For Official Use Only)
- Files requiring login, registration, or special credentials to access
- "Export Controlled", "ITAR", "EAR controlled"
Only mark true if files explicitly require special access -- standard public attachments are fine.

Return ONLY a JSON object with this exact structure:
{{
    "has_site_visit": true/false,
    "has_controlled_attachments": true/false,{in_person_json_fields}
    "reasoning": "Brief explanation"
}}
"""

    max_content_chars = 900000
    truncated_text = solicitation_text[:max_content_chars] if len(solicitation_text) > max_content_chars else solicitation_text

    full_prompt = f"{SITE_VISIT_PROMPT}\n\nSOLICITATION TEXT TO ANALYZE:\n\n{truncated_text}"

    result_text = call_llm(full_prompt, temperature=0.1,
                           response_mime_type="application/json", max_retries=max_retries)
    if result_text:
        try:
            parsed = json.loads(result_text)
            return {
                "has_site_visit": parsed.get("has_site_visit", False),
                "has_controlled_attachments": parsed.get("has_controlled_attachments", False),
                "requires_in_person_submission": parsed.get("requires_in_person_submission", False),
                "missing_bid_documents": parsed.get("missing_bid_documents", False),
                "external_documents_url": parsed.get("external_documents_url", None),
                "reasoning": parsed.get("reasoning", "")
            }
        except (json.JSONDecodeError, KeyError) as parse_err:
            print(f"Failed to parse site-visit response: {parse_err}")
            default_result["reasoning"] = f"Parse error: {parse_err}"
            return default_result

    default_result["reasoning"] = "All API attempts exhausted (Gemini + OpenAI fallback)"
    return default_result


def is_construction_contract(solicitation_text, max_retries=3):
    """
    Determines if a solicitation is a construction contract OR requires physical/hand delivery
    using Gemini API. These types of contracts should be skipped.
    
    Args:
        solicitation_text (str): Combined text from detail page and downloaded files
        max_retries (int): Maximum number of API key rotations to try
        
    Returns:
        dict: {
            "is_construction": bool,
            "requires_physical_delivery": bool,
            "should_skip": bool,  # True if either condition is met
            "reasoning": str
        }
    """
    default_result = {
        "is_construction": False,
        "requires_physical_delivery": False,
        "should_skip": False,
        "reasoning": ""
    }
    
    if not GEMINI_API_KEYS:
        default_result["reasoning"] = "No API keys configured, defaulting to not skip"
        return default_result
    
    if not solicitation_text or len(solicitation_text.strip()) < 50:
        default_result["reasoning"] = "Insufficient text to analyze"
        return default_result
    
    CONSTRUCTION_CHECK_PROMPT = """
You are an expert in government procurement classification. Analyze the following solicitation and determine:

1. Is this a CONSTRUCTION contract?
2. Does this contract require PHYSICAL/HAND DELIVERY by the contractor?

**CONSTRUCTION CONTRACTS** - Mark as construction if it involves:
- Building, renovating, or repairing structures (buildings, roads, bridges, etc.)
- Site preparation, excavation, or demolition
- General contracting or construction management
- Roofing, plumbing, electrical installation in a construction context
- Infrastructure construction (water systems, sewage, utilities installation)
- Major facility modifications requiring construction crews


**PHYSICAL/HAND DELIVERY CONTRACTS** - The contract does not allow for online submissions or digital delivery.And requires the contractor to physically deliver the goods in person or mail the proposal.

Return ONLY a JSON object with this exact structure:
{
    "is_construction": true/false,
    "requires_physical_delivery": true/false,
    "reasoning": "Brief explanation covering both determinations"
}
"""
    
    # Truncate if too long
    max_content_chars = 50000
    truncated_text = solicitation_text[:max_content_chars] if len(solicitation_text) > max_content_chars else solicitation_text
    
    full_prompt = f"{CONSTRUCTION_CHECK_PROMPT}\n\nSOLICITATION TEXT TO ANALYZE:\n\n{truncated_text}"
    
    result_text = call_llm(full_prompt, temperature=0.1,
                           response_mime_type="application/json", max_retries=max_retries)
    if result_text:
        try:
            parsed = json.loads(result_text)
            is_construction = parsed.get("is_construction", False)
            requires_physical_delivery = parsed.get("requires_physical_delivery", False)
            return {
                "is_construction": is_construction,
                "requires_physical_delivery": requires_physical_delivery,
                "should_skip": is_construction or requires_physical_delivery,
                "reasoning": parsed.get("reasoning", "")
            }
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Failed to parse construction check response: {e}")
            default_result["reasoning"] = f"Parse error: {e}"
            return default_result

    default_result["reasoning"] = "All API attempts exhausted (Gemini + OpenAI fallback)"
    return default_result


def check_biddability(ui_text, max_retries=5):
    """
    Lightweight biddability pre-check using Gemini.
    Determines if a SAM.gov opportunity is actually biddable for a
    Total Small Business before running the expensive full analysis.
    
    Args:
        ui_text (str): Raw text content scraped from the SAM.gov opportunity page
        max_retries (int): Maximum number of API retry attempts
        
    Returns:
        dict: {
            "notice_type": str,          # e.g., "Combined Synopsis/Solicitation", "Sources Sought"
            "set_aside_type": str|None,  # e.g., "Total Small Business Set-Aside (FAR 19.5)"
            "is_sole_source": bool,
            "due_datetime_iso": str|None,
            "is_past_deadline": bool,
            "is_due_within_5_days": bool,
            "is_dla": bool,
            "is_dibbs": bool,
            "is_construction": bool,
            "has_controlled_attachments": bool,
            "is_biddable": bool,         # Overall verdict
            "skip_reason": str|None      # Human-readable reason if not biddable
        }
    """
    default_result = {
        "notice_type": None,
        "set_aside_type": None,
        "is_sole_source": False,
        "due_datetime_iso": None,
        "is_past_deadline": False,
        "is_due_within_5_days": False,
        "is_dla": False,
        "is_dibbs": False,
        "is_construction": False,
        "has_controlled_attachments": False,
        "is_biddable": False,
        "skip_reason": "Could not determine biddability"
    }
    
    if not GEMINI_API_KEYS:
        default_result["skip_reason"] = "No API keys configured"
        return default_result
    
    if not ui_text or len(ui_text.strip()) < 50:
        default_result["skip_reason"] = "Insufficient page text to analyze"
        return default_result

    currentDate = datetime.now().strftime('%Y-%m-%d')
    
    BIDDABILITY_PROMPT = f"""You are an expert in government procurement. Analyze this SAM.gov opportunity page text and determine if it is a biddable contract opportunity for a Total Small Business.

Extract these fields and apply the filtering rules below:

**FIELDS TO EXTRACT:**
1. notice_type - The exact opportunity/notice type shown on SAM.gov (e.g., "Combined Synopsis/Solicitation", "Solicitation", "Sources Sought", "Presolicitation", "Special Notice", "Award Notice", "Sole Source", "Intent to Bundle Requirements", "Request for Information")
2. set_aside_type - The exact set-aside classification (e.g., "Total Small Business Set-Aside (FAR 19.5)", "8(a) Set-Aside", "HUBZone Set-Aside", "Service-Disabled Veteran-Owned Small Business Set Aside", "Partial Small Business Set-Aside (FAR 19.5)", or null if none/unrestricted)
3. is_sole_source - true if the opportunity is sole-sourced to a specific vendor, names a specific awardee, or is a single-award extension/renewal to an existing vendor
4. due_datetime_iso - The response/offer due date in ISO format
5. is_past_deadline - true if the due date is before today ({currentDate})
6. is_due_within_5_days - true if the due date is within 5 days from today ({currentDate})
7. is_dla - true if this is a Defense Logistics Agency contract
8. is_dibbs - true if this mentions DIBBS (DLA Internet Bid Board System)
9. is_construction - true if this is a construction contract requiring physical on-site building/renovation/paving work
10. has_controlled_attachments - true if any attached files have non-public access (e.g., "Controlled Unclassified Information", CUI, restricted distribution statements like Distribution B/C/D/E/F, or password-protected documents). Only true if files explicitly require special access -- standard public attachments are fine.

**FILTERING RULES - Mark is_biddable as FALSE if ANY of these are true:**
- notice_type is NOT "Combined Synopsis/Solicitation" or "Solicitation" (Sources Sought, Presolicitation, Special Notice, Award Notice, RFI, etc. are informational only)
- is_sole_source is true (can't compete against a named vendor)
- set_aside_type is one we don't qualify for: "8(a) Set-Aside", "HUBZone Set-Aside", "Women-Owned Small Business", "Economically Disadvantaged Women-Owned Small Business". We DO qualify for: "Total Small Business Set-Aside (FAR 19.5)", "Partial Small Business Set-Aside (FAR 19.5)", "Service-Disabled Veteran-Owned Small Business Set Aside", "Veteran Set Aside", or null/unrestricted
- is_past_deadline is true
- is_due_within_5_days is true
- is_dla and is_dibbs are both true
- is_construction is true
- has_controlled_attachments is true (can't access the required documents)

If is_biddable is false, provide a concise skip_reason explaining why.
If is_biddable is true, set skip_reason to null.

Return ONLY a JSON object with this exact structure:
{{
    "notice_type": "string or null",
    "set_aside_type": "string or null",
    "is_sole_source": true/false,
    "due_datetime_iso": "ISO datetime string or null",
    "is_past_deadline": true/false,
    "is_due_within_5_days": true/false,
    "is_dla": true/false,
    "is_dibbs": true/false,
    "is_construction": true/false,
    "has_controlled_attachments": true/false,
    "is_biddable": true/false,
    "skip_reason": "string or null"
}}"""

    # Truncate text if needed (SAM.gov pages can be large)
    max_content_chars = 100000
    truncated_text = ui_text[:max_content_chars] if len(ui_text) > max_content_chars else ui_text
    
    full_prompt = f"{BIDDABILITY_PROMPT}\n\nSAM.gov Page Text:\n{truncated_text}"
    
    print("  Checking biddability...")
    result_text = call_llm(full_prompt, temperature=0.1,
                           response_mime_type="application/json", max_retries=max_retries)
    if result_text:
        try:
            parsed = json.loads(result_text)
            default_result.update(parsed)
            return default_result
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  Failed to parse biddability response: {e}")
            default_result["skip_reason"] = f"Parse error: {e}"
            return default_result

    default_result["skip_reason"] = "All API attempts exhausted (Gemini + OpenAI fallback)"
    return default_result


# =====================================================================
# GEMINI 2.5 PRO — Vendor Lead Generation with Google Search Grounding
# Replacement for OpenAI web-search based lead generation
# =====================================================================

GEMINI_25_PRO_MODEL = "gemini-2.5-pro"

# Separate key pool for Gemini 2.5 Pro (the file-loaded keys may not have
# billing/quota for the heavier Pro model).  These keys have confirmed 2.5-Pro access.
_GEMINI_25_PRO_KEYS = [
    "AIzaSyDM7I-cSOcujhBFRLiyiwjRj_o0L8gI3CY",
    "AIzaSyCD82F4kKDsRcxV71KR63ABcO-DaneEaGU",
    "AIzaSyAR0pg5qjUYH5JtetRTzltbD6t7alRAyQQ",
    "AIzaSyA6ThMTzrP7HoS5iO44b9Rl4l9of1v3QjM",
]
_pro_key_index = 0

def _get_pro_key():
    """Get the current Gemini 2.5 Pro API key."""
    global _pro_key_index
    key = _GEMINI_25_PRO_KEYS[_pro_key_index]
    return key

def _rotate_pro_key():
    """Rotate to the next Gemini 2.5 Pro API key."""
    global _pro_key_index
    _pro_key_index = (_pro_key_index + 1) % len(_GEMINI_25_PRO_KEYS)
    print(f"[2.5 Pro] Rotated to key {_pro_key_index + 1}/{len(_GEMINI_25_PRO_KEYS)}")

def _load_prompt_template(prompt_file="promptv2.txt"):
    """Load the vendor-lead prompt template from disk."""
    with open(prompt_file, "r", encoding="utf-8") as f:
        return f.read()


def _build_vendor_prompt(solicitation_text, source="SAM.GOV"):
    """Build the full prompt identical to the one used by the OpenAI service."""
    template = _load_prompt_template()
    return f"""{template}

================================================================================
SOLICITATION DETAILS FROM {source}:
================================================================================

{solicitation_text}

================================================================================
END OF SOLICITATION DETAILS
================================================================================

Based on the above solicitation details, please find local vendors and create the email as specified in the prompt instructions.
"""


def _parse_vendor_response(response_text):
    """
    Parse the Gemini response to extract emails, subject, and body.
    Uses regex first; falls back to Gemini extraction if needed.
    """
    result = {
        "emails": "Not found",
        "subject": "Not found",
        "body": "Not found",
    }

    if not response_text:
        return result

    # --- emails ---
    email_pat = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pat, response_text)
    if emails:
        seen = set()
        unique = []
        for e in emails:
            low = e.lower()
            if low not in seen:
                unique.append(e)
                seen.add(low)
        result["emails"] = "; ".join(unique)

    # --- subject ---
    for pat in [r'Subject:\s*([^\n\r]+)', r'subject:\s*([^\n\r]+)', r'SUBJECT:\s*([^\n\r]+)']:
        m = re.search(pat, response_text)
        if m:
            result["subject"] = m.group(1).strip().strip("*")
            break

    # --- body ---
    for pat in [
        r'(?:Body:|BODY:)\s*\n(.*)',
        r'(Hi[,\s].*?(?:www\.\S+|Thanks[,\s].*?$))',
        r'(Hello[,\s].*?(?:www\.\S+|Thanks[,\s].*?$))',
    ]:
        m = re.search(pat, response_text, re.DOTALL)
        if m:
            result["body"] = m.group(1).strip()
            break

    if result["body"] == "Not found":
        subject_idx = response_text.find("Subject:")
        if subject_idx != -1:
            after_subject = response_text[subject_idx:]
            newline = after_subject.find("\n")
            if newline != -1:
                result["body"] = after_subject[newline + 1:].strip()

    return result


def generate_vendor_leads_gemini(solicitation_text, source="SAM.GOV",
                                  subject_suffix="", max_retries=10):
    """
    Generate vendor leads using Gemini 2.5 Pro with Google Search grounding.
    Drop-in replacement for services.openai_service.generate_vendor_leads().

    Args:
        solicitation_text: Extracted text from the solicitation
        source: Source identifier (SAM.GOV or ESBD)
        subject_suffix: Optional suffix to append to subject line
        max_retries: Max API retry attempts with key rotation

    Returns:
        dict with 'emails', 'subject', 'body' keys
    """
    error_stub = lambda msg: {"emails": msg, "subject": msg, "body": msg}

    if not _GEMINI_25_PRO_KEYS:
        return error_stub("ERROR: No Gemini 2.5 Pro API keys configured")

    # Build the prompt
    try:
        prompt = _build_vendor_prompt(solicitation_text, source)
        print(f"Built vendor-lead prompt ({len(prompt)} chars)")
    except Exception as e:
        return error_stub(f"ERROR: Could not build prompt: {e}")

    system_message = (
        "You are an expert at finding local vendors for government contracts. "
        "Use Google Search to find real, verified vendor information including "
        "company names, emails, and phone numbers. Prioritize local small/medium "
        "businesses within the specified geographic area."
    )

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "systemInstruction": {
            "parts": [{"text": system_message}]
        },
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 32000,
            "thinkingConfig": {"thinkingBudget": 4096},
        },
        "tools": [{"google_search": {}}],
    }

    consecutive_failures = 0

    for attempt in range(max_retries):
        try:
            api_key = _get_pro_key()
            url = f"{BASE_URL}/{GEMINI_25_PRO_MODEL}:generateContent"
            print(f"[Gemini 2.5 Pro] Attempt {attempt + 1}/{max_retries} (key {_pro_key_index + 1}/{len(_GEMINI_25_PRO_KEYS)})...")

            response = requests.post(
                url,
                params={"key": api_key},
                headers=headers,
                json=payload,
                timeout=180,
            )

            if response.status_code == 200:
                rj = response.json()
                # Collect text from all parts of all candidates
                full_text = ""
                for candidate in rj.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        if "text" in part:
                            full_text += part["text"]

                if not full_text.strip():
                    consecutive_failures += 1
                    print(f"WARNING: Empty response text from Gemini 2.5 Pro (attempt {attempt + 1})")
                    if attempt < max_retries - 1:
                        _rotate_pro_key()
                        time.sleep(min(2 ** consecutive_failures, 15))
                        continue
                    return error_stub("ERROR: Empty response from Gemini 2.5 Pro after retries")

                print(f"[Gemini 2.5 Pro] Received {len(full_text)} chars of response")

                # Parse out emails / subject / body
                parsed = _parse_vendor_response(full_text)

                # Append subject suffix
                if subject_suffix and parsed["subject"] and parsed["subject"] != "Not found":
                    parsed["subject"] += subject_suffix

                # Also store raw response for debugging
                parsed["_raw_response"] = full_text

                print(f"[Gemini 2.5 Pro] Extracted {len(parsed['emails'].split(';'))} email(s)")
                return parsed

            elif response.status_code == 429:
                consecutive_failures += 1
                print(f"Rate limit hit on Pro key {_pro_key_index + 1}. Rotating...")
                _rotate_pro_key()
                sleep_time = min(2 ** consecutive_failures, 30)
                time.sleep(sleep_time)
                continue

            elif response.status_code in (500, 502, 503):
                consecutive_failures += 1
                print(f"Server error {response.status_code}. Retrying in {min(2 ** consecutive_failures, 30)}s...")
                _rotate_pro_key()
                time.sleep(min(2 ** consecutive_failures, 30))
                continue

            else:
                err = f"Gemini 2.5 Pro API Error (Status {response.status_code}): {response.text[:300]}"
                print(err)
                return error_stub(f"ERROR: {err}")

        except requests.exceptions.Timeout:
            consecutive_failures += 1
            print(f"Request timeout (attempt {attempt + 1})")
            _rotate_pro_key()
            time.sleep(min(2 ** consecutive_failures, 30))
            continue

        except Exception as e:
            print(f"Unexpected error: {e}")
            return error_stub(f"ERROR: {e}")

    return error_stub(f"ERROR: All {max_retries} retries exhausted")


# =====================================================================
# TEST HARNESS — run with:  python gemini.py
# =====================================================================
if __name__ == "__main__":
    import time as _time

    print("=" * 80)
    print("GEMINI 2.5 PRO — Vendor Lead Generation Test")
    print("=" * 80)

    # Load two recent cached contracts for testing
    test_cache_files = [
        "text_cache/sam_text_491a5c346265f98fca372769bfceae6a.json",  # MT Ennis NFH Mobile Fish Pump
        "text_cache/sam_text_a0a8fc7444b652e5e95398f82357d45a.json",  # Cold Spray Corrosion Repair
    ]

    for idx, cache_file in enumerate(test_cache_files, 1):
        print(f"\n{'='*80}")
        print(f"TEST CONTRACT {idx}")
        print(f"{'='*80}")
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            sol_url = cache_data.get("url", "N/A")
            sol_text = cache_data.get("text", "")
            print(f"URL:  {sol_url}")
            print(f"Text: {len(sol_text)} characters")
            print(f"Preview: {sol_text[:150]}...\n")

            start = _time.time()
            result = generate_vendor_leads_gemini(sol_text, source="SAM.GOV", subject_suffix=" k2-gemini")
            elapsed = _time.time() - start

            print(f"\n--- RESULT (took {elapsed:.1f}s) ---")
            print(f"Emails:  {result['emails'][:200]}{'...' if len(result.get('emails',''))>200 else ''}")
            print(f"Subject: {result['subject']}")
            body_preview = result['body'][:300] if result['body'] else 'N/A'
            print(f"Body:    {body_preview}...")
            print(f"{'='*80}\n")

        except FileNotFoundError:
            print(f"Cache file not found: {cache_file} — skipping")
        except Exception as e:
            print(f"Error testing contract {idx}: {e}")

    print("\nAll tests complete.")