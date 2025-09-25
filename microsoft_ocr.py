#!/usr/bin/env python3
"""
Microsoft Azure Computer Vision OCR Integration
This module provides OCR capabilities using Microsoft's Azure Computer Vision API
"""

import os
import sys
import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from PIL import Image
import io

# Azure Computer Vision imports
from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from azure.cognitiveservices.vision.computervision.models import OperationStatusCodes
from msrest.authentication import CognitiveServicesCredentials


@dataclass
class OCRResult:
    """Container for OCR results"""
    text: str
    confidence: float
    bounding_box: List[int]
    language: str = "es"  # Default to Spanish


class MicrosoftOCR:
    """Microsoft Azure Computer Vision OCR Client"""

    def __init__(self, endpoint: Optional[str] = None, subscription_key: Optional[str] = None):
        """
        Initialize the Microsoft OCR client

        Args:
            endpoint: Azure Computer Vision endpoint URL
            subscription_key: Azure subscription key
        """
        # Try to get credentials from environment variables if not provided
        self.endpoint = endpoint or os.getenv('AZURE_COMPUTER_VISION_ENDPOINT')
        self.subscription_key = subscription_key or os.getenv('AZURE_COMPUTER_VISION_KEY')

        if not self.endpoint or not self.subscription_key:
            raise ValueError(
                "Azure Computer Vision credentials not provided. "
                "Please set AZURE_COMPUTER_VISION_ENDPOINT and AZURE_COMPUTER_VISION_KEY "
                "environment variables or pass them as arguments."
            )

        # Create client
        self.client = ComputerVisionClient(
            self.endpoint,
            CognitiveServicesCredentials(self.subscription_key)
        )

    def ocr_image(self, image_path: str, language: str = "es") -> List[OCRResult]:
        """
        Perform OCR on an image file

        Args:
            image_path: Path to the image file
            language: Language code (default: "es" for Spanish)

        Returns:
            List of OCR results with text, confidence, and bounding boxes
        """
        results = []

        # Open and read the image file
        with open(image_path, "rb") as image_stream:
            # Call API with image stream
            ocr_result = self.client.recognize_printed_text_in_stream(
                image_stream,
                language=language
            )

        # Extract text from regions
        for region in ocr_result.regions:
            for line in region.lines:
                line_text = ""
                for word in line.words:
                    line_text += word.text + " "

                if line_text.strip():
                    results.append(OCRResult(
                        text=line_text.strip(),
                        confidence=0.95,  # Azure doesn't provide confidence for printed text
                        bounding_box=line.bounding_box.split(',') if hasattr(line, 'bounding_box') else [],
                        language=language
                    ))

        return results

    def read_handwritten(self, image_path: str, language: str = "es") -> List[OCRResult]:
        """
        Perform OCR on handwritten text using Read API (better for handwriting)

        Args:
            image_path: Path to the image file
            language: Language code (default: "es" for Spanish)

        Returns:
            List of OCR results with text, confidence, and bounding boxes
        """
        results = []

        # Open the image file
        with open(image_path, "rb") as image_stream:
            # Call Read API
            read_response = self.client.read_in_stream(
                image_stream,
                language=language,
                raw=True
            )

        # Get the operation location (URL with ID)
        read_operation_location = read_response.headers["Operation-Location"]
        # Extract the operation ID from the URL
        operation_id = read_operation_location.split("/")[-1]

        # Wait for the operation to complete
        while True:
            read_result = self.client.get_read_result(operation_id)
            if read_result.status not in ['notStarted', 'running']:
                break
            time.sleep(1)

        # Extract text if successful
        if read_result.status == OperationStatusCodes.succeeded:
            for text_result in read_result.analyze_result.read_results:
                for line in text_result.lines:
                    results.append(OCRResult(
                        text=line.text,
                        confidence=max([word.confidence for word in line.words]) if line.words else 0.0,
                        bounding_box=line.bounding_box,
                        language=language
                    ))

        return results

    def extract_form_data(self, image_path: str) -> Dict[str, str]:
        """
        Extract structured form data from an image

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary of extracted form fields
        """
        # Use Read API for better accuracy
        ocr_results = self.read_handwritten(image_path)

        # Parse form fields (customize based on your form structure)
        form_data = {}
        current_field = None

        for result in ocr_results:
            text = result.text.strip()

            # Look for field labels (customize these patterns)
            if "Nombre:" in text or "Name:" in text:
                current_field = "name"
                value = text.split(":")[-1].strip()
                if value:
                    form_data[current_field] = value
            elif "Apellido:" in text or "Surname:" in text:
                current_field = "surname"
                value = text.split(":")[-1].strip()
                if value:
                    form_data[current_field] = value
            elif "Fecha:" in text or "Date:" in text:
                current_field = "date"
                value = text.split(":")[-1].strip()
                if value:
                    form_data[current_field] = value
            elif "Email:" in text or "Correo:" in text:
                current_field = "email"
                value = text.split(":")[-1].strip()
                if value:
                    form_data[current_field] = value
            elif current_field and ":" not in text:
                # Continuation of previous field
                form_data[current_field] = form_data.get(current_field, "") + " " + text

        return form_data

    def batch_process(self, image_paths: List[str], use_handwriting: bool = False) -> Dict[str, List[OCRResult]]:
        """
        Process multiple images in batch

        Args:
            image_paths: List of image file paths
            use_handwriting: Whether to use handwriting recognition

        Returns:
            Dictionary mapping image paths to OCR results
        """
        results = {}

        for image_path in image_paths:
            print(f"Processing: {image_path}")
            try:
                if use_handwriting:
                    results[image_path] = self.read_handwritten(image_path)
                else:
                    results[image_path] = self.ocr_image(image_path)
            except Exception as e:
                print(f"Error processing {image_path}: {e}")
                results[image_path] = []

        return results


def main():
    """Demo usage of Microsoft OCR"""

    # Check for Azure credentials
    if not os.getenv('AZURE_COMPUTER_VISION_ENDPOINT'):
        print("⚠️  Azure Computer Vision credentials not found!")
        print("\nTo use Microsoft OCR, you need:")
        print("1. An Azure account (free tier available)")
        print("2. Create a Computer Vision resource in Azure Portal")
        print("3. Set environment variables:")
        print("   export AZURE_COMPUTER_VISION_ENDPOINT='your-endpoint-url'")
        print("   export AZURE_COMPUTER_VISION_KEY='your-subscription-key'")
        print("\nFor testing, you can use the free tier which includes:")
        print("- 5,000 transactions per month")
        print("- 20 calls per minute")
        return

    # Initialize OCR client
    ocr = MicrosoftOCR()

    # Check if we have a test image
    test_image = "WhatsApp Image 2025-09-23 at 2.03.05 PM.jpeg"
    if os.path.exists(test_image):
        print(f"\n📸 Processing image: {test_image}")

        # Try printed text OCR
        print("\n1️⃣ Printed Text Recognition:")
        printed_results = ocr.ocr_image(test_image)
        for i, result in enumerate(printed_results[:5], 1):
            print(f"   {i}. {result.text}")

        # Try handwriting recognition
        print("\n2️⃣ Handwriting Recognition (Read API):")
        handwritten_results = ocr.read_handwritten(test_image)
        for i, result in enumerate(handwritten_results[:5], 1):
            print(f"   {i}. {result.text} (confidence: {result.confidence:.2f})")

        # Try form extraction
        print("\n3️⃣ Form Data Extraction:")
        form_data = ocr.extract_form_data(test_image)
        for field, value in form_data.items():
            print(f"   {field}: {value}")
    else:
        print(f"Test image not found: {test_image}")
        print("Please provide an image path to test OCR functionality")


if __name__ == "__main__":
    main()