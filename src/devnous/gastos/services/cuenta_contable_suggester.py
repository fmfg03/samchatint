"""
Cuenta Contable Auto-Suggest Service

Implements a multi-tier decision system for suggesting accounting accounts:
0. Torneo/Proyecto rule (primary): base from tournament, sub_cuenta from concepto mapping
1. AMEX batch rule: base 2120-002, suffix 062-066 from empleado (learned)
2. Deterministic rules (proveedor, keyword match, metodo_pago boost)
3. Learned mappings from historical data (concepto)
4. LLM fallback (only when confidence is low)

This is assistive intelligence - suggestions are advisory only and never auto-assign.
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class CuentaContableSuggestion:
    """
    A suggested cuenta contable with confidence and reasoning.
    """
    cuenta_contable_id: UUID
    cuenta_codigo: str
    cuenta_nombre: str
    confidence_score: float  # 0.0 to 1.0
    reason: str
    tier: str  # 'torneo', 'amex_batch', 'rules', 'learned', 'llm'

    def to_dict(self) -> Dict[str, Any]:
        return {
            'cuenta_contable_id': str(self.cuenta_contable_id),
            'cuenta_codigo': self.cuenta_codigo,
            'cuenta_nombre': self.cuenta_nombre,
            'confidence_score': self.confidence_score,
            'reason': self.reason,
            'tier': self.tier,
        }

    @property
    def confidence_label(self) -> str:
        """Human-readable confidence label."""
        if self.confidence_score >= 0.8:
            return "Alta"
        elif self.confidence_score >= 0.5:
            return "Media"
        else:
            return "Baja"

    @property
    def confidence_color(self) -> str:
        """Color for UI display."""
        if self.confidence_score >= 0.8:
            return "#4CAF50"  # Green
        elif self.confidence_score >= 0.5:
            return "#FF9800"  # Orange
        else:
            return "#f44336"  # Red


# =============================================================================
# KEYWORD RULES CONFIGURATION
# =============================================================================

# Keyword patterns for concepto matching (case-insensitive)
# Maps keyword patterns to cuenta contable codes
CONCEPTO_KEYWORD_RULES: List[Dict[str, Any]] = [
    # Viáticos - Scouting (viajes de prospección/visoreo)
    {
        'keywords': ['scouting', 'visoreo', 'visoria', 'prospeccion', 'prospección'],
        'cuenta_codigo_pattern': '5300',
        'cuenta_nombre_patterns': ['viatic', 'viaje', 'operaci'],
        'confidence': 0.88,
        'reason_template': "Concepto '{concepto}' corresponde a viaje de scouting"
    },
    # Viáticos - Supervisión (viajes de supervisión en sede)
    {
        'keywords': ['supervision', 'supervisión', 'supervisar', 'seguimiento en sede'],
        'cuenta_codigo_pattern': '5300',
        'cuenta_nombre_patterns': ['viatic', 'viaje', 'operaci'],
        'confidence': 0.88,
        'reason_template': "Concepto '{concepto}' corresponde a viaje de supervisión"
    },
    # Viáticos - Transporte a sedes
    {
        'keywords': ['transporte a sede', 'transporte a sedes', 'traslado a sede', 'traslado a sedes', 'movilidad a sede', 'transpoorte'],
        'cuenta_codigo_pattern': '5300',
        'cuenta_nombre_patterns': ['transporte', 'viatic', 'viaje'],
        'confidence': 0.90,
        'reason_template': "Concepto '{concepto}' corresponde a transporte a sede"
    },
    # Viáticos - Transporte
    {
        'keywords': ['transporte', 'taxi', 'uber', 'didi', 'camión', 'autobus', 'autobús', 'metro', 'pasaje', 'peaje', 'caseta', 'estacionamiento', 'gasolina', 'combustible'],
        'cuenta_codigo_pattern': '5300',  # Matches accounts starting with 5300
        'cuenta_nombre_pattern': 'transporte',
        'confidence': 0.85,
        'reason_template': "Concepto '{concepto}' contiene palabras clave de transporte"
    },
    # Viáticos - Hospedaje
    {
        'keywords': ['hotel', 'hospedaje', 'hostal', 'airbnb', 'alojamiento', 'habitación', 'habitacion'],
        'cuenta_codigo_pattern': '5300',
        'cuenta_nombre_pattern': 'hospedaje',
        'confidence': 0.85,
        'reason_template': "Concepto '{concepto}' contiene palabras clave de hospedaje"
    },
    # Viáticos - Alimentación
    {
        'keywords': ['alimento', 'alimentación', 'alimentacion', 'comida', 'desayuno', 'almuerzo', 'cena', 'restaurant', 'restaurante', 'cafetería', 'cafeteria', 'lunch'],
        'cuenta_codigo_pattern': '5300',
        'cuenta_nombre_pattern': 'aliment',
        'confidence': 0.85,
        'reason_template': "Concepto '{concepto}' contiene palabras clave de alimentación"
    },
    # Suministros de oficina
    {
        'keywords': ['papelería', 'papeleria', 'oficina', 'tinta', 'papel', 'folder', 'engrapadora', 'pluma', 'lápiz', 'lapiz', 'cuaderno', 'libreta'],
        'cuenta_codigo_pattern': '5200',
        'cuenta_nombre_pattern': 'oficina',
        'confidence': 0.75,
        'reason_template': "Concepto '{concepto}' contiene palabras clave de suministros de oficina"
    },
    # Comunicaciones
    {
        'keywords': ['teléfono', 'telefono', 'celular', 'internet', 'comunicación', 'comunicacion', 'telcel', 'at&t', 'movistar', 'datos'],
        'cuenta_codigo_pattern': '5100',
        'cuenta_nombre_pattern': 'comunic',
        'confidence': 0.75,
        'reason_template': "Concepto '{concepto}' contiene palabras clave de comunicaciones"
    },
    # Servicios profesionales
    {
        'keywords': ['honorarios', 'consultoría', 'consultoria', 'asesoría', 'asesoria', 'legal', 'abogado', 'contador', 'contable'],
        'cuenta_codigo_pattern': '5400',
        'cuenta_nombre_pattern': 'profesional',
        'confidence': 0.70,
        'reason_template': "Concepto '{concepto}' contiene palabras clave de servicios profesionales"
    },
    # Mantenimiento
    {
        'keywords': ['mantenimiento', 'reparación', 'reparacion', 'limpieza', 'servicio técnico', 'servicio tecnico'],
        'cuenta_codigo_pattern': '5500',
        'cuenta_nombre_pattern': 'manten',
        'confidence': 0.70,
        'reason_template': "Concepto '{concepto}' contiene palabras clave de mantenimiento"
    },
]

# AMEX batch: base cuenta 2120-002, suffixes 062-066 map to empleado (cardholder)
AMEX_BASE_CUENTA = "2120-002"
AMEX_SUFFIXES = ("062", "063", "064", "065", "066")

# Metodo de pago rules - some payment methods indicate specific account types
METODO_PAGO_RULES: Dict[str, Dict[str, Any]] = {
    'tarjeta_empresa': {
        'cuenta_nombre_pattern': 'tarjeta',
        'confidence_boost': 0.1,
        'reason': "Pago con tarjeta de empresa"
    },
    'efectivo': {
        'cuenta_nombre_pattern': 'caja',
        'confidence_boost': 0.05,
        'reason': "Pago en efectivo"
    },
}


class CuentaContableSuggester:
    """
    Multi-tier cuenta contable suggestion system.

    Tier -1: Partida presupuestal catalog mapping (budget_concepts.cuenta_contable_id)
    Tier 0: Torneo/Proyecto (primary) - base from tournament, sub_cuenta from concepto mapping
    Tier 0.5: AMEX batch - base 2120-002, suffix from empleado (learned)
    Tier 1: Deterministic rules (keyword, metodo_pago boost)
    Tier 2: Learned mappings from historical data (concepto)
    Tier 3: LLM fallback (optional)
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self._cuentas_cache: Optional[List[Any]] = None

    def _is_valid_uuid(self, value: str) -> bool:
        """Check if string is a valid UUID."""
        if not value or not isinstance(value, str):
            return False
        try:
            UUID(value)
            return True
        except (ValueError, TypeError):
            return False

    async def get_suggestion(
        self,
        expense_id: UUID,
        concepto: str,
        proveedor_cliente_id: Optional[UUID] = None,
        metodo_pago: Optional[str] = None,
        proyecto: Optional[str] = None,
        gasto_cantidad: Optional[float] = None,
        tournament_id: Optional[UUID] = None,
        origen: Optional[str] = None,
        fase_torneo: Optional[str] = None,
        empleado_id: Optional[UUID] = None,
        budget_concept_id: Optional[UUID] = None,
        use_llm: bool = True,
        llm_confidence_threshold: float = 0.7,
    ) -> Optional[CuentaContableSuggestion]:
        """
        Get a cuenta contable suggestion for an expense.

        Tier order: Partida catalog → Torneo/Proyecto → AMEX batch → rules → learned → LLM.
        """
        if self._cuentas_cache is None:
            await self._load_cuentas_contables()

        suggestion: Optional[CuentaContableSuggestion] = None

        partida_suggestion = await self._apply_partida_catalog_rule(
            budget_concept_id=budget_concept_id,
        )
        if partida_suggestion:
            suggestion = partida_suggestion
        if suggestion and suggestion.confidence_score >= llm_confidence_threshold:
            logger.debug(
                f"Tier partida suggestion for expense {expense_id}: {suggestion.cuenta_codigo}"
            )
            return suggestion

        # Tier 0: Torneo/Proyecto (primary)
        torneo_suggestion = await self._apply_torneo_proyecto_rule(
            proyecto=proyecto,
            tournament_id=tournament_id,
            concepto=concepto,
        )
        if torneo_suggestion and (
            suggestion is None or torneo_suggestion.confidence_score > suggestion.confidence_score
        ):
            suggestion = torneo_suggestion
        if suggestion and suggestion.confidence_score >= llm_confidence_threshold:
            logger.debug(f"Tier 0 (torneo) suggestion for expense {expense_id}: {suggestion.cuenta_codigo}")
            return suggestion

        # Tier 0.5: AMEX batch
        amex_suggestion = await self._apply_amex_batch_rule(
            origen=origen,
            fase_torneo=fase_torneo,
            metodo_pago=metodo_pago,
            empleado_id=empleado_id,
        )
        if amex_suggestion and (
            suggestion is None or amex_suggestion.confidence_score > suggestion.confidence_score
        ):
            suggestion = amex_suggestion
        if suggestion and suggestion.confidence_score >= llm_confidence_threshold:
            logger.debug(f"Tier 0.5 (amex_batch) suggestion for expense {expense_id}: {suggestion.cuenta_codigo}")
            return suggestion

        # Tier 1: Deterministic rules (keyword + metodo_pago boost)
        rules_suggestion = await self._apply_rules(
            concepto=concepto,
            proveedor_cliente_id=proveedor_cliente_id,
            metodo_pago=metodo_pago,
            proyecto=proyecto,
        )
        if rules_suggestion and (
            suggestion is None or rules_suggestion.confidence_score > suggestion.confidence_score
        ):
            suggestion = rules_suggestion
        if suggestion and suggestion.confidence_score >= llm_confidence_threshold:
            logger.debug(f"Tier 1 (rules) suggestion for expense {expense_id}: {suggestion.cuenta_codigo}")
            return suggestion

        # Tier 2: Learned mappings (concepto)
        learned_suggestion = await self._apply_learned_mappings(
            concepto=concepto,
            proveedor_cliente_id=proveedor_cliente_id,
        )
        if learned_suggestion and (
            suggestion is None or learned_suggestion.confidence_score > suggestion.confidence_score
        ):
            suggestion = learned_suggestion
        if suggestion and suggestion.confidence_score >= llm_confidence_threshold:
            logger.debug(f"Tier 2 (learned) suggestion for expense {expense_id}: {suggestion.cuenta_codigo}")
            return suggestion

        # Tier 3: LLM fallback
        if use_llm and (suggestion is None or suggestion.confidence_score < llm_confidence_threshold):
            llm_suggestion = await self._apply_llm_fallback(
                concepto=concepto,
                proveedor_cliente_id=proveedor_cliente_id,
                metodo_pago=metodo_pago,
            )
            if llm_suggestion and (
                suggestion is None or llm_suggestion.confidence_score > suggestion.confidence_score
            ):
                suggestion = llm_suggestion
                logger.debug(f"Tier 3 (LLM) suggestion for expense {expense_id}: {suggestion.cuenta_codigo}")

        return suggestion

    async def get_suggestions_batch(
        self,
        expenses: List[Dict[str, Any]],
        use_llm: bool = True,
        llm_confidence_threshold: float = 0.7,
    ) -> Dict[UUID, Optional[CuentaContableSuggestion]]:
        """
        Get suggestions for multiple expenses efficiently.

        Args:
            expenses: List of expense dictionaries with keys:
                - id: UUID
                - concepto: str
                - proveedor_cliente_id: Optional[UUID]
                - metodo_pago: Optional[str]
                - proyecto: Optional[str]
                - gasto_cantidad: Optional[float]
                - tournament_id: Optional[UUID]
                - origen: Optional[str] (e.g. 'amex_batch')
                - fase_torneo: Optional[str] (e.g. 'AMEX_BATCH')
                - empleado_id: Optional[UUID]
                - budget_concept_id: Optional[UUID]
            use_llm: Whether to use LLM fallback
            llm_confidence_threshold: Minimum confidence to skip LLM

        Returns:
            Dictionary mapping expense_id to suggestion
        """
        # Pre-load cuentas contables
        if self._cuentas_cache is None:
            await self._load_cuentas_contables()

        results = {}
        for exp in expenses:
            suggestion = await self.get_suggestion(
                expense_id=exp['id'],
                concepto=exp.get('concepto', ''),
                proveedor_cliente_id=exp.get('proveedor_cliente_id'),
                metodo_pago=exp.get('metodo_pago'),
                proyecto=exp.get('proyecto'),
                gasto_cantidad=exp.get('gasto_cantidad'),
                tournament_id=exp.get('tournament_id'),
                origen=exp.get('origen'),
                fase_torneo=exp.get('fase_torneo'),
                empleado_id=exp.get('empleado_id'),
                budget_concept_id=exp.get('budget_concept_id'),
                use_llm=use_llm,
                llm_confidence_threshold=llm_confidence_threshold,
            )
            results[exp['id']] = suggestion

        return results

    async def _load_cuentas_contables(self) -> None:
        """Load and cache active cuentas contables."""
        from devnous.gastos.models import CuentaContable

        result = await self.session.execute(
            select(CuentaContable)
            .where(CuentaContable.activo == True)
            .order_by(CuentaContable.codigo)
        )
        self._cuentas_cache = result.scalars().all()
        logger.debug(f"Loaded {len(self._cuentas_cache)} cuentas contables")

    async def _apply_partida_catalog_rule(
        self,
        budget_concept_id: Optional[UUID],
    ) -> Optional[CuentaContableSuggestion]:
        """Tier partida: suggest cuenta from budget concept catalog mapping."""
        if not budget_concept_id:
            return None

        from devnous.gastos.models import BudgetConcept
        from sqlalchemy.orm import selectinload

        result = await self.session.execute(
            select(BudgetConcept)
            .options(selectinload(BudgetConcept.cuenta_contable))
            .where(BudgetConcept.id == budget_concept_id)
        )
        concept = result.scalar_one_or_none()
        if concept is None or concept.cuenta_contable is None:
            return None

        cuenta = concept.cuenta_contable
        return CuentaContableSuggestion(
            cuenta_contable_id=cuenta.id,
            cuenta_codigo=cuenta.codigo,
            cuenta_nombre=cuenta.nombre or "",
            confidence_score=0.95,
            reason=(
                f"Catálogo presupuestal: partida '{concept.concept_name}' "
                f"→ {cuenta.codigo}"
            ),
            tier="partida",
        )

    def _find_cuenta_by_codigo(self, codigo: str) -> Optional[Any]:
        """Find a cuenta contable by exact codigo."""
        if not codigo or not self._cuentas_cache:
            return None
        for cuenta in self._cuentas_cache:
            if cuenta.codigo == codigo:
                return cuenta
        return None

    async def _apply_torneo_proyecto_rule(
        self,
        proyecto: Optional[str],
        tournament_id: Optional[UUID],
        concepto: str,
    ) -> Optional[CuentaContableSuggestion]:
        """
        Tier 0: Suggest cuenta from torneo/proyecto.
        Base from Tournament.cuenta_contable_relacionada (or mapping parent_account);
        sub_cuenta from TournamentConceptoMapping by concepto.
        """
        from devnous.gastos.models import Tournament, TournamentConceptoMapping, CuentaContable

        if not self._cuentas_cache:
            return None

        # Resolve tournament: by UUID or by proyecto name
        tournament = None
        if tournament_id:
            result = await self.session.execute(select(Tournament).where(Tournament.id == tournament_id))
            tournament = result.scalar_one_or_none()
        if not tournament and proyecto:
            if self._is_valid_uuid(proyecto):
                result = await self.session.execute(
                    select(Tournament).where(Tournament.id == UUID(proyecto))
                )
                tournament = result.scalar_one_or_none()
            else:
                result = await self.session.execute(
                    select(Tournament)
                    .where(func.lower(Tournament.name) == proyecto.lower().strip())
                    .where(Tournament.active == True)
                )
                tournament = result.scalar_one_or_none()
        if not tournament:
            return None

        # Base: Tournament.cuenta_contable_relacionada (e.g. 5300-010) or from first mapping
        base = tournament.cuenta_contable_relacionada
        if not base:
            mapping_result = await self.session.execute(
                select(TournamentConceptoMapping)
                .where(
                    TournamentConceptoMapping.tournament_id == tournament.id,
                    TournamentConceptoMapping.active == True,
                )
                .limit(1)
            )
            first_mapping = mapping_result.scalar_one_or_none()
            if first_mapping and first_mapping.parent_account:
                base = first_mapping.parent_account
        if not base:
            return None

        # Sub_cuenta: match concepto to TournamentConceptoMapping (exact or contains)
        concepto_normalized = self._normalize_concepto(concepto) if concepto else ""
        sub_cuenta = None
        mapping_result = await self.session.execute(
            select(TournamentConceptoMapping)
            .where(
                TournamentConceptoMapping.tournament_id == tournament.id,
                TournamentConceptoMapping.active == True,
            )
        )
        mappings = mapping_result.scalars().all()
        for m in mappings:
            if m.concepto and concepto_normalized:
                if m.concepto.lower().strip() == concepto_normalized:
                    sub_cuenta = m.sub_cuenta
                    break
                if concepto_normalized in m.concepto.lower() or m.concepto.lower() in concepto_normalized:
                    sub_cuenta = m.sub_cuenta
                    break
        if not sub_cuenta and mappings:
            sub_cuenta = mappings[0].sub_cuenta

        if not sub_cuenta:
            full_codigo = base
        else:
            full_codigo = f"{base}-{sub_cuenta}"

        cuenta = self._find_cuenta_by_codigo(full_codigo)
        if not cuenta:
            for c in self._cuentas_cache:
                if c.codigo and c.codigo.startswith(base):
                    cuenta = c
                    full_codigo = c.codigo
                    break
        if not cuenta:
            return None

        reason = f"Torneo/Proyecto: {tournament.name}"
        if sub_cuenta:
            reason += f"; concepto → sub_cuenta {sub_cuenta}"
        return CuentaContableSuggestion(
            cuenta_contable_id=cuenta.id,
            cuenta_codigo=cuenta.codigo,
            cuenta_nombre=cuenta.nombre,
            confidence_score=0.90,
            reason=reason,
            tier="torneo",
        )

    async def _apply_amex_batch_rule(
        self,
        origen: Optional[str],
        fase_torneo: Optional[str],
        metodo_pago: Optional[str],
        empleado_id: Optional[UUID],
    ) -> Optional[CuentaContableSuggestion]:
        """
        Tier 0.5: AMEX batch expenses → base 2120-002, suffix 062-066 from empleado.
        Learned: past AMEX expenses with cuenta_contable_id 2120-002-XXX → empleado_id → suffix.
        """
        from devnous.gastos.models import ExpenseReport, CuentaContable

        if not self._cuentas_cache:
            return None

        # Detect AMEX batch: origen or fase_torneo + metodo_pago
        is_amex = (
            origen == "amex_batch"
            or (fase_torneo == "AMEX_BATCH" and metodo_pago and "AMEX" in metodo_pago.upper())
        )
        if not is_amex:
            return None

        suffix = None
        if empleado_id:
            # Learned: past AMEX expenses (fase_torneo; origen used for detection only) with cuenta 2120-002-06X
            result = await self.session.execute(
                select(ExpenseReport.cuenta_contable_id, func.count(ExpenseReport.id).label("cnt"))
                .where(ExpenseReport.empleado_id == empleado_id)
                .where(ExpenseReport.estado_gasto == "activo")
                .where(ExpenseReport.cuenta_contable_id.isnot(None))
                .where(ExpenseReport.fase_torneo == "AMEX_BATCH")
                .group_by(ExpenseReport.cuenta_contable_id)
                .order_by(func.count(ExpenseReport.id).desc())
            )
            rows = result.all()
            for cuenta_id, _ in rows:
                cc_result = await self.session.execute(
                    select(CuentaContable).where(CuentaContable.id == cuenta_id)
                )
                cc = cc_result.scalar_one_or_none()
                if cc and cc.codigo and cc.codigo.startswith(AMEX_BASE_CUENTA):
                    parts = cc.codigo.split("-")
                    if len(parts) >= 3 and parts[-1] in AMEX_SUFFIXES:
                        suffix = parts[-1]
                        break
            if not suffix and rows:
                cc_result = await self.session.execute(
                    select(CuentaContable).where(CuentaContable.id == rows[0][0])
                )
                cc = cc_result.scalar_one_or_none()
                if cc and cc.codigo and cc.codigo.startswith(AMEX_BASE_CUENTA):
                    parts = cc.codigo.split("-")
                    if len(parts) >= 3:
                        suffix = parts[-1]

        if not suffix:
            suffix = AMEX_SUFFIXES[0]

        full_codigo = f"{AMEX_BASE_CUENTA}-{suffix}"
        cuenta = self._find_cuenta_by_codigo(full_codigo)
        if not cuenta:
            return None

        reason = "AMEX batch: cuenta base 2120-002"
        if empleado_id and suffix:
            reason += f"; empleado → subcuenta {suffix}"
        return CuentaContableSuggestion(
            cuenta_contable_id=cuenta.id,
            cuenta_codigo=cuenta.codigo,
            cuenta_nombre=cuenta.nombre,
            confidence_score=0.88,
            reason=reason,
            tier="amex_batch",
        )

    async def _apply_rules(
        self,
        concepto: str,
        proveedor_cliente_id: Optional[UUID],
        metodo_pago: Optional[str],
        proyecto: Optional[str],
    ) -> Optional[CuentaContableSuggestion]:
        """
        Apply deterministic rules to suggest a cuenta contable.

        Rules are applied in priority order:
        1. Proveedor/Cliente mapping (if has cuenta_contable_id)
        2. Keyword matching on concepto
        3. Metodo de pago rules (confidence boost only)
        """
        # Rule 1: Proveedor/Cliente has a pre-assigned cuenta contable
        if proveedor_cliente_id:
            proveedor_suggestion = await self._check_proveedor_cuenta(proveedor_cliente_id)
            if proveedor_suggestion:
                return proveedor_suggestion

        # Rule 2: Keyword matching on concepto
        if concepto:
            keyword_suggestion = self._match_concepto_keywords(concepto)
            if keyword_suggestion:
                # Rule 3: Apply metodo_pago confidence boost
                if metodo_pago:
                    keyword_suggestion = self._apply_metodo_pago_boost(
                        keyword_suggestion, metodo_pago
                    )
                return keyword_suggestion

        return None

    async def _check_proveedor_cuenta(
        self,
        proveedor_cliente_id: UUID,
    ) -> Optional[CuentaContableSuggestion]:
        """
        Check if proveedor/cliente has a pre-assigned cuenta contable.

        NOTE: Per LEAP_SPEC_CUENTA_BANCARIA_CLEANUP, proveedores/clientes NO LONGER
        have cuenta_contable assignments. This is intentional - cuenta_bancaria (bank account)
        is NOT the same as cuenta_contable (accounting classification).

        Cuenta contable assignment must be explicit on expenses/informes, never implicit
        from proveedor/cliente.
        """
        # Proveedores/clientes no longer have cuenta_contable mapping
        # This method now always returns None (no implicit assignment)
        return None

    def _match_concepto_keywords(self, concepto: str) -> Optional[CuentaContableSuggestion]:
        """Match concepto against keyword rules."""
        concepto_lower = concepto.lower().strip()

        if not concepto_lower or not self._cuentas_cache:
            return None

        for rule in CONCEPTO_KEYWORD_RULES:
            # Check if any keyword matches
            matched_keyword = None
            for keyword in rule['keywords']:
                if keyword in concepto_lower:
                    matched_keyword = keyword
                    break

            if matched_keyword:
                # Find matching cuenta contable (supports one or many nombre patterns)
                cuenta = None
                nombre_patterns: List[Optional[str]] = []
                if rule.get('cuenta_nombre_pattern'):
                    nombre_patterns.append(rule['cuenta_nombre_pattern'])
                if rule.get('cuenta_nombre_patterns'):
                    nombre_patterns.extend(rule['cuenta_nombre_patterns'])
                if not nombre_patterns:
                    nombre_patterns = [None]

                for nombre_pattern in nombre_patterns:
                    cuenta = self._find_cuenta_by_pattern(
                        codigo_pattern=rule.get('cuenta_codigo_pattern'),
                        nombre_pattern=nombre_pattern,
                    )
                    if cuenta:
                        break
                if not cuenta and rule.get('cuenta_codigo_pattern'):
                    # Last fallback by account code family if no name pattern matched.
                    cuenta = self._find_cuenta_by_pattern(
                        codigo_pattern=rule.get('cuenta_codigo_pattern'),
                        nombre_pattern=None,
                    )

                if cuenta:
                    return CuentaContableSuggestion(
                        cuenta_contable_id=cuenta.id,
                        cuenta_codigo=cuenta.codigo,
                        cuenta_nombre=cuenta.nombre,
                        confidence_score=rule['confidence'],
                        reason=rule['reason_template'].format(concepto=concepto),
                        tier='rules',
                    )

        return None

    def _find_cuenta_by_pattern(
        self,
        codigo_pattern: Optional[str] = None,
        nombre_pattern: Optional[str] = None,
    ) -> Optional[Any]:
        """Find a cuenta contable matching the given patterns."""
        if not self._cuentas_cache:
            return None

        candidates = []

        for cuenta in self._cuentas_cache:
            score = 0

            # Check codigo pattern
            if codigo_pattern and cuenta.codigo:
                if cuenta.codigo.startswith(codigo_pattern):
                    score += 2
                elif codigo_pattern in cuenta.codigo:
                    score += 1

            # Check nombre pattern
            if nombre_pattern and cuenta.nombre:
                nombre_lower = cuenta.nombre.lower()
                if nombre_pattern.lower() in nombre_lower:
                    score += 3  # Nombre match is more specific

            if score > 0:
                candidates.append((cuenta, score))

        if candidates:
            # Return the best match
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]

        return None

    def _apply_metodo_pago_boost(
        self,
        suggestion: CuentaContableSuggestion,
        metodo_pago: str,
    ) -> CuentaContableSuggestion:
        """Apply confidence boost based on payment method."""
        metodo_lower = metodo_pago.lower().strip() if metodo_pago else ''

        for key, rule in METODO_PAGO_RULES.items():
            if key in metodo_lower or metodo_lower in key:
                # Only boost if we don't exceed 1.0
                new_confidence = min(1.0, suggestion.confidence_score + rule['confidence_boost'])
                return CuentaContableSuggestion(
                    cuenta_contable_id=suggestion.cuenta_contable_id,
                    cuenta_codigo=suggestion.cuenta_codigo,
                    cuenta_nombre=suggestion.cuenta_nombre,
                    confidence_score=new_confidence,
                    reason=f"{suggestion.reason}. {rule['reason']}",
                    tier=suggestion.tier,
                )

        return suggestion

    async def _apply_learned_mappings(
        self,
        concepto: str,
        proveedor_cliente_id: Optional[UUID],
    ) -> Optional[CuentaContableSuggestion]:
        """
        Apply learned mappings from historical expense data.

        Uses two history sources in order:

        1. Imported COI accounting history (`accounting_polizas` + `accounting_poliza_lines`)
        2. Existing operational expense history (`expense_reports`)

        NOTE: Per LEAP_SPEC_CUENTA_BANCARIA_CLEANUP, proveedor_cliente_id is no longer
        used for cuenta_contable suggestions (proveedores/clientes don't have accounting codes).
        """
        from devnous.gastos.models import (
            AccountingPoliza,
            AccountingPolizaLine,
            CuentaContable,
            ExpenseReport,
            ProveedorCliente,
        )

        MIN_OCCURRENCES = 3  # Minimum historical matches for confidence
        provider_name = None
        if proveedor_cliente_id:
            proveedor_result = await self.session.execute(
                select(ProveedorCliente).where(ProveedorCliente.id == proveedor_cliente_id)
            )
            proveedor = proveedor_result.scalar_one_or_none()
            if proveedor and proveedor.nombre:
                provider_name = self._normalize_text_key(proveedor.nombre)

        if concepto:
            concepto_normalized = self._normalize_concepto(concepto)

            if concepto_normalized:
                # Strategy 1: imported COI history
                coi_conditions = [
                    AccountingPolizaLine.cuenta_contable_id.isnot(None),
                    AccountingPoliza.origen == 'coi_xlsx',
                    or_(
                        func.lower(AccountingPoliza.concepto_resumen).contains(concepto_normalized.lower()),
                        func.lower(AccountingPoliza.concepto).contains(concepto_normalized.lower()),
                    ),
                ]
                if provider_name:
                    coi_conditions.insert(
                        0,
                        func.lower(func.coalesce(AccountingPoliza.beneficiario_nombre, '')).contains(provider_name.lower()),
                    )

                result = await self.session.execute(
                    select(
                        AccountingPolizaLine.cuenta_contable_id,
                        func.count(AccountingPolizaLine.id).label('count')
                    )
                    .join(AccountingPoliza, AccountingPoliza.id == AccountingPolizaLine.poliza_id)
                    .where(and_(*coi_conditions))
                    .group_by(AccountingPolizaLine.cuenta_contable_id)
                    .order_by(func.count(AccountingPolizaLine.id).desc())
                )
                rows = result.all()
                if rows:
                    top_cuenta_id, count = rows[0]
                    total_matches = sum(r[1] for r in rows)
                    min_occurrences = 1 if provider_name else 2
                    if count >= min_occurrences:
                        consistency_ratio = count / total_matches if total_matches > 0 else 0
                        base_confidence = 0.72 if provider_name else 0.66
                        confidence = min(0.93, base_confidence + (consistency_ratio * 0.18))
                        cuenta_result = await self.session.execute(
                            select(CuentaContable).where(CuentaContable.id == top_cuenta_id)
                        )
                        cuenta = cuenta_result.scalar_one_or_none()
                        if cuenta:
                            reason = (
                                f"COI histórico: {count} partida(s) contables del cliente con concepto similar"
                            )
                            if provider_name:
                                reason += " y beneficiario coincidente"
                            reason += f" ({int(consistency_ratio * 100)}% consistencia)"
                            return CuentaContableSuggestion(
                                cuenta_contable_id=cuenta.id,
                                cuenta_codigo=cuenta.codigo,
                                cuenta_nombre=cuenta.nombre,
                                confidence_score=confidence,
                                reason=reason,
                                tier='learned',
                            )

                # Strategy 2: operational expense history
                result = await self.session.execute(
                    select(
                        ExpenseReport.cuenta_contable_id,
                        func.count(ExpenseReport.id).label('count')
                    )
                    .where(
                        and_(
                            ExpenseReport.cuenta_contable_id.isnot(None),
                            ExpenseReport.estado_gasto == 'activo',
                            func.lower(ExpenseReport.concepto).contains(concepto_normalized.lower())
                        )
                    )
                    .group_by(ExpenseReport.cuenta_contable_id)
                    .order_by(func.count(ExpenseReport.id).desc())
                )

                rows = result.all()

                if rows:
                    top_cuenta_id, count = rows[0]
                    total_matches = sum(r[1] for r in rows)

                    if count >= MIN_OCCURRENCES:
                        # Calculate confidence based on consistency
                        consistency_ratio = count / total_matches if total_matches > 0 else 0
                        base_confidence = 0.6  # Base confidence for learned mappings
                        confidence = min(0.9, base_confidence + (consistency_ratio * 0.3))

                        # Load cuenta details
                        cuenta_result = await self.session.execute(
                            select(CuentaContable).where(CuentaContable.id == top_cuenta_id)
                        )
                        cuenta = cuenta_result.scalar_one_or_none()

                        if cuenta:
                            return CuentaContableSuggestion(
                                cuenta_contable_id=cuenta.id,
                                cuenta_codigo=cuenta.codigo,
                                cuenta_nombre=cuenta.nombre,
                                confidence_score=confidence,
                                reason=f"Aprendido: {count} gastos previos con concepto similar usan esta cuenta ({int(consistency_ratio*100)}% consistencia)",
                                tier='learned',
                            )

        return None

    def _normalize_text_key(self, text: str) -> str:
        if not text:
            return ""
        normalized = re.sub(r'[^a-záéíóúñü\s]', ' ', text.lower().strip())
        normalized = re.sub(r'\s+', ' ', normalized)
        return normalized.strip()

    def _normalize_concepto(self, concepto: str) -> str:
        """
        Normalize concepto for comparison.

        - Lowercase
        - Remove extra whitespace
        - Remove special characters
        - Extract key words
        """
        if not concepto:
            return ''

        # Lowercase and strip
        normalized = concepto.lower().strip()

        # Remove special characters except spaces
        normalized = re.sub(r'[^a-záéíóúñü\s]', ' ', normalized)

        # Collapse multiple spaces
        normalized = re.sub(r'\s+', ' ', normalized)

        return normalized.strip()

    async def _apply_llm_fallback(
        self,
        concepto: str,
        proveedor_cliente_id: Optional[UUID],
        metodo_pago: Optional[str],
    ) -> Optional[CuentaContableSuggestion]:
        """
        Use LLM to suggest a cuenta contable when rules and learned mappings fail.

        This is strictly optional and wrapped in try/except to never affect the request.

        LLM input:
        - Expense concept
        - Proveedor/Cliente name (if available)
        - List of available cuentas contables (codes + names)

        LLM output (strict JSON):
        {
            "cuenta_codigo": "5300-001",
            "confidence": 0.42,
            "reason": "Concepto sugiere gasto de transporte"
        }
        """
        try:
            # Check if LLM is configured
            anthropic_key = os.environ.get('ANTHROPIC_API_KEY')
            openai_key = os.environ.get('OPENAI_API_KEY')

            if not anthropic_key and not openai_key:
                logger.debug("No LLM API key configured, skipping LLM fallback")
                return None

            # Prepare cuentas list for LLM
            if not self._cuentas_cache:
                return None

            cuentas_list = "\n".join([
                f"- {c.codigo}: {c.nombre} (tipo: {c.tipo})"
                for c in self._cuentas_cache[:50]  # Limit to avoid token overflow
            ])

            # Get proveedor name if available
            proveedor_name = None
            if proveedor_cliente_id:
                from devnous.gastos.models import ProveedorCliente
                result = await self.session.execute(
                    select(ProveedorCliente).where(ProveedorCliente.id == proveedor_cliente_id)
                )
                proveedor = result.scalar_one_or_none()
                if proveedor:
                    proveedor_name = proveedor.nombre

            # Build prompt
            prompt = f"""Eres un asistente contable mexicano. Dado el siguiente gasto, sugiere la cuenta contable más apropiada.

CONCEPTO DEL GASTO: {concepto}
{f'PROVEEDOR/CLIENTE: {proveedor_name}' if proveedor_name else ''}
{f'MÉTODO DE PAGO: {metodo_pago}' if metodo_pago else ''}

CUENTAS CONTABLES DISPONIBLES:
{cuentas_list}

Responde SOLO con JSON válido en este formato exacto:
{{"cuenta_codigo": "CODIGO", "confidence": 0.XX, "reason": "Explicación breve"}}

Reglas:
- confidence debe ser un número entre 0.0 y 1.0
- cuenta_codigo debe ser exactamente uno de los códigos de la lista
- Si no estás seguro, usa confidence bajo (< 0.5)
- reason debe ser una explicación breve en español"""

            # Try Anthropic first
            if anthropic_key:
                suggestion = await self._call_anthropic(prompt, anthropic_key)
                if suggestion:
                    return suggestion

            # Fall back to OpenAI
            if openai_key:
                suggestion = await self._call_openai(prompt, openai_key)
                if suggestion:
                    return suggestion

            return None

        except Exception as e:
            # Never let LLM errors affect the request
            logger.warning(f"LLM fallback error (ignored): {e}")
            return None

    async def _call_anthropic(self, prompt: str, api_key: str) -> Optional[CuentaContableSuggestion]:
        """Call Anthropic Claude API for suggestion."""
        try:
            import httpx
            import json

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-3-haiku-20240307",  # Fast, cost-effective
                        "max_tokens": 200,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )

                if response.status_code != 200:
                    logger.warning(f"Anthropic API error: {response.status_code}")
                    return None

                data = response.json()
                content = data.get('content', [{}])[0].get('text', '')

                return self._parse_llm_response(content)

        except Exception as e:
            logger.warning(f"Anthropic API call failed: {e}")
            return None

    async def _call_openai(self, prompt: str, api_key: str) -> Optional[CuentaContableSuggestion]:
        """Call OpenAI API for suggestion."""
        try:
            import httpx
            import json

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-3.5-turbo",  # Fast, cost-effective
                        "max_tokens": 200,
                        "temperature": 0.3,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )

                if response.status_code != 200:
                    logger.warning(f"OpenAI API error: {response.status_code}")
                    return None

                data = response.json()
                content = data.get('choices', [{}])[0].get('message', {}).get('content', '')

                return self._parse_llm_response(content)

        except Exception as e:
            logger.warning(f"OpenAI API call failed: {e}")
            return None

    def _parse_llm_response(self, content: str) -> Optional[CuentaContableSuggestion]:
        """Parse LLM JSON response into a suggestion."""
        try:
            import json

            # Try to extract JSON from response
            content = content.strip()

            # Handle markdown code blocks
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]

            data = json.loads(content.strip())

            cuenta_codigo = data.get('cuenta_codigo', '')
            confidence = float(data.get('confidence', 0.0))
            reason = data.get('reason', 'Sugerido por IA')

            # Find the cuenta by codigo
            if self._cuentas_cache and cuenta_codigo:
                for cuenta in self._cuentas_cache:
                    if cuenta.codigo == cuenta_codigo:
                        # Cap LLM confidence at 0.7 to prefer rules/learned
                        capped_confidence = min(0.7, confidence)

                        return CuentaContableSuggestion(
                            cuenta_contable_id=cuenta.id,
                            cuenta_codigo=cuenta.codigo,
                            cuenta_nombre=cuenta.nombre,
                            confidence_score=capped_confidence,
                            reason=f"IA: {reason}",
                            tier='llm',
                        )

            return None

        except Exception as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            return None


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def get_cuenta_suggestion(
    session: AsyncSession,
    expense_id: UUID,
    concepto: str,
    proveedor_cliente_id: Optional[UUID] = None,
    metodo_pago: Optional[str] = None,
    proyecto: Optional[str] = None,
    gasto_cantidad: Optional[float] = None,
    use_llm: bool = True,
) -> Optional[CuentaContableSuggestion]:
    """
    Convenience function to get a single cuenta suggestion.
    """
    suggester = CuentaContableSuggester(session)
    return await suggester.get_suggestion(
        expense_id=expense_id,
        concepto=concepto,
        proveedor_cliente_id=proveedor_cliente_id,
        metodo_pago=metodo_pago,
        proyecto=proyecto,
        gasto_cantidad=gasto_cantidad,
        use_llm=use_llm,
    )


async def get_cuenta_suggestions_batch(
    session: AsyncSession,
    expenses: List[Dict[str, Any]],
    use_llm: bool = True,
) -> Dict[UUID, Optional[CuentaContableSuggestion]]:
    """
    Convenience function to get suggestions for multiple expenses.
    """
    suggester = CuentaContableSuggester(session)
    return await suggester.get_suggestions_batch(
        expenses=expenses,
        use_llm=use_llm,
    )
