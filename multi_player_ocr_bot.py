#!/usr/bin/env python3
"""
Copa Telmex - Multi-Player OCR Bot

Extracts 10-16 players + team/manager from registration forms.
"""

import asyncio
import logging
import os
import sys
from typing import Dict, Any, Optional, List
from pathlib import Path
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


class MultiPlayerTelegramBot:
    """
    Multi-player OCR bot for Copa Telmex registration forms.

    Extracts:
    - Team/Club name
    - Manager/Coach name
    - Category (U10/U12/U14/U16/U18/Open)
    - 10-16 players with names and birth dates
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
        """Process multi-player registration form"""
        chat_id = message['chat']['id']

        try:
            await self.send_message(
                chat_id,
                "📸 Procesando formulario de equipo...\n"
                "⏳ 10-15 segundos (extrayendo múltiples jugadores)"
            )

            # Download photo
            photos = message['photo']
            largest_photo = max(photos, key=lambda p: p.get('file_size', 0))

            logger.info(f"📥 Downloading team photo: {largest_photo['file_id']}")
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

            # Call Claude Vision for multi-player OCR
            logger.info("🤖 Calling Claude Vision API for multi-player extraction...")
            loop = asyncio.get_event_loop()
            ocr_result = await loop.run_in_executor(
                None,
                self._call_claude_vision_multi_player,
                image_b64
            )

            logger.info(f"🔍 Multi-Player OCR Result: {ocr_result}")

            # Process the multi-player result
            await self._process_multi_player_result(chat_id, ocr_result)

        except Exception as e:
            logger.error(f"❌ Error processing team form: {e}", exc_info=True)
            await self.send_message(
                chat_id,
                f"❌ *Error processing team form*\n\n`{str(e)}`\n\n"
                "Please try again with a clearer photo."
            )

    async def _process_multi_player_result(self, chat_id: int, ocr_result: Dict[str, Any]):
        """Process multi-player OCR results and validate names"""

        team_name = ocr_result.get('team_name', '')
        manager_name = ocr_result.get('manager_name', '')
        category = ocr_result.get('category', '')
        players = ocr_result.get('players', [])
        overall_confidence = ocr_result.get('confidence', 0.0)

        logger.info(f"🏆 Team: {team_name}")
        logger.info(f"👨‍💼 Manager: {manager_name}")
        logger.info(f"📊 Category: {category}")
        logger.info(f"👥 Players found: {len(players)}")

        # Validate team name
        team_validation = None
        if team_name and team_name != 'no visible':
            team_validation = self.validator.validate_full_name(team_name, confidence=overall_confidence)

        # Validate manager name
        manager_validation = None
        if manager_name and manager_name != 'no visible':
            manager_validation = self.validator.validate_full_name(manager_name, confidence=overall_confidence)

        # Validate each player
        player_validations = []
        for i, player in enumerate(players):
            player_name = player.get('name', '')
            if player_name and player_name != 'no visible':
                validation = self.validator.validate_full_name(player_name, confidence=overall_confidence)
                player_validations.append({
                    'index': i,
                    'name': player_name,
                    'birth_date': player.get('birth_date', 'no visible'),
                    'validation': validation
                })

        # Check if any need human review
        needs_review = False
        if team_validation and team_validation.get('needs_human_review'):
            needs_review = True
        if manager_validation and manager_validation.get('needs_human_review'):
            needs_review = True
        for player_val in player_validations:
            if player_val['validation'].get('needs_human_review'):
                needs_review = True
                break

        if needs_review:
            await self._request_team_verification(
                chat_id,
                ocr_result,
                team_validation,
                manager_validation,
                player_validations
            )
        else:
            await self._send_team_confirmation(chat_id, ocr_result)

    async def _request_team_verification(
        self,
        chat_id: int,
        ocr_result: Dict[str, Any],
        team_validation: Optional[Dict],
        manager_validation: Optional[Dict],
        player_validations: List[Dict]
    ):
        """Request verification for team information"""

        message = "❓ *Verificación de Equipo Necesaria*\n\n"

        # Show team info
        team_name = ocr_result.get('team_name', 'no visible')
        manager_name = ocr_result.get('manager_name', 'no visible')
        category = ocr_result.get('category', 'no visible')

        message += f"🏆 *Equipo:* {team_name}\n"
        message += f"👨‍💼 *Manager:* {manager_name}\n"
        message += f"📊 *Categoría:* {category}\n\n"

        # Show players that need review
        needs_review_players = [pv for pv in player_validations if pv['validation'].get('needs_human_review')]

        if needs_review_players:
            message += "👤 *Jugadores que necesitan verificación:*\n"
            for pv in needs_review_players[:5]:  # Show max 5 to avoid long message
                message += f"• {pv['name']}\n"
            if len(needs_review_players) > 5:
                message += f"... y {len(needs_review_players) - 5} más\n"

        message += f"\n📊 *Confianza general:* {ocr_result.get('confidence', 0.0)*100:.0f}%"

        # Store for verification
        self.pending_verifications[chat_id] = {
            'ocr_result': ocr_result,
            'team_validation': team_validation,
            'manager_validation': manager_validation,
            'player_validations': player_validations
        }

        # Create inline keyboard
        keyboard = {"inline_keyboard": [
            [{"text": "✅ Confirmar equipo", "callback_data": "confirm_team"}],
            [{"text": "✏️ Corregir información", "callback_data": "edit_team"}],
            [{"text": "❌ Cancelar", "callback_data": "cancel_team"}]
        ]}

        await self.send_message(
            chat_id,
            message,
            reply_markup=keyboard
        )

    async def _send_team_confirmation(self, chat_id: int, ocr_result: Dict[str, Any]):
        """Send final team confirmation"""

        team_name = ocr_result.get('team_name', 'no visible')
        manager_name = ocr_result.get('manager_name', 'no visible')
        category = ocr_result.get('category', 'no visible')
        players = ocr_result.get('players', [])
        confidence = ocr_result.get('confidence', 0.0)

        message = "✅ *Equipo Registrado Exitosamente*\n\n"
        message += f"🏆 *Equipo:* {team_name}\n"
        message += f"👨‍💼 *Manager:* {manager_name}\n"
        message += f"📊 *Categoría:* {category}\n"
        message += f"📊 *Confianza:* {confidence*100:.0f}%\n\n"

        message += f"👥 *Jugadores ({len(players)}):*\n"
        for i, player in enumerate(players[:10], 1):  # Show max 10
            name = player.get('name', 'N/A')
            birth_date = player.get('birth_date', 'N/A')
            message += f"{i}. {name} ({birth_date})\n"

        if len(players) > 10:
            message += f"... y {len(players) - 10} jugadores más\n"

        # Save to database
        saved = await self._save_team_to_database(chat_id, ocr_result)

        if saved:
            message += "\n✨ *Equipo guardado en base de datos*"
        else:
            message += "\n⚠️ *Error: No se pudo guardar en base de datos*"

        await self.send_message(chat_id, message)

    async def _save_team_to_database(self, chat_id: int, ocr_result: Dict[str, Any]) -> bool:
        """Save team and all players to database"""
        try:
            async with self.async_session_maker() as session:
                copa_db = CopaTelmexDB(session)

                # Create team
                team_name = ocr_result.get('team_name', 'Unknown Team')
                manager_name = ocr_result.get('manager_name', '')
                category = ocr_result.get('category', '')

                team = await copa_db.create_team(
                    name=team_name,
                    telegram_chat_id=chat_id,
                    category=category if category != 'no visible' else None
                )

                logger.info(f"📝 Created team: {team_name} (ID: {team.id})")

                # Create players
                players = ocr_result.get('players', [])
                for player_data in players:
                    player_name = player_data.get('name', '')
                    birth_date_str = player_data.get('birth_date', '')

                    if not player_name or player_name == 'no visible':
                        continue

                    # Parse player name
                    name_parts = player_name.split()
                    if len(name_parts) < 2:
                        first_name = name_parts[0] if name_parts else ''
                        last_name = ''
                    else:
                        first_name = name_parts[0]
                        last_name = ' '.join(name_parts[1:])

                    # Parse birth date
                    birth_date = None
                    if birth_date_str and birth_date_str != 'no visible':
                        try:
                            if '/' in birth_date_str:
                                parts = birth_date_str.split('/')
                                if len(parts) == 3:
                                    day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                                    if year < 100:
                                        year = 2000 + year if year < 50 else 1900 + year
                                    birth_date = date(year, month, day)
                        except (ValueError, IndexError):
                            logger.warning(f"Could not parse birth date: {birth_date_str}")

                    # Create player
                    player = await copa_db.create_player(
                        team_id=team.id,
                        first_name=first_name,
                        last_name=last_name,
                        birth_date=birth_date,
                        ocr_confidence=ocr_result.get('confidence', 0.0),
                        needs_review=False,
                        verified_by_human=True
                    )

                    logger.info(f"📝 Created player: {player_name}")

                # Create team registration log
                await copa_db.create_ocr_registration(
                    telegram_chat_id=chat_id,
                    ocr_result=ocr_result,
                    validation_result={},
                    team_id=team.id
                )

                await copa_db.commit()
                logger.info(f"✅ Saved team with {len(players)} players to database")
                return True

        except Exception as e:
            logger.error(f"❌ Database save error: {e}", exc_info=True)
            return False

    def _call_claude_vision_multi_player(self, image_b64: str) -> Dict[str, Any]:
        """Call Claude Vision API for multi-player extraction"""
        try:
            message = self.claude.messages.create(
                model="claude-3-5-sonnet-20240620",
                max_tokens=2048,  # Increased for multiple players
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
                                    "Extrae la información COMPLETA del formulario de registro de equipo Copa Telmex.\n\n"
                                    "IMPORTANTE: Este formulario contiene MÚLTIPLES jugadores (10-16) + equipo + manager.\n\n"
                                    "Debes extraer:\n\n"
                                    "1. **team_name**: Nombre del equipo/club\n"
                                    "2. **manager_name**: Nombre del manager/entrenador\n"
                                    "3. **category**: Categoría (U10/U12/U14/U16/U18/Open/Juvenil)\n"
                                    "4. **players**: Lista de TODOS los jugadores visibles\n"
                                    "   - Cada jugador necesita: 'name' y 'birth_date'\n"
                                    "   - Extrae TODOS los nombres que veas (hasta 16)\n"
                                    "   - Si no ves fecha de nacimiento, usa 'no visible'\n\n"
                                    "EJEMPLO de estructura:\n"
                                    "Si ves 'Juan García 15/03/2012', extrae:\n"
                                    "name: 'Juan García', birth_date: '15/03/2012'\n\n"
                                    "Reglas IMPORTANTES:\n"
                                    "- Extrae TODOS los jugadores que encuentres (mínimo 5, máximo 16)\n"
                                    "- Los nombres deben ser COMPLETOS (nombre + apellido)\n"
                                    "- Si un campo no es visible, usa 'no visible'\n"
                                    "- NO inventes información\n"
                                    "- Las fechas deben estar en formato dd/mm/yyyy\n\n"
                                    "Responde ÚNICAMENTE en formato JSON:\n"
                                    "{\n"
                                    '  "team_name": "nombre del equipo o no visible",\n'
                                    '  "manager_name": "nombre del manager o no visible",\n'
                                    '  "category": "categoría o no visible",\n'
                                    '  "players": [\n'
                                    '    {"name": "jugador 1 nombre completo", "birth_date": "dd/mm/yyyy o no visible"},\n'
                                    '    {"name": "jugador 2 nombre completo", "birth_date": "dd/mm/yyyy o no visible"},\n'
                                    '    {"name": "jugador 3 nombre completo", "birth_date": "dd/mm/yyyy o no visible"},\n'
                                    '    "... extrae TODOS los jugadores que veas"\n'
                                    '  ],\n'
                                    '  "confidence": 0.0-1.0\n'
                                    "}\n\n"
                                    "CRÍTICO: Extrae TODOS los nombres de jugadores que puedas ver en el formulario."
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

                # Ensure we have players array
                if 'players' not in result:
                    result['players'] = []

                # Log player count
                player_count = len(result.get('players', []))
                logger.info(f"👥 Extracted {player_count} players from form")

                return result

            return {
                'team_name': '',
                'manager_name': '',
                'category': '',
                'players': [],
                'confidence': 0.0,
                'error': 'Could not parse response'
            }

        except Exception as e:
            logger.error(f"❌ Claude Vision error: {e}", exc_info=True)
            return {
                'team_name': '',
                'manager_name': '',
                'category': '',
                'players': [],
                'confidence': 0.0,
                'error': str(e)
            }

    async def handle_update(self, update: Dict[str, Any]):
        """Handle Telegram updates"""
        try:
            if 'message' in update:
                message = update['message']

                if 'photo' in message:
                    logger.info(f"📸 Team form photo from chat {message['chat']['id']}")
                    await self.process_photo_message(message)

                elif 'text' in message:
                    text = message.get('text', '')
                    chat_id = message['chat']['id']

                    if text == '/start':
                        await self.send_message(
                            chat_id,
                            "🏆 *Copa Telmex - Multi-Player OCR Bot*\n\n"
                            "📸 *Envía foto del formulario de equipo*\n\n"
                            "Extrae automáticamente:\n"
                            "• 🏆 Nombre del equipo\n"
                            "• 👨‍💼 Manager/Entrenador\n"
                            "• 📊 Categoría\n"
                            "• 👥 Todos los jugadores (10-16)\n"
                            "• 📅 Fechas de nacimiento\n\n"
                            "✅ Validación de nombres mexicanos\n"
                            "💡 Correcciones automáticas\n"
                            "👤 Verificación humana si es necesario"
                        )

        except Exception as e:
            logger.error(f"❌ Error: {e}", exc_info=True)

    async def poll_updates(self):
        """Poll for Telegram updates"""
        logger.info("🚀 Multi-Player OCR Bot iniciado!")
        logger.info("📸 Esperando formularios de equipo...")

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
                    logger.info(f"✅ Multi-Player Bot: @{bot_username}")

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
    print("🏆 Copa Telmex - Multi-Player OCR Bot")
    print("=" * 60)
    print()
    print("✅ Extracts 10-16 players + team + manager")
    print("✅ Mexican name validation for all players")
    print("✅ Team registration to database")
    print("✅ Human verification when needed")
    print()

    bot = MultiPlayerTelegramBot(telegram_token, anthropic_key)

    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("\n👋 Stopped")


if __name__ == "__main__":
    asyncio.run(main())