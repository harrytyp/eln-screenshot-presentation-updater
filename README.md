# ELN Screenshot Presentation Updater

Automatically update screenshots in an eLabFTW training presentation by capturing fresh screenshots from the [TUM eLabFTW test instance](https://elntest.ub.tum.de).

## Quick Start

```bash
# 1. Set up credentials
cp .env.template .env
# Edit .env with your test instance credentials

# 2. Full pipeline: analyze + capture + update
python main.py run path/to/your_presentation.pptx --env .env
```

## Commands

### `analyze` — See what will be updated

```bash
python main.py analyze 20260616_eLabFTW_Essentials_V07.pptx
```

Output: a table showing each image, whether it's a 'likely screenshot', and its mapped ELN page.

### `capture` — Take fresh screenshots from the ELN test instance

```bash
python main.py capture 20260616_eLabFTW_Essentials_V07.pptx --env .env
```

This logs into elntest.ub.tum.de using Camoufox and captures screenshots
of each page listed in `screenshot_mapping.yaml`.

### `update` — Replace old images with new ones

```bash
python main.py update 20260616_eLabFTW_Essentials_V07.pptx --captured-dir screenshots/ -o updated.pptx
```

### `run` — Full pipeline

```bash
python main.py run 20260616_eLabFTW_Essentials_V07.pptx --env .env -o 20260616_eLabFTW_Essentials_V08.pptx
```

## How It Works

1. **Analyze**: Extracts all images from the PPTX. Classifies each as a 'screenshot' (large PNG with visible content) or 'icon/diagram' (small images, logos, QR codes).
2. **Map**: Each screenshot is matched to an ELN page URL using `screenshot_mapping.yaml`.
3. **Capture**: Uses Camoufox (headed Firefox) to log into elntest.ub.tum.de and take screenshots of each mapped page.
4. **Resize**: New screenshots are resized to match the original image dimensions so they fit perfectly in the PPTX layout.
5. **Update**: Old image blobs are replaced with new ones in the PPTX XML structure.

## Requirements

- Python 3.10+
- Camoufox CLI (`camofox`) — auto-downloads on first use
- See `requirements.txt` for Python dependencies

## Configuration

### `screenshot_mapping.yaml`

Maps each image in the PPTX (by slide number + shape name) to an ELN page URL. Edit this file to:
- Add new screenshot mappings
- Change which page a screenshot should come from
- Exclude certain images from updates

### `.env`

```
ELNTEST_URL=https://elntest.ub.tum.de
ELNTEST_EMAIL=your-email@example.org
ELNTEST_PASSWORD=your-password
```

## Project Structure

```
├── main.py                    # CLI entry point
├── screenshot_mapping.yaml    # Image → ELN page mapping
├── pptx_analyzer.py           # Extract + analyze images from PPTX
├── eln_capturer.py            # Login + screenshot capture via Camoufox
├── pptx_updater.py            # Replace images in PPTX
├── requirements.txt
├── .env.template
└── README.md
```
