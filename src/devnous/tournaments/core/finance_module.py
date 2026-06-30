"""
Finance Module - Manages tournament finances.

Handles:
- Team payments and registration fees
- Sponsorships tracking
- Budget management
- Financial reports
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)


class PaymentStatus(Enum):
    """Payment status"""
    PENDING = "pending"
    RECEIVED = "received"
    OVERDUE = "overdue"
    REFUNDED = "refunded"


class SponsorshipStatus(Enum):
    """Sponsorship status"""
    NEGOTIATING = "negotiating"
    CONFIRMED = "confirmed"
    PAID = "paid"
    CANCELLED = "cancelled"


class FinanceModule:
    """
    Finance management module for tournaments.

    Features:
    - Track team payments
    - Manage sponsorships
    - Budget monitoring
    - Financial reports
    """

    def __init__(self, tournament_id: str, config: Dict[str, Any], db=None):
        """
        Initialize finance module.

        Args:
            tournament_id: Tournament identifier
            config: Configuration dictionary
            db: Database instance (optional)
        """
        self.tournament_id = tournament_id
        # Support both legacy config (top-level "finance") and new layout under "modules".
        self.config = config.get("finance", {}) or config.get("modules", {}).get("finance", {})
        self.db = db

        # In-memory cache (would be DB in production)
        self.payments = []
        self.sponsorships = []
        self.expenses = []

        logger.info(f"💰 Finance module initialized for {tournament_id}")

    async def handle(self, message) -> str:
        """
        Handle finance-related messages.

        Args:
            message: Message object with text and data

        Returns:
            Response text
        """
        text = message.text.lower()

        if 'registrar pago' in text or 'register payment' in text:
            return await self.register_payment(message)

        elif 'ver pagos' in text or 'list payments' in text:
            return await self.list_payments()

        elif 'patrocinio' in text or 'sponsorship' in text:
            if 'registrar' in text or 'add' in text:
                return await self.add_sponsorship(message)
            else:
                return await self.list_sponsorships()

        elif 'presupuesto' in text or 'budget' in text:
            return await self.get_budget_status()

        elif 'reporte financiero' in text or 'financial report' in text:
            return await self.generate_financial_report()

        else:
            return self.get_finance_help()

    async def register_payment(self, message) -> str:
        """Register a team payment"""
        # In production, parse from message.data or use conversation flow
        payment = {
            'id': len(self.payments) + 1,
            'team_id': message.data.get('team_id', 'unknown'),
            'team_name': message.data.get('team_name', 'Team'),
            'amount': Decimal(message.data.get('amount', 0)),
            'concept': message.data.get('concept', 'Registration'),
            'status': PaymentStatus.RECEIVED.value,
            'date': datetime.now(),
            'received_by': message.user_id
        }

        self.payments.append(payment)

        if self.db:
            await self.db.save_payment(payment)

        return f"""✅ *Pago Registrado*

Equipo: {payment['team_name']}
Monto: ${payment['amount']:,.2f}
Concepto: {payment['concept']}
Fecha: {payment['date'].strftime('%Y-%m-%d %H:%M')}

Total recibido: ${self.get_total_income():,.2f}
"""

    async def add_sponsorship(self, message) -> str:
        """Add or update sponsorship"""
        sponsorship = {
            'id': len(self.sponsorships) + 1,
            'sponsor_name': message.data.get('sponsor_name', 'Sponsor'),
            'amount': Decimal(message.data.get('amount', 0)),
            'status': message.data.get('status', SponsorshipStatus.NEGOTIATING.value),
            'contract_start': message.data.get('start_date'),
            'contract_end': message.data.get('end_date'),
            'benefits': message.data.get('benefits', []),
            'created_at': datetime.now()
        }

        self.sponsorships.append(sponsorship)

        if self.db:
            await self.db.save_sponsorship(sponsorship)

        return f"""✅ *Patrocinio Agregado*

Patrocinador: {sponsorship['sponsor_name']}
Monto: ${sponsorship['amount']:,.2f}
Estado: {sponsorship['status']}

Total patrocinios: ${self.get_total_sponsorships():,.2f}
"""

    async def list_payments(self) -> str:
        """List recent payments"""
        if not self.payments:
            return "📭 No hay pagos registrados"

        lines = ["💰 *Pagos Registrados*\n"]

        for payment in self.payments[-10:]:  # Last 10
            lines.append(
                f"• {payment['team_name']}: ${payment['amount']:,.2f} "
                f"({payment['status']}) - {payment['date'].strftime('%d/%m')}"
            )

        lines.append(f"\n*Total:* ${self.get_total_income():,.2f}")

        return "\n".join(lines)

    async def list_sponsorships(self) -> str:
        """List sponsorships"""
        if not self.sponsorships:
            return "📭 No hay patrocinios registrados"

        lines = ["🤝 *Patrocinios*\n"]

        total_by_status = {}
        for s in self.sponsorships:
            status = s['status']
            total_by_status[status] = total_by_status.get(status, 0) + s['amount']

            lines.append(
                f"• {s['sponsor_name']}: ${s['amount']:,.2f} ({s['status']})"
            )

        lines.append("\n*Resumen:*")
        for status, amount in total_by_status.items():
            lines.append(f"  {status}: ${amount:,.2f}")

        return "\n".join(lines)

    async def get_budget_status(self) -> str:
        """Get current budget status"""
        income = self.get_total_income()
        sponsorships = self.get_total_sponsorships()
        expenses = self.get_total_expenses()
        total_income = income + sponsorships
        profit = total_income - expenses

        return f"""📊 *Estado del Presupuesto*

💰 *Ingresos:*
  Pagos de equipos: ${income:,.2f}
  Patrocinios: ${sponsorships:,.2f}
  *Total ingresos:* ${total_income:,.2f}

💸 *Gastos:*
  Total gastado: ${expenses:,.2f}

💵 *Utilidad:*
  {profit >= 0 and '✅' or '⚠️'} ${profit:,.2f}

📈 *Margen:* {(profit / total_income * 100) if total_income > 0 else 0:.1f}%
"""

    async def generate_financial_report(self) -> str:
        """Generate comprehensive financial report"""
        lines = [
            f"📑 *Reporte Financiero - {self.tournament_id}*",
            f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "=" * 40,
            ""
        ]

        # Summary
        income = self.get_total_income()
        sponsorships = self.get_total_sponsorships()
        expenses = self.get_total_expenses()

        lines.extend([
            "📊 *Resumen:*",
            f"  Pagos recibidos: ${income:,.2f}",
            f"  Patrocinios: ${sponsorships:,.2f}",
            f"  Gastos: ${expenses:,.2f}",
            f"  *Utilidad neta:* ${(income + sponsorships - expenses):,.2f}",
            ""
        ])

        # Payment breakdown
        if self.payments:
            lines.extend([
                "💰 *Pagos por estado:*"
            ])
            by_status = {}
            for p in self.payments:
                status = p['status']
                by_status[status] = by_status.get(status, 0) + p['amount']

            for status, amount in by_status.items():
                lines.append(f"  {status}: ${amount:,.2f}")
            lines.append("")

        # Sponsorships
        if self.sponsorships:
            lines.extend([
                "🤝 *Patrocinios:*",
                f"  Total patrocinadores: {len(self.sponsorships)}",
                f"  Monto total: ${self.get_total_sponsorships():,.2f}",
                ""
            ])

        return "\n".join(lines)

    def get_total_income(self) -> Decimal:
        """Calculate total income from payments"""
        return sum(
            p['amount'] for p in self.payments
            if p['status'] != PaymentStatus.REFUNDED.value
        )

    def get_total_sponsorships(self) -> Decimal:
        """Calculate total from sponsorships"""
        return sum(
            s['amount'] for s in self.sponsorships
            if s['status'] in [SponsorshipStatus.CONFIRMED.value, SponsorshipStatus.PAID.value]
        )

    def get_total_expenses(self) -> Decimal:
        """Calculate total expenses"""
        return sum(e['amount'] for e in self.expenses)

    async def get_metrics(self) -> Dict[str, Any]:
        """
        Get finance metrics for dashboard.

        Returns:
            Dictionary with key financial metrics
        """
        income = self.get_total_income()
        sponsorships = self.get_total_sponsorships()
        expenses = self.get_total_expenses()
        total_income = income + sponsorships

        pending_payments = sum(
            1 for p in self.payments
            if p['status'] == PaymentStatus.PENDING.value
        )

        return {
            'total_income': float(income),
            'total_sponsorships': float(sponsorships),
            'total_expenses': float(expenses),
            'total_revenue': float(total_income),
            'profit': float(total_income - expenses),
            'profit_margin': float((total_income - expenses) / total_income * 100) if total_income > 0 else 0,
            'pending_payments': pending_payments,
            'total_payments': len(self.payments),
            'total_sponsors': len(self.sponsorships),
            'confirmed_sponsors': sum(
                1 for s in self.sponsorships
                if s['status'] == SponsorshipStatus.CONFIRMED.value
            )
        }

    def get_finance_help(self) -> str:
        """Get finance module help"""
        return """💰 *Módulo de Finanzas*

*Comandos disponibles:*

• Registrar pago de equipo
• Ver pagos
• Agregar patrocinio
• Ver patrocinios
• Ver presupuesto
• Generar reporte financiero

*Ejemplo:*
"Registrar pago del equipo Alaska por $5000"
"""

    async def cleanup(self):
        """Cleanup resources"""
        logger.info(f"🔌 Finance module cleanup for {self.tournament_id}")
        # Save any pending data to DB
        pass
