#!/usr/bin/env python3
"""
Copa Telmex - Telegram ROSTER OCR Bot (Multi-Player Extraction)

Extracts 10-16 players from roster/table forms (CÉDULA DE INSCRIPCIÓN).
Handles both:
- Single player forms
- Multi-player roster tables

Features:
- Claude Vision for superior OCR
- Automatic roster vs single detection
- Batch player extraction
- Mexican names validation
- Database integration
"""

import asyncio
import logging
import os
import sys
import json
import base64
import io
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

import aiohttp
from PIL import Image
import anthropic
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from devnous.validation import MexicanNamesValidator
from devnous.copa_telmex.database import CopaTelmexDB
from devnous.copa_telmex.google_sheets_integration import get_sheets_exporter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TelegramRosterOCRBot:
    """
    Telegram Roster OCR Bot for Copa Telmex.

    Handles extraction of 10-16 players from roster forms.
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
                payload["reply_markup"] = json.dumps(reply_markup)

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
            # Get file path
            async with session.get(
                f"{self.api_base}/getFile?file_id={file_id}"
            ) as resp:
                result = await resp.json()
                file_path = result['result']['file_path']

            # Download file
            async with session.get(
                f"https://api.telegram.org/file/bot{self.telegram_token}/{file_path}"
            ) as resp:
                return await resp.read()

    def _call_claude_vision_roster(self, image_b64: str) -> Dict[str, Any]:
        """
        Call Claude Vision API for ROSTER extraction (10-16 players).

        Returns:
            {
                "form_type": "roster" or "single",
                "team_name": "Alaska",
                "category": "Juvenil",
                "players": [
                    {
                        "player_name": "Abraham Antonio Ramos",
                        "birth_date": "22/02/2010",
                        "position": "Delantero"
                    },
                    ...
                ]
            }
        """
        try:
            message = self.claude.messages.create(
                model="claude-3-5-sonnet-20240620",
                max_tokens=4000,  # Increased for multiple players
                temperature=0.0,
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
                                "text": self._get_roster_extraction_prompt()
                            }
                        ],
                    }
                ],
            )

            response_text = message.content[0].text.strip()
            logger.info(f"📋 Claude response length: {len(response_text)} chars")

            # Extract JSON from response
            json_match = response_text
            if "```json" in response_text:
                json_match = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_match = response_text.split("```")[1].split("```")[0].strip()

            result = json.loads(json_match)
            logger.info(f"✅ Extracted {len(result.get('players', []))} players")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON decode error: {e}")
            logger.error(f"Response: {response_text[:500]}")
            return {
                "form_type": "error",
                "error": "Failed to parse JSON",
                "raw_response": response_text[:500]
            }
        except Exception as e:
            logger.error(f"❌ Claude Vision error: {e}", exc_info=True)
            return {
                "form_type": "error",
                "error": str(e)
            }

    def _get_roster_extraction_prompt(self) -> str:
        """Get the roster extraction prompt"""
        return """Analiza esta imagen de formulario de Copa Telmex (CÉDULA DE INSCRIPCIÓN).

PASO 1: Determina el tipo de formulario:
- **"roster"**: Si es una CÉDULA DE INSCRIPCIÓN o tabla con MÚLTIPLES jugadores (10-16 filas)
- **"single"**: Si es un formulario de un solo jugador

PASO 2: Extrae la información DEL ENCABEZADO:
- Nombre del Equipo
- Categoría (U10/U12/U14/U16/U18/Juvenil/Open)
- Rama (Varonil/Femenil)
- Estado (estado de México)
- Municipio
- Liga
- Representante del Equipo (nombre completo)

PASO 3: Extrae los JUGADORES de la tabla:
Si es ROSTER (tabla con múltiples jugadores):
- Lee TODAS las filas de la tabla
- Cada fila tiene: Nombre del jugador, Fecha de nacimiento
- Cada fila también tiene una FOTOGRAFÍA del jugador (usualmente en la primera columna)
- Extrae TODOS los jugadores visibles (10-16 jugadores típicamente)

PASO 4: Identifica las FOTOGRAFÍAS:
- Cada jugador tiene una fotografía (foto tipo credencial/pasaporte)
- Indica la posición relativa de cada foto (top, middle, bottom)
- Indica si la foto está visible o no

Responde en formato JSON:

```json
{
  "form_type": "roster",
  "team_name": "nombre del equipo del encabezado",
  "category": "U10/U12/U14/U16/U18/Juvenil/Open",
  "gender": "Varonil o Femenil",
  "state": "nombre del estado",
  "municipality": "nombre del municipio",
  "league": "nombre de la liga",
  "representative_name": "nombre completo del representante",
  "players": [
    {
      "player_name": "nombre completo del jugador (nombre + apellidos)",
      "birth_date": "dd/mm/yyyy",
      "photo_visible": true,
      "photo_position": "top-left",
      "row_number": 1
    },
    {
      "player_name": "siguiente jugador...",
      "birth_date": "dd/mm/yyyy",
      "photo_visible": true,
      "photo_position": "left side of row 2",
      "row_number": 2
    }
  ],
  "has_photos": true,
  "photo_column": "left",
  "confidence": 0.9
}
```

IMPORTANTE:
- Extrae TODOS los campos del encabezado (team_name, category, gender, state, municipality, league, representative_name)
- Si es roster, extrae TODOS los jugadores de TODAS las filas (10-16 jugadores)
- Los nombres deben ser nombres DE PERSONA (no equipos)
- Si un campo no es visible o está vacío, usa null
- Asegúrate de extraer al menos 8-16 jugadores si es un roster completo

Responde SOLO el JSON, sin explicaciones adicionales."""

    async def process_photo_message(self, message: Dict[str, Any]):
        """Process photo message and extract players"""
        try:
            chat_id = message['chat']['id']
            logger.info(f"📸 Photo from chat {chat_id}")

            # Get largest photo
            photos = message['photo']
            largest = max(photos, key=lambda p: p['file_size'])
            file_id = largest['file_id']

            # Download photo
            logger.info(f"📥 Downloading photo: {file_id}")
            photo_bytes = await self.download_photo(file_id)

            # Convert to base64
            img = Image.open(io.BytesIO(photo_bytes))
            logger.info(f"🖼️  Image: {img.size}, {img.mode}")

            # Encode to base64
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=95)
            image_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

            # Call Claude Vision
            await self.send_message(chat_id, "🤖 *Procesando con Claude Vision...*\n\n⏳ Esto puede tomar 5-10 segundos...")

            logger.info("🤖 Calling Claude Vision API...")
            extraction_result = self._call_claude_vision_roster(image_b64)

            # Check form type
            form_type = extraction_result.get('form_type', 'unknown')

            if form_type == 'error':
                await self.send_message(
                    chat_id,
                    f"❌ *Error en OCR*\n\n{extraction_result.get('error', 'Unknown error')}"
                )
                return

            # Handle roster extraction
            if form_type == 'roster':
                await self._process_roster_extraction(chat_id, extraction_result, img)
            else:
                await self._process_single_extraction(chat_id, extraction_result)

        except Exception as e:
            logger.error(f"❌ Error: {e}", exc_info=True)
            await self.send_message(
                chat_id,
                f"❌ *Error*\n\n`{str(e)}`\n\nIntenta de nuevo."
            )

    async def _process_roster_extraction(self, chat_id: int, result: Dict[str, Any], roster_image: Optional[Image.Image] = None):
        """Process roster with multiple players and extract photos"""
        players = result.get('players', [])
        team_name = result.get('team_name', 'Equipo desconocido')
        category = result.get('category', 'N/A')
        gender = result.get('gender', None)
        state = result.get('state', None)
        municipality = result.get('municipality', None)
        league = result.get('league', None)
        representative_name = result.get('representative_name', None)
        confidence = result.get('confidence', 0.0)

        if not players:
            await self.send_message(
                chat_id,
                "⚠️  *No se detectaron jugadores en el roster*\n\n"
                "Verifica que:\n"
                "• La imagen esté clara\n"
                "• La tabla de jugadores sea visible"
            )
            return

        # Extract player photos if image is provided
        player_photos = {}
        if roster_image:
            try:
                # Generate team_id for photo storage
                import hashlib
                team_id_hash = hashlib.md5(f"{team_name}_{chat_id}".encode()).hexdigest()[:12]

                # Save full roster image
                roster_image_path = await self._save_roster_image(team_id_hash, roster_image)
                logger.info(f"📸 Saved roster image: {roster_image_path}")

                # Extract individual player photos
                has_photos = result.get('has_photos', True)
                photo_column = result.get('photo_column', 'left')
                player_photos = await self._extract_player_photos(
                    team_id=team_id_hash,
                    roster_image=roster_image,
                    players=players,
                    has_photos=has_photos,
                    photo_column=photo_column
                )
                logger.info(f"📸 Extracted {len(player_photos)} player photos")
            except Exception as e:
                logger.error(f"❌ Error extracting photos: {e}", exc_info=True)
                # Continue without photos if extraction fails

        # Send summary with ALL team data
        summary = f"✅ *Roster Extraído*\n\n"
        summary += f"⚽ *Equipo:* {team_name}\n"
        summary += f"🏆 *Categoría:* {category}\n"
        if gender:
            summary += f"👥 *Rama:* {gender}\n"
        if state:
            summary += f"📍 *Estado:* {state}\n"
        if municipality:
            summary += f"🏘️  *Municipio:* {municipality}\n"
        if league:
            summary += f"🎯 *Liga:* {league}\n"
        if representative_name:
            summary += f"👤 *Representante:* {representative_name}\n"
        summary += f"\n👥 *Jugadores detectados:* {len(players)}\n"
        summary += f"📊 *Confianza:* {confidence*100:.0f}%\n\n"
        summary += "📋 *Jugadores:*\n"

        for i, player in enumerate(players[:5], 1):  # First 5
            name = player.get('player_name', 'N/A')
            dob = player.get('birth_date', 'N/A')
            summary += f"{i}. {name} ({dob})\n"

        if len(players) > 5:
            summary += f"\n...y {len(players) - 5} jugadores más\n"

        summary += f"\n🔄 *Procesando y validando...*"

        await self.send_message(chat_id, summary)

        # Validate and save each player
        saved_count = 0
        needs_review_list = []

        # First pass: validate all players
        for player in players:
            player_name = player.get('player_name')
            if not player_name or player_name == 'null':
                continue

            # Validate name
            validation = self.validator.validate_full_name(player_name, confidence=confidence)

            if validation['needs_human_review']:
                needs_review_list.append({
                    'name': player_name,
                    'validation': validation,
                    'player_data': player
                })
            else:
                # Save to database with photo
                try:
                    photo_path = player_photos.get(player_name)  # Get photo path for this player
                    await self._save_player_to_db(
                        team_name=team_name,
                        player_name=player_name,
                        birth_date=player.get('birth_date'),
                        category=category,
                        gender=gender,
                        state=state,
                        municipality=municipality,
                        league=league,
                        representative_name=representative_name,
                        chat_id=chat_id,
                        photo_path=photo_path
                    )
                    saved_count += 1
                except Exception as e:
                    logger.error(f"Error saving {player_name}: {e}")

        # Send progress report
        if needs_review_list:
            report = f"✅ *Auto-validados:* {saved_count}/{len(players)} jugadores\n"
            report += f"⚠️  *Necesitan revisión:* {len(needs_review_list)}\n\n"
            report += "Ahora verificaremos los nombres que necesitan revisión...\n"
            await self.send_message(chat_id, report)

            # Store the entire review list and start with first player
            # Create team_data dict with all fields including photos
            team_data = {
                'team_name': team_name,
                'category': category,
                'gender': gender,
                'state': state,
                'municipality': municipality,
                'league': league,
                'representative_name': representative_name,
                'player_photos': player_photos  # Include photo paths for verification flow
            }
            await self._start_review_queue(chat_id, needs_review_list, team_data)

        else:
            # All players validated automatically
            report = f"✅ *Procesamiento Completado*\n\n"
            report += f"💾 *Guardados:* {saved_count}/{len(players)} jugadores\n"
            report += f"\n✅ Todos los nombres validados correctamente"
            await self.send_message(chat_id, report)

            # Export to Google Sheets
            await self._export_to_google_sheets(
                team_name=team_name,
                category=category,
                gender=gender,
                state=state,
                municipality=municipality,
                league=league,
                representative_name=representative_name,
                players=players,
                chat_id=chat_id
            )

    async def _process_single_extraction(self, chat_id: int, result: Dict[str, Any]):
        """Process single player form"""
        players = result.get('players', [])
        if not players:
            await self.send_message(
                chat_id,
                "⚠️  *No se detectó jugador*\n\nVerifica la imagen."
            )
            return

        player = players[0]
        player_name = player.get('player_name', 'N/A')

        await self.send_message(
            chat_id,
            f"✅ *Jugador Detectado*\n\n"
            f"👤 {player_name}\n"
            f"📅 {player.get('birth_date', 'N/A')}\n"
            f"\n💾 Guardando..."
        )

        # Save player
        try:
            team_name = result.get('team_name', 'Sin equipo')
            await self._save_player_to_db(
                team_name=team_name,
                player_name=player_name,
                birth_date=player.get('birth_date'),
                category=result.get('category'),
                chat_id=chat_id
            )
            await self.send_message(chat_id, "✅ *Guardado exitosamente*")
        except Exception as e:
            logger.error(f"Error saving: {e}")
            await self.send_message(chat_id, f"❌ Error guardando: {e}")

    async def _start_review_queue(
        self,
        chat_id: int,
        needs_review_list: List[Dict[str, Any]],
        team_data: Dict[str, Any]
    ):
        """Start the review queue and show first player"""
        # Store the entire queue in pending_verifications
        self.pending_verifications[chat_id] = {
            'review_queue': needs_review_list,
            'current_index': 0,
            'team_data': team_data,  # Store all team data
            'processing': False  # Prevent duplicate clicks
        }

        # Show first player
        await self._process_next_review(chat_id)

    async def _finish_current_review(self, chat_id: int):
        """Finish current review and move to next player"""
        if chat_id not in self.pending_verifications:
            return

        pending = self.pending_verifications[chat_id]

        # Check if this is part of a review queue
        if 'review_queue' in pending:
            # Increment index and reset processing flag
            pending['current_index'] = pending.get('current_index', 0) + 1
            pending['processing'] = False

            # Process next player
            await self._process_next_review(chat_id)
        else:
            # Single player verification, just clean up
            del self.pending_verifications[chat_id]

    async def _process_next_review(self, chat_id: int):
        """Process the next player in the review queue"""
        if chat_id not in self.pending_verifications:
            return

        pending = self.pending_verifications[chat_id]
        review_queue = pending.get('review_queue', [])
        current_index = pending.get('current_index', 0)

        # Check if we're done with all reviews
        if current_index >= len(review_queue):
            # All players reviewed!
            await self.send_message(
                chat_id,
                f"✅ *Verificación Completada*\n\n"
                f"Todos los {len(review_queue)} jugadores han sido verificados y guardados."
            )

            # Export to Google Sheets
            team_data = pending.get('team_data', {})
            # Collect all player data from review queue
            all_players = [item['player_data'] for item in review_queue]
            await self._export_to_google_sheets(
                team_name=team_data.get('team_name'),
                category=team_data.get('category'),
                gender=team_data.get('gender'),
                state=team_data.get('state'),
                municipality=team_data.get('municipality'),
                league=team_data.get('league'),
                representative_name=team_data.get('representative_name'),
                players=all_players,
                chat_id=chat_id
            )

            # Clean up
            del self.pending_verifications[chat_id]
            return

        # Get current player
        item = review_queue[current_index]
        team_data = pending['team_data']

        # Show progress
        await self.send_message(
            chat_id,
            f"👤 Jugador {current_index + 1}/{len(review_queue)}"
        )

        # Show verification UI
        await self._request_human_verification(
            chat_id,
            item['name'],
            item['validation'],
            item['player_data'],
            team_data
        )

    async def _request_human_verification(
        self,
        chat_id: int,
        detected_name: str,
        validation_result: Dict[str, Any],
        player_data: Dict[str, Any],
        team_data: Dict[str, Any]
    ):
        """Request human verification with inline keyboard for a single player"""

        # Get suggestions from validation
        parts = validation_result.get('parts', {})

        if isinstance(parts, list):
            # Empty list means incomplete name
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

        # Check if we'll have actual suggestions after filtering
        will_have_suggestions = bool(first_name_suggestions)

        if first_name_suggestions or surname_suggestions:
            message += "\n💡 ¿Es correcto el nombre?\n"
        elif 'al menos nombre y apellido' in reason.lower():
            message += f"\n⚠️ Parece faltar apellido\n"
        else:
            message += f"\n⚠️ Verifica el nombre\n"

        # Build inline keyboard
        keyboard = {"inline_keyboard": []}

        # Add suggestion buttons (with smart filtering)
        all_suggestions = []

        if first_name_suggestions:
            # Reconstruct full name with first name suggestion
            name_parts = detected_name.split()
            for suggestion in first_name_suggestions[:2]:  # Top 2
                suggested_full = f"{suggestion} {' '.join(name_parts[1:])}" if len(name_parts) > 1 else suggestion
                all_suggestions.append(suggested_full)

        if surname_suggestions and len(detected_name.split()) > 1:
            # Filter out nonsensical surname suggestions
            # Mexican names: First name + (optional second name) + paternal surname + maternal surname
            # Example: "Luis Alberto Medina Morales"
            #   - Luis = first name
            #   - Alberto = second name (often a common first name!)
            #   - Medina = paternal surname
            #   - Morales = maternal surname

            name_parts = detected_name.split()
            filtered_surname_suggestions = []

            for i, surname_result in enumerate(parts.get('surnames', [])):
                original_surname = name_parts[i + 1] if i + 1 < len(name_parts) else None

                if original_surname:
                    # Position 1 (index 0 in surnames) is likely a second given name if:
                    # 1. It's a common first name
                    # 2. The full name has 4 parts (name + second name + 2 surnames)
                    is_likely_second_name = (
                        i == 0 and  # First "surname" position
                        len(name_parts) >= 4 and  # Has at least 4 parts
                        original_surname in [
                            # Most common second names (male)
                            "Alberto", "Alejandro", "Fernando", "Francisco", "Gabriel", "Luis",
                            "Miguel", "Rafael", "Ricardo", "Antonio", "José", "Carlos", "Pedro",
                            "Juan", "Manuel", "Ángel", "Angel", "David", "Daniel", "Diego",
                            "Emilio", "Eduardo", "Enrique", "Ernesto", "Esteban", "Felipe",
                            "Gustavo", "Hugo", "Ignacio", "Javier", "Jorge", "Julio", "Leonardo",
                            "Lorenzo", "Marco", "Martín", "Mateo", "Pablo", "Ramón", "Raúl",
                            "Roberto", "Rodrigo", "Rubén", "Salvador", "Sebastián", "Sergio",
                            "Santiago", "Víctor", "Vicente", "Agustín", "Adrián", "Alfonso",
                            "Alfredo", "Andrés", "Armando", "Arturo", "Benjamín", "César",
                            "Cristian", "Damián", "Emiliano", "Fabián", "Gerardo", "Guillermo",
                            "Héctor", "Iván", "Joaquín", "Lucas", "Mario", "Mauricio", "Nicolás",
                            "Óscar", "Omar", "Samuel", "Tomás", "Ulises", "Xavier",
                            # Most common second names (female)
                            "María", "Carmen", "Rosa", "Ana", "Isabel", "Elena", "Gloria",
                            "Teresa", "Guadalupe", "Patricia", "Laura", "Luisa", "Fernanda",
                            "Sofía", "Daniela", "Mariana", "Alejandra", "Andrea", "Beatriz",
                            "Camila", "Carolina", "Cecilia", "Clara", "Cristina", "Dolores",
                            "Emma", "Esther", "Eva", "Gabriela", "Inés", "Josefina", "Julia",
                            "Leticia", "Lorena", "Lucía", "Luz", "Margarita", "Mónica",
                            "Natalia", "Paula", "Pilar", "Regina", "Rocío", "Sara", "Silvia",
                            "Susana", "Valentina", "Valeria", "Verónica", "Victoria", "Yolanda",
                            "Adriana", "Alicia", "Amparo", "Angélica", "Araceli", "Aurora",
                            "Blanca", "Catalina", "Claudia", "Concepción", "Delia", "Diana",
                            "Emilia", "Esperanza", "Eugenia", "Francisca", "Graciela", "Helena",
                            "Irene", "Isabella", "Juana", "Julieta", "Lilia", "Linda", "Lourdes",
                            "Magdalena", "Marcela", "Martha", "Matilde", "Mercedes", "Miriam",
                            "Norma", "Olivia", "Paloma", "Paulina", "Raquel", "Rebeca", "Renata",
                            "Rita", "Rosario", "Soledad", "Sonia", "Ximena", "Zoila"
                        ]
                    )

                    if is_likely_second_name:
                        # This is a second given name, skip surname suggestions
                        continue

                # Only suggest corrections for actual surnames (positions 2+)
                # And only if the suggestion is significantly different
                for suggestion in surname_result.get('suggestions', [])[:1]:  # Only top 1
                    if suggestion not in filtered_surname_suggestions and suggestion != original_surname:
                        filtered_surname_suggestions.append(suggestion)

            # Only use filtered suggestions if they make sense
            # Don't show suggestions that replace the entire surname structure
            if filtered_surname_suggestions and len(filtered_surname_suggestions) <= 1:
                for suggestion in filtered_surname_suggestions[:1]:  # Max 1 surname suggestion
                    # Keep the rest of the name intact
                    suggested_full = f"{name_parts[0]} {suggestion}"
                    all_suggestions.append(suggested_full)

        # Add buttons for suggestions (compact) - only if we have GOOD suggestions
        if all_suggestions:
            for i, suggestion in enumerate(all_suggestions[:2]):  # Max 2 to save space
                keyboard["inline_keyboard"].append([{
                    "text": f"✅ {suggestion}",
                    "callback_data": f"confirm_{i}_{suggestion}"
                }])

        # Add "use detected" button (ALWAYS show this - it's the most important)
        keyboard["inline_keyboard"].append([{
            "text": f"👍 {detected_name}",
            "callback_data": f"use_detected_{detected_name}"
        }])

        # Add "write manually" button (ALWAYS show this option)
        keyboard["inline_keyboard"].append([{
            "text": "✏️ Corregir",
            "callback_data": "write_manually"
        }])

        # Update pending verification (preserve review_queue if it exists)
        if chat_id in self.pending_verifications:
            # Update existing entry (preserves review_queue)
            self.pending_verifications[chat_id].update({
                'detected_name': detected_name,
                'suggestions': all_suggestions,
                'player_data': player_data,
                'validation_result': validation_result
            })
        else:
            # Single player verification (not part of a queue)
            self.pending_verifications[chat_id] = {
                'detected_name': detected_name,
                'suggestions': all_suggestions,
                'player_data': player_data,
                'validation_result': validation_result,
                'team_data': team_data
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
            # Check if we're not already processing (prevent duplicate clicks)
            if chat_id in self.pending_verifications:
                if self.pending_verifications[chat_id].get('processing', False):
                    await self.answer_callback_query(
                        callback_id,
                        "⏳ Procesando... espera un momento"
                    )
                    return

                # Set processing flag
                self.pending_verifications[chat_id]['processing'] = True

            if data.startswith("confirm_"):
                # User selected a suggestion
                parts = data.split("_", 2)
                if len(parts) >= 3:
                    selected_name = parts[2]

                    await self.answer_callback_query(
                        callback_id,
                        f"✅ Confirmado: {selected_name}"
                    )

                    # Update and save
                    if chat_id in self.pending_verifications:
                        pending = self.pending_verifications[chat_id]
                        # Get photo path for original player name
                        original_player_name = pending['player_data'].get('player_name', '')
                        player_photos = pending['team_data'].get('player_photos', {})
                        photo_path = player_photos.get(original_player_name)

                        await self._save_verified_player(
                            chat_id,
                            selected_name,
                            pending['player_data'],
                            pending['team_data'],
                            human_verified=True,
                            photo_path=photo_path
                        )

                        # Move to next player or clean up
                        await self._finish_current_review(chat_id)

            elif data.startswith("use_detected_"):
                # User confirmed detected name
                detected_name = data.replace("use_detected_", "")

                await self.answer_callback_query(
                    callback_id,
                    f"✅ Usando: {detected_name}"
                )

                if chat_id in self.pending_verifications:
                    pending = self.pending_verifications[chat_id]
                    # Get photo path for original player name
                    original_player_name = pending['player_data'].get('player_name', '')
                    player_photos = pending['team_data'].get('player_photos', {})
                    photo_path = player_photos.get(original_player_name)

                    await self._save_verified_player(
                        chat_id,
                        detected_name,
                        pending['player_data'],
                        pending['team_data'],
                        human_verified=True,
                        photo_path=photo_path
                    )

                    # Move to next player or clean up
                    await self._finish_current_review(chat_id)

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
        """Handle text messages (manual name input)"""
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
                    await self.send_message(
                        chat_id,
                        f"✅ *Nombre confirmado:* {manual_name}\n\n"
                        "Procesando registro..."
                    )

                    # Get photo path for original player name
                    original_player_name = pending['player_data'].get('player_name', '')
                    player_photos = pending['team_data'].get('player_photos', {})
                    photo_path = player_photos.get(original_player_name)

                    await self._save_verified_player(
                        chat_id,
                        manual_name,
                        pending['player_data'],
                        pending['team_data'],
                        human_verified=True,
                        manually_entered=True,
                        photo_path=photo_path
                    )

                    # Move to next player or clean up
                    await self._finish_current_review(chat_id)
                else:
                    await self.send_message(
                        chat_id,
                        f"⚠️  *Nombre inválido:* {manual_name}\n\n"
                        "Por favor escribe nombre y apellido completos.\n"
                        "Ejemplo: Juan García López"
                    )

    async def _save_roster_image(
        self,
        team_id: str,
        image: Image.Image
    ) -> str:
        """Save full roster image to disk"""
        photos_dir = Path("/root/samchat/photos/rosters")
        photos_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(datetime.now().timestamp())
        filename = f"{team_id}_{timestamp}.jpg"
        filepath = photos_dir / filename

        image.save(filepath, format='JPEG', quality=95)
        logger.info(f"💾 Saved roster image: {filepath}")
        return str(filepath)

    async def _extract_player_photos(
        self,
        team_id: str,
        roster_image: Image.Image,
        players: List[Dict],
        has_photos: bool = True,
        photo_column: str = "left"
    ) -> Dict[str, str]:
        """
        Extract individual player photos from roster image.

        Args:
            team_id: Team UUID
            roster_image: PIL Image object of full roster
            players: List of player dicts with row_number and photo_visible
            has_photos: Whether roster has photos
            photo_column: "left" or "right"

        Returns:
            Dict mapping player_name -> photo_path
        """
        if not has_photos:
            logger.info("📸 Roster has no photos, skipping extraction")
            return {}

        photos_dir = Path("/root/samchat/photos/players") / team_id
        photos_dir.mkdir(parents=True, exist_ok=True)

        # Analyze roster dimensions
        img_width, img_height = roster_image.size
        num_players = len(players)

        # Heuristics for typical roster layout
        header_height = int(img_height * 0.15)  # ~15% for header
        footer_height = int(img_height * 0.05)  # ~5% for footer
        table_height = img_height - header_height - footer_height
        row_height = table_height / num_players if num_players > 0 else 100

        # Standard passport photo dimensions (adjust based on actual size)
        photo_width = 80  # pixels
        photo_height = 100  # pixels

        # Photo column X position
        if photo_column == "left":
            photo_x = 10  # Left margin
        else:
            photo_x = img_width - photo_width - 10  # Right margin

        player_photos = {}
        extracted_count = 0

        for i, player in enumerate(players):
            # Check if photo is visible for this player
            if not player.get('photo_visible', True):
                logger.info(f"⚠️  No photo visible for {player.get('player_name', f'Player {i+1}')}")
                continue

            # Calculate photo coordinates based on row number
            row_num = player.get('row_number', i + 1)
            y_start = header_height + int((row_num - 1) * row_height) + 5  # +5px padding
            y_end = y_start + photo_height
            x_start = photo_x
            x_end = x_start + photo_width

            # Ensure coordinates are within image bounds
            y_start = max(0, min(y_start, img_height))
            y_end = max(0, min(y_end, img_height))
            x_start = max(0, min(x_start, img_width))
            x_end = max(0, min(x_end, img_width))

            try:
                # Crop photo from roster
                photo_crop = roster_image.crop((x_start, y_start, x_end, y_end))

                # Generate unique filename
                player_name = player.get('player_name', f'player_{i+1}')
                player_name_slug = player_name.replace(' ', '_').lower()
                # Remove special characters
                player_name_slug = ''.join(c for c in player_name_slug if c.isalnum() or c == '_')
                timestamp = int(datetime.now().timestamp() * 1000)  # milliseconds for uniqueness
                filename = f"{player_name_slug}_{timestamp}.jpg"
                filepath = photos_dir / filename

                # Save cropped photo
                photo_crop.save(filepath, format='JPEG', quality=90)

                player_photos[player_name] = str(filepath)
                extracted_count += 1
                logger.info(f"📸 Extracted photo {extracted_count}/{num_players}: {player_name} -> {filepath}")

            except Exception as e:
                logger.error(f"❌ Error extracting photo for {player.get('player_name', f'Player {i+1}')}: {e}")
                continue

        logger.info(f"✅ Extracted {extracted_count} player photos from roster")
        return player_photos

    async def _save_verified_player(
        self,
        chat_id: int,
        player_name: str,
        player_data: Dict[str, Any],
        team_data: Dict[str, Any],
        human_verified: bool = False,
        manually_entered: bool = False,
        photo_path: Optional[str] = None
    ):
        """Save player after human verification"""
        try:
            await self._save_player_to_db(
                team_name=team_data['team_name'],
                player_name=player_name,
                birth_date=player_data.get('birth_date'),
                category=team_data.get('category'),
                gender=team_data.get('gender'),
                state=team_data.get('state'),
                municipality=team_data.get('municipality'),
                league=team_data.get('league'),
                representative_name=team_data.get('representative_name'),
                chat_id=chat_id,
                photo_path=photo_path
            )

            # Send confirmation
            response = f"✅ *Jugador Guardado*\n\n"
            response += f"👤 {player_name}\n"
            response += f"📅 {player_data.get('birth_date', 'N/A')}\n"
            response += f"⚽ Equipo: {team_data['team_name']}\n"

            if manually_entered:
                response += "\n✏️ Verificado manualmente"
            elif human_verified:
                response += "\n👍 Verificado por humano"

            await self.send_message(chat_id, response)

        except Exception as e:
            logger.error(f"Error saving verified player: {e}")
            await self.send_message(
                chat_id,
                f"❌ Error guardando: {e}"
            )

    async def _save_player_to_db(
        self,
        team_name: str,
        player_name: str,
        birth_date: Optional[str],
        category: Optional[str],
        gender: Optional[str],
        state: Optional[str],
        municipality: Optional[str],
        league: Optional[str],
        representative_name: Optional[str],
        chat_id: int,
        photo_path: Optional[str] = None
    ):
        """Save player to database with complete team data and photo"""
        async with self.async_session_maker() as session:
            db = CopaTelmexDB(session)

            # Find or create team (filter by chat_id to avoid duplicates)
            team = await db.get_team_by_name(team_name, category=category, telegram_chat_id=chat_id)
            if not team:
                # Create team with ALL fields
                team = await db.create_team(
                    name=team_name,
                    gender=gender,
                    category=category,
                    league=league,
                    representative_name=representative_name,
                    state=state,
                    municipality=municipality,
                    telegram_chat_id=chat_id
                )

            team_id = team.id

            # Split name
            parts = player_name.split()
            first_name = parts[0] if parts else player_name
            last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''

            # Parse birth date
            birth_date_obj = None
            if birth_date and birth_date != 'null':
                try:
                    from datetime import datetime
                    birth_date_obj = datetime.strptime(birth_date, "%d/%m/%Y").date()
                except:
                    pass

            # Create player with photo
            player_data = await db.create_player(
                team_id=team_id,
                first_name=first_name,
                last_name=last_name,
                birth_date=birth_date_obj,
                photo_path=photo_path
            )

            if photo_path:
                logger.info(f"✅ Saved: {player_name} to team {team_name} (with photo: {photo_path})")
            else:
                logger.info(f"✅ Saved: {player_name} to team {team_name}")

    async def _export_to_google_sheets(
        self,
        team_name: str,
        category: Optional[str],
        gender: Optional[str],
        state: Optional[str],
        municipality: Optional[str],
        league: Optional[str],
        representative_name: Optional[str],
        players: List[Dict[str, Any]],
        chat_id: int
    ):
        """
        Export team registration to Google Sheets.

        Args:
            team_name: Name of the team
            category: Team category (U10, U12, etc.)
            gender: Gender (varonil, femenil)
            state: State
            municipality: Municipality
            league: League name
            representative_name: Representative name
            players: List of player data dicts
            chat_id: Telegram chat ID for notifications
        """
        try:
            logger.info(f"📊 Exporting team '{team_name}' to Google Sheets...")

            # Get sheets exporter
            exporter = get_sheets_exporter()

            # Prepare team data
            team_data = {
                'team_name': team_name,
                'category': category,
                'gender': gender,
                'state': state,
                'municipality': municipality,
                'league': league,
                'representative_name': representative_name
            }

            # Export to sheets
            success = exporter.export_team_registration(
                team_data=team_data,
                players=players,
                sheet_name="Registros"
            )

            if success:
                await self.send_message(
                    chat_id,
                    "📊 *Exportado a Google Sheets*\n\n"
                    "✅ Registro guardado en la hoja de cálculo"
                )
                logger.info(f"✅ Successfully exported team '{team_name}' to Google Sheets")
            else:
                logger.warning(f"⚠️ Failed to export team '{team_name}' to Google Sheets")
                # Don't notify user about export failure - it's not critical

        except Exception as e:
            logger.error(f"❌ Error exporting to Google Sheets: {e}", exc_info=True)
            # Don't fail the entire process if Google Sheets export fails

    async def run(self):
        """Run bot polling loop"""
        logger.info("🚀 Bot iniciado!")
        logger.info("📸 Esperando fotos de rosters...")

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

                        if data['ok'] and data['result']:
                            for update in data['result']:
                                self.last_update_id = update['update_id']

                                # Handle message
                                if 'message' in update:
                                    message = update['message']

                                    # Handle /start
                                    if 'text' in message and message['text'] == '/start':
                                        await self.send_message(
                                            message['chat']['id'],
                                            "🏆 *Copa Telmex - Roster OCR Bot*\n\n"
                                            "📸 Envía una foto de:\n"
                                            "• CÉDULA DE INSCRIPCIÓN (10-16 jugadores)\n"
                                            "• Formulario individual\n\n"
                                            "✅ Extracción automática de TODOS los jugadores\n"
                                            "✅ Validación de nombres mexicanos\n"
                                            "✅ Verificación humana interactiva\n"
                                            "✅ Guardado en base de datos\n\n"
                                            "🚀 ¡Envía una foto para empezar!"
                                        )

                                    # Handle text messages (for manual input)
                                    elif 'text' in message and message['text'] != '/start':
                                        await self.handle_text_message(message)

                                    # Handle photo
                                    elif 'photo' in message:
                                        await self.process_photo_message(message)

                                # Handle callback queries (inline keyboard buttons)
                                elif 'callback_query' in update:
                                    await self.handle_callback_query(update['callback_query'])

            except Exception as e:
                logger.error(f"❌ Error en polling: {e}")
                await asyncio.sleep(5)


async def main():
    """Main entry point"""
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')

    if not telegram_token or not anthropic_key:
        logger.error("❌ Missing TELEGRAM_BOT_TOKEN or ANTHROPIC_API_KEY")
        return

    bot = TelegramRosterOCRBot(telegram_token, anthropic_key)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
