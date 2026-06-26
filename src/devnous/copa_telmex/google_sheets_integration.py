"""
Google Sheets Integration for Copa Telmex Registration System

This module handles exporting team and player registrations to Google Sheets.
"""
import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class GoogleSheetsExporter:
    """
    Exports Copa Telmex team registrations to Google Sheets.

    Required environment variables:
        GOOGLE_SHEETS_CREDENTIALS_PATH: Path to service account JSON file
        GOOGLE_SHEETS_SPREADSHEET_ID: ID of the target spreadsheet
    """

    # Google Sheets API scopes
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        spreadsheet_id: Optional[str] = None
    ):
        """
        Initialize Google Sheets exporter.

        Args:
            credentials_path: Path to service account JSON (defaults to env var)
            spreadsheet_id: Target spreadsheet ID (defaults to env var)
        """
        self.credentials_path = credentials_path or os.getenv(
            'GOOGLE_SHEETS_CREDENTIALS_PATH',
            '/root/samchat/config/google_sheets_credentials.json'
        )
        self.spreadsheet_id = spreadsheet_id or os.getenv(
            'GOOGLE_SHEETS_SPREADSHEET_ID',
            '1ZJL4t70zdFaBbxKD3bJLQHS6MHcEfbjlgvxTO1GaY2E'
        )

        self.service = None
        self._initialize_service()

    def _initialize_service(self):
        """Initialize Google Sheets API service."""
        try:
            if not Path(self.credentials_path).exists():
                logger.warning(
                    f"⚠️ Google Sheets credentials not found at {self.credentials_path}. "
                    "Sheets export will be disabled."
                )
                return

            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=self.SCOPES
            )

            self.service = build('sheets', 'v4', credentials=credentials)
            logger.info("✅ Google Sheets API service initialized")

        except Exception as e:
            logger.error(f"❌ Error initializing Google Sheets service: {e}")
            self.service = None

    def export_team_registration(
        self,
        team_data: Dict[str, Any],
        players: List[Dict[str, Any]],
        sheet_name: str = "Registros"
    ) -> bool:
        """
        Export team and players to Google Sheets.

        Args:
            team_data: Team information dict with keys:
                - team_name
                - category
                - gender
                - state
                - municipality
                - league
                - representative_name
            players: List of player dicts with keys:
                - full_name (or first_name + last_name)
                - birth_date
                - curp (optional)
                - photo_path (optional)
            sheet_name: Target sheet name (default: "Registros")

        Returns:
            bool: True if export successful, False otherwise
        """
        if not self.service:
            logger.warning("⚠️ Google Sheets service not initialized. Skipping export.")
            return False

        try:
            # Prepare team row
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            team_row = [
                timestamp,
                team_data.get('team_name', 'N/A'),
                team_data.get('category', 'N/A'),
                team_data.get('gender', 'N/A'),
                team_data.get('state', 'N/A'),
                team_data.get('municipality', 'N/A'),
                team_data.get('league', 'N/A'),
                team_data.get('representative_name', 'N/A'),
                len(players)  # Number of players
            ]

            # Prepare player rows
            player_rows = []
            for i, player in enumerate(players, 1):
                # Get player name
                if 'full_name' in player:
                    player_name = player['full_name']
                else:
                    first = player.get('first_name', '')
                    last = player.get('last_name', '')
                    player_name = f"{first} {last}".strip()

                # Get birth date
                birth_date = player.get('birth_date')
                if birth_date:
                    if hasattr(birth_date, 'isoformat'):
                        birth_date = birth_date.isoformat()
                    elif hasattr(birth_date, 'strftime'):
                        birth_date = birth_date.strftime('%Y-%m-%d')
                    birth_date = str(birth_date)
                else:
                    birth_date = 'N/A'

                player_row = [
                    '',  # Empty timestamp (aligns with team row)
                    team_data.get('team_name', 'N/A'),  # Team name for reference
                    f"Jugador {i}",  # Player number
                    player_name,
                    birth_date,
                    player.get('curp', 'N/A'),
                    '✓' if player.get('photo_path') else '✗'  # Photo indicator
                ]
                player_rows.append(player_row)

            # Combine team and player rows
            all_rows = [team_row] + player_rows

            # Append to sheet
            result = self._append_rows(sheet_name, all_rows)

            if result:
                logger.info(
                    f"✅ Exported team '{team_data.get('team_name')}' "
                    f"with {len(players)} players to Google Sheets"
                )
                return True
            else:
                logger.error("❌ Failed to export to Google Sheets")
                return False

        except Exception as e:
            logger.error(f"❌ Error exporting to Google Sheets: {e}", exc_info=True)
            return False

    def _append_rows(
        self,
        sheet_name: str,
        rows: List[List[Any]]
    ) -> bool:
        """
        Append rows to a sheet.

        Args:
            sheet_name: Name of the sheet to append to
            rows: List of rows (each row is a list of values)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            body = {
                'values': rows
            }

            result = self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{sheet_name}!A:Z",
                valueInputOption='USER_ENTERED',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()

            updates = result.get('updates', {})
            rows_updated = updates.get('updatedRows', 0)

            logger.info(f"📊 Appended {rows_updated} rows to '{sheet_name}'")
            return True

        except HttpError as e:
            if e.resp.status == 404:
                logger.error(
                    f"❌ Sheet '{sheet_name}' not found in spreadsheet. "
                    "Creating it..."
                )
                if self._create_sheet(sheet_name):
                    return self._append_rows(sheet_name, rows)
            else:
                logger.error(f"❌ HTTP error appending to sheet: {e}")
            return False

        except Exception as e:
            logger.error(f"❌ Error appending to sheet: {e}", exc_info=True)
            return False

    def _create_sheet(self, sheet_name: str) -> bool:
        """
        Create a new sheet in the spreadsheet.

        Args:
            sheet_name: Name of the sheet to create

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            body = {
                'requests': [{
                    'addSheet': {
                        'properties': {
                            'title': sheet_name
                        }
                    }
                }]
            }

            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body=body
            ).execute()

            logger.info(f"✅ Created sheet '{sheet_name}'")

            # Add header row
            header = [
                'Timestamp',
                'Equipo',
                'Categoría',
                'Rama',
                'Estado',
                'Municipio',
                'Liga',
                'Representante',
                'Núm. Jugadores',
                '',  # Separator
                'Jugador #',
                'Nombre Completo',
                'Fecha Nacimiento',
                'CURP',
                'Foto'
            ]

            self._append_rows(sheet_name, [header])
            return True

        except Exception as e:
            logger.error(f"❌ Error creating sheet: {e}", exc_info=True)
            return False

    def initialize_sheet_with_headers(self, sheet_name: str = "Registros") -> bool:
        """
        Initialize a sheet with proper headers.

        Args:
            sheet_name: Name of the sheet

        Returns:
            bool: True if successful
        """
        if not self.service:
            logger.warning("⚠️ Google Sheets service not initialized")
            return False

        try:
            # Check if sheet exists
            sheet_metadata = self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id
            ).execute()

            sheets = sheet_metadata.get('sheets', [])
            sheet_exists = any(
                sheet['properties']['title'] == sheet_name
                for sheet in sheets
            )

            if not sheet_exists:
                logger.info(f"📊 Creating sheet '{sheet_name}' with headers...")
                return self._create_sheet(sheet_name)
            else:
                logger.info(f"✅ Sheet '{sheet_name}' already exists")
                return True

        except Exception as e:
            logger.error(f"❌ Error initializing sheet: {e}", exc_info=True)
            return False


# Singleton instance
_exporter_instance: Optional[GoogleSheetsExporter] = None


def get_sheets_exporter() -> GoogleSheetsExporter:
    """Get or create singleton GoogleSheetsExporter instance."""
    global _exporter_instance
    if _exporter_instance is None:
        _exporter_instance = GoogleSheetsExporter()
    return _exporter_instance
