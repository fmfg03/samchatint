"""
Expense Management Module for Copa Telmex.

This module provides functionality to:
- Process expense receipts via Telegram
- Generate CFDIs automatically using Tocino AI
- Track expense reports and reimbursements
- Link expenses to Copa Telmex teams/tournaments
"""

from .models import ExpenseReport, InvoiceReport
from .expense_handler import ExpenseHandler

__all__ = ['ExpenseReport', 'InvoiceReport', 'ExpenseHandler']
