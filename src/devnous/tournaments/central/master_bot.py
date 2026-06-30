"""
Master Tournament Bot - Central bot that monitors all tournaments.

This bot provides:
- Consolidated dashboard across all tournaments
- Cross-tournament analytics
- Alerts and notifications
- Global reporting
"""

import logging
from typing import Dict, Any, List
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


class MasterTournamentBot:
    """
    Central bot that monitors and manages all tournament bots.

    Features:
    - Consolidated dashboard
    - Cross-tournament analytics
    - Global financial overview
    - Alerts system
    - Comparative reports
    """

    def __init__(self):
        """Initialize master bot"""
        self.tournaments = {}  # Dict[tournament_id, TournamentBot]
        self.started_at = datetime.now()

        logger.info("🎯 Master Tournament Bot initialized")

    def register_tournament(self, tournament_id: str, bot):
        """
        Register a tournament bot.

        Args:
            tournament_id: Unique tournament identifier
            bot: TournamentBot instance
        """
        self.tournaments[tournament_id] = bot
        logger.info(f"✅ Tournament registered: {tournament_id}")

    def unregister_tournament(self, tournament_id: str):
        """Unregister a tournament"""
        if tournament_id in self.tournaments:
            del self.tournaments[tournament_id]
            logger.info(f"🗑️  Tournament unregistered: {tournament_id}")

    async def get_consolidated_dashboard(self) -> Dict[str, Any]:
        """
        Get consolidated dashboard with data from ALL tournaments.

        Returns:
            Dictionary with aggregated metrics
        """
        dashboard = {
            'timestamp': datetime.now().isoformat(),
            'total_tournaments': len(self.tournaments),
            'tournaments': {}
        }

        # Collect data from each tournament
        for tournament_id, bot in self.tournaments.items():
            try:
                status = await bot.get_status()
                dashboard['tournaments'][tournament_id] = status
            except Exception as e:
                logger.error(f"Error getting status for {tournament_id}: {e}")
                dashboard['tournaments'][tournament_id] = {'error': str(e)}

        # Calculate totals
        dashboard['totals'] = self._calculate_totals(dashboard['tournaments'])

        return dashboard

    def _calculate_totals(self, tournaments_data: Dict[str, Dict]) -> Dict[str, Any]:
        """Calculate totals across all tournaments"""
        totals = {
            'finance': {
                'total_income': 0.0,
                'total_expenses': 0.0,
                'total_profit': 0.0,
                'total_sponsorships': 0.0,
            },
            'operations': {
                'total_teams': 0,
                'total_players': 0,
                'total_matches': 0,
            },
            'marketing': {
                'total_announcements': 0,
                'total_reach': 0,
            }
        }

        for tournament_data in tournaments_data.values():
            if 'error' in tournament_data:
                continue

            # Finance totals
            if 'finance' in tournament_data:
                f = tournament_data['finance']
                totals['finance']['total_income'] += f.get('total_income', 0)
                totals['finance']['total_expenses'] += f.get('total_expenses', 0)
                totals['finance']['total_profit'] += f.get('profit', 0)
                totals['finance']['total_sponsorships'] += f.get('total_sponsorships', 0)

            # Operations totals
            if 'operations' in tournament_data:
                o = tournament_data['operations']
                totals['operations']['total_teams'] += o.get('teams_registered', 0)
                totals['operations']['total_players'] += o.get('players_registered', 0)
                totals['operations']['total_matches'] += o.get('matches_scheduled', 0)

            # Marketing totals
            if 'marketing' in tournament_data:
                m = tournament_data['marketing']
                totals['marketing']['total_announcements'] += m.get('announcements_sent', 0)
                totals['marketing']['total_reach'] += m.get('social_reach', 0)

        return totals

    async def format_consolidated_dashboard(self) -> str:
        """
        Format consolidated dashboard as readable text.

        Returns:
            Formatted dashboard message
        """
        dashboard = await self.get_consolidated_dashboard()
        totals = dashboard['totals']

        lines = [
            "🎯 *DASHBOARD CONSOLIDADO*",
            f"📅 {dashboard['timestamp'][:16]}",
            "",
            "=" * 50,
            ""
        ]

        # Overall summary
        lines.extend([
            f"🏆 *Torneos Activos:* {dashboard['total_tournaments']}",
            ""
        ])

        # Financial summary
        f = totals['finance']
        lines.extend([
            "💰 *FINANZAS GLOBALES:*",
            f"  Ingresos totales: ${f['total_income']:,.2f}",
            f"  Patrocinios: ${f['total_sponsorships']:,.2f}",
            f"  Gastos: ${f['total_expenses']:,.2f}",
            f"  *Utilidad total:* ${f['total_profit']:,.2f}",
            ""
        ])

        # Operations summary
        o = totals['operations']
        lines.extend([
            "🏃 *OPERACIONES GLOBALES:*",
            f"  Equipos registrados: {o['total_teams']}",
            f"  Jugadores: {o['total_players']}",
            f"  Partidos programados: {o['total_matches']}",
            ""
        ])

        # Marketing summary
        m = totals['marketing']
        lines.extend([
            "📣 *MARKETING GLOBAL:*",
            f"  Comunicados enviados: {m['total_announcements']}",
            f"  Alcance total: {m['total_reach']:,}",
            ""
        ])

        # Individual tournaments
        lines.extend([
            "📊 *DESGLOSE POR TORNEO:*",
            ""
        ])

        for tournament_id, data in dashboard['tournaments'].items():
            if 'error' in data:
                lines.append(f"❌ {tournament_id}: Error")
                continue

            name = data.get('name', tournament_id)
            finance = data.get('finance', {})
            operations = data.get('operations', {})

            profit = finance.get('profit', 0)
            teams = operations.get('teams_registered', 0)

            status_emoji = "✅" if profit >= 0 else "⚠️"

            lines.append(
                f"{status_emoji} *{name}*: "
                f"${finance.get('total_income', 0):,.0f} ingresos, "
                f"{teams} equipos"
            )

        return "\n".join(lines)

    async def get_top_performing_tournaments(self, metric: str = 'profit', limit: int = 5) -> List[Dict]:
        """
        Get top performing tournaments by metric.

        Args:
            metric: Metric to sort by ('profit', 'teams', 'revenue')
            limit: Number of tournaments to return

        Returns:
            List of tournament data sorted by metric
        """
        dashboard = await self.get_consolidated_dashboard()

        tournaments_list = []
        for tournament_id, data in dashboard['tournaments'].items():
            if 'error' in data:
                continue

            finance = data.get('finance', {})
            operations = data.get('operations', {})

            tournaments_list.append({
                'id': tournament_id,
                'name': data.get('name', tournament_id),
                'profit': finance.get('profit', 0),
                'revenue': finance.get('total_revenue', 0),
                'teams': operations.get('teams_registered', 0),
                'players': operations.get('players_registered', 0)
            })

        # Sort by metric
        tournaments_list.sort(key=lambda x: x.get(metric, 0), reverse=True)

        return tournaments_list[:limit]

    async def get_alerts(self) -> List[Dict[str, Any]]:
        """
        Get alerts across all tournaments.

        Returns:
            List of alert dictionaries
        """
        alerts = []
        dashboard = await self.get_consolidated_dashboard()

        for tournament_id, data in dashboard['tournaments'].items():
            if 'error' in data:
                alerts.append({
                    'severity': 'high',
                    'tournament': tournament_id,
                    'message': f"Error accessing {tournament_id}",
                    'type': 'system_error'
                })
                continue

            finance = data.get('finance', {})
            operations = data.get('operations', {})

            # Financial alerts
            profit = finance.get('profit', 0)
            if profit < 0:
                alerts.append({
                    'severity': 'medium',
                    'tournament': tournament_id,
                    'message': f"Utilidad negativa: ${profit:,.2f}",
                    'type': 'financial'
                })

            pending_payments = finance.get('pending_payments', 0)
            if pending_payments > 5:
                alerts.append({
                    'severity': 'low',
                    'tournament': tournament_id,
                    'message': f"{pending_payments} pagos pendientes",
                    'type': 'financial'
                })

            # Operational alerts
            teams = operations.get('teams_registered', 0)
            if teams < 4:
                alerts.append({
                    'severity': 'medium',
                    'tournament': tournament_id,
                    'message': f"Solo {teams} equipos registrados",
                    'type': 'operational'
                })

        return alerts

    async def format_alerts(self) -> str:
        """Format alerts as readable message"""
        alerts = await self.get_alerts()

        if not alerts:
            return "✅ *Sin Alertas* - Todo funcionando correctamente"

        lines = [
            "⚠️  *ALERTAS DEL SISTEMA*",
            f"Total: {len(alerts)}",
            ""
        ]

        # Group by severity
        by_severity = {'high': [], 'medium': [], 'low': []}
        for alert in alerts:
            by_severity[alert['severity']].append(alert)

        # High priority alerts
        if by_severity['high']:
            lines.append("🔴 *PRIORIDAD ALTA:*")
            for alert in by_severity['high']:
                lines.append(f"  • {alert['tournament']}: {alert['message']}")
            lines.append("")

        # Medium priority alerts
        if by_severity['medium']:
            lines.append("🟡 *PRIORIDAD MEDIA:*")
            for alert in by_severity['medium']:
                lines.append(f"  • {alert['tournament']}: {alert['message']}")
            lines.append("")

        # Low priority alerts
        if by_severity['low']:
            lines.append("🟢 *PRIORIDAD BAJA:*")
            for alert in by_severity['low']:
                lines.append(f"  • {alert['tournament']}: {alert['message']}")

        return "\n".join(lines)

    async def handle_query(self, query: str) -> str:
        """
        Handle queries to master bot.

        Args:
            query: User query

        Returns:
            Response text
        """
        query_lower = query.lower()

        if 'dashboard' in query_lower or 'resumen' in query_lower:
            return await self.format_consolidated_dashboard()

        elif 'alertas' in query_lower or 'alerts' in query_lower:
            return await self.format_alerts()

        elif 'top' in query_lower or 'mejor' in query_lower:
            top_tournaments = await self.get_top_performing_tournaments('profit', 3)
            lines = ["🏆 *TOP TORNEOS POR UTILIDAD:*\n"]
            for i, t in enumerate(top_tournaments, 1):
                lines.append(f"{i}. {t['name']}: ${t['profit']:,.2f}")
            return "\n".join(lines)

        elif 'ingresos' in query_lower or 'revenue' in query_lower:
            dashboard = await self.get_consolidated_dashboard()
            total = dashboard['totals']['finance']['total_income']
            return f"💰 Ingresos totales: ${total:,.2f}"

        else:
            return """🎯 *Master Bot - Comandos Disponibles:*

• dashboard - Ver resumen consolidado
• alertas - Ver alertas del sistema
• top - Ver torneos con mejor desempeño
• ingresos - Ver ingresos totales
"""

    async def shutdown(self):
        """Shutdown all tournaments"""
        logger.info("🔌 Shutting down master bot...")

        for tournament_id, bot in self.tournaments.items():
            try:
                await bot.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down {tournament_id}: {e}")

        logger.info("✅ Master bot shutdown complete")
