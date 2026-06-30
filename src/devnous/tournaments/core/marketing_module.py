"""
Marketing Module - Manages tournament marketing and communications.

Handles:
- Announcements and communications
- Social media tracking
- Reports and statistics
- Media relations
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class MarketingModule:
    """Marketing and communications for tournaments"""

    def __init__(self, tournament_id: str, config: Dict[str, Any], db=None):
        self.tournament_id = tournament_id
        # Support both legacy config (top-level "marketing") and new layout under "modules".
        self.config = config.get("marketing", {}) or config.get("modules", {}).get("marketing", {})
        self.db = db

        # In-memory storage
        self.announcements = []
        self.social_posts = []
        self.media_contacts = []

        logger.info(f"📣 Marketing module initialized for {tournament_id}")

    async def handle(self, message) -> str:
        """Handle marketing messages"""
        text = message.text.lower()

        if 'comunicado' in text or 'announcement' in text:
            return await self.send_announcement(message)
        elif 'estadisticas' in text or 'stats' in text:
            return await self.get_statistics()
        elif 'reporte' in text or 'report' in text:
            return await self.generate_report()
        else:
            return self.get_marketing_help()

    async def send_announcement(self, message) -> str:
        """Send announcement to all teams"""
        announcement = {
            'id': len(self.announcements) + 1,
            'title': message.data.get('title', 'Anuncio'),
            'content': message.data.get('content', message.text),
            'sent_at': datetime.now(),
            'sent_by': message.user_id,
            'reach': message.data.get('reach', 100)  # Estimated reach
        }

        self.announcements.append(announcement)

        return f"""✅ *Comunicado Enviado*

Título: {announcement['title']}
Alcance estimado: {announcement['reach']} personas
Fecha: {announcement['sent_at'].strftime('%Y-%m-%d %H:%M')}

Total comunicados: {len(self.announcements)}
"""

    async def get_statistics(self) -> str:
        """Get marketing statistics"""
        total_reach = sum(a.get('reach', 0) for a in self.announcements)

        return f"""📊 *Estadísticas de Marketing*

📢 *Comunicados:*
  Total enviados: {len(self.announcements)}
  Alcance total: {total_reach:,} personas
  Promedio por comunicado: {total_reach // len(self.announcements) if self.announcements else 0:,}

📱 *Redes Sociales:*
  Posts publicados: {len(self.social_posts)}

📰 *Medios:*
  Contactos de prensa: {len(self.media_contacts)}
"""

    async def generate_report(self) -> str:
        """Generate marketing report"""
        lines = [
            f"📑 *Reporte de Marketing - {self.tournament_id}*",
            f"Fecha: {datetime.now().strftime('%Y-%m-%d')}",
            "",
            "=" * 40,
            ""
        ]

        # Recent announcements
        if self.announcements:
            lines.extend([
                "📢 *Últimos Comunicados:*"
            ])
            for ann in self.announcements[-5:]:
                lines.append(f"• {ann['title']} - {ann['sent_at'].strftime('%d/%m')}")
            lines.append("")

        # Summary
        total_reach = sum(a.get('reach', 0) for a in self.announcements)
        lines.extend([
            "📊 *Resumen:*",
            f"  Comunicados totales: {len(self.announcements)}",
            f"  Alcance total: {total_reach:,} personas",
            ""
        ])

        return "\n".join(lines)

    async def get_metrics(self) -> Dict[str, Any]:
        """Get marketing metrics"""
        total_reach = sum(a.get('reach', 0) for a in self.announcements)

        return {
            'announcements_sent': len(self.announcements),
            'social_reach': total_reach,
            'social_posts': len(self.social_posts),
            'media_contacts': len(self.media_contacts),
            'avg_reach_per_announcement': total_reach // len(self.announcements) if self.announcements else 0
        }

    def get_marketing_help(self) -> str:
        """Get help message"""
        return """📣 *Módulo de Marketing*

*Comandos:*
• Enviar comunicado
• Ver estadísticas
• Generar reporte
"""

    async def cleanup(self):
        """Cleanup"""
        logger.info(f"🔌 Marketing module cleanup for {self.tournament_id}")
