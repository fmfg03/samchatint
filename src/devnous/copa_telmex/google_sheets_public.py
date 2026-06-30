"""
Google Sheets Integration - Public Access (No Auth)

⚠️ WARNING: This module requires the spreadsheet to be publicly editable.
This is INSECURE and should only be used for testing/development.

For production, use google_sheets_integration.py with service account.
"""
import logging
from typing import List, Dict, Any
from datetime import datetime
import requests

logger = logging.getLogger(__name__)


class PublicGoogleSheetsExporter:
    """
    Exports data to publicly editable Google Sheets using Google Apps Script Web App.

    ⚠️ SECURITY WARNING: This requires the spreadsheet to have "Anyone with link can edit"
    permissions, which is NOT SECURE for production use.

    Setup required:
    1. Make spreadsheet publicly editable
    2. Create Google Apps Script Web App (see documentation)
    3. Deploy as web app with "Anyone" access
    """

    def __init__(self, webapp_url: str):
        """
        Initialize public sheets exporter.

        Args:
            webapp_url: URL of deployed Google Apps Script Web App
        """
        self.webapp_url = webapp_url

    def export_team_registration(
        self,
        team_data: Dict[str, Any],
        players: List[Dict[str, Any]],
        sheet_name: str = "Registros"
    ) -> bool:
        """
        Export team and players to Google Sheets via Web App.

        Args:
            team_data: Team information dict
            players: List of player dicts
            sheet_name: Target sheet name

        Returns:
            bool: True if successful
        """
        try:
            # Prepare data
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Team row
            team_row = [
                timestamp,
                team_data.get('team_name', 'N/A'),
                team_data.get('category', 'N/A'),
                team_data.get('gender', 'N/A'),
                team_data.get('state', 'N/A'),
                team_data.get('municipality', 'N/A'),
                team_data.get('league', 'N/A'),
                team_data.get('representative_name', 'N/A'),
                len(players)
            ]

            # Player rows
            rows = [team_row]
            for i, player in enumerate(players, 1):
                player_name = player.get('full_name') or \
                              f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()

                birth_date = str(player.get('birth_date', 'N/A'))
                if hasattr(player.get('birth_date'), 'isoformat'):
                    birth_date = player['birth_date'].isoformat()

                player_row = [
                    '',  # Empty timestamp
                    team_data.get('team_name', 'N/A'),
                    f"Jugador {i}",
                    player_name,
                    birth_date,
                    player.get('curp', 'N/A'),
                    '✓' if player.get('photo_path') else '✗'
                ]
                rows.append(player_row)

            # Send to Web App
            response = requests.post(
                self.webapp_url,
                json={
                    'action': 'appendRows',
                    'sheetName': sheet_name,
                    'rows': rows
                },
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"✅ Exported team '{team_data.get('team_name')}' via public Web App")
                return True
            else:
                logger.error(f"❌ Web App returned status {response.status_code}: {response.text}")
                return False

        except Exception as e:
            logger.error(f"❌ Error exporting via Web App: {e}", exc_info=True)
            return False


# Simple HTTP-based approach (even less secure)
def append_to_public_sheet_simple(
    spreadsheet_id: str,
    sheet_name: str,
    rows: List[List[Any]]
) -> bool:
    """
    ⚠️ EXTREMELY INSECURE: Direct append to publicly editable sheet.

    This won't work without proper setup. Included as documentation only.
    For production, use service account authentication.
    """
    logger.warning(
        "⚠️ Cannot append to Google Sheets without authentication. "
        "Please use google_sheets_integration.py with service account credentials."
    )
    return False
