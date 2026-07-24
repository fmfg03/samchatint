# CTT OCR Pipeline - Quick Start Guide

## 5-Minute Setup

### Prerequisites

```bash
# Ensure dependencies are installed
pip install opencv-python numpy pymupdf anthropic httpx
```

### Required Files

1. **Template PDF:** `config/CedulaInscripcion_CTT_2026.pdf`
2. **Layout JSON:** `config/layout_ctt_2026.json`

---

## Usage Options

### Option 1: Telegram Bot (Recommended)

```bash
# Set credentials
export TELEGRAM_BOT_TOKEN="your-bot-token"
export ANTHROPIC_API_KEY="your-claude-key"

# Start bot
cd /root/samchat
source .venv/bin/activate
python3 ctt_telegram_bot.py

# Monitor
tail -f /tmp/ctt_bot.log
```

**User flow:**
1. Send `/start` to bot
2. Send photo of form FRONT
3. Send photo of form BACK
4. Receive extracted data

### Option 2: Command Line

```bash
python3 -m src.devnous.vision.ctt_form_extractor \
    --template-pdf config/CedulaInscripcion_CTT_2026.pdf \
    --layout config/layout_ctt_2026.json \
    --photo /path/to/form_photo.jpg \
    --out ./output_crops \
    --prefix scan001
```

**Output:**
```
output_crops/
├── scan001__aligned__front.png      # Aligned full page
├── scan001__header__equipo_nombre.png
├── scan001__header__equipo_nombre__v0.png
├── scan001__header__equipo_nombre__v1.png
├── scan001__header__equipo_nombre__v2.png
├── scan001__jugador_1__nombre.png
├── ...
└── scan001__front__crops.json       # Metadata
```

### Option 3: Python API

```python
from src.devnous.vision import CTTFormExtractor

# Initialize
extractor = CTTFormExtractor(
    template_pdf_path='config/CedulaInscripcion_CTT_2026.pdf',
    layout_json_path='config/layout_ctt_2026.json'
)

# Process photo
result = extractor.process_photo(
    photo_path='form_front.jpg',
    out_dir='./crops',
    prefix='player001'
)

# Access results
print(f"Side: {result['side']}")  # 'front' or 'back'
print(f"Fields: {len(result['fields'])}")

for field_key, field_data in result['fields'].items():
    if not field_data['empty']:
        print(f"  {field_key}: {field_data['variants']}")
```

---

## OCR Integration

After extraction, OCR each field with Claude Vision:

```python
import anthropic
import base64

client = anthropic.Anthropic()

def ocr_field(crop_path: str, field_type: str) -> str:
    with open(crop_path, 'rb') as f:
        image_b64 = base64.b64encode(f.read()).decode()

    prompts = {
        'curp': 'Transcribe EXACTAMENTE los 18 caracteres de la CURP manuscrita.',
        'name': 'Transcribe EXACTAMENTE el texto manuscrito.',
        'date': 'Transcribe EXACTAMENTE la fecha en formato DD/MM/AA.',
    }

    response = client.messages.create(
        model='claude-sonnet-4-5-20250929',
        max_tokens=100,
        temperature=0,
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': image_b64}},
                {'type': 'text', 'text': prompts.get(field_type, prompts['name'])}
            ]
        }]
    )
    return response.content[0].text.strip()

# OCR with ensemble voting
texts = [ocr_field(v, 'name') for v in field_data['variants']]
from collections import Counter
consensus = Counter(texts).most_common(1)[0][0]
```

---

## Layout Calibration

To adjust field positions for a new template:

### Step 1: Render template
```python
from src.devnous.vision import render_pdf_pages
pages = render_pdf_pages('template.pdf', dpi=300)
# pages[0] = front, pages[1] = back
```

### Step 2: Identify field coordinates
```python
import cv2

img = pages[0].copy()

# Click handler to get normalized coords
def on_click(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        h, w = img.shape[:2]
        print(f"x: {x/w:.4f}, y: {y/h:.4f}")

cv2.imshow('Template', img)
cv2.setMouseCallback('Template', on_click)
cv2.waitKey(0)
```

### Step 3: Update layout JSON
```json
{
  "field_name": {
    "x": 0.2392,  // Left edge (normalized)
    "y": 0.4300,  // Top edge (normalized)
    "w": 0.2537,  // Width (normalized)
    "h": 0.0273   // Height (normalized)
  }
}
```

---

## Validation Rules

### CURP (Mexican ID)
```python
import re

CURP_RE = re.compile(
    r"^[A-Z][AEIOUX][A-Z]{2}\d{2}(0[1-9]|1[0-2])([0-2]\d|3[01])[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[A-Z0-9]\d$"
)

def validate_curp(curp: str) -> bool:
    curp = curp.upper().replace(' ', '').replace('-', '')
    return len(curp) == 18 and bool(CURP_RE.match(curp))
```

### Names
```python
import unicodedata

def validate_name(name: str) -> bool:
    # Normalize accents
    name = unicodedata.normalize('NFD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    # Check valid characters
    return bool(re.match(r'^[A-Z\s]+$', name.upper()))
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Alignment fails | Better lighting, capture full page |
| Low accuracy | Use all 3 variants, check template version |
| Bot conflict (409) | Kill other instances, wait 35s, restart |
| Empty field false positive | Increase `min_ink_ratio` |

---

## Performance Tips

1. **Batch processing:** Use `multiprocessing` for multiple forms
2. **Caching:** Cache template rendering (expensive)
3. **Parallel OCR:** OCR variants concurrently with `asyncio.gather()`

```python
import asyncio

async def ocr_ensemble(variants, field_type):
    tasks = [asyncio.to_thread(ocr_field, v, field_type) for v in variants]
    return await asyncio.gather(*tasks)
```

---

## Next Steps

- Read full documentation: [CTT_OCR_PIPELINE.md](CTT_OCR_PIPELINE.md)
- Customize validation: `src/devnous/validation/hard_validator.py`
- Add new field types: Edit `config/layout_ctt_2026.json`
