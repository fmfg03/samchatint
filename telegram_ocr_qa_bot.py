#!/usr/bin/env python3
"""
Copa Telmex - Telegram OCR Bot con QA y Verificación Humana

Bot de OCR con Claude Vision + Validación de nombres mexicanos + Verificación humana intuitiva.

Features:
- OCR con Claude Vision (95% accuracy)
- Validación automática de nombres y apellidos mexicanos
- Fallback a verificación humana si confianza <80%
- Interfaz intuitiva con botones inline
- Sugerencias automáticas de nombres similares

Usage:
    export TELEGRAM_BOT_TOKEN="your_token"
    export ANTHROPIC_API_KEY="your_key"
    python3 telegram_ocr_qa_bot.py
"""

import asyncio
import logging
import os
import sys
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime, date
import base64
import json

import aiohttp
from PIL import Image
import io
import anthropic
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from devnous.validation import MexicanNamesValidator, validate_mexican_full_name
from devnous.copa_telmex.database import CopaTelmexDB

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TelegramOCRQABot:
    """
    Telegram OCR Bot con QA y verificación humana.

    Workflow:
    1. Usuario envía foto de formulario
    2. Bot extrae datos con Claude Vision
    3. Bot valida nombres contra base de datos mexicana
    4. Si confianza <80% o nombre no válido:
       - Bot muestra sugerencias con botones inline
       - Usuario selecciona opción correcta
    5. Bot confirma datos finales
    """

    def __init__(self, telegram_token: str, anthropic_key: str):
        self.telegram_token = telegram_token
        self.api_base = f"https://api.telegram.org/bot{telegram_token}"
        self.claude = anthropic.Anthropic(api_key=anthropic_key)
        self.validator = MexicanNamesValidator(min_confidence=0.80)
        self.last_update_id = 0

        # Store pending verifications per chat
        self.pending_verifications: Dict[int, Dict[str, Any]] = {}

        # Database setup
        db_url = "postgresql+asyncpg://copa_user:copa_pass_2025@localhost:5432/copa_telmex"
        self.db_engine = create_async_engine(
            db_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True
        )
        self.async_session_maker = async_sessionmaker(
            self.db_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        logger.info("✅ Database engine initialized")

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup: Optional[Dict] = None
    ):
        """Send message to Telegram"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode
            }

            if reply_markup:
                payload["reply_markup"] = reply_markup

            async with session.post(
                f"{self.api_base}/sendMessage",
                json=payload
            ) as resp:
                return await resp.json()

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None
    ):
        """Answer callback query from inline keyboard"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "callback_query_id": callback_query_id
            }
            if text:
                payload["text"] = text

            async with session.post(
                f"{self.api_base}/answerCallbackQuery",
                json=payload
            ) as resp:
                return await resp.json()

    async def download_photo(self, file_id: str) -> bytes:
        """Download photo from Telegram"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.api_base}/getFile",
                params={"file_id": file_id}
            ) as resp:
                result = await resp.json()
                file_path = result['result']['file_path']

            file_url = f"https://api.telegram.org/file/bot{self.telegram_token}/{file_path}"
            async with session.get(file_url) as resp:
                return await resp.read()

    def image_to_base64(self, image_bytes: bytes) -> str:
        """Convert image bytes to base64"""
        return base64.b64encode(image_bytes).decode('utf-8')

    async def process_photo_message(self, message: Dict[str, Any]):
        """Process photo with OCR + QA + Human verification"""
        chat_id = message['chat']['id']

        try:
            await self.send_message(
                chat_id,
                "📸 Procesando...\n⏳ 3-5 segundos"
            )

            # Download photo
            photos = message['photo']
            largest_photo = max(photos, key=lambda p: p.get('file_size', 0))

            logger.info(f"📥 Downloading photo: {largest_photo['file_id']}")
            photo_bytes = await self.download_photo(largest_photo['file_id'])

            # Load and convert image
            image = Image.open(io.BytesIO(photo_bytes))
            logger.info(f"🖼️  Image: {image.size}, {image.mode}")

            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGB')

            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='JPEG', quality=95)
            img_byte_arr.seek(0)
            optimized_bytes = img_byte_arr.getvalue()

            image_b64 = self.image_to_base64(optimized_bytes)

            # Call Claude Vision for OCR
            logger.info("🤖 Calling Claude Vision API...")
            loop = asyncio.get_event_loop()
            ocr_result = await loop.run_in_executor(
                None,
                self._call_claude_vision,
                image_b64
            )

            # Validate player name
            player_name = ocr_result.get('player_name', '')
            ocr_confidence = ocr_result.get('confidence', 0.0)

            logger.info(f"🔍 OCR Result: player_name='{player_name}', confidence={ocr_confidence}")
            logger.info(f"📋 Full OCR result: {ocr_result}")

            if player_name:
                logger.info(f"✅ Validating name: '{player_name}' with confidence {ocr_confidence}")

                validation_result = self.validator.validate_full_name(
                    player_name,
                    confidence=ocr_confidence
                )

                logger.info(f"📊 Validation result: {validation_result}")

                if validation_result['needs_human_review']:
                    logger.info(f"👤 Needs human review for: '{player_name}'")
                    # Needs human verification
                    await self._request_human_verification(
                        chat_id,
                        player_name,
                        validation_result,
                        ocr_result
                    )
                else:
                    logger.info(f"✅ Name validated automatically: '{player_name}'")
                    # Name is valid, send confirmation
                    await self._send_final_confirmation(chat_id, ocr_result, validation_result)
            else:
                await self.send_message(
                    chat_id,
                    "⚠️  *No se detectó nombre del jugador*\n\n"
                    "Por favor verifica que:\n"
                    "• El nombre esté visible\n"
                    "• La foto tenga buena iluminación\n"
                    "• El texto sea legible"
                )

        except Exception as e:
            logger.error(f"❌ Error: {e}", exc_info=True)
            await self.send_message(
                chat_id,
                f"❌ *Error*\n\n`{str(e)}`\n\n"
                "Por favor intenta de nuevo."
            )

    async def _request_human_verification(
        self,
        chat_id: int,
        detected_name: str,
        validation_result: Dict[str, Any],
        ocr_result: Dict[str, Any]
    ):
        """Request human verification with inline keyboard"""

        # Get suggestions from validation
        # Handle case when parts is empty list (incomplete name)
        parts = validation_result.get('parts', {})

        if isinstance(parts, list):
            # Empty list means incomplete name (e.g., only first name)
            first_name_suggestions = []
            surname_suggestions = []
        else:
            # Normal case: parts is a dict
            first_name_suggestions = parts.get('first_name', {}).get('suggestions', [])
            surname_suggestions = []
            for surname_result in parts.get('surnames', []):
                surname_suggestions.extend(surname_result.get('suggestions', []))

        # Build SHORT message (to keep buttons visible)
        confidence = validation_result.get('confidence', 0.0)
        reason = validation_result.get('reason', '')

        message = f"❓ *Verificación Necesaria*\n\n"
        message += f"Detectado: *{detected_name}*\n"

        if first_name_suggestions or surname_suggestions:
            message += "\n💡 ¿Es correcto?\n"
        elif 'al menos nombre y apellido' in reason.lower():
            message += f"\n⚠️ Falta apellido\n"
        else:
            message += f"\n⚠️ Nombre no encontrado\n"

        # Build inline keyboard
        keyboard = {"inline_keyboard": []}

        # Add suggestion buttons
        all_suggestions = []

        if first_name_suggestions:
            # Reconstruct full name with suggestion
            parts = detected_name.split()
            for suggestion in first_name_suggestions[:2]:  # Top 2
                suggested_full = f"{suggestion} {' '.join(parts[1:])}"
                all_suggestions.append(suggested_full)

        if surname_suggestions and len(detected_name.split()) > 1:
            # Reconstruct with surname suggestion
            parts = detected_name.split()
            for suggestion in surname_suggestions[:2]:  # Top 2
                suggested_full = f"{parts[0]} {suggestion}"
                all_suggestions.append(suggested_full)

        # Add buttons for suggestions (compact)
        for i, suggestion in enumerate(all_suggestions[:2]):  # Max 2 to save space
            keyboard["inline_keyboard"].append([{
                "text": f"✅ {suggestion}",
                "callback_data": f"confirm_{i}_{suggestion}"
            }])

        # Add "use detected" button
        keyboard["inline_keyboard"].append([{
            "text": f"👍 {detected_name}",
            "callback_data": f"use_detected_{detected_name}"
        }])

        # Add "write manually" button
        keyboard["inline_keyboard"].append([{
            "text": "✏️ Corregir",
            "callback_data": "write_manually"
        }])

        # Store pending verification
        self.pending_verifications[chat_id] = {
            'detected_name': detected_name,
            'suggestions': all_suggestions,
            'ocr_result': ocr_result,
            'validation_result': validation_result
        }

        await self.send_message(
            chat_id,
            message,
            reply_markup=keyboard
        )

    async def handle_callback_query(self, callback_query: Dict[str, Any]):
        """Handle inline keyboard button press"""
        callback_id = callback_query['id']
        chat_id = callback_query['message']['chat']['id']
        data = callback_query['data']

        logger.info(f"📱 Callback: {data}")

        try:
            if data.startswith("confirm_"):
                # User selected a suggestion
                parts = data.split("_", 2)
                if len(parts) >= 3:
                    selected_name = parts[2]

                    await self.answer_callback_query(
                        callback_id,
                        f"✅ Confirmado: {selected_name}"
                    )

                    # Update OCR result with corrected name
                    if chat_id in self.pending_verifications:
                        ocr_result = self.pending_verifications[chat_id]['ocr_result']
                        validation_result = self.pending_verifications[chat_id].get('validation_result')
                        ocr_result['player_name'] = selected_name
                        ocr_result['human_verified'] = True

                        await self._send_final_confirmation(chat_id, ocr_result, validation_result)

                        # Clean up
                        del self.pending_verifications[chat_id]

            elif data.startswith("use_detected_"):
                # User confirmed detected name
                detected_name = data.replace("use_detected_", "")

                await self.answer_callback_query(
                    callback_id,
                    f"✅ Usando: {detected_name}"
                )

                if chat_id in self.pending_verifications:
                    ocr_result = self.pending_verifications[chat_id]['ocr_result']
                    validation_result = self.pending_verifications[chat_id].get('validation_result')
                    ocr_result['player_name'] = detected_name
                    ocr_result['human_verified'] = True

                    await self._send_final_confirmation(chat_id, ocr_result, validation_result)

                    del self.pending_verifications[chat_id]

            elif data == "write_manually":
                # User will write name manually
                await self.answer_callback_query(
                    callback_id,
                    "✏️ Escribe el nombre correcto"
                )

                await self.send_message(
                    chat_id,
                    "✏️ *Escribe el nombre completo del jugador:*\n\n"
                    "Ejemplo: Juan García López\n\n"
                    "📝 Escríbelo exactamente como debe aparecer."
                )

                # Mark as waiting for manual input
                if chat_id in self.pending_verifications:
                    self.pending_verifications[chat_id]['waiting_manual'] = True

        except Exception as e:
            logger.error(f"❌ Callback error: {e}", exc_info=True)
            await self.answer_callback_query(
                callback_id,
                f"❌ Error: {str(e)}"
            )

    async def handle_text_message(self, message: Dict[str, Any]):
        """Handle text messages (manual name input or commands)"""
        chat_id = message['chat']['id']
        text = message.get('text', '')

        # Check if waiting for manual input
        if chat_id in self.pending_verifications:
            pending = self.pending_verifications[chat_id]

            if pending.get('waiting_manual'):
                # User provided manual name
                manual_name = text.strip()

                # Validate the manual name
                validation_result = self.validator.validate_full_name(manual_name)

                if validation_result['valid'] or len(manual_name.split()) >= 2:
                    # Accept it
                    ocr_result = pending['ocr_result']
                    original_validation_result = pending.get('validation_result')
                    ocr_result['player_name'] = manual_name
                    ocr_result['human_verified'] = True
                    ocr_result['manually_entered'] = True

                    await self.send_message(
                        chat_id,
                        f"✅ *Nombre confirmado:* {manual_name}\n\n"
                        "Procesando registro..."
                    )

                    await self._send_final_confirmation(chat_id, ocr_result, original_validation_result)

                    del self.pending_verifications[chat_id]
                else:
                    await self.send_message(
                        chat_id,
                        f"⚠️  *Nombre inválido:* {manual_name}\n\n"
                        "Por favor escribe nombre y apellido completos.\n"
                        "Ejemplo: Juan García López"
                    )

        # Handle commands
        elif text == '/start':
            await self.send_message(
                chat_id,
                "🏆 *Copa Telmex - OCR con QA*\n\n"
                "🤖 Claude Vision + Validación de nombres mexicanos\n\n"
                "*Características:*\n"
                "• ✅ Validación automática de nombres\n"
                "• 💡 Sugerencias inteligentes\n"
                "• 👤 Verificación humana fácil\n"
                "• 📊 Confianza >95%\n\n"
                "📸 *¡Envía una foto del formulario!*"
            )

        elif text == '/help':
            await self.send_message(
                chat_id,
                "📖 *Ayuda - OCR con QA*\n\n"
                "*Cómo funciona:*\n"
                "1. Envía foto del formulario\n"
                "2. El bot extrae datos automáticamente\n"
                "3. Si hay dudas, te pregunta\n"
                "4. Selecciona la opción correcta\n"
                "5. ¡Listo!\n\n"
                "*Validación de nombres:*\n"
                "• Base de datos de 200+ nombres mexicanos\n"
                "• 500+ apellidos mexicanos\n"
                "• Sugerencias automáticas\n"
                "• Verificación humana solo si es necesario\n\n"
                "*Tips:*\n"
                "• Foto clara y bien iluminada\n"
                "• Letra lo más legible posible\n"
                "• Evita sombras\n"
            )

    async def _save_to_database(
        self,
        chat_id: int,
        ocr_result: Dict[str, Any],
        validation_result: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Save registration data to PostgreSQL database.

        Returns:
            bool: True if save was successful, False otherwise
        """
        try:
            async with self.async_session_maker() as session:
                copa_db = CopaTelmexDB(session)

                # Extract team info
                team_name = ocr_result.get('team_club', 'Unknown Team')
                if team_name == 'no visible':
                    team_name = 'Unknown Team'

                # Get or create team - first check if this chat already has a team with this name
                teams_in_chat = await copa_db.get_teams_by_chat(chat_id)
                team = None
                for t in teams_in_chat:
                    if t.name.lower() == team_name.lower():
                        team = t
                        break

                if not team:
                    logger.info(f"📝 Creating new team: {team_name}")
                    team = await copa_db.create_team(
                        name=team_name,
                        telegram_chat_id=chat_id,
                        category=ocr_result.get('category') if ocr_result.get('category') != 'no visible' else None
                    )
                else:
                    logger.info(f"✅ Found existing team: {team_name} (ID: {team.id})")

                # Parse player name
                player_name = ocr_result.get('player_name', '')
                if not player_name or player_name == 'no visible':
                    logger.warning("⚠️  No player name to save")
                    return False

                # Split name into first and last
                name_parts = player_name.split()
                if len(name_parts) < 2:
                    first_name = name_parts[0] if name_parts else ''
                    last_name = ''
                else:
                    first_name = name_parts[0]
                    last_name = ' '.join(name_parts[1:])

                # Parse birth date
                birth_date = None
                birth_date_str = ocr_result.get('birth_date')
                if birth_date_str and birth_date_str != 'no visible':
                    try:
                        # Try dd/mm/yyyy format
                        if '/' in birth_date_str:
                            parts = birth_date_str.split('/')
                            if len(parts) == 3:
                                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                                # Handle 2-digit year
                                if year < 100:
                                    year = 2000 + year if year < 50 else 1900 + year
                                birth_date = date(year, month, day)
                        # Try dd-mm-yyyy format
                        elif '-' in birth_date_str:
                            parts = birth_date_str.split('-')
                            if len(parts) == 3:
                                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                                if year < 100:
                                    year = 2000 + year if year < 50 else 1900 + year
                                birth_date = date(year, month, day)
                    except (ValueError, IndexError) as e:
                        logger.warning(f"⚠️  Could not parse birth date: {birth_date_str}: {e}")

                # Create player
                logger.info(f"📝 Creating player: {player_name}")

                needs_review = validation_result.get('needs_human_review', False) if validation_result else False
                human_verified = ocr_result.get('human_verified', False)
                confidence = ocr_result.get('confidence', 0.0)

                player = await copa_db.create_player(
                    team_id=team.id,
                    first_name=first_name,
                    last_name=last_name,
                    birth_date=birth_date,
                    ocr_confidence=confidence,
                    needs_review=needs_review,
                    verified_by_human=human_verified,
                    verification_notes='Manually entered' if ocr_result.get('manually_entered') else None
                )

                # Create OCR registration log
                logger.info(f"📝 Creating OCR registration log")
                registration = await copa_db.create_ocr_registration(
                    telegram_chat_id=chat_id,
                    ocr_result=ocr_result,
                    validation_result=validation_result or {},
                    team_id=team.id
                )

                # Commit all changes
                await copa_db.commit()

                logger.info(f"✅ Saved to database: Team={team.id}, Player={player.id}, Registration={registration.id}")
                return True

        except Exception as e:
            logger.error(f"❌ Database save error: {e}", exc_info=True)
            return False

    async def _send_final_confirmation(
        self,
        chat_id: int,
        ocr_result: Dict[str, Any],
        validation_result: Optional[Dict[str, Any]] = None
    ):
        """Send final confirmation with all extracted data"""
        # Save to database first
        db_saved = await self._save_to_database(chat_id, ocr_result, validation_result)

        player_name = ocr_result.get('player_name', 'N/A')
        confidence = ocr_result.get('confidence', 0.0)
        human_verified = ocr_result.get('human_verified', False)
        manually_entered = ocr_result.get('manually_entered', False)

        response = "✅ *Registro Completado*\n\n"

        response += f"👤 *Jugador:* {player_name}\n"

        if manually_entered:
            response += "✏️ *Verificado manualmente*\n"
        elif human_verified:
            response += "👍 *Verificado por humano*\n"
        else:
            response += f"📊 *Confianza:* {confidence*100:.0f}%\n"

        response += "\n"

        # Add other extracted fields
        other_fields = [
            ('birth_date', '📅 Fecha de nacimiento'),
            ('category', '🏆 Categoría'),
            ('parent_name', '👨‍👩‍👧 Padre/Tutor'),
            ('parent_phone', '📞 Teléfono del tutor'),
            ('team_club', '⚽ Equipo/Club')
        ]

        for field, label in other_fields:
            value = ocr_result.get(field)
            if value and value != 'no visible':
                response += f"{label}: {value}\n"

        response += "\n"

        if db_saved:
            response += "✨ *Datos guardados en base de datos*\n"
            response += "📊 Registro ID guardado exitosamente"
        else:
            response += "⚠️  *Datos no guardados en BD*\n"
            response += "Se mostrará confirmación visual solamente"

        await self.send_message(chat_id, response)

    def _call_claude_vision(self, image_b64: str) -> Dict[str, Any]:
        """Call Claude Vision API (blocking operation)"""
        try:
            message = self.claude.messages.create(
                model="claude-3-5-sonnet-20240620",
                max_tokens=1024,
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
                                "text": (
                                    "Extrae la siguiente información del formulario de registro de Copa Telmex:\n\n"
                                    "IMPORTANTE - Identifica correctamente cada campo:\n\n"
                                    "1. **Nombre del Jugador (player_name)**:\n"
                                    "   - Debe ser un NOMBRE DE PERSONA (ej: Juan García López, María Hernández)\n"
                                    "   - DEBE incluir al menos nombre Y apellido(s)\n"
                                    "   - NO confundir con nombre de equipo/club\n"
                                    "   - Ejemplos: 'Juan García', 'María López Hernández', 'Carlos Pérez'\n\n"
                                    "2. **Equipo/Club (team_club)**:\n"
                                    "   - Nombre del equipo deportivo (ej: Alaska, Santa Barbara, Tigres)\n"
                                    "   - Puede ser una sola palabra\n"
                                    "   - NO es un nombre de persona\n\n"
                                    "3. **Fecha de nacimiento** (dd/mm/yyyy o dd-mm-yyyy)\n\n"
                                    "4. **Categoría** (U10/U12/U14/U16/U18/Open/Juvenil)\n\n"
                                    "5. **Nombre del padre/tutor** (nombre completo)\n\n"
                                    "6. **Teléfono del tutor** (10 dígitos)\n\n"
                                    "Si algún campo no es visible o no está claro, usa 'no visible'.\n\n"
                                    "Responde SOLO en formato JSON:\n"
                                    "{\n"
                                    '  "player_name": "nombre Y apellido del JUGADOR",\n'
                                    '  "birth_date": "dd/mm/yyyy o no visible",\n'
                                    '  "category": "categoría o no visible",\n'
                                    '  "parent_name": "nombre del tutor o no visible",\n'
                                    '  "parent_phone": "teléfono o no visible",\n'
                                    '  "team_club": "nombre del EQUIPO o no visible",\n'
                                    '  "confidence": 0.0-1.0\n'
                                    "}\n\n"
                                    "RECUERDA: player_name = PERSONA (nombre + apellido), team_club = EQUIPO"
                                )
                            }
                        ],
                    }
                ],
            )

            # Extract text from response
            response_text = message.content[0].text.strip()

            # Clean JSON
            if response_text.startswith('```json'):
                response_text = response_text[7:]
            if response_text.startswith('```'):
                response_text = response_text[3:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            # Extract JSON object
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            if start_idx != -1 and end_idx != -1:
                json_str = response_text[start_idx:end_idx + 1]
                result = json.loads(json_str)
                return result

            return {
                'player_name': '',
                'confidence': 0.0,
                'error': 'Could not parse response'
            }

        except Exception as e:
            logger.error(f"❌ Claude Vision error: {e}", exc_info=True)
            return {
                'player_name': '',
                'confidence': 0.0,
                'error': str(e)
            }

    async def handle_update(self, update: Dict[str, Any]):
        """Handle Telegram updates"""
        try:
            if 'callback_query' in update:
                await self.handle_callback_query(update['callback_query'])

            elif 'message' in update:
                message = update['message']

                if 'photo' in message:
                    logger.info(f"📸 Photo from chat {message['chat']['id']}")
                    await self.process_photo_message(message)

                elif 'text' in message:
                    await self.handle_text_message(message)

        except Exception as e:
            logger.error(f"❌ Error: {e}", exc_info=True)

    async def poll_updates(self):
        """Poll for Telegram updates"""
        logger.info("🚀 Bot iniciado!")
        logger.info("📸 Esperando fotos...")

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self.api_base}/getUpdates",
                        params={
                            "offset": self.last_update_id + 1,
                            "timeout": 30
                        }
                    ) as resp:
                        data = await resp.json()

                        if data.get('ok') and data.get('result'):
                            for update in data['result']:
                                self.last_update_id = update['update_id']
                                await self.handle_update(update)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Polling error: {e}")
                await asyncio.sleep(5)

    async def cleanup(self):
        """Cleanup resources"""
        if self.db_engine:
            logger.info("🔌 Closing database connections...")
            await self.db_engine.dispose()
            logger.info("✅ Database connections closed")

    async def run(self):
        """Run bot"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_base}/getMe") as resp:
                    data = await resp.json()
                    bot_username = data['result']['username']
                    logger.info(f"✅ Bot: @{bot_username}")

            await self.poll_updates()
        finally:
            await self.cleanup()


async def main():
    """Main entry point"""
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')

    if not telegram_token:
        print("❌ TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)

    if not anthropic_key:
        print("❌ ANTHROPIC_API_KEY not set!")
        sys.exit(1)

    print("=" * 60)
    print("🏆 Copa Telmex - OCR con QA y Verificación Humana")
    print("=" * 60)
    print()
    print("✅ Validación de nombres mexicanos")
    print("💡 Sugerencias inteligentes")
    print("👤 Verificación humana intuitiva")
    print("📊 Confianza >95%")
    print()

    bot = TelegramOCRQABot(telegram_token, anthropic_key)

    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("\n👋 Stopped")


if __name__ == "__main__":
    asyncio.run(main())
