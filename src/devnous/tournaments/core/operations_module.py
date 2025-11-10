"""
Operations Module - Manages tournament operations.

Handles:
- Team and player registration (with OCR)
- Match scheduling
- Venue management
- Logistics
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from pathlib import Path
import sys
import base64
import json
import io
from PIL import Image

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

logger = logging.getLogger(__name__)


class OperationsModule:
    """Operations management for tournaments"""

    def __init__(self, tournament_id: str, config: Dict[str, Any], db=None, anthropic_key: str = None):
        self.tournament_id = tournament_id
        self.config = config.get('operations', {})
        self.db = db

        # In-memory storage
        self.teams = []
        self.players = []
        self.matches = []
        self.venues = []

        # OCR configuration
        self.ocr_enabled = self.config.get('ocr_enabled', False)
        self.ocr_provider = self.config.get('ocr_provider', 'claude_vision')

        # Initialize OCR components if enabled
        if self.ocr_enabled and anthropic_key:
            import anthropic
            from devnous.validation import MexicanNamesValidator

            self.claude = anthropic.Anthropic(api_key=anthropic_key)
            self.validator = MexicanNamesValidator(min_confidence=0.80)
            self.pending_verifications = {}  # Store pending verifications per chat
            logger.info(f"✅ OCR enabled with {self.ocr_provider}")
        else:
            self.claude = None
            self.validator = None
            self.pending_verifications = {}
            logger.info("📭 OCR disabled")

        logger.info(f"🏃 Operations module initialized for {tournament_id}")

    async def handle(self, message):
        """Handle operations messages"""
        text = message.text.lower()

        # Check if this is an OCR registration (photo message)
        if message.photo and self.ocr_enabled:
            return await self.process_ocr_registration(message)
        elif 'registro_ocr' in text and message.photo and self.ocr_enabled:
            return await self.process_ocr_registration(message)
        elif 'registrar equipo' in text:
            return await self.register_team(message)
        elif 'ver equipos' in text:
            return await self.list_teams()
        elif 'programar partido' in text:
            return await self.schedule_match(message)
        elif 'calendario' in text:
            return await self.show_calendar()
        else:
            return self.get_operations_help()

    async def register_team(self, message) -> str:
        """Register a team (with OCR if photo provided)"""
        team = {
            'id': len(self.teams) + 1,
            'name': message.data.get('team_name', 'Team'),
            'category': message.data.get('category', 'General'),
            'registered_at': datetime.now(),
            'players_count': 0
        }

        self.teams.append(team)

        return f"""✅ *Equipo Registrado*

Nombre: {team['name']}
Categoría: {team['category']}
Fecha: {team['registered_at'].strftime('%Y-%m-%d')}

Total equipos: {len(self.teams)}
"""

    async def list_teams(self) -> str:
        """List registered teams"""
        if not self.teams:
            return "📭 No hay equipos registrados"

        lines = ["🏆 *Equipos Registrados*\n"]
        for team in self.teams:
            lines.append(f"• {team['name']} ({team['category']}) - {team['players_count']} jugadores")

        return "\n".join(lines)

    async def schedule_match(self, message) -> str:
        """Schedule a match"""
        match = {
            'id': len(self.matches) + 1,
            'team_a': message.data.get('team_a', 'Team A'),
            'team_b': message.data.get('team_b', 'Team B'),
            'date': message.data.get('date', datetime.now()),
            'venue': message.data.get('venue', 'TBD'),
            'status': 'scheduled'
        }

        self.matches.append(match)

        return f"""✅ *Partido Programado*

{match['team_a']} vs {match['team_b']}
Fecha: {match['date']}
Cancha: {match['venue']}

Total partidos: {len(self.matches)}
"""

    async def show_calendar(self) -> str:
        """Show match calendar"""
        if not self.matches:
            return "📭 No hay partidos programados"

        lines = ["📅 *Calendario de Partidos*\n"]
        for match in self.matches[-10:]:
            lines.append(f"• {match['team_a']} vs {match['team_b']} - {match['date']}")

        return "\n".join(lines)

    async def get_metrics(self) -> Dict[str, Any]:
        """Get operations metrics"""
        return {
            'teams_registered': len(self.teams),
            'players_registered': sum(t.get('players_count', 0) for t in self.teams),
            'matches_scheduled': len(self.matches),
            'matches_completed': sum(1 for m in self.matches if m['status'] == 'completed'),
            'venues_booked': len(self.venues)
        }

    # ==================== OCR FUNCTIONALITY ====================

    async def process_ocr_registration(self, message):
        """Process player registration with OCR"""
        if not self.ocr_enabled or not self.claude:
            return "❌ OCR no está habilitado para este torneo"

        try:
            chat_id = message.chat_id
            photo_bytes = message.photo

            # Process image
            image = Image.open(io.BytesIO(photo_bytes))
            logger.info(f"🖼️  Image: {image.size}, {image.mode}")

            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGB')

            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='JPEG', quality=95)
            img_byte_arr.seek(0)
            optimized_bytes = img_byte_arr.getvalue()

            image_b64 = base64.b64encode(optimized_bytes).decode('utf-8')

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

            if player_name:
                validation_result = self.validator.validate_full_name(
                    player_name,
                    confidence=ocr_confidence
                )

                logger.info(f"📊 Validation result: {validation_result}")

                if validation_result['needs_human_review']:
                    logger.info(f"👤 Needs human review for: '{player_name}'")
                    # Return dict with inline keyboard for verification
                    return await self._request_human_verification(
                        chat_id,
                        player_name,
                        validation_result,
                        ocr_result
                    )
                else:
                    logger.info(f"✅ Name validated automatically: '{player_name}'")
                    # Name is valid, save and send confirmation
                    return await self._send_final_confirmation(chat_id, ocr_result, validation_result)
            else:
                return "⚠️  *No se detectó nombre del jugador*\n\nPor favor verifica que:\n• El nombre esté visible\n• La foto tenga buena iluminación\n• El texto sea legible"

        except Exception as e:
            logger.error(f"❌ OCR Error: {e}", exc_info=True)
            return f"❌ *Error*\n\n`{str(e)}`\n\nPor favor intenta de nuevo."

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
                                    "Extrae la siguiente información del formulario de registro:\n\n"
                                    "1. **Nombre del Jugador (player_name)**: DEBE incluir nombre Y apellido(s)\n"
                                    "2. **Equipo/Club (team_club)**: Nombre del equipo deportivo\n"
                                    "3. **Fecha de nacimiento** (dd/mm/yyyy)\n"
                                    "4. **Categoría** (U10/U12/U14/U16/U18/Open)\n"
                                    "5. **Nombre del padre/tutor**\n"
                                    "6. **Teléfono del tutor**\n\n"
                                    "Si algún campo no es visible, usa 'no visible'.\n\n"
                                    "Responde SOLO en formato JSON:\n"
                                    "{\n"
                                    '  "player_name": "nombre Y apellido",\n'
                                    '  "birth_date": "dd/mm/yyyy o no visible",\n'
                                    '  "category": "categoría o no visible",\n'
                                    '  "parent_name": "nombre o no visible",\n'
                                    '  "parent_phone": "teléfono o no visible",\n'
                                    '  "team_club": "equipo o no visible",\n'
                                    '  "confidence": 0.0-1.0\n'
                                    "}"
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

            return {'player_name': '', 'confidence': 0.0, 'error': 'Could not parse response'}

        except Exception as e:
            logger.error(f"❌ Claude Vision error: {e}", exc_info=True)
            return {'player_name': '', 'confidence': 0.0, 'error': str(e)}

    async def _request_human_verification(
        self,
        chat_id: int,
        detected_name: str,
        validation_result: Dict[str, Any],
        ocr_result: Dict[str, Any]
    ):
        """Request human verification with inline keyboard"""

        # Build inline keyboard
        keyboard = {"inline_keyboard": []}

        # Get suggestions
        parts = validation_result.get('parts', {})
        all_suggestions = []

        if isinstance(parts, dict):
            first_name_suggestions = parts.get('first_name', {}).get('suggestions', [])
            surname_suggestions = []
            for surname_result in parts.get('surnames', []):
                surname_suggestions.extend(surname_result.get('suggestions', []))

            # Reconstruct full name suggestions
            if first_name_suggestions:
                name_parts = detected_name.split()
                for suggestion in first_name_suggestions[:2]:
                    suggested_full = f"{suggestion} {' '.join(name_parts[1:])}"
                    all_suggestions.append(suggested_full)

            if surname_suggestions and len(detected_name.split()) > 1:
                name_parts = detected_name.split()
                for suggestion in surname_suggestions[:2]:
                    suggested_full = f"{name_parts[0]} {suggestion}"
                    all_suggestions.append(suggested_full)

        # Build message
        message_text = f"❓ *Verificación Necesaria*\n\nDetectado: *{detected_name}*\n\n"

        if all_suggestions:
            message_text += "💡 ¿Es correcto?\n"
        else:
            message_text += "⚠️ Nombre no encontrado\n"

        # Add suggestion buttons
        for i, suggestion in enumerate(all_suggestions[:2]):
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

        return {
            'text': message_text,
            'reply_markup': keyboard
        }

    async def handle_callback_query(self, callback_query: Dict[str, Any], telegram_adapter):
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

                    await telegram_adapter.answer_callback_query(
                        callback_id,
                        f"✅ Confirmado: {selected_name}"
                    )

                    if chat_id in self.pending_verifications:
                        ocr_result = self.pending_verifications[chat_id]['ocr_result']
                        validation_result = self.pending_verifications[chat_id].get('validation_result')
                        ocr_result['player_name'] = selected_name
                        ocr_result['human_verified'] = True

                        response = await self._send_final_confirmation(chat_id, ocr_result, validation_result)
                        await telegram_adapter.send_message(chat_id, response)

                        del self.pending_verifications[chat_id]

            elif data.startswith("use_detected_"):
                detected_name = data.replace("use_detected_", "")

                await telegram_adapter.answer_callback_query(
                    callback_id,
                    f"✅ Usando: {detected_name}"
                )

                if chat_id in self.pending_verifications:
                    ocr_result = self.pending_verifications[chat_id]['ocr_result']
                    validation_result = self.pending_verifications[chat_id].get('validation_result')
                    ocr_result['player_name'] = detected_name
                    ocr_result['human_verified'] = True

                    response = await self._send_final_confirmation(chat_id, ocr_result, validation_result)
                    await telegram_adapter.send_message(chat_id, response)

                    del self.pending_verifications[chat_id]

            elif data == "write_manually":
                await telegram_adapter.answer_callback_query(
                    callback_id,
                    "✏️ Escribe el nombre correcto"
                )

                await telegram_adapter.send_message(
                    chat_id,
                    "✏️ *Escribe el nombre completo del jugador:*\n\nEjemplo: Juan García López\n\n📝 Escríbelo exactamente como debe aparecer."
                )

                if chat_id in self.pending_verifications:
                    self.pending_verifications[chat_id]['waiting_manual'] = True

        except Exception as e:
            logger.error(f"❌ Callback error: {e}", exc_info=True)
            await telegram_adapter.answer_callback_query(
                callback_id,
                f"❌ Error: {str(e)}"
            )

    async def _save_to_database(
        self,
        chat_id: int,
        ocr_result: Dict[str, Any],
        validation_result: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Save registration data to database"""
        if not self.db:
            logger.warning("⚠️  No database connection")
            return False

        try:
            from devnous.copa_telmex.database import CopaTelmexDB

            copa_db = CopaTelmexDB(self.db)

            # Extract team info
            team_name = ocr_result.get('team_club', 'Unknown Team')
            if team_name == 'no visible':
                team_name = 'Unknown Team'

            # Get or create team
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

            # Split name
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
                    if '/' in birth_date_str:
                        parts = birth_date_str.split('/')
                        if len(parts) == 3:
                            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                            if year < 100:
                                year = 2000 + year if year < 50 else 1900 + year
                            birth_date = date(year, month, day)
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

            # Commit
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
    ) -> str:
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

        return response

    # ==================== END OCR FUNCTIONALITY ====================

    def get_operations_help(self) -> str:
        """Get help message"""
        ocr_help = ""
        if self.ocr_enabled:
            ocr_help = "• 📸 Enviar foto de formulario (OCR automático)\n"

        return f"""🏃 *Módulo de Operaciones*

*Comandos:*
{ocr_help}• Registrar equipo
• Ver equipos
• Programar partido
• Ver calendario
"""

    async def cleanup(self):
        """Cleanup"""
        logger.info(f"🔌 Operations module cleanup for {self.tournament_id}")
