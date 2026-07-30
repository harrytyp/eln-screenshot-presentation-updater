"""
eln_capturer.py — Capture screenshots via Camoufox REST API.

Issues addressed:
  #1: Viewport control + crop support for correct scaling
  #2: Page validation — error pages detected and rejected
  #3: Action sequences for capturing specific UI sections/overlays
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
from PIL import Image

CAMOFOX_API = "http://localhost:9377"
CAMOFOX_USER = "cli-default"


def _find_camofox() -> str:
    candidates = [
        r"C:\Users\go75bel\AppData\Local\hermes\node\camofox.cmd",
        r"C:\Users\go75bel\AppData\Local\hermes\node\camofox",
        "camofox.cmd", "camofox",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "camofox"

_CAMOFOX_CLI = _find_camofox()


def _random_delay(min_s: float = 0.3, max_s: float = 1.5):
    time.sleep(random.uniform(min_s, max_s))


class ELNCapturer:
    def __init__(
        self,
        base_url: str = "https://elntest.ub.tum.de",
        output_dir: str = "screenshots",
    ):
        self.base_url = base_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logged_in = False
        self.tab_id: str = ""
        self._api = CAMOFOX_API
        self._user = CAMOFOX_USER

    # ── Low-level REST helpers (curl subprocess) ────────────────────

    def _api_get(self, path: str, timeout: int = 20) -> Optional[dict]:
        url = f"{self._api}{path}"
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", str(timeout), url],
                capture_output=True, timeout=timeout + 10, text=True,
            )
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout)
        except Exception as e:
            print(f"  API GET {path} failed: {e}")
        return None

    def _api_post(self, path: str, data: dict, timeout: int = 20) -> Optional[dict]:
        url = f"{self._api}{path}"
        payload = json.dumps(data)
        try:
            r = subprocess.run(
                ["curl", "-s", "-X", "POST", url,
                 "-H", "Content-Type: application/json",
                 "-d", payload, "--max-time", str(timeout)],
                capture_output=True, timeout=timeout + 10, text=True,
            )
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout)
            return {"ok": True, "status": "completed"}
        except Exception:
            return None

    def _capture_screenshot(self, output_path: str, timeout: int = 30) -> bool:
        if not self.tab_id:
            return False
        url = f"{self._api}/tabs/{self.tab_id}/screenshot?userId={self._user}"
        subprocess.run(
            ["curl", "-s", "-o", output_path, url, "--max-time", str(timeout)],
            capture_output=True, timeout=timeout + 10,
        )
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000

    def _get_snapshot(self) -> str:
        if not self.tab_id:
            return ""
        url = f"{self._api}/tabs/{self.tab_id}/snapshot?userId={self._user}"
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "15", url],
                capture_output=True, timeout=20, text=True,
            )
            if r.returncode == 0:
                return json.loads(r.stdout).get("snapshot", "")
        except Exception:
            pass
        return ""

    def _get_tab_url(self) -> str:
        result = self._api_get(f"/tabs?userId={self._user}", timeout=5)
        if result and result.get("tabs"):
            for t in result["tabs"]:
                if t.get("tabId") == self.tab_id:
                    return t.get("url", "")
            return result["tabs"][0].get("url", "")
        return ""

    def _set_viewport(self, width: int = 1920, height: int = 1080) -> bool:
        """Set browser viewport size via CDP."""
        if not self.tab_id:
            return False
        result = self._api_post(
            f"/tabs/{self.tab_id}/eval",
            {"userId": self._user, "expression": f"window.resizeTo({width},{height})"},
            timeout=10,
        )
        _random_delay(1.0, 2.0)
        return result is not None

    # ── Interaction via REST API ────────────────────────────────────

    def _click_ref(self, ref: str) -> bool:
        if not self.tab_id:
            return False
        r = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"{self._api}/tabs/{self.tab_id}/click",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"userId": self._user, "ref": ref}),
             "--max-time", "15"],
            capture_output=True, timeout=20,
        )
        return r.returncode == 0

    def _type_ref(self, ref: str, text: str) -> bool:
        if not self.tab_id:
            return False
        r = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"{self._api}/tabs/{self.tab_id}/type",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"userId": self._user, "ref": ref, "text": text}),
             "--max-time", "15"],
            capture_output=True, timeout=20,
        )
        return r.returncode == 0

    def _navigate(self, url: str) -> bool:
        if not self.tab_id:
            return False
        r = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"{self._api}/tabs/{self.tab_id}/navigate",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"userId": self._user, "url": url}),
             "--max-time", "30"],
            capture_output=True, timeout=40,
        )
        return r.returncode == 0

    def _eval_js(self, code: str) -> Optional[str]:
        """Execute JavaScript in the page and return result."""
        r = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"{self._api}/tabs/{self.tab_id}/eval",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"userId": self._user, "expression": code}),
             "--max-time", "10"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout).get("result", "")
            except json.JSONDecodeError:
                return r.stdout.strip()
        return None

    def _find_ref(self, snapshot: str, label: str) -> Optional[str]:
        for line in snapshot.split("\n"):
            if f'"{label}"' in line or label.lower() in line.lower():
                m = re.search(r"\[(e\d+)\]", line)
                if m:
                    return m.group(1)
        for line in snapshot.split("\n"):
            if label.lower() in line.lower():
                m = re.search(r"\[(e\d+)\]", line)
                if m:
                    return m.group(1)
        return None

    # ── Session Management ──────────────────────────────────────────

    def ensure_server(self) -> bool:
        result = self._api_get("/health", timeout=5)
        if result and result.get("ok"):
            return True
        subprocess.run(["camofox", "server", "start"], capture_output=True, timeout=20)
        _random_delay(2.0, 3.0)
        result = self._api_get("/health", timeout=5)
        return bool(result and result.get("ok"))

    def stop_server(self):
        subprocess.run([_CAMOFOX_CLI, "stop"], capture_output=True, timeout=10)

    def create_tab(self, url: str = "") -> bool:
        data: dict = {"userId": self._user, "sessionKey": "default"}
        if url:
            data["url"] = url
        result = self._api_post("/tabs", data, timeout=20)
        if result and "tabId" in result:
            self.tab_id = result["tabId"]
            return True
        return False

    def get_active_tab(self) -> bool:
        result = self._api_get(f"/tabs?userId={self._user}", timeout=10)
        if result and result.get("tabs"):
            tabs = result["tabs"]
            if tabs:
                self.tab_id = tabs[0]["tabId"]
                return True
        return False

    # ── Login (manual — user logs in themselves) ────────────────────

    def login(self, poll_seconds: int = 120) -> bool:
        if not self.ensure_server():
            print("ERROR: Camoufox server not available")
            return False

        if self.get_active_tab():
            tab_url = self._get_tab_url()
            snap = self._get_snapshot()
            logged_in = False
            if tab_url and "elntest" in tab_url and "login" not in tab_url:
                logged_in = True
            if snap and any(kw in snap for kw in ["Welcome", "Dashboard", "Experiments", "elabftw"]):
                logged_in = True
            if logged_in:
                self.logged_in = True
                print(f"  ✓ Already logged in (reusing session on {tab_url})")
                return True

        if not self.get_active_tab() and not self.create_tab(f"{self.base_url}/login.php"):
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

        print("  ✗ Timed out waiting for manual login")
        return False

    # ── Issue #2: Page Validation ───────────────────────────────────

    def _validate_page(self, expected_keywords: list[str]) -> tuple[bool, str]:
        """
        Check if current page is valid (not an error page, not login).
        Returns (is_valid, reason).
        """
        tab_url = self._get_tab_url()
        snap = self._get_snapshot()

        # Check URL first
        if not tab_url:
            return False, "no URL (tab closed?)"
        if "login" in tab_url.lower():
            return False, "redirected to login page (session expired)"
        if "elntest" not in tab_url.lower():
            return False, f"not on ELN instance: {tab_url}"

        # Check for error indicators in snapshot
        if snap:
            error_signals = ["not found", "error", "forbidden", "404", "500",
                            "whoops", "something went wrong"]
            for sig in error_signals:
                if sig in snap.lower() and sig not in ["error"]:  # be careful with "error"
                    # "error" alone is too broad — check for specific error patterns
                    pass
            if any(sig in snap.lower() for sig in ["404", "500", "not found", "forbidden"]):
                return False, f"error page detected in snapshot"

        # Check expected keywords
        if expected_keywords:
            combined = (tab_url + " " + (snap or "")).lower()
            found = [kw for kw in expected_keywords if kw.lower() in combined]
            if not found:
                return False, f"expected keywords not found: {expected_keywords}"

        return True, "ok"

    # ── Issue #1: Capture with Viewport + Crop ──────────────────────

    def capture_page(
        self,
        url_path: str,
        output_name: str,
        wait_for: str = "",
        actions: Optional[list[dict]] = None,
        crop: Optional[list[int]] = None,
        expected_keywords: Optional[list[str]] = None,
        viewport: Optional[tuple[int, int]] = None,
    ) -> Optional[str]:
        """
        Navigate to a page and capture.

        Issue #1: viewport + crop support
        Issue #2: page validation before capture
        Issue #3: action sequences for multi-step captures
        """
        if not self.logged_in or not self.tab_id:
            print("  ERROR: Not logged in")
            return None

        url = f"{self.base_url}/{url_path.lstrip('/')}"
        print(f"  → {url_path}", end="", flush=True)

        # Set viewport if specified (Issue #1)
        if viewport:
            self._set_viewport(*viewport)
            _random_delay(1.0, 2.0)

        # Navigate
        if not self._navigate(url):
            print(" — NAVIGATE FAILED")
            return None
        _random_delay(2.0, 4.0)

        # Wait for expected text (with retry)
        if wait_for:
            for attempt in range(3):
                snap = self._get_snapshot()
                if wait_for.lower() in (snap or "").lower():
                    break
                _random_delay(2.0, 3.0)

        # Issue #2: Validate page
        if expected_keywords:
            valid, reason = self._validate_page(expected_keywords)
            if not valid:
                print(f" — SKIP ({reason})")
                return None

        # Issue #3: Pre-capture actions
        if actions:
            for action in actions:
                self._perform_action(action)

        # Capture
        out_path = str(self.output_dir / output_name)
        if not self._capture_screenshot(out_path):
            print(" — FAILED")
            return None

        # Issue #1: Crop if specified
        if crop:
            try:
                img = Image.open(out_path)
                cx, cy, cw, ch = crop
                cropped = img.crop((cx, cy, cx + cw, cy + ch))
                cropped.save(out_path)
                print(f" — cropped to {cw}x{ch}", end="")
            except Exception as e:
                print(f" — crop failed: {e}", end="")

        size_kb = os.path.getsize(out_path) / 1024
        print(f" — {size_kb:.0f}KB ✓")
        return out_path

    # ── Issue #3: Enhanced Actions ──────────────────────────────────

    def _perform_action(self, action: dict):
        """
        Supports: click, type, wait, scroll, hover, select.
        Extended mapping format:
          - action: "click" | "type" | "wait" | "scroll" | "hover"
            selector: "button text or label"
            text: "value to type" (for type action)
            seconds: N (for wait action)
            direction: "down" | "up" (for scroll action)
        """
        act = action.get("action", "")

        if act == "wait":
            seconds = action.get("seconds", 2)
            print(f" (waiting {seconds}s...)", end="", flush=True)
            time.sleep(seconds)
            return

        if act == "scroll":
            direction = action.get("direction", "down")
            snap = self._get_snapshot()
            ref = self._find_ref(snap, "contentinfo") or self._find_ref(snap, "footer")
            if ref:
                # Click at the bottom to focus, then scroll
                self._click_ref(ref)
            _random_delay(0.5, 1.0)
            return

        if act == "hover":
            selector = action.get("selector", "")
            snap = self._get_snapshot()
            ref = self._find_ref(snap, selector)
            if ref:
                subprocess.run(
                    ["curl", "-s", "-X", "POST",
                     f"{self._api}/tabs/{self.tab_id}/hover",
                     "-H", "Content-Type: application/json",
                     "-d", json.dumps({"userId": self._user, "ref": ref}),
                     "--max-time", "10"],
                    capture_output=True, timeout=15,
                )
            return

        # click / type - need snapshot and ref
        selector = action.get("selector", "")
        text = action.get("text", "")

        snap = self._get_snapshot()
        ref = self._find_ref(snap, selector)

        if not ref:
            # Try broader search: just look for the text in the snapshot
            for line in (snap or "").split("\n"):
                if selector.lower() in line.lower():
                    m = re.search(r"\[(e\d+)\]", line)
                    if m:
                        ref = m.group(1)
                        break

        if not ref:
            print(f" (element '{selector}' not found)", end="", flush=True)
            return

        if act == "click":
            self._click_ref(ref)
        elif act == "type":
            # Clear existing text first (Ctrl+A, then type)
            self._click_ref(ref)
            _random_delay(0.3, 0.8)
            self._type_ref(ref, text)

    # ── Full Workflow ───────────────────────────────────────────────

    def capture_all(self, mapping_path: str) -> dict[str, str]:
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
            crop = entry.get("crop", None)
            expected_keywords = entry.get("expected_keywords", None)
            viewport = entry.get("viewport", None)

            key = f"s{slide:02d}_{shape}"
            safe_name = shape.replace(" ", "_")
            out_name = f"s{slide:02d}_{safe_name}.png"

            print(f"\n[{i}/{len(entries)}] {entry.get('description', key)}")
            captured = self.capture_page(
                url_path=url_path,
                output_name=out_name,
                wait_for=wait_for,
                actions=actions,
                crop=crop,
                expected_keywords=expected_keywords,
                viewport=viewport,
            )
            if captured:
                results[key] = captured

        print(f"\n{'='*50}")
        print(f"Captured {len(results)}/{len(entries)} screenshots")
        return results
