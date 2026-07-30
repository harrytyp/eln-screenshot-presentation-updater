"""
pptx_updater.py — Replace images in a PPTX with new screenshots.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


def replace_screenshot_images(
    pptx_path: str,
    output_path: str,
    captured: dict[str, str],
    mapping_path: str,
) -> tuple[int, list[str]]:
    from pptx_analyzer import extract_images, map_screenshots

    warnings: list[str] = []
    images = extract_images(pptx_path)
    unmapped = map_screenshots(images, mapping_path)

    if unmapped:
        for img in unmapped:
            warnings.append(
                f"Slide {img.slide_number} '{img.shape_name}' — "
                f"no mapping entry; screenshot NOT replaced."
            )

    prs = Presentation(pptx_path)
    replacements = 0

    for image_info in images:
        if not image_info.is_likely_screenshot:
            continue

        key = f"s{image_info.slide_number:02d}_{image_info.shape_name}"

        if key not in captured:
            warnings.append(f"Slide {image_info.slide_number} '{image_info.shape_name}' — no captured screenshot available")
            continue

        new_image_path = captured[key]
        if not os.path.exists(new_image_path):
            warnings.append(f"Captured file not found: {new_image_path}")
            continue

        # Validate: reject tiny files (25B = JSON error "Tab not found")
        img_size = os.path.getsize(new_image_path)
        if img_size < 1000:
            warnings.append(f"Slide {image_info.slide_number} '{image_info.shape_name}' — captured file too small ({img_size}B), skipping")
            continue

        slide = prs.slides[image_info.slide_index]
        replaced = _replace_shape_image(slide, image_info.shape_name, new_image_path)
        if replaced:
            replacements += 1
        else:
            warnings.append(f"Slide {image_info.slide_number} '{image_info.shape_name}' — shape not found for replacement")

    prs.save(output_path)
    return replacements, warnings


def _replace_shape_image(slide, shape_name: str, new_image_path: str) -> bool:
    NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    for shape in slide.shapes:
        if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
            continue
        if shape.name != shape_name:
            continue

        with open(new_image_path, "rb") as f:
            new_blob = f.read()

        if not new_blob:
            return False

        blip = shape._element.find(f".//{{{NS_A}}}blip")
        if blip is None:
            return False

        rId = blip.get(f"{{{NS_R}}}embed")
        if not rId:
            return False

        rel = slide.part.rels[rId]
        rel.target_part.blob = new_blob
        return True

    return False


def resize_and_crop_screenshots(
    captured: dict[str, str],
    reference_dir: str,
    mapping_path: str,
    output_dir: str = "screenshots_resized",
) -> dict[str, str]:
    """
    Resize new screenshots to match original dimensions AND apply crop
    regions from the mapping YAML.
    """
    import yaml
    os.makedirs(output_dir, exist_ok=True)
    resized: dict[str, str] = {}

    # Load mapping to get crop info
    with open(mapping_path) as f:
        mapping = yaml.safe_load(f)
    crop_map = {}
    for entry in mapping.get("screenshots", []):
        key = f"s{entry['slide']:02d}_{entry['shape']}"
        if "crop" in entry:
            crop_map[key] = entry["crop"]

    for key, new_path in captured.items():
        ref_file = None
        safe_key = key.replace(" ", "_")
        for f in os.listdir(reference_dir):
            if f.startswith(safe_key) or f.startswith(key.replace(" ", "_")):
                ref_file = os.path.join(reference_dir, f)
                break
        if not ref_file:
            slide_part = key.split("_")[0]
            for f in os.listdir(reference_dir):
                if f.startswith(slide_part):
                    ref_file = os.path.join(reference_dir, f)
                    break

        if not ref_file:
            resized[key] = new_path
            continue

        ref_img = Image.open(ref_file)
        ref_w, ref_h = ref_img.size

        new_img = Image.open(new_path)
        new_w, new_h = new_img.size

        # Step 1: Apply crop if specified in mapping
        if key in crop_map:
            cx, cy, cw, ch = crop_map[key]
            new_img = new_img.crop((cx, cy, cx + cw, cy + ch))
            new_w, new_h = new_img.size

        # Step 2: Resize to match original dimensions
        if (new_w, new_h) != (ref_w, ref_h):
            resized_img = new_img.resize((ref_w, ref_h), Image.Resampling.LANCZOS)
            out_path = os.path.join(output_dir, os.path.basename(new_path))
            if resized_img.mode == "RGBA":
                resized_img = resized_img.convert("RGB")
            resized_img.save(out_path)
            resized[key] = out_path
            print(f"  Resized {os.path.basename(new_path)}: {new_w}x{new_h} → {ref_w}x{ref_h}")
        else:
            resized[key] = new_path

    return resized
