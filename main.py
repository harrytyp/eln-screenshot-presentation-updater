#!/usr/bin/env python3
"""
eln-screenshot-presentation-updater — Main CLI entry point.

Usage:
    # Analyze a PPTX to see which screenshots will be updated
    python main.py analyze path/to/presentation.pptx

    # Capture new screenshots from the ELN test instance
    python main.py capture --env .env path/to/presentation.pptx

    # Update the PPTX with new screenshots
    python main.py update path/to/presentation.pptx --captured screenshots/

    # Full pipeline: analyze + capture + update
    python main.py run path/to/presentation.pptx --env .env --output updated.pptx

Environment variables:
    ELNTEST_URL      Base URL of the ELN (default: https://elntest.ub.tum.de)
    ELNTEST_EMAIL    Login email
    ELNTEST_PASSWORD Login password
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pptx_analyzer import extract_images, map_screenshots, generate_report
from eln_capturer import ELNCapturer
from pptx_updater import replace_screenshot_images, resize_and_crop_screenshots


def cmd_analyze(args):
    """Analyze a PPTX and report which screenshots will be updated."""
    pptx_path = args.pptx
    if not os.path.exists(pptx_path):
        print(f"ERROR: File not found: {pptx_path}")
        sys.exit(1)

    print(f"Analyzing: {pptx_path}")
    print()

    images = extract_images(pptx_path, output_dir=args.extract)
    unmapped = map_screenshots(images, args.mapping)

    report = generate_report(images)
    print(report)

    if unmapped:
        print()
        print("=" * 70)
        print("WARNING: Some screenshots have no mapping entry!")
        print("These images won't be updated unless you edit screenshot_mapping.yaml")
        for img in unmapped:
            print(f"  Slide {img.slide_number:>2} '{img.shape_name}' — {img.width_px}x{img.height_px}px")

    return images


def cmd_capture(args):
    """Capture new screenshots from the ELN test instance."""
    base_url = "https://elntest.ub.tum.de"

    # First analyze the PPTX
    images = extract_images(args.pptx)
    map_screenshots(images, args.mapping)

    # Initialize capturer (no credentials needed — user logs in manually)
    capturer = ELNCapturer(
        base_url=base_url,
        output_dir=args.outdir,
    )

    try:
        # Login
        print("Logging into ELN test instance...")
        if not capturer.login():
            print("ERROR: Login failed.")
            sys.exit(1)

        # Capture all mapped screenshots
        print("\nCapturing screenshots...")
        captured = capturer.capture_all(args.mapping)

    finally:
        # Cleanup
        capturer.stop_server()

    # Save the capture manifest
    manifest_path = os.path.join(args.outdir, "capture_manifest.txt")
    with open(manifest_path, "w") as f:
        f.write(f"# Screenshot capture manifest\n")
        f.write(f"# Source: {args.pptx}\n")
        f.write(f"# Date: {__import__('datetime').datetime.now().isoformat()}\n\n")
        for key, path in sorted(captured.items()):
            f.write(f"{key}={path}\n")
    print(f"\nManifest saved: {manifest_path}")
    print(f"Captured {len(captured)} screenshots in '{args.outdir}/'")

    return captured


def cmd_update(args):
    """Update the PPTX with new screenshots."""
    pptx_path = args.pptx
    if not os.path.exists(pptx_path):
        print(f"ERROR: File not found: {pptx_path}")
        sys.exit(1)

    # Load captured screenshots
    captured = {}
    if args.manifest:
        # Load from manifest file
        with open(args.manifest, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, path = line.partition("=")
                    captured[key.strip()] = path.strip()
    elif args.captured_dir:
        # Scan directory for screenshot files
        from pptx_analyzer import extract_images, map_screenshots
        images = extract_images(pptx_path)
        map_screenshots(images, args.mapping)

        for img in images:
            if not img.is_likely_screenshot:
                continue
            key = f"s{img.slide_number:02d}_{img.shape_name}"
            # Look for matching file
            safe_name = img.shape_name.replace(" ", "_")
            for f in os.listdir(args.captured_dir):
                if f.startswith(f"s{img.slide_number:02d}_{safe_name}") or \
                   f.startswith(f"s{img.slide_number:02d}_{img.shape_name}"):
                    captured[key] = os.path.join(args.captured_dir, f)
                    break

    if not captured:
        print("ERROR: No captured screenshots found. Use --manifest or --captured-dir")
        sys.exit(1)

    print(f"Found {len(captured)} captured screenshots")
    print()

    # Resize + crop screenshots to match original dimensions
    if args.resize:
        print("Resizing screenshots to match original dimensions...")
        reference_dir = args.extract or "extracted_images"
        captured = resize_and_crop_screenshots(captured, reference_dir, args.mapping, args.resize_dir)

    # Replace images in PPTX
    output_path = args.output or pptx_path.replace(".pptx", "_updated.pptx")
    print(f"Updating PPTX → {output_path}")
    print()

    replacements, warnings = replace_screenshot_images(
        pptx_path=pptx_path,
        output_path=output_path,
        captured=captured,
        mapping_path=args.mapping,
    )

    print(f"\n{'='*50}")
    print(f"✓ Updated {replacements} screenshots")
    print(f"✗ {len(warnings)} warnings")

    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  • {w}")

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"\nOutput: {output_path} ({size_mb:.1f} MB)")


def cmd_run(args):
    """Full pipeline: analyze → capture → resize → update."""
    if not args.output:
        base = args.pptx.replace(".pptx", "")
        args.output = f"{base}_updated.pptx"

    # Step 1: Analyze
    print("=" * 60)
    print("STEP 1/4: Analyze PPTX")
    print("=" * 60)
    images = extract_images(args.pptx, output_dir=args.extract)
    unmapped = map_screenshots(images, args.mapping)
    report = generate_report(images)
    print(report)
    print()

    if unmapped:
        print("WARNING: Some screenshots have no mapping entry!")
        for img in unmapped:
            print(f"  Slide {img.slide_number} '{img.shape_name}' — UNMAPPED")
        print("These will NOT be updated.")

    # Step 2: Capture (no credentials needed — user logs in manually)
    print("=" * 60)
    print("STEP 2/4: Capture screenshots from ELN")
    print("=" * 60)
    capturer = ELNCapturer(
        base_url="https://elntest.ub.tum.de",
        output_dir=args.capture_dir,
    )

    try:
        if not capturer.login():
            print("ERROR: Login failed.")
            sys.exit(1)

        captured = capturer.capture_all(args.mapping)
    finally:
        capturer.stop_server()

    if not captured:
        print("ERROR: No screenshots captured.")
        sys.exit(1)

    # Resize + crop screenshots to match original dimensions
    print("=" * 60)
    print("STEP 3/4: Resize + crop screenshots")
    print("=" * 60)
    captured = resize_and_crop_screenshots(
        captured, args.extract, args.mapping, args.resize_dir)

    # Step 5: Update PPTX
    print("=" * 60)
    print("STEP 4/4: Update PPTX")
    print("=" * 60)
    replacements, warnings = replace_screenshot_images(
        pptx_path=args.pptx,
        output_path=args.output,
        captured=captured,
        mapping_path=args.mapping,
    )

    print(f"\n{'='*60}")
    print(f"✓ COMPLETE: {replacements} screenshots updated")
    if warnings:
        print(f"Warnings ({len(warnings)}):")
        for w in warnings[:10]:
            print(f"  • {w}")
        if len(warnings) > 10:
            print(f"  … and {len(warnings) - 10} more")
    print(f"Output: {args.output} ({os.path.getsize(args.output) / 1024 / 1024:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(
        description="Update screenshots in an eLabFTW training presentation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mapping", default="screenshot_mapping.yaml",
                        help="Path to screenshot mapping YAML (default: screenshot_mapping.yaml)")
    parser.add_argument("--extract", default="extracted_images",
                        help="Directory to extract/save original images (default: extracted_images)")

    sub = parser.add_subparsers(dest="command", required=True)

    # analyze
    p = sub.add_parser("analyze", help="Analyze a PPTX and report screenshot status")
    p.add_argument("pptx", help="Path to the PowerPoint file")
    p.set_defaults(func=cmd_analyze)

    # capture
    p = sub.add_parser("capture", help="Capture new screenshots from the ELN instance")
    p.add_argument("pptx", help="Path to the PowerPoint file (for image analysis)")
    p.add_argument("--env", default=".env", help="Path to .env file with credentials")
    p.add_argument("--outdir", default="screenshots", help="Output directory for screenshots")
    p.set_defaults(func=cmd_capture)

    # update
    p = sub.add_parser("update", help="Update the PPTX with new screenshots")
    p.add_argument("pptx", help="Path to the PowerPoint file")
    p.add_argument("--output", "-o", help="Output PPTX path (default: *_updated.pptx)")
    p.add_argument("--manifest", help="Capture manifest file")
    p.add_argument("--captured-dir", help="Directory with captured screenshots")
    p.add_argument("--resize", action="store_true", help="Resize screenshots to match originals")
    p.add_argument("--resize-dir", default="screenshots_resized", help="Resized screenshot output dir")
    p.set_defaults(func=cmd_update)

    # run (full pipeline)
    p = sub.add_parser("run", help="Full pipeline: analyze → capture → update")
    p.add_argument("pptx", help="Path to the PowerPoint file")
    p.add_argument("--env", default=".env", help="Path to .env file with credentials")
    p.add_argument("--output", "-o", help="Output PPTX path")
    p.add_argument("--capture-dir", default="screenshots", help="Screenshot output dir")
    p.add_argument("--resize-dir", default="screenshots_resized", help="Resized screenshot output dir")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
