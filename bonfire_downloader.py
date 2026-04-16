#!/usr/bin/env python3
"""
Bonfire Hub Document Downloader - Integration Module

Provides a reusable BonfireSession that can be called from localContracts_texas.py
when the filter detects bid documents hosted on a Bonfire portal.

Uses SeleniumBase UC mode to bypass Cloudflare Turnstile challenges.
Adapted from: https://github.com/avinash753159/TX-Buy
"""

import os
import re
import time
import json
import zipfile
import threading
import pygsheets

from seleniumbase import SB
from selenium.webdriver.common.by import By
from google_drive_utils import extract_text_from_file_content

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bonfire_downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

CF_INDICATORS = [
    "turnstile", "challenges.cloudflare", "just a moment",
    "verify you are human", "security verification",
]

_session_lock = threading.Lock()
_active_session = None


def _has_cloudflare(page_source):
    src = page_source.lower()
    return any(ind in src for ind in CF_INDICATORS)


def _sanitize(name):
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip()[:100]


def _get_bonfire_credentials():
    gc = pygsheets.authorize(service_file="key.json")
    sh = gc.open("Quote Request")
    pw_wks = sh.worksheet_by_title("bonfirePasswords")
    records = pw_wks.get_all_records()
    if not records:
        raise ValueError("No credentials in bonfirePasswords sheet")
    return records[0]["email"], records[0]["password"]


def _verify_download(directory, timeout=120):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if not os.path.exists(directory):
            time.sleep(1)
            continue
        files = os.listdir(directory)
        downloading = [f for f in files if f.endswith(".crdownload") or f.endswith(".tmp")]
        if downloading:
            time.sleep(2)
            continue
        real = [f for f in files if not f.endswith(".crdownload") and not f.endswith(".tmp")]
        if real:
            return real
        time.sleep(2)
    return []


def _extract_bonfire_host(url):
    """Extract the Bonfire host from a URL like https://txdot.bonfirehub.com/..."""
    m = re.search(r'(https?://[^/]*bonfirehub\.com)', url)
    if m:
        return m.group(1)
    m = re.search(r'(https?://[^/]*euna\w*\.com)', url)
    if m:
        return m.group(1)
    return None


class BonfireSession:
    """
    Manages a single SeleniumBase UC session for downloading Bonfire documents.
    Lazily initialized on first use; reused across multiple solicitations.
    Thread-safe via _session_lock.
    """

    def __init__(self):
        self._sb = None
        self._sb_context = None
        self._logged_in_hosts = set()
        self._project_cache = {}  # host -> {ref_id -> project_id}

    def _ensure_browser(self):
        if self._sb is not None:
            return
        print("[Bonfire] Starting SeleniumBase UC browser...")
        self._sb_context = SB(uc=True, test=True, headless2=True, locale="en")
        self._sb = self._sb_context.__enter__()
        print("[Bonfire] Browser ready")

    def _login(self, host):
        if host in self._logged_in_hosts:
            return
        email, password = _get_bonfire_credentials()
        login_url = f"{host}/login"
        print(f"[Bonfire] Logging into {login_url}...")
        self._sb.uc_open_with_reconnect(login_url, reconnect_time=5)
        time.sleep(3)
        self._sb.type("input#input-email", email)
        self._sb.click("button[type='submit'], input[type='submit']")
        time.sleep(4)
        self._sb.type("input#input-password", password)
        self._sb.click("button[type='submit']")
        time.sleep(5)
        print(f"[Bonfire] Logged in: {self._sb.get_current_url()}")
        self._logged_in_hosts.add(host)

    def _load_project_map(self, host):
        if host in self._project_cache:
            return self._project_cache[host]

        print(f"[Bonfire] Loading project map from {host}...")
        self._sb.driver.get(f"{host}/portal/?tab=openOpportunities")
        time.sleep(5)

        result = self._sb.driver.execute_async_script("""
            var cb = arguments[arguments.length - 1];
            fetch('/PublicPortal/getOpenPublicOpportunitiesSectionData', {credentials: 'include'})
            .then(r => r.json())
            .then(d => cb(JSON.stringify(d)))
            .catch(e => cb('ERROR: ' + e));
        """)

        mapping = {}
        try:
            data = json.loads(result)
            projects = data.get("payload", {}).get("projects", {})
            for pid, pdata in projects.items():
                ref_id = pdata.get("ReferenceID", "")
                if ref_id:
                    mapping[ref_id] = {
                        "project_id": pid,
                        "project_name": pdata.get("ProjectName", ""),
                    }
        except (json.JSONDecodeError, TypeError):
            print("[Bonfire] Failed to parse portal API response")

        print(f"[Bonfire] Loaded {len(mapping)} projects from portal")
        self._project_cache[host] = mapping
        return mapping

    def download_solicitation_files(self, bonfire_url, solicitation_id):
        """
        Download all files for a solicitation from Bonfire.

        Args:
            bonfire_url: The Bonfire/Euna URL from the ESBD page
            solicitation_id: The ESBD solicitation reference ID

        Returns:
            dict with keys:
                'files': list of absolute file paths
                'text': concatenated extracted text
                'download_dir': path to download directory
            or None on failure
        """
        with _session_lock:
            return self._download_impl(bonfire_url, solicitation_id)

    def _download_impl(self, bonfire_url, solicitation_id):
        host = _extract_bonfire_host(bonfire_url)
        if not host:
            print(f"[Bonfire] Cannot extract host from URL: {bonfire_url}")
            return None

        self._ensure_browser()
        self._login(host)

        project_map = self._load_project_map(host)
        if solicitation_id not in project_map:
            print(f"[Bonfire] Solicitation {solicitation_id} not found in portal")
            return None

        project_id = project_map[solicitation_id]["project_id"]
        project_name = project_map[solicitation_id]["project_name"]
        opp_url = f"{host}/opportunities/{project_id}"
        print(f"[Bonfire] Downloading {solicitation_id} -> ProjectID {project_id} ({project_name[:60]})")

        opp_dir = os.path.join(DOWNLOADS_DIR, _sanitize(f"BONFIRE_{solicitation_id}"))
        os.makedirs(opp_dir, exist_ok=True)

        # Check cache
        existing = [f for f in os.listdir(opp_dir) if os.path.isfile(os.path.join(opp_dir, f))]
        if len(existing) > 1:
            print(f"[Bonfire] Using cached download ({len(existing)} files)")
            return self._build_result(opp_dir)

        # Navigate with CF bypass
        self._sb.uc_open_with_reconnect(opp_url, reconnect_time=8)
        time.sleep(3)
        if _has_cloudflare(self._sb.get_page_source()):
            self._sb.uc_open_with_reconnect(opp_url, reconnect_time=12)
            time.sleep(5)
            if _has_cloudflare(self._sb.get_page_source()):
                print("[Bonfire] Still blocked by Cloudflare")
                return None

        print(f"[Bonfire] Page loaded: {self._sb.get_title()}")

        # Set download dir
        try:
            self._sb.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow", "downloadPath": opp_dir,
            })
        except Exception:
            pass

        # Auto-accept dialogs
        self._sb.driver.execute_script(
            "window.confirm = function(){return true}; window.alert = function(){};"
        )

        # Trigger download via JS (avoids header click interception)
        clicked = False
        try:
            m = re.search(
                r"BFUtil\.downloadOpportunityPublicDocuments\('(\d+)'\)",
                self._sb.get_page_source()
            )
            if m:
                self._sb.driver.execute_script(
                    f"BFUtil.downloadOpportunityPublicDocuments('{m.group(1)}')"
                )
                print(f"[Bonfire] Triggered download for project {m.group(1)}")
                clicked = True
        except Exception:
            pass

        if not clicked:
            for sel in [
                'button[data-cy="opportunity_download_public_documents"]',
                'button[onclick*="downloadOpportunityPublicDocuments"]',
            ]:
                try:
                    els = self._sb.driver.find_elements(By.CSS_SELECTOR, sel)
                    if els:
                        self._sb.driver.execute_script("arguments[0].click();", els[0])
                        clicked = True
                        break
                except Exception:
                    pass

        if not clicked:
            print("[Bonfire] No download button found")
            return None

        time.sleep(2)
        try:
            alert = self._sb.driver.switch_to.alert
            alert.accept()
        except Exception:
            pass

        # Wait for download
        print("[Bonfire] Waiting for download...")
        files = _verify_download(opp_dir, timeout=120)

        if not files:
            import shutil
            default_dl = os.path.expanduser("~/Downloads")
            if os.path.exists(default_dl):
                for f in os.listdir(default_dl):
                    fp = os.path.join(default_dl, f)
                    if os.path.isfile(fp) and time.time() - os.path.getmtime(fp) < 120:
                        shutil.move(fp, os.path.join(opp_dir, f))
                files = [f for f in os.listdir(opp_dir) if os.path.isfile(os.path.join(opp_dir, f))]

        if not files:
            print("[Bonfire] Download failed - no files")
            return None

        # Extract zips
        for f in list(files):
            fp = os.path.join(opp_dir, f)
            if f.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(fp, "r") as zf:
                        zf.extractall(opp_dir)
                    print(f"[Bonfire] Extracted: {f}")
                except Exception as e:
                    print(f"[Bonfire] Zip extract error: {e}")

        return self._build_result(opp_dir)

    def _build_result(self, opp_dir):
        all_files = []
        all_text = []
        for f in os.listdir(opp_dir):
            fp = os.path.join(opp_dir, f)
            if not os.path.isfile(fp):
                continue
            all_files.append(fp)
            if not f.lower().endswith(".zip"):
                try:
                    with open(fp, "rb") as fh:
                        content = fh.read()
                    text = extract_text_from_file_content(f, content)
                    if text:
                        all_text.append(text)
                except Exception:
                    pass

        combined_text = "\n\n".join(all_text)
        MAX_TEXT = 50000
        if len(combined_text) > MAX_TEXT:
            combined_text = combined_text[:MAX_TEXT]

        print(f"[Bonfire] {len(all_files)} files, {len(combined_text)} chars of text extracted")
        return {
            "files": all_files,
            "text": combined_text,
            "download_dir": opp_dir,
        }

    def close(self):
        if self._sb_context is not None:
            try:
                self._sb_context.__exit__(None, None, None)
            except Exception:
                pass
            self._sb = None
            self._sb_context = None
            self._logged_in_hosts.clear()
            self._project_cache.clear()
            print("[Bonfire] Session closed")


def get_bonfire_session():
    """Get the global BonfireSession singleton (lazily created)."""
    global _active_session
    if _active_session is None:
        _active_session = BonfireSession()
    return _active_session


def close_bonfire_session():
    """Close the global BonfireSession if active."""
    global _active_session
    if _active_session is not None:
        _active_session.close()
        _active_session = None


def is_bonfire_url(url):
    """Check if a URL points to a Bonfire/Euna portal."""
    if not url:
        return False
    return "bonfirehub.com" in url.lower() or "euna" in url.lower()
