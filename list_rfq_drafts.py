"""
Gmail Draft Finder for Request for Quote

This script lists all Gmail drafts that have "request for quote" in the subject line (case insensitive).

Features:
- Lists all drafts matching the subject filter
- Shows draft details including subject, recipient, and preview of body
- Ensures drafts have proper signature and opt-out line
- Uses Gemini AI to reformat drafts if needed
- Outputs in HTML format to preserve formatting
- Multi-account support

Setup:
1. Install required packages: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
2. Run generate_token.py to create authentication tokens for each email account
3. Configure EMAIL_ACCOUNTS list with account details

Usage:
    python list_rfq_drafts.py
"""

import os
import pickle
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import base64
import re

# Import Gemini API configuration
try:
    from gemini import format_email_draft_with_signature
    GEMINI_AVAILABLE = True
except ImportError:
    print("Warning: Could not import gemini module. Draft formatting will be disabled.")
    GEMINI_AVAILABLE = False

# Gmail API scopes needed
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose'  # Needed to update drafts
]

# Email accounts configuration
EMAIL_ACCOUNTS = [
    # {
    #     'name': 'avinash@xleagle.com',
    #     'token_file': 'token.pickle',
    #     'credentials_file': 'credentials.json',
    # },
    # {
    #     'name': 'info@xleagle.com',
    #     'token_file': 'token2.pickle',
    #     'credentials_file': 'credentials2.json',
    # },
    # {
    #     "name":"info@thenexan.com",
    #     "token_file":"token3.pickle",
    #     "credentials_file":"credentials_nexan.json",
    # },
    {
        "name":"avinash@thenexan.com",
        "token_file":"token4.pickle",
        "credentials_file":"credentials_nexan.json",
    }
    # Example for future additional accounts:
    # {
    #     'name': 'Secondary Account',
    #     'token_file': 'token2.pickle',
    #     'credentials_file': 'credentials2.json',
    # },
]


class GmailDraftFinder:
    """Handler for finding Gmail drafts"""
    
    def __init__(self, account_config):
        """
        Initialize Gmail draft finder for a specific account
        
        Args:
            account_config (dict): Account configuration with token_file and credentials_file
        """
        self.account_name = account_config['name']
        self.token_file = account_config['token_file']
        self.credentials_file = account_config['credentials_file']
        self.service = None
        self.user_email = None
        
    def authenticate(self):
        """Authenticate with Gmail API"""
        creds = None
        
        # Load existing credentials
        if os.path.exists(self.token_file):
            with open(self.token_file, 'rb') as token:
                creds = pickle.load(token)
        
        # Refresh or create new credentials if needed
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"Error refreshing token for {self.account_name}: {e}")
                    creds = None
            
            if not creds:
                if not os.path.exists(self.credentials_file):
                    print(f"❌ ERROR: {self.credentials_file} not found for {self.account_name}!")
                    print("Please run generate_token.py first to set up authentication.")
                    return False
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, 
                    SCOPES
                )
                creds = flow.run_local_server(port=0, access_type='offline', prompt='consent')
                
                # Save credentials for future use
                with open(self.token_file, 'wb') as token:
                    pickle.dump(creds, token)
        
        # Build Gmail service
        self.service = build('gmail', 'v1', credentials=creds)
        
        # Get user's email address
        profile = self.service.users().getProfile(userId='me').execute()
        self.user_email = profile.get('emailAddress')
        
        return True
    
    def get_all_drafts(self):
        """
        Get all drafts from Gmail
        
        Returns:
            list: List of draft objects
        """
        try:
            drafts = []
            page_token = None
            
            while True:
                results = self.service.users().drafts().list(
                    userId='me',
                    pageToken=page_token
                ).execute()
                
                batch_drafts = results.get('drafts', [])
                drafts.extend(batch_drafts)
                
                page_token = results.get('nextPageToken')
                if not page_token:
                    break
            
            return drafts
            
        except Exception as e:
            print(f"Error fetching drafts for {self.account_name}: {e}")
            return []
    
    def get_draft_details(self, draft_id):
        """
        Get detailed information about a draft
        
        Args:
            draft_id (str): Draft ID
            
        Returns:
            dict: Draft details including subject, to, body, etc.
        """
        try:
            draft = self.service.users().drafts().get(
                userId='me',
                id=draft_id,
                format='full'
            ).execute()
            
            message = draft.get('message', {})
            payload = message.get('payload', {})
            headers = {}
            
            # Extract headers
            for header in payload.get('headers', []):
                name = header['name'].lower()
                value = header['value']
                
                if name == 'from':
                    headers['from'] = value
                elif name == 'to':
                    headers['to'] = value
                elif name == 'cc':
                    headers['cc'] = value
                elif name == 'bcc':
                    headers['bcc'] = value
                elif name == 'subject':
                    headers['subject'] = value
                elif name == 'date':
                    headers['date'] = value
                elif name == 'message-id':
                    headers['message-id'] = value
            
            # Extract body
            body = self._extract_body(payload)
            
            return {
                'draft_id': draft_id,
                'message_id': message.get('id'),
                'thread_id': message.get('threadId'),
                'from': headers.get('from', '[Unknown]'),
                'to': headers.get('to', '[Unknown]'),
                'cc': headers.get('cc', ''),
                'bcc': headers.get('bcc', ''),
                'subject': headers.get('subject', '[No Subject]'),
                'date': headers.get('date', '[Unknown]'),
                'message_id_header': headers.get('message-id', ''),
                'body': body
            }
            
        except Exception as e:
            print(f"Error fetching draft {draft_id}: {e}")
            return None
    
    def _extract_body(self, payload):
        """
        Extract message body text from payload
        
        Args:
            payload (dict): Message payload
            
        Returns:
            str: Message body text
        """
        try:
            # Check for plain text in body
            if 'data' in payload.get('body', {}):
                data = payload['body']['data']
                text = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                return text
            
            # Check for parts (multipart message)
            parts = payload.get('parts', [])
            for part in parts:
                if part.get('mimeType') == 'text/plain':
                    if 'data' in part.get('body', {}):
                        data = part['body']['data']
                        text = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                        return text
                
                # Check nested parts
                if 'parts' in part:
                    for subpart in part['parts']:
                        if subpart.get('mimeType') == 'text/plain':
                            if 'data' in subpart.get('body', {}):
                                data = subpart['body']['data']
                                text = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                                return text
            
            return "[No readable body found]"
            
        except Exception as e:
            return f"[Error extracting body: {e}]"
    
    def update_draft(self, draft_id, formatted_html, original_to, original_subject, original_message_id_header, original_cc='', original_bcc=''):
        """
        Update an existing Gmail draft with formatted HTML content
        
        Args:
            draft_id (str): The ID of the draft to update
            formatted_html (str): The formatted HTML content
            original_to (str): Original recipient
            original_subject (str): Original subject
            original_message_id_header (str): Original message ID header (if replying to something)
            original_cc (str): Original CC recipients
            original_bcc (str): Original BCC recipients
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            # Create a multipart message to support HTML
            message = MIMEMultipart('alternative')
            message['to'] = original_to
            message['subject'] = original_subject
            
            # Add CC and BCC if they exist
            if original_cc:
                message['cc'] = original_cc
            if original_bcc:
                message['bcc'] = original_bcc
            
            # Add the HTML part
            html_part = MIMEText(formatted_html, 'html')
            message.attach(html_part)
            
            # Encode the message
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            # Get the original draft to preserve thread ID
            original_draft = self.service.users().drafts().get(
                userId='me',
                id=draft_id
            ).execute()
            
            thread_id = original_draft.get('message', {}).get('threadId')
            
            # Update draft body
            draft_body = {
                'message': {
                    'raw': raw_message
                }
            }
            
            # Preserve thread ID if it exists
            if thread_id:
                draft_body['message']['threadId'] = thread_id
            
            # Update the draft
            updated_draft = self.service.users().drafts().update(
                userId='me',
                id=draft_id,
                body=draft_body
            ).execute()
            
            print(f"✓ Successfully updated draft {draft_id} in Gmail")
            return True
            
        except Exception as e:
            print(f"❌ Error updating draft {draft_id}: {e}")
            return False
    
    def find_rfq_drafts(self):
        """
        Find all drafts with "request for quote" in the subject line (case insensitive)
        """
        # print(f"\n{'='*80}")
        # print(f"Checking account: {self.account_name}")
        # print(f"Email: {self.user_email}")
        # print(f"{'='*80}\n")
        
        print("Fetching all drafts...")
        drafts = self.get_all_drafts()
        
        if not drafts:
            print("✓ No drafts found in this account.\n")
            return
        
        print(f"Found {len(drafts)} total draft(s). Filtering for 'request for quote' in subject...\n")
        
        # Filter drafts by subject
        matching_drafts = []
        
        for draft in drafts:
            draft_id = draft['id']
            details = self.get_draft_details(draft_id)
            
            if details:
                subject = details['subject']
                # Case-insensitive search for "request for quote"
                if re.search(r'request\s+for\s+quote', subject, re.IGNORECASE):
                    matching_drafts.append(details)
        
        # Print results
        if not matching_drafts:
            print("✓ No drafts found with 'request for quote' in the subject line.\n")
            return
        
        print(f"\n{'*'*80}")
        print(f"FOUND {len(matching_drafts)} DRAFT(S) WITH 'REQUEST FOR QUOTE' IN SUBJECT")
        print(f"{'*'*80}\n")
        
        # Process and format each draft
        for idx, draft in enumerate(matching_drafts, 1):
            print(f"\n{'='*80}")
            print(f"DRAFT #{idx}")
            print(f"{'='*80}")
            print(f"Draft ID: {draft['draft_id']}")
            print(f"Thread ID: {draft['thread_id']}")
            print(f"To: {draft['to']}")
            if draft.get('cc'):
                print(f"CC: {draft['cc']}")
            if draft.get('bcc'):
                print(f"BCC: {draft['bcc']}")
            print(f"Subject: {draft['subject']}")
            
            # Check if body is readable - skip draft if body can't be read
            body = draft.get('body', '')
            if (not body or 
                body.startswith('[No readable body found]') or 
                body.startswith('[Error extracting body') or
                len(body.strip()) < 10):
                print(f"\n⚠ Skipping draft #{idx}: Cannot read draft body - will not modify")
                print(f"Body content: {body if body else '(empty)'}")
                print()
                continue
            
            # Check and format the draft using Gemini
            print(f"\nProcessing draft with Gemini AI for formatting...")
            
            if not GEMINI_AVAILABLE:
                format_result = {
                    'success': False,
                    'formatted_html': None,
                    'error': 'Gemini module not available'
                }
            else:
                format_result = format_email_draft_with_signature(body, sender_email=self.user_email)
                print(format_result)
            
            if format_result['success']:
                print(f"✓ Successfully formatted draft #{idx}")
                
                # Show original draft
                print(f"\n{'-'*80}")
                print(f"ORIGINAL DRAFT:")
                print(f"{'-'*80}")
                print(body)
                print(f"{'-'*80}")
                
                # Show new formatted draft
                print(f"\n{'-'*80}")
                print(f"NEW FORMATTED DRAFT (HTML) - THIS WILL BE UPDATED IN GMAIL:")
                print(f"{'-'*80}")
                print(format_result['formatted_html'])
                print(f"{'-'*80}")
                
                # Update the draft in Gmail
                print(f"\nUpdating draft in Gmail...")
                update_success = self.update_draft(
                    draft_id=draft['draft_id'],
                    formatted_html=format_result['formatted_html'],
                    original_to=draft['to'],
                    original_subject=draft['subject'],
                    original_message_id_header=draft.get('message_id_header', ''),
                    original_cc=draft.get('cc', ''),
                    original_bcc=draft.get('bcc', '')
                )
                
                if update_success:
                    # Also save to file for reference
                    output_filename = f"draft_{draft['draft_id']}_formatted.html"
                    try:
                        
                        # with open(output_filename, 'w', encoding='utf-8') as f:
                        #     # Write a complete HTML document
                        #     f.write('<!DOCTYPE html>\n<html>\n<head>\n')
                        #     f.write('<meta charset="UTF-8">\n')
                        #     f.write(f'<title>Draft: {draft["subject"]}</title>\n')
                        #     f.write('<style>body { font-family: Arial, sans-serif; max-width: 800px; margin: 20px; }</style>\n')
                        #     f.write('</head>\n<body>\n')
                        #     f.write(f'<h2>To: {draft["to"]}</h2>\n')
                        #     f.write(f'<h3>Subject: {draft["subject"]}</h3>\n')
                        #     f.write('<hr>\n')
                        #     f.write(format_result['formatted_html'])
                        #     f.write('\n</body>\n</html>')
                        # print(f"✓ Saved backup copy to: {output_filename}")
                        print("added")
                    except Exception as e:
                        print(f"⚠ Could not save backup to file: {e}")
                else:
                    print(f"⚠ Draft was formatted but not updated in Gmail")
            else:
                print(f"⚠ Could not format draft #{idx}: {format_result['error']}")
                print(f"Draft will NOT be modified. Showing original draft body (first 500 characters):")
                print(f"{'-'*80}")
                body_preview = body[:500]
                if len(body) > 500:
                    body_preview += "..."
                print(body_preview)
                print(f"{'-'*80}")
            
            print()
        
        print(f"\n{'='*80}")
        print(f"✓ Processed {len(matching_drafts)} draft(s)")
        print(f"{'='*80}\n")


def rename_rfq_drafts():
    """Main function to process all configured email accounts"""
    
    print("\n" + "="*80)
    print("Gmail Draft Finder - Request for Quote")
    print("="*80)
    
    total_accounts = len(EMAIL_ACCOUNTS)
    
    for idx, account_config in enumerate(EMAIL_ACCOUNTS, 1):
        print(f"\nProcessing account {idx}/{total_accounts}...")
        
        finder = GmailDraftFinder(account_config)
        
        # Authenticate
        if not finder.authenticate():
            print(f"❌ Failed to authenticate {account_config['name']}. Skipping...\n")
            continue
        
        print(f"✓ Successfully authenticated as {finder.user_email}")
        
        # Find and list RFQ drafts
        finder.find_rfq_drafts()
    
    print("\n" + "="*80)
    print("✓ All accounts processed!")
    print("="*80 + "\n")


if __name__ == "__main__":
    rename_rfq_drafts()

