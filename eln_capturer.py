"""
eln_capturer.py — Log into elntest.ub.tum.de and capture screenshots via Camoufox.

Uses the Camoufox REST API (localhost:9377) to drive the browser — this is more
reliable than the CLI on Windows, especially for screenshots.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import random
from pathlib import Path
from typing import Optional

import yaml

# REST API base for Camoufox
CAMOFOX_API = "http://localhost:9377"
CAMOFOX_USER = "cli-default"

# Find camofox CLI path
def _find_camofox() -> str:
    """Find the camofox CLI executable."""
    candidates = [
        r"C:\Users\go75bel\AppData\Local\hermes\node\camofox.cmd",
        r"C:\Users\go75bel\AppData\Local\hermes\node\camofox",
        "camofox.cmd",
        "camofox",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "camofox"  # fallback — might work if in PATH

_CAMOFOX_CLI = _find_camofox()

# Random delay helpers (human-like behavior)
def _random_delay(min_s: float = 0.3, max_s: float = 1.5):
    time.sleep(random.uniform(min_s, max_s))


class ELNCapturer:
    """
    Handles logging into the ELN test instance and capturing page screenshots
    via the Camoufox REST API.
    """

    def __init__(
        self,
        base_url: str = "https://elntest.ub.tum.de",
        email: str = "",
        password: str = "",
        output_dir: str = "screenshots",
    ):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logged_in = False
        self.tab_id: str = ""
        self._api = CAMOFOX_API
        self._user = CAMOFOX_USER

    # ── Low-level REST helpers ──────────────────────────────────────

    def _api_get(self, path: str, timeout: int = 20) -> Optional[dict]:
        """GET request to Camoufox REST API via curl subprocess."""
        url = f"{self._api}{path}"
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", str(timeout), url],
                capture_output=True, timeout=timeout + 10, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except Exception as e:
            print(f"  API GET {path} failed: {e}")
        return None

    def _api_post(self, path: str, data: dict, timeout: int = 20) -> Optional[dict]:
        """POST request to Camoufox REST API via curl subprocess."""
        url = f"{self._api}{path}"
        payload = json.dumps(data)
        try:
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", url,
                 "-H", "Content-Type: application/json",
                 "-d", payload, "--max-time", str(timeout)],
                capture_output=True, timeout=timeout + 10, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
            return {"ok": True, "status": "completed"}
        except Exception as e:
            print(f"  API POST {path} failed: {e}")
        return None

    def _capture_screenshot(self, output_path: str, timeout: int = 30) -> bool:
        """Download a screenshot via REST API and save to output_path."""
        if not self.tab_id:
            return False
        url = f"{self._api}/tabs/{self.tab_id}/screenshot?userId={self._user}"
        result = subprocess.run(
            ["curl", "-s", "-o", output_path, url, "--max-time", str(timeout)],
            capture_output=True, timeout=timeout + 10,
        )
        return result.returncode == 0 and os.path.exists(output_path)

    def _get_snapshot(self) -> str:
        """Get current page snapshot text via REST API (curl subprocess)."""
        if not self.tab_id:
            return ""
        url = f"{self._api}/tabs/{self.tab_id}/snapshot?userId={self._user}"
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "15", url],
                capture_output=True, timeout=20, text=True,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get("snapshot", "")
        except Exception:
            pass
        return ""

    def _get_tab_url(self) -> str:
        """Get current tab URL via REST API (from tab listing)."""
        result = self._api_get(f"/tabs?userId={self._user}", timeout=5)
        if result and result.get("tabs"):
            tabs = result["tabs"]
            # Find our tab
            for t in tabs:
                if t.get("tabId") == self.tab_id:
                    return t.get("url", "")
            # Fallback: first tab
            if tabs:
                return tabs[0].get("url", "")
        return ""

    def _click_ref(self, ref: str) -> bool:
        """Click an element by ref via REST API."""
        if not self.tab_id:
            return False
        api_url = f"{self._api}/tabs/{self.tab_id}/click"
        data = json.dumps({"userId": self._user, "ref": ref})
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", api_url,
             "-H", "Content-Type: application/json",
             "-d", data, "--max-time", "15"],
            capture_output=True, timeout=20,
        )
        return result.returncode == 0

    def _type_ref(self, ref: str, text: str) -> bool:
        """Type text into an element via REST API."""
        if not self.tab_id:
            return False
        api_url = f"{self._api}/tabs/{self.tab_id}/type"
        data = json.dumps({"userId": self._user, "ref": ref, "text": text})
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", api_url,
             "-H", "Content-Type: application/json",
             "-d", data, "--max-time", "15"],
            capture_output=True, timeout=20,
        )
        return result.returncode == 0

    def _navigate(self, url: str) -> bool:
        """Navigate the current tab to a URL via REST API."""
        if not self.tab_id:
            return False
        api_url = f"{self._api}/tabs/{self.tab_id}/navigate"
        data = json.dumps({"userId": self._user, "url": url})
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", api_url,
             "-H", "Content-Type: application/json",
             "-d", data, "--max-time", "30"],
            capture_output=True, timeout=40,
        )
        return result.returncode == 0

    def _find_ref(self, snapshot: str, label: str) -> Optional[str]:
        """
        Find the ref (e.g. 'e4') for an element matching label text.

        REST snapshot format:
          - textbox "Email" [e4]
          - button "Login" [e9]
          - textbox "Password" [e5]
        """
        # Strategy: find a line containing the label (in quotes) AND a [eN] ref
        for line in snapshot.split("\n"):
            if f'"{label}"' in line or label.lower() in line.lower():
                m = re.search(r"\[(e\d+)\]", line)
                if m:
                    return m.group(1)
        # Broader: label anywhere on the line
        for line in snapshot.split("\n"):
            if label.lower() in line.lower():
                m = re.search(r"\[(e\d+)\]", line)
                if m:
                    return m.group(1)
        return None

    # ── Session Management ──────────────────────────────────────────

    def ensure_server(self) -> bool:
        """Make sure the Camoufox server is running."""
        result = self._api_get("/health", timeout=5)
        if result and result.get("ok"):
            return True
        # Try starting it
        subprocess.run(["camofox", "server", "start"], capture_output=True, timeout=20)
        _random_delay(2.0, 3.0)
        result = self._api_get("/health", timeout=5)
        return bool(result and result.get("ok"))

    def stop_server(self):
        """Stop the Camoufox server via CLI."""
        subprocess.run([_CAMOFOX_CLI, "stop"], capture_output=True, timeout=10)

    def create_tab(self, url: str = "") -> bool:
        """Create a new browser tab. Returns True on success."""
        data: dict = {"userId": self._user, "sessionKey": "default"}
        if url:
            data["url"] = url
        result = self._api_post("/tabs", data, timeout=20)
        if result and "tabId" in result:
            self.tab_id = result["tabId"]
            return True
        return False

    def get_active_tab(self) -> bool:
        """Find the active tab via the REST API."""
        result = self._api_get(f"/tabs?userId={self._user}", timeout=10)
        if result and result.get("tabs"):
            tabs = result["tabs"]
            if tabs:
                self.tab_id = tabs[0]["tabId"]
                return True
        return False

    # ── Login ───────────────────────────────────────────────────────

    def login(self, poll_seconds: int = 120) -> bool:
        """
        Detect existing session or wait for user to log in manually.

        The user always logs in themselves in the Camoufox browser window.
        This method waits until the browser is on an ELN page (not login).

        Args:
            poll_seconds: Max seconds to wait for user to log in
        """
        if not self.ensure_server():
            print("ERROR: Camoufox server not available")
            return False

        # Try to reuse existing tab/session first
        if self.get_active_tab():
            tab_url = self._get_tab_url()
            snap = self._get_snapshot()
            logged_in = False
            if tab_url and "elntest" in tab_url and "login" not in tab_url:
                logged_in = True
            if snap and ("Welcome" in snap or "Dashboard" in snap or "Experiments" in snap or "elabftw" in snap.lower()):
                logged_in = True

            if logged_in:
                self.logged_in = True
                print(f"  ✓ Already logged in (reusing session on {tab_url})")
                return True

        # No active session — open login page and wait for user
        if not self.get_active_tab() and not self.create_tab(f"{self.base_url}/login.php"):
            print("  ERROR: Could not create tab. Opening browser manually...")
            subprocess.run([_CAMOFOX_CLI, "open", f"{self.base_url}/login.php"],
                          capture_output=True, timeout=15)

        print(f"  → Opened {self.base_url}/login.php")
        print(f"  → Please log in manually in the Camoufox browser window")
        print(f"  → Waiting up to {poll_seconds}s...")

        deadline = time.time() + poll_seconds
        while time.time() < deadline:
            _random_delay(3.0, 5.0)
            tab_url = self._get_tab_url()
            if tab_url and "elntest" in tab_url and "login" not in tab_url:
                self.logged_in = True
                print(f"  ✓ Detected login! Current page: {tab_url}")
                return True
            # Also check snapshot
            snap = self._get_snapshot()
            if snap and ("Welcome" in snap or "Dashboard" in snap or "Experiments" in snap or "elabftw" in snap.lower()):
                self.logged_in = True
                print("  ✓ Detected login! (via snapshot)")
                return True

        print("  ✗ Timed out waiting for manual login")
        return False

    # ── Screenshot Capture ──────────────────────────────────────────

    def capture_page(
        self,
        url_path: str,
        output_name: str,
        wait_for: str = "",
        actions: Optional[list[dict]] = None,
    ) -> Optional[str]:
        """
        Navigate to a page and take a screenshot.

        Args:
            url_path: URL path relative to base_url (e.g. "dashboard.php")
            output_name: Filename for the screenshot
            wait_for: Text to verify in snapshot after navigation
            actions: List of pre-capture interactions

        Returns:
            Path to saved screenshot, or None on failure.
        """
        if not self.logged_in or not self.tab_id:
            print("  ERROR: Not logged in. Call login() first.")
            return None

        url = f"{self.base_url}/{url_path.lstrip('/')}"
        print(f"  → {url_path}", end="", flush=True)

        if not self._navigate(url):
            print(" — NAVIGATE FAILED")
            return None
        _random_delay(2.0, 4.0)

        # Verify page loaded if wait_for specified
        if wait_for:
            snap = self._get_snapshot()
            if wait_for.lower() not in snap.lower():
                print(f" (waiting for '{wait_for}'...)")
                _random_delay(2.0, 3.0)
                snap = self._get_snapshot()
                if wait_for.lower() not in snap.lower():
                    print(f"  ⚠ '{wait_for}' not found, capturing anyway")

        # Pre-capture actions
        if actions:
            for action in actions:
                self._perform_action(action)
                _random_delay(0.5, 1.5)

        # Screenshot
        out_path = str(self.output_dir / output_name)
        if self._capture_screenshot(out_path):
            size_kb = os.path.getsize(out_path) / 1024
            print(f" — {size_kb:.0f}KB ✓")
            return out_path
        else:
            print(" — FAILED")
            return None

    def _perform_action(self, action: dict):
        """Perform a single interaction via REST API."""
        act = action.get("action", "")
        selector = action.get("selector", "")
        text = action.get("text", "")

        snap = self._get_snapshot()
        ref = self._find_ref(snap, selector)

        if not ref:
            print(f"  ⚠ Element '{selector}' not found, skipping")
            return

        if act == "click":
            self._click_ref(ref)
        elif act == "type":
            self._type_ref(ref, text)

    # ── Full Workflow ───────────────────────────────────────────────

    def capture_all(self, mapping_path: str) -> dict[str, str]:
        """Capture all screenshots from the mapping YAML."""
        with open(mapping_path, "r", encoding="utf-8") as f:
            mapping_data = yaml.safe_load(f)

        entries = mapping_data.get("screenshots", [])
        results: dict[str, str] = {}

        for i, entry in enumerate(entries, 1):
            slide = entry["slide"]
            shape = entry["shape"]
            url_path = entry["url"]
            wait_for = entry.get("wait_for", "")
            actions = entry.get("actions", None)

            key = f"s{slide:02d}_{shape}"
            safe_name = shape.replace(" ", "_")
            out_name = f"s{slide:02d}_{safe_name}.png"

            print(f"\n[{i}/{len(entries)}] {entry.get('description', key)}")
            captured = self.capture_page(
                url_path=url_path,
                output_name=out_name,
                wait_for=wait_for,
                actions=actions,
            )
            if captured:
                results[key] = captured

        print(f"\n{'='*50}")
        print(f"Captured {len(results)}/{len(entries)} screenshots")
        return results
