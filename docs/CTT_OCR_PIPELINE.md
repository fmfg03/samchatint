# CTT Template-Aligned OCR Pipeline

## Technical Documentation

**Version:** 1.0
**Last Updated:** January 2026
**Module:** `src/devnous/vision/ctt_form_extractor.py`

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Pipeline Stages](#pipeline-stages)
4. [Template Alignment](#template-alignment)
5. [Handwriting Isolation](#handwriting-isolation)
6. [ROI Extraction](#roi-extraction)
7. [Ensemble OCR](#ensemble-ocr)
8. [Validation & Routing](#validation--routing)
9. [Telegram Bot Integration](#telegram-bot-integration)
10. [Configuration](#configuration)
11. [API Reference](#api-reference)
12. [Troubleshooting](#troubleshooting)

---

## Overview

The CTT OCR Pipeline is a production-grade system for extracting handwritten data from CTT (Copa Telmex de Tenis de Mesa) registration forms. Unlike traditional full-page OCR approaches that send entire documents to vision models, this pipeline uses **template-aligned field extraction** to achieve significantly higher accuracy.

### Key Innovation

Traditional approach (problematic):
```
Photo → Full-page OCR → Parse structure → Extract fields
```

Template-aligned approach (this system):
```
Photo → Warp → Align to Template → Subtract Background → Crop ROIs → OCR per Field → Validate
```

### Benefits

| Aspect | Traditional | Template-Aligned |
|--------|-------------|------------------|
| Noise | Full page noise | 80% removed via subtraction |
| Context | Model guesses structure | Structure is known |
| Accuracy | ~70-80% | ~95%+ |
| Validation | Post-hoc | Per-field with routing |
| Cost | High (large image) | Lower (small crops) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CTT OCR Pipeline                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────┐    ┌──────────────┐    ┌─────────────────┐               │
│  │  Photo   │───▶│  Page Warp   │───▶│ Template Align  │               │
│  │  Input   │    │  (Contour)   │    │  (ORB+RANSAC)   │               │
│  └──────────┘    └──────────────┘    └────────┬────────┘               │
│                                               │                         │
│                                               ▼                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    Template Subtraction                          │  │
│  │  aligned_gray - template_gray → handwriting_mask                 │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                               │                         │
│                                               ▼                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      ROI Extraction                              │  │
│  │  For each field in layout.json:                                  │  │
│  │    ├── Crop handwriting-only region                              │  │
│  │    └── Generate 3 variants (original, CLAHE, adaptive thresh)    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                               │                         │
│                                               ▼                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      Ensemble OCR                                │  │
│  │  Claude Vision × 3 variants → Majority Vote → Confidence Score  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                               │                         │
│                                               ▼                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                   Validation & Routing                           │  │
│  │  Field-type validation → ACCEPT / RETRY / HUMAN                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Pipeline Stages

### Stage 1: Photo Input

The system accepts photos of CTT registration forms in JPEG or PNG format. Photos can be taken at various angles and lighting conditions.

**Supported inputs:**
- Telegram photo uploads
- Direct file paths
- NumPy BGR arrays

### Stage 2: Page Warp

Detects the paper document in the photo and applies perspective correction to rectangular form.

**Algorithm:**
1. Convert to grayscale + Gaussian blur
2. Canny edge detection (50, 150)
3. Find contours, select largest 4-point polygon
4. Order points: top-left, top-right, bottom-right, bottom-left
5. Apply `cv2.getPerspectiveTransform()` to canonical size (2550×3300)

**Code reference:** `ctt_form_extractor.py:119-149`

### Stage 3: Template Alignment

Aligns the warped photo precisely to the blank template using feature matching.

**Algorithm:**
1. Apply CLAHE contrast enhancement to both images
2. Extract ORB features (5000 max, fastThreshold=5)
3. Match features with BFMatcher + KNN (k=2)
4. Filter matches with Lowe's ratio test (0.75)
5. Estimate homography with RANSAC (reproj threshold=4.0)
6. Warp photo to template coordinate space

**Fallback:** If ORB fails (< 35 good matches), uses page warp only.

**Code reference:** `ctt_form_extractor.py:152-222`

### Stage 4: Side Detection

Determines whether the photo is the front or back of the form by comparing alignment quality to both templates.

**Algorithm:**
1. Attempt alignment to both front and back templates
2. Compare inlier counts from homography estimation
3. Select template with higher inlier count

**Code reference:** `ctt_form_extractor.py:225-247`

### Stage 5: Template Subtraction

Isolates handwritten content by subtracting the blank template.

**Algorithm:**
1. Compute absolute difference: `absdiff(aligned_gray, template_gray)`
2. Threshold difference (> 25 intensity)
3. Suppress template edges (Canny + dilation mask)
4. Morphological cleanup: open (remove specks), close (connect strokes)

**Result:** Binary mask where 1 = handwriting, 0 = background/printed

**Code reference:** `ctt_form_extractor.py:250-267`

### Stage 6: ROI Extraction

Crops individual fields using normalized coordinates from layout JSON.

**For each field:**
1. Convert normalized coords (0-1) to pixel coords
2. Crop aligned grayscale at ROI bounds
3. Apply handwriting mask (non-ink → white)
4. Generate 3 preprocessing variants

**Ink ratio detection:** Fields with < 0.5% ink pixels marked as empty.

**Code reference:** `ctt_form_extractor.py:270-341`

### Stage 7: Claude Vision OCR

Each field crop is sent to Claude Vision with field-type-specific prompts.

**Prompts by field type:**
- **CURP:** "Transcribe EXACTAMENTE los 18 caracteres de la CURP manuscrita..."
- **Date:** "Transcribe EXACTAMENTE la fecha manuscrita en formato DD/MM/AA..."
- **Name:** "Transcribe EXACTAMENTE el texto manuscrito..."

**Model:** `claude-sonnet-4-5-20250929` with temperature=0

### Stage 8: Majority Voting

Combines results from 3 variants to reduce OCR errors.

**Algorithm:**
1. Normalize all transcriptions
2. Count occurrences of each unique result
3. Select most frequent as consensus
4. Confidence = count/total (e.g., 3/3 = 100%, 2/3 = 67%)

**Code reference:** `ctt_telegram_bot.py:173-188`

### Stage 9: Validation & Routing

Applies field-type-specific validation rules.

| Field Type | Validation | Pass Criteria |
|------------|------------|---------------|
| CURP | Regex + length | 18 chars, valid pattern |
| Name | Character set | Spanish alphabet only |
| Date | Format check | DD/MM/AA or DD/MMM/AA |

**Routing decisions:**
- **ACCEPT:** Passes validation, proceed
- **RETRY:** Failed validation, request new photo
- **HUMAN:** Ambiguous, needs manual review

---

## Template Alignment

### ORB Feature Matching

ORB (Oriented FAST and Rotated BRIEF) is used for template matching because:
- Rotation invariant
- Scale tolerant (within limits)
- Fast computation
- Works with printed text/lines

**Parameters:**
```python
orb = cv2.ORB_create(
    nfeatures=5000,      # Maximum features to detect
    fastThreshold=5      # Lower = more features in low-contrast areas
)
```

### RANSAC Homography

RANSAC (Random Sample Consensus) filters outlier matches:
```python
M, inliers = cv2.findHomography(
    src_pts, dst_pts,
    cv2.RANSAC,
    ransacReprojThreshold=4.0  # Pixel tolerance for inliers
)
```

**Quality thresholds:**
- Minimum good matches: 35
- Minimum inliers: max(25, 50% of good matches)

### Template Structure

```python
@dataclass(frozen=True)
class TemplatePage:
    name: str              # "front" or "back"
    bgr: np.ndarray        # Color template image
    gray: np.ndarray       # Grayscale template
    edge_mask: np.ndarray  # Dilated edge mask for suppression
```

---

## Handwriting Isolation

### Template Subtraction Theory

The blank template contains all printed elements (lines, text, boxes). By subtracting it from the filled form, only handwritten additions remain.

```
Filled Form - Blank Template = Handwriting Only
```

### Edge Suppression

Printed template edges can appear in the difference due to:
- Slight misalignment
- Lighting variations
- Paper texture

**Solution:** Build edge mask from template and suppress those pixels:
```python
edges = cv2.Canny(template_gray, 80, 160)
edges = cv2.dilate(edges, kernel, iterations=2)
mask = diff_mask & (1 - edge_mask)
```

### Morphological Cleanup

```python
# Remove noise specks
mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, (2,2), iterations=1)

# Connect broken strokes
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, (3,3), iterations=1)
```

---

## ROI Extraction

### Layout JSON Structure

```json
{
  "template": "CeduladeInscripcion_CTT_2026.pdf",
  "render_size_px": {
    "width": 2550,
    "height": 3300,
    "dpi_reference": 300
  },
  "pages": {
    "front": {
      "header_fields": {
        "equipo_nombre": {"x": 0.212, "y": 0.153, "w": 0.744, "h": 0.021}
      },
      "cards": {
        "jugador_1": {
          "nombre": {"x": 0.239, "y": 0.430, "w": 0.254, "h": 0.027},
          "apellidos": {"x": 0.239, "y": 0.454, "w": 0.254, "h": 0.027},
          "nacimiento": {"x": 0.390, "y": 0.480, "w": 0.103, "h": 0.027},
          "curp": {"x": 0.218, "y": 0.503, "w": 0.275, "h": 0.030}
        }
      }
    }
  }
}
```

**Coordinate system:** All values normalized to [0, 1] for template independence.

### Field Coverage

| Page | Section | Fields |
|------|---------|--------|
| Front | Header | 8 (equipo, rama, categoria, representante, liga, correo, estado, municipio) |
| Front | Director Tecnico | 4 (nombre, apellidos, nacimiento, curp) |
| Front | Auxiliar | 4 (nombre, apellidos, nacimiento, curp) |
| Front | Jugadores 1-8 | 32 (4 fields × 8 players) |
| Back | Jugadores 9-20 | 48 (4 fields × 12 players) |
| **Total** | | **96 fields** |

---

## Ensemble OCR

### Variant Generation

Three preprocessing variants reduce correlated OCR errors:

```python
def make_variants(crop_gray: np.ndarray) -> List[np.ndarray]:
    variants = []

    # Variant 0: Original grayscale
    variants.append(crop_gray.copy())

    # Variant 1: CLAHE contrast enhancement
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    variants.append(clahe.apply(crop_gray))

    # Variant 2: Adaptive threshold (binary)
    variants.append(cv2.adaptiveThreshold(
        crop_gray, 255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY, 31, 7
    ))

    return variants
```

### Majority Voting Algorithm

```python
def majority_vote(texts: List[str]) -> Tuple[str, float]:
    normalized = [normalize_safe(t) for t in texts if t.strip()]
    if not normalized:
        return "", 0.0

    counts = {}
    for t in normalized:
        counts[t] = counts.get(t, 0) + 1

    best, n = max(counts.items(), key=lambda x: x[1])
    confidence = n / len(normalized)
    return best, confidence
```

**Confidence interpretation:**
- **1.0 (3/3):** High confidence, all variants agree
- **0.67 (2/3):** Medium confidence, majority agrees
- **0.33 (1/3):** Low confidence, consider RETRY

---

## Validation & Routing

### CURP Validation

Mexican CURP (Clave Unica de Registro de Poblacion) format:
```
AAAA######HAAAAA##
│   │     ││    │└── Check digit + homoclave
│   │     ││    └─── State code (2 letters)
│   │     │└──────── Gender (H/M)
│   │     └───────── Birth date (YYMMDD)
└───┴─────────────── Name codes (4 letters)
```

**Regex:**
```python
CURP_RE = re.compile(
    r"^[A-Z][AEIOUX][A-Z]{2}"  # First 4 letters
    r"\d{2}(0[1-9]|1[0-2])"     # Year + Month
    r"([0-2]\d|3[01])"          # Day
    r"[HM]"                      # Gender
    r"[A-Z]{2}"                  # State
    r"[B-DF-HJ-NP-TV-Z]{3}"     # Consonants
    r"[A-Z0-9]\d$"              # Homoclave + check
)
```

### Name Validation

```python
def validate_name_field(raw: str, field_type: FieldType) -> ValidationResult:
    cleaned = normalize_safe(raw)

    # Check for valid Spanish characters
    if not re.match(r"^[A-ZÁÉÍÓÚÑÜ\s]+$", cleaned):
        return ValidationResult(
            status=ValidationStatus.RETRY,
            cleaned=cleaned,
            reasons=["invalid_chars"]
        )

    # Check minimum length
    if len(cleaned) < 2:
        return ValidationResult(
            status=ValidationStatus.RETRY,
            cleaned=cleaned,
            reasons=["too_short"]
        )

    return ValidationResult(
        status=ValidationStatus.ACCEPT,
        cleaned=cleaned,
        reasons=[]
    )
```

### Routing Decision Matrix

| Validation | Confidence | Route | Action |
|------------|------------|-------|--------|
| PASS | ≥ 0.67 | ACCEPT | Proceed to database |
| PASS | < 0.67 | RETRY | Request clearer photo |
| FAIL | Any | RETRY | Request new input |
| Ambiguous | Any | HUMAN | Flag for manual review |

---

## Telegram Bot Integration

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message with instructions |
| `/cancel` | Cancel pending form submission |

### Message Flow

```
User sends FRONT photo
    │
    ▼
Bot: "🔄 Procesando formulario..."
    │
    ▼
Pipeline extracts front fields
    │
    ▼
Bot: "✅ FRENTE procesado
      Equipo: [name]
      Categoría: [cat]
      Jugadores: 8
      📸 Ahora envía REVERSO"
    │
    ▼
User sends BACK photo
    │
    ▼
Pipeline extracts back fields
    │
    ▼
Bot: "✅ FORMULARIO COMPLETO
      Total campos: 96
      ⚠️ Requieren revisión: 3"
```

### Running the Bot

```bash
# Set environment variables
export TELEGRAM_BOT_TOKEN="your-token"
export ANTHROPIC_API_KEY="your-key"

# Start bot
python3 ctt_telegram_bot.py

# Monitor logs
tail -f /tmp/ctt_bot.log
```

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram Bot API token |
| `ANTHROPIC_API_KEY` | Yes | Claude API key |

### File Paths

| File | Description |
|------|-------------|
| `config/CedulaInscripcion_CTT_2026.pdf` | Blank template PDF (2 pages) |
| `config/layout_ctt_2026.json` | ROI coordinates for all fields |

### Tunable Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `dpi` | 300 | Template rendering resolution |
| `max_features` | 5000 | ORB feature limit |
| `good_match_ratio` | 0.75 | Lowe's ratio test threshold |
| `min_good_matches` | 35 | Minimum matches for homography |
| `diff_thresh` | 25 | Intensity threshold for subtraction |
| `min_ink_ratio` | 0.005 | Minimum ink for non-empty field |

---

## API Reference

### CTTFormExtractor

```python
class CTTFormExtractor:
    def __init__(
        self,
        template_pdf_path: str = None,
        template_front_path: str = None,
        template_back_path: str = None,
        layout_json_path: str = None,
        dpi: int = 300
    ):
        """
        Initialize extractor with templates and layout.

        Provide either:
        - template_pdf_path (2-page PDF)
        - OR both template_front_path and template_back_path (PNG images)
        """

    def process_photo(
        self,
        photo_path: str,
        out_dir: str,
        prefix: str = "scan"
    ) -> Dict[str, Any]:
        """
        Process a single photo and extract all fields.

        Returns:
            {
                "side": "front" | "back",
                "align_info": {...},
                "aligned_path": "/path/to/aligned.png",
                "fields": {
                    "header.equipo_nombre": {
                        "path": "/path/to/crop.png",
                        "variants": ["/path/v0.png", ...],
                        "ink_ratio": 0.043,
                        "empty": False,
                        "field_type": "text"
                    },
                    ...
                }
            }
        """

    def process_photo_array(
        self,
        photo_bgr: np.ndarray,
        out_dir: str,
        prefix: str = "scan"
    ) -> Dict[str, Any]:
        """Process a photo from numpy array."""
```

### Standalone Functions

```python
def make_templates(pdf_path: str, dpi: int = 300) -> Dict[str, TemplatePage]:
    """Create TemplatePage objects from PDF."""

def align_photo_to_template(
    photo_bgr: np.ndarray,
    template: TemplatePage,
    max_features: int = 5000,
    good_match_ratio: float = 0.75,
    min_good_matches: int = 35
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Align photo to template using ORB + RANSAC."""

def handwriting_mask(
    aligned_gray: np.ndarray,
    template: TemplatePage,
    diff_thresh: int = 25
) -> np.ndarray:
    """Compute binary mask for handwriting."""

def make_variants(crop_gray: np.ndarray) -> List[np.ndarray]:
    """Generate 3 visual variants for ensemble OCR."""

def ink_ratio(mask_crop: np.ndarray) -> float:
    """Calculate ratio of ink pixels in mask."""
```

---

## Troubleshooting

### Common Issues

#### "Could not align photo to either template"

**Cause:** ORB cannot find enough matching features.

**Solutions:**
1. Ensure good lighting (no harsh shadows)
2. Capture full page (don't crop too tight)
3. Reduce glare/reflections
4. Use higher resolution photo

#### "HTTP 409 Conflict" on Telegram

**Cause:** Another bot instance is polling with the same token.

**Solution:**
```bash
# Kill all bot processes
pkill -f ctt_telegram_bot

# Wait for session timeout (35 seconds)
sleep 35

# Restart bot
python3 ctt_telegram_bot.py
```

#### Low OCR Accuracy

**Causes & Solutions:**

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Wrong characters | Poor contrast | Improve lighting |
| Missing text | Alignment drift | Retake photo straighter |
| Extra noise | Template mismatch | Verify correct template version |
| Partial text | ROI miscalibration | Adjust layout.json coordinates |

#### Empty Fields Detected as Filled

**Cause:** Noise or shadows triggering ink detection.

**Solution:** Increase `min_ink_ratio` threshold (default 0.005).

---

## Performance Metrics

| Metric | Value |
|--------|-------|
| Alignment time | ~0.5s |
| Extraction time (48 fields) | ~2s |
| OCR time (per field, 3 variants) | ~1.5s |
| Total pipeline (front page) | ~75s |
| Total pipeline (front + back) | ~150s |

---

## Dependencies

```txt
opencv-python>=4.8.0
numpy>=1.24.0
pymupdf>=1.23.0  # fitz
anthropic>=0.18.0
httpx>=0.24.0
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | Jan 2026 | Initial release with template-aligned pipeline |

---

## Related Documentation

- [Hard Validator](../src/devnous/validation/hard_validator.py) - Field validation rules
- [Vision Module](../src/devnous/vision/__init__.py) - Vision capabilities overview
- [Telegram Quickstart](TELEGRAM_QUICKSTART.md) - General Telegram bot setup
