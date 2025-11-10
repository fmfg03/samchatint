#!/usr/bin/env python3
"""
Quick test of Claude Vision API connection
"""
import os
import anthropic
import base64

def test_claude_vision():
    """Test Claude Vision API connection"""
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set")
        return False

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # Test with a simple text message (no image needed for basic connection test)
        message = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": "Hello! Can you respond with 'Claude Vision API is working!'"
            }]
        )

        response = message.content[0].text
        print(f"✅ Claude Vision API Response: {response}")
        return True

    except Exception as e:
        print(f"❌ Claude Vision API Error: {e}")
        return False

if __name__ == "__main__":
    print("🧪 Testing Claude Vision API connection...")
    print("=" * 50)

    success = test_claude_vision()

    print("=" * 50)
    if success:
        print("✅ Claude Vision API is ready!")
        print("🏆 OCR Copa Telmex bot can now process images!")
    else:
        print("❌ Claude Vision API connection failed")
        print("Please check your API key")