#!/usr/bin/env python3
"""
Backfill Folder Links for LocalContracts

This script:
1. Reads all rows from localContracts worksheet
2. For each row, gets the solicitation ID
3. Downloads ESBD files for that solicitation
4. Creates a subfolder in the parent Google Drive folder
5. Uploads all files to that subfolder
6. Updates column J in the sheet with the folder link
"""

import os
import json
import time
import pickle
import requests
import pygsheets
from download_esbd_files import download_esbd_files
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# Configuration
PARENT_FOLDER_ID = "1lfRQ8kUL7RwR1tx9QHEY8h_P4qrAk9LN"  # Extract from URL
TOKEN_FILE = 'tokenv2.pickle'
CREDENTIALS_FILE = 'credentialsv2.json'
SCOPES = ['https://www.googleapis.com/auth/drive']

def get_drive_access_token():
    """Get OAuth access token from tokenv2.pickle file for Google Drive (similar to Gmail token approach)"""
    creds = None
    
    # Load existing token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
    
    # If credentials don't exist or are invalid, create new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh expired credentials
            print("Refreshing expired credentials...")
            creds.refresh(Request())
            # Save refreshed credentials
            with open(TOKEN_FILE, 'wb') as token:
                pickle.dump(creds, token)
        else:
            # Create new credentials via OAuth flow
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"\n❌ ERROR: {CREDENTIALS_FILE} not found!")
                print("Please download OAuth credentials from Google Cloud Console")
                raise FileNotFoundError(f"{CREDENTIALS_FILE} not found")
            
            print("Starting OAuth flow - browser will open for authentication...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            
            # Save credentials
            with open(TOKEN_FILE, 'wb') as token:
                pickle.dump(creds, token)
            print(f"✓ Credentials saved to {TOKEN_FILE}")
    
    # Get access token
    access_token = creds.token
    print(f"✓ Got Drive access token: {access_token[:20]}...")
    return access_token

def create_drive_folder(access_token, folder_name, parent_folder_id):
    """Create a folder in Google Drive using REST API."""
    try:
        url = 'https://www.googleapis.com/drive/v3/files'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        body = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id]
        }
        
        response = requests.post(url, headers=headers, json=body, params={'fields': 'id,webViewLink'})
        
        if response.status_code != 200:
            print(f"❌ Error creating folder: {response.text}")
            return None, None
        
        result = response.json()
        folder_id = result.get('id')
        folder_link = result.get('webViewLink')
        
        print(f"✓ Created folder: {folder_name}")
        print(f"  Folder Link: {folder_link}")
        
        return folder_id, folder_link
        
    except Exception as e:
        print(f"❌ Error creating folder {folder_name}: {e}")
        return None, None

def upload_file_to_drive(access_token, file_path, folder_id):
    """Upload a file to Google Drive folder using REST API."""
    try:
        filename = os.path.basename(file_path)
        
        # First, create the file metadata
        metadata_url = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart'
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        # Read file content
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        # Create multipart body
        metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        
        files = {
            'data': ('metadata', json.dumps(metadata), 'application/json; charset=UTF-8'),
            'file': (filename, file_content)
        }
        
        response = requests.post(metadata_url, headers=headers, files=files)
        
        if response.status_code != 200:
            print(f"  ❌ Error uploading {filename}: {response.text}")
            return None
        
        print(f"  ✓ Uploaded: {filename}")
        return response.json()
        
    except Exception as e:
        print(f"  ❌ Error uploading {os.path.basename(file_path)}: {e}")
        return None

def process_row(row_number, record, access_token, worksheet):
    """Process a single row from localContracts."""
    solicitation_id = str(record.get('Solicitation ID', '')).strip()
    
    if not solicitation_id:
        print(f"Row {row_number}: No Solicitation ID, skipping...")
        return False
    
    # Check if already has folder link
    try:
        existing_link = worksheet.cell(f'J{row_number}').value
        if existing_link and existing_link.strip() and existing_link.startswith('http'):
            print(f"Row {row_number} ({solicitation_id}): Already has folder link, skipping...")
            return True
    except:
        pass
    
    print(f"\n{'='*80}")
    print(f"Processing Row {row_number}: {solicitation_id}")
    print(f"{'='*80}")
    
    esbd_url = f"https://www.txsmartbuy.gov/esbd/{solicitation_id}"
    
    try:
        # Download ESBD files
        print(f"Downloading files from {esbd_url}...")
        download_result = download_esbd_files(esbd_url, extract_text=False)
        
        if not download_result or not download_result.get('files'):
            print(f"❌ No files downloaded for {solicitation_id}")
            worksheet.update_value(f'J{row_number}', "No files found")
            return False
        
        downloaded_files = download_result['files']
        print(f"✓ Downloaded {len(downloaded_files)} files")
        
        # Create folder in Google Drive
        print(f"Creating folder in Google Drive...")
        folder_name = f"ESBD_{solicitation_id}"
        folder_id, folder_link = create_drive_folder(access_token, folder_name, PARENT_FOLDER_ID)
        
        if not folder_id:
            worksheet.update_value(f'J{row_number}', "Error: Folder creation failed")
            return False
        
        # Upload files
        print(f"Uploading {len(downloaded_files)} files...")
        download_dir = os.path.join('esbd_downloads', solicitation_id)
        
        uploaded_count = 0
        for filename in downloaded_files:
            file_path = os.path.join(download_dir, filename)
            if os.path.exists(file_path):
                if upload_file_to_drive(access_token, file_path, folder_id):
                    uploaded_count += 1
        
        print(f"✓ Uploaded {uploaded_count}/{len(downloaded_files)} files")
        
        # Update spreadsheet
        worksheet.update_value(f'J{row_number}', folder_link)
        print(f"✓ Updated cell J{row_number} with folder link")
        print(f"✅ Successfully processed {solicitation_id}\n")
        
        return True
        
    except Exception as e:
        print(f"❌ Error processing {solicitation_id}: {e}")
        worksheet.update_value(f'J{row_number}', f"Error: {str(e)[:100]}")
        return False

def main():
    print("=" * 80)
    print("ESBD Files to Google Drive Uploader")
    print("=" * 80)
    print()
    
    # Get Drive access token using service account
    print("Getting Google Drive access token...")
    try:
        access_token = get_drive_access_token()
        print("✓ Google Drive API initialized\n")
    except Exception as e:
        print(f"❌ Failed to get Drive access token: {e}")
        return
    
    # Connect to Google Sheets
    print("Connecting to Google Sheets...")
    try:
        gc = pygsheets.authorize(service_file='key.json')
        spreadsheet = gc.open('Quote Request')
        worksheet = spreadsheet.worksheet_by_title('localContracts')
        print("✓ Connected to localContracts worksheet\n")
    except Exception as e:
        print(f"❌ Failed to connect to Google Sheets: {e}")
        return
    
    # Get all records
    print("Reading all records from localContracts...")
    records = worksheet.get_all_records()
    print(f"✓ Found {len(records)} rows to process\n")
    
    # Process each row in REVERSE order
    success_count = 0
    skip_count = 0
    
    # Reverse the records list and calculate correct row numbers
    total_rows = len(records)
    for idx, record in enumerate(reversed(records)):
        # Calculate actual row number (reversed)
        i = total_rows - idx + 1  # +1 because row 1 is headers
        
        try:
            if process_row(i, record, access_token, worksheet):
                success_count += 1
            else:
                skip_count += 1
            time.sleep(2)  # Rate limiting
        except Exception as e:
            print(f"❌ Unexpected error on row {i}: {e}")
            skip_count += 1
    
    # Summary
    print("\n" + "=" * 80)
    print("PROCESSING COMPLETE")
    print("=" * 80)
    print(f"✓ Successful: {success_count}")
    print(f"⏭️  Skipped: {skip_count}")
    print(f"📊 Total: {len(records)}")
    print("=" * 80)

if __name__ == '__main__':
    main()