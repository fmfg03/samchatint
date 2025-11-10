"""
OCRAgent - Claude Vision-powered OCR for handwritten text extraction.

This agent provides production-ready OCR capabilities optimized for:
- Spanish handwritten text
- Registration forms and documents
- Tournament player data extraction
- Multi-field form processing

Powered by Claude 3.5 Sonnet Vision API for human-level accuracy.
"""

import logging
import base64
import re
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from pathlib import Path
from PIL import Image
import io
import anthropic

from samchat.base_agent import BaseAgent, Message, ProjectContext, LLMProvider


logger = logging.getLogger(__name__)


class OCRAgent(BaseAgent):
    """
    Claude Vision-powered OCR agent for handwritten text extraction.

    This agent provides comprehensive OCR capabilities including:
    - Handwritten text recognition (Spanish/English)
    - Name and date extraction
    - Form field extraction
    - Multi-language support
    - Confidence scoring
    - Structured data output

    Optimized for Copa Telmex player registration forms and tournament documents.

    Example:
        >>> agent = OCRAgent(anthropic_api_key="...")
        >>> result = await agent.extract_text_from_image("player_form.jpg")
        >>> print(result['text'])
        >>> print(result['extracted_data']['names'])
    """

    def __init__(
        self,
        name: str = "OCR Specialist",
        role: str = "Vision OCR and Document Analysis",
        anthropic_api_key: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20240620",
        temperature: float = 0.0,  # Low temperature for accurate OCR
        max_tokens: int = 2048,
        **kwargs
    ):
        """
        Initialize OCR Agent with Claude Vision.

        Args:
            name: Agent name
            role: Agent role description
            anthropic_api_key: Anthropic API key for Claude Vision
            model: Claude model to use (must be vision-capable)
            temperature: LLM temperature (0.0 for deterministic OCR)
            max_tokens: Max tokens for OCR responses
            **kwargs: Additional BaseAgent parameters
        """
        super().__init__(
            name=name,
            role=role,
            llm_provider=LLMProvider.ANTHROPIC,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )

        # Initialize Claude Vision client
        if anthropic_api_key:
            self.claude = anthropic.Anthropic(api_key=anthropic_api_key)
        else:
            import os
            api_key = os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not provided and not found in environment")
            self.claude = anthropic.Anthropic(api_key=api_key)

        self.initialized = True
        logger.info(f"{self.name} initialized with Claude Vision")

    def get_system_prompt(self) -> str:
        """Get the system prompt for OCR operations."""
        return """You are a Vision OCR Specialist using Claude 3.5 Sonnet Vision.

Your role is to accurately extract text from images, with special focus on:

1. HANDWRITTEN TEXT RECOGNITION
   - Spanish and English handwriting
   - Cursive and print writing
   - Tournament registration forms
   - Player information documents
   - Multi-line text extraction

2. STRUCTURED DATA EXTRACTION
   - Names (first, last, full names)
   - Dates (multiple formats)
   - Phone numbers
   - Addresses
   - Tournament categories
   - Age/birthdate
   - Team names

3. FORM FIELD RECOGNITION
   - Labeled fields (e.g., "Nombre:", "Fecha:")
   - Checkbox values
   - Table data
   - Multi-column layouts
   - Handwritten entries in printed forms

4. QUALITY REQUIREMENTS
   - Transcribe EXACTLY what is written
   - Preserve original language (Spanish/English)
   - Separate multiple entries with line breaks
   - Flag uncertain text with confidence indicators
   - Handle lined paper and background noise

Key Capabilities:
- Human-level accuracy on handwritten text
- Context-aware interpretation
- Multi-language support (Spanish primary)
- Structured output in JSON format
- Confidence scoring for each field

Output Format:
Always provide JSON with:
- transcribed_text: Full text as written
- extracted_data: Structured fields (names, dates, etc.)
- confidence: Overall confidence score
- language_detected: Primary language
- metadata: Processing details

Focus on accuracy and completeness. When uncertain, indicate confidence level."""

    async def process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """
        Process a conversation requesting OCR operations.

        This is the main entry point for BaseAgent compatibility.
        Extracts OCR requests from conversation and processes them.

        Args:
            conversation: List of messages
            context: Project context

        Returns:
            OCR results and extracted data
        """
        start_time = datetime.now()

        try:
            # Extract OCR requests from conversation
            image_paths = self._extract_image_paths(conversation)

            if not image_paths:
                return {
                    'status': 'no_images',
                    'message': 'No images found in conversation for OCR processing'
                }

            # Process each image
            results = []
            for image_path in image_paths:
                result = await self.extract_text_from_image(image_path)
                results.append(result)

            # Add processing metadata
            processing_time = (datetime.now() - start_time).total_seconds() * 1000

            return {
                'status': 'success',
                'ocr_results': results,
                'total_images': len(results),
                'metadata': {
                    'agent_name': self.name,
                    'processing_time_ms': round(processing_time, 2),
                    'model': self.model,
                    'timestamp': datetime.now().isoformat()
                }
            }

        except Exception as e:
            logger.error(f"Error in process_conversation: {e}")
            return self._fallback_response(str(e))

    async def extract_text_from_image(
        self,
        image: Union[str, Path, Image.Image, bytes],
        language: str = "spanish",
        extract_fields: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Extract text from image using Claude Vision.

        Args:
            image: Image path, PIL Image, or bytes
            language: Primary language hint ("spanish", "english", "auto")
            extract_fields: Specific fields to extract (e.g., ["names", "dates"])

        Returns:
            OCR result with transcribed text and structured data

        Example:
            >>> result = await agent.extract_text_from_image("form.jpg")
            >>> print(result['transcribed_text'])
            >>> print(result['extracted_data']['names'])
        """
        start_time = datetime.now()

        try:
            # Load and prepare image
            image_b64 = self._prepare_image(image)

            # Build prompt based on language and fields
            prompt = self._build_ocr_prompt(language, extract_fields)

            # Call Claude Vision
            logger.info(f"Processing image with Claude Vision (language: {language})")
            response = await self._call_claude_vision(image_b64, prompt)

            # Parse response
            result = self._parse_ocr_response(response)

            # Add metadata
            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            result['metadata'] = {
                'processing_time_ms': round(processing_time, 2),
                'model': self.model,
                'language_hint': language,
                'timestamp': datetime.now().isoformat(),
                'agent_name': self.name
            }

            logger.info(
                f"OCR completed: {len(result['transcribed_text'])} chars, "
                f"{processing_time:.0f}ms"
            )

            return result

        except Exception as e:
            logger.error(f"OCR extraction failed: {e}")
            return self._fallback_ocr_result(str(e))

    async def extract_form_fields(
        self,
        image: Union[str, Path, Image.Image, bytes],
        field_schema: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Extract specific form fields from image.

        Args:
            image: Image containing form
            field_schema: Dictionary mapping field names to descriptions
                         e.g., {"player_name": "Full name of player",
                                "birth_date": "Date of birth",
                                "category": "Tournament category"}

        Returns:
            Dictionary with extracted field values

        Example:
            >>> schema = {
            ...     "player_name": "Player full name",
            ...     "birth_date": "Date of birth",
            ...     "category": "Tournament category (U10/U12/etc)"
            ... }
            >>> result = await agent.extract_form_fields("form.jpg", schema)
            >>> print(result['fields']['player_name'])
        """
        start_time = datetime.now()

        try:
            # Prepare image
            image_b64 = self._prepare_image(image)

            # Build structured extraction prompt
            prompt = self._build_form_extraction_prompt(field_schema)

            # Call Claude Vision
            logger.info(f"Extracting {len(field_schema)} form fields")
            response = await self._call_claude_vision(image_b64, prompt)

            # Parse structured response
            fields = self._parse_form_fields(response, field_schema)

            processing_time = (datetime.now() - start_time).total_seconds() * 1000

            return {
                'status': 'success',
                'fields': fields,
                'metadata': {
                    'processing_time_ms': round(processing_time, 2),
                    'fields_requested': len(field_schema),
                    'fields_extracted': len(fields),
                    'timestamp': datetime.now().isoformat()
                }
            }

        except Exception as e:
            logger.error(f"Form field extraction failed: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'fields': {}
            }

    async def batch_process_images(
        self,
        images: List[Union[str, Path, Image.Image]],
        language: str = "spanish"
    ) -> Dict[str, Any]:
        """
        Process multiple images in batch.

        Args:
            images: List of images to process
            language: Primary language hint

        Returns:
            Batch processing results with individual OCR results
        """
        start_time = datetime.now()

        results = []
        successful = 0
        failed = 0

        for idx, image in enumerate(images):
            try:
                logger.info(f"Processing image {idx + 1}/{len(images)}")
                result = await self.extract_text_from_image(image, language)
                results.append(result)

                if result.get('status') != 'error':
                    successful += 1
                else:
                    failed += 1

            except Exception as e:
                logger.error(f"Batch processing failed for image {idx}: {e}")
                failed += 1
                results.append(self._fallback_ocr_result(str(e)))

        total_time = (datetime.now() - start_time).total_seconds() * 1000
        avg_time = total_time / len(images) if images else 0

        return {
            'status': 'completed',
            'results': results,
            'summary': {
                'total_images': len(images),
                'successful': successful,
                'failed': failed,
                'success_rate': successful / len(images) if images else 0.0,
                'total_processing_time_ms': round(total_time, 2),
                'average_time_per_image_ms': round(avg_time, 2)
            },
            'metadata': {
                'timestamp': datetime.now().isoformat(),
                'agent_name': self.name
            }
        }

    # Helper methods

    def _prepare_image(self, image: Union[str, Path, Image.Image, bytes]) -> str:
        """Convert image to base64 for Claude Vision"""
        try:
            # Handle different input types
            if isinstance(image, bytes):
                image_bytes = image
            elif isinstance(image, (str, Path)):
                with open(image, 'rb') as f:
                    image_bytes = f.read()
            elif isinstance(image, Image.Image):
                # Convert PIL Image to JPEG bytes
                img_byte_arr = io.BytesIO()
                if image.mode not in ('RGB', 'RGBA'):
                    image = image.convert('RGB')
                image.save(img_byte_arr, format='JPEG', quality=95)
                image_bytes = img_byte_arr.getvalue()
            else:
                raise ValueError(f"Unsupported image type: {type(image)}")

            # Convert to base64
            return base64.b64encode(image_bytes).decode('utf-8')

        except Exception as e:
            logger.error(f"Image preparation failed: {e}")
            raise

    def _build_ocr_prompt(
        self,
        language: str,
        extract_fields: Optional[List[str]] = None
    ) -> str:
        """Build OCR prompt based on requirements"""
        prompt = (
            "Lee todo el texto en esta imagen y transcríbelo EXACTAMENTE como está escrito.\n\n"
            if language == "spanish" else
            "Read all text in this image and transcribe it EXACTLY as written.\n\n"
        )

        prompt += (
            "Transcribe palabra por palabra, línea por línea.\n"
            "Si el texto está manuscrito, léelo con cuidado.\n"
            "Si hay múltiples líneas, sepáralas con saltos de línea.\n\n"
            if language == "spanish" else
            "Transcribe word by word, line by line.\n"
            "If text is handwritten, read it carefully.\n"
            "If there are multiple lines, separate them with line breaks.\n\n"
        )

        # Add field extraction instructions if specified
        if extract_fields:
            fields_str = ", ".join(extract_fields)
            prompt += (
                f"\nTambién extrae estos campos específicos: {fields_str}\n"
                if language == "spanish" else
                f"\nAlso extract these specific fields: {fields_str}\n"
            )

        prompt += (
            "\nResponde en formato JSON con:\n"
            "- transcribed_text: El texto completo transcrito\n"
            "- extracted_data: Datos estructurados (nombres, fechas, etc.)\n"
            "- confidence: Tu confianza en la transcripción (0.0-1.0)\n"
            "- language_detected: Idioma detectado\n"
            if language == "spanish" else
            "\nRespond in JSON format with:\n"
            "- transcribed_text: Full transcribed text\n"
            "- extracted_data: Structured data (names, dates, etc.)\n"
            "- confidence: Your confidence in transcription (0.0-1.0)\n"
            "- language_detected: Detected language\n"
        )

        return prompt

    def _build_form_extraction_prompt(self, field_schema: Dict[str, str]) -> str:
        """Build prompt for structured form extraction"""
        prompt = "Extract the following fields from this form image:\n\n"

        for field_name, description in field_schema.items():
            prompt += f"- {field_name}: {description}\n"

        prompt += (
            "\nRespond in JSON format with each field as a key.\n"
            "If a field is not found or unclear, use null.\n"
            "Include a confidence score for each field (0.0-1.0).\n\n"
            "Format:\n"
            "{\n"
            "  \"field_name\": {\"value\": \"extracted_value\", \"confidence\": 0.95},\n"
            "  ...\n"
            "}"
        )

        return prompt

    async def _call_claude_vision(self, image_b64: str, prompt: str) -> str:
        """Call Claude Vision API (async wrapper for blocking call)"""
        import asyncio

        # Run blocking API call in executor
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self._call_claude_vision_sync,
            image_b64,
            prompt
        )

        return result

    def _call_claude_vision_sync(self, image_b64: str, prompt: str) -> str:
        """Synchronous Claude Vision API call"""
        try:
            message = self.claude.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ],
                    }
                ],
            )

            # Extract text from response
            return message.content[0].text.strip()

        except Exception as e:
            logger.error(f"Claude Vision API call failed: {e}")
            raise

    def _parse_ocr_response(self, response: str) -> Dict[str, Any]:
        """Parse Claude Vision response into structured OCR result"""
        try:
            # Try to parse as JSON
            import json

            # Remove markdown code blocks if present
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            result = json.loads(response)

            # Ensure required fields exist
            if 'transcribed_text' not in result:
                result['transcribed_text'] = response

            if 'extracted_data' not in result:
                result['extracted_data'] = self._extract_structured_data(
                    result['transcribed_text']
                )

            if 'confidence' not in result:
                result['confidence'] = 0.85  # Default confidence

            if 'language_detected' not in result:
                result['language_detected'] = 'spanish'

            result['status'] = 'success'

            return result

        except json.JSONDecodeError:
            # Fallback: treat response as plain text
            logger.warning("Response not in JSON format, using fallback parsing")
            return {
                'status': 'success',
                'transcribed_text': response,
                'extracted_data': self._extract_structured_data(response),
                'confidence': 0.75,  # Lower confidence for non-JSON
                'language_detected': 'spanish'
            }

    def _extract_structured_data(self, text: str) -> Dict[str, Any]:
        """Extract structured data from transcribed text"""
        data = {}

        # Extract names (Spanish pattern)
        name_pattern = r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+\b'
        names = re.findall(name_pattern, text)
        if names:
            data['names'] = list(set(names))[:10]  # Top 10 unique names

        # Extract dates (multiple formats)
        date_patterns = [
            r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',
            r'\b\d{1,2}\s+(?:de\s+)?(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?\\d{2,4}\b'
        ]
        dates = []
        for pattern in date_patterns:
            dates.extend(re.findall(pattern, text, re.IGNORECASE))
        if dates:
            data['dates'] = list(set(dates))

        # Extract phone numbers
        phone_pattern = r'\b(?:\+?52\s?)?(?:\d{2,3}[-\s]?)?\d{3,4}[-\s]?\d{4}\b'
        phones = re.findall(phone_pattern, text)
        if phones:
            data['phone_numbers'] = list(set(phones))

        # Extract ages/numbers
        number_pattern = r'\b(?:edad|age):\s*(\d{1,3})\b'
        ages = re.findall(number_pattern, text, re.IGNORECASE)
        if ages:
            data['ages'] = [int(age) for age in ages]

        return data

    def _parse_form_fields(
        self,
        response: str,
        field_schema: Dict[str, str]
    ) -> Dict[str, Any]:
        """Parse structured form field extraction"""
        try:
            import json

            # Clean response - extract JSON portion
            response = response.strip()

            # Remove markdown code blocks
            if response.startswith('```json'):
                response = response[7:]
            elif response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            # Extract JSON object (find first { to last })
            start_idx = response.find('{')
            end_idx = response.rfind('}')

            if start_idx != -1 and end_idx != -1:
                json_str = response[start_idx:end_idx + 1]
            else:
                json_str = response

            fields = json.loads(json_str)

            # Ensure all requested fields are present
            for field_name in field_schema.keys():
                if field_name not in fields:
                    fields[field_name] = {
                        'value': None,
                        'confidence': 0.0
                    }

            return fields

        except Exception as e:
            logger.error(f"Form field parsing failed: {e}")
            logger.error(f"Response was: {response[:500]}")
            # Return empty fields with zero confidence
            return {
                field_name: {'value': None, 'confidence': 0.0}
                for field_name in field_schema.keys()
            }

    def _extract_image_paths(self, conversation: List[Message]) -> List[str]:
        """Extract image paths from conversation messages"""
        image_paths = []

        for message in conversation:
            # Look for image references in message content
            # Patterns: "image: path/to/image.jpg", "photo: ...", "[image](path)"
            patterns = [
                r'image:\s*([^\s,]+\.(?:jpg|jpeg|png|gif))',
                r'photo:\s*([^\s,]+\.(?:jpg|jpeg|png|gif))',
                r'\[image\]\(([^\)]+)\)',
                r'file:\s*([^\s,]+\.(?:jpg|jpeg|png|gif))'
            ]

            for pattern in patterns:
                matches = re.findall(pattern, message.content, re.IGNORECASE)
                image_paths.extend(matches)

        return image_paths

    def _fallback_response(self, error: str) -> Dict[str, Any]:
        """Fallback response for conversation processing"""
        return {
            'status': 'error',
            'error': error,
            'message': 'OCR processing failed',
            'metadata': {
                'agent_name': self.name,
                'timestamp': datetime.now().isoformat()
            }
        }

    def _fallback_ocr_result(self, error: str) -> Dict[str, Any]:
        """Fallback result for OCR extraction"""
        return {
            'status': 'error',
            'error': error,
            'transcribed_text': '',
            'extracted_data': {},
            'confidence': 0.0,
            'language_detected': 'unknown',
            'metadata': {
                'timestamp': datetime.now().isoformat()
            }
        }
