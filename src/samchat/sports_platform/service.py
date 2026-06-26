"""Read-only sports platform projection over the canonical tournament snapshot."""

from __future__ import annotations

from typing import Any


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _soul(snapshot: dict[str, Any]) -> dict[str, Any]:
    return dict(snapshot.get("soul") or {})


def _operations(snapshot: dict[str, Any]) -> dict[str, Any]:
    return dict(snapshot.get("operations") or {})


def _communications(snapshot: dict[str, Any]) -> dict[str, Any]:
    return dict(snapshot.get("communications") or {})


def _marketing(snapshot: dict[str, Any]) -> dict[str, Any]:
    return dict(snapshot.get("marketing") or {})


def _teams_from_entities(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity in (_soul(snapshot).get("operations") or {}).get("entities") or []:
        for team in entity.get("teams") or []:
            rows.append({"entity_name": entity.get("entity_name"), **dict(team)})
    return rows


def _team_readiness(team: dict[str, Any]) -> dict[str, Any]:
    players_count = _safe_int(team.get("players_count"))
    complete = _safe_int(team.get("documents_complete_players"))
    verified = _safe_int(team.get("documents_verified_players"))
    has_contact = bool(team.get("primary_manager"))
    completion_rate = _ratio(complete, players_count)
    verification_rate = _ratio(verified, players_count)
    score = 0
    score += round(completion_rate * 45)
    score += round(verification_rate * 35)
    score += 10 if has_contact else 0
    score += 10 if players_count else 0
    if not players_count or completion_rate < 0.75:
        status = "blocked"
    elif completion_rate < 1 or not has_contact:
        status = "risk"
    else:
        status = "ready"
    return {
        "score": min(score, 100),
        "status": status,
        "players_count": players_count,
        "document_completion_rate": completion_rate,
        "document_verification_rate": verification_rate,
        "has_primary_contact": has_contact,
    }


def _match_is_open(match: dict[str, Any]) -> bool:
    cedula_status = _safe_str(match.get("cedula_status")).lower()
    match_status = _safe_str(match.get("status")).lower()
    return cedula_status not in {"closed", "cerrada", "final"} or match_status in {
        "scheduled",
        "pending",
        "live",
    }


def _payment_status_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in snapshot.get("registrations") or []:
        status = _safe_str(row.get("payment_status") or "unknown").lower() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _build_command_center(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("summary") or {}
    soul = _soul(snapshot)
    operations = _operations(snapshot)
    marketing = _marketing(snapshot)
    comms = _communications(snapshot)
    matches = list(operations.get("matches") or [])
    return {
        "title": "Tournament Command Center",
        "tournament": soul.get("tournament")
        or (snapshot.get("tournaments") or [{}])[0],
        "cards": [
            {"label": "Equipos", "value": _safe_int(summary.get("teams_count"))},
            {"label": "Jugadores", "value": _safe_int(summary.get("players_count"))},
            {"label": "Partidos", "value": _safe_int(summary.get("matches_count"))},
            {
                "label": "Docs incompletos",
                "value": _safe_int(summary.get("teams_with_incomplete_documents")),
            },
            {
                "label": "WhatsApp sin leer",
                "value": _safe_int(comms.get("whatsapp_unread")),
            },
            {
                "label": "Fotos",
                "value": _safe_int((marketing.get("media") or {}).get("photos_count")),
            },
        ],
        "next_matches": matches[:8],
        "pending_actions": list(soul.get("pending_actions") or [])[:8],
    }


def _build_team_portal(snapshot: dict[str, Any]) -> dict[str, Any]:
    teams = _teams_from_entities(snapshot)
    portal_rows = []
    for team in teams:
        readiness = _team_readiness(team)
        portal_rows.append(
            {
                "team_id": team.get("team_id"),
                "team_name": team.get("team_name"),
                "entity_name": team.get("entity_name"),
                "category": team.get("category"),
                "primary_manager": team.get("primary_manager"),
                "players_count": readiness["players_count"],
                "document_completion_rate": readiness["document_completion_rate"],
                "document_verification_rate": readiness["document_verification_rate"],
                "readiness_score": readiness["score"],
                "readiness_status": readiness["status"],
                "status": (
                    "ready" if readiness["status"] == "ready" else "action_needed"
                ),
                "public_profile_ready": bool(
                    _safe_str(team.get("instagram_url"))
                    or _safe_str(team.get("facebook_url"))
                    or _safe_str(team.get("shield_url"))
                ),
            }
        )
    return {
        "title": "Portal para equipos",
        "teams_count": len(portal_rows),
        "teams": portal_rows[:50],
        "missing_contact_count": sum(
            1 for row in portal_rows if not row.get("primary_manager")
        ),
        "action_needed_count": sum(
            1 for row in portal_rows if row["status"] != "ready"
        ),
    }


def _build_sports_mission_control(
    snapshot: dict[str, Any],
    *,
    team_portal: dict[str, Any],
    matchday: dict[str, Any],
    risk_radar: dict[str, Any],
    communications: dict[str, Any],
) -> dict[str, Any]:
    blocked_teams = [
        team
        for team in team_portal.get("teams") or []
        if team.get("readiness_status") == "blocked"
    ]
    risk_teams = [
        team
        for team in team_portal.get("teams") or []
        if team.get("readiness_status") == "risk"
    ]
    open_matches = [
        match for match in matchday.get("next_matches") or [] if _match_is_open(match)
    ]
    today_plan = []
    if blocked_teams:
        today_plan.append(f"Desbloquear {len(blocked_teams)} equipo(s) por documentos.")
    if risk_teams:
        today_plan.append(f"Revisar {len(risk_teams)} equipo(s) en riesgo.")
    if open_matches:
        today_plan.append(
            f"Cerrar o preparar {len(open_matches)} partido(s)/cedula(s)."
        )
    unread_total = _safe_int(communications.get("whatsapp_unread")) + _safe_int(
        communications.get("email_inbox_unread")
    )
    if unread_total:
        today_plan.append(f"Atender {unread_total} mensaje(s) oficiales pendientes.")
    if risk_radar.get("high_attention_count"):
        today_plan.append(
            f"Resolver {risk_radar.get('high_attention_count')} riesgo(s) operativos."
        )
    if not today_plan:
        today_plan.append("Mantener monitoreo y preparar briefing preventivo.")
    return {
        "title": "Sports Mission Control",
        "today_plan": today_plan,
        "agenda_7_days": list(matchday.get("next_matches") or [])[:7],
        "blocked_teams": blocked_teams[:10],
        "risk_teams": risk_teams[:10],
        "open_matches": open_matches[:10],
        "urgent_communications": {
            "whatsapp_unread": _safe_int(communications.get("whatsapp_unread")),
            "email_inbox_unread": _safe_int(communications.get("email_inbox_unread")),
        },
        "ops_brief": " ".join(today_plan),
        "read_only": True,
    }


def _build_team_journeys(snapshot: dict[str, Any]) -> dict[str, Any]:
    operations = _operations(snapshot)
    matches = list(operations.get("matches") or [])
    teams = _teams_from_entities(snapshot)
    journeys: list[dict[str, Any]] = []
    for team in teams:
        team_id = _safe_str(team.get("team_id"))
        team_matches = [
            match
            for match in matches
            if team_id
            and team_id
            in {
                _safe_str(match.get("home_team_id")),
                _safe_str(match.get("away_team_id")),
                _safe_str(match.get("team_id")),
            }
        ]
        readiness = _team_readiness(team)
        journeys.append(
            {
                "team_id": team.get("team_id"),
                "team_name": team.get("team_name"),
                "entity_name": team.get("entity_name"),
                "category": team.get("category"),
                "primary_manager": team.get("primary_manager"),
                "readiness": readiness,
                "payments": {"status": "see_registrations", "source": "registrations"},
                "calendar": team_matches[:20],
                "incidents": [],
                "communications": [],
                "next_actions": _team_next_actions(team, readiness, team_matches),
            }
        )
    return {
        "title": "Team Journey",
        "teams": journeys[:50],
        "ready_count": sum(
            1 for journey in journeys if journey["readiness"]["status"] == "ready"
        ),
        "risk_count": sum(
            1 for journey in journeys if journey["readiness"]["status"] == "risk"
        ),
        "blocked_count": sum(
            1 for journey in journeys if journey["readiness"]["status"] == "blocked"
        ),
    }


def _team_next_actions(
    team: dict[str, Any],
    readiness: dict[str, Any],
    team_matches: list[dict[str, Any]],
) -> list[str]:
    actions: list[str] = []
    if not readiness["players_count"]:
        actions.append("Cargar roster.")
    if readiness["document_completion_rate"] < 1:
        actions.append("Completar documentos faltantes.")
    if readiness["document_verification_rate"] < 1:
        actions.append("Verificar documentos del roster.")
    if not readiness["has_primary_contact"]:
        actions.append("Asignar responsable primario.")
    if not team_matches:
        actions.append("Confirmar calendario del equipo.")
    if not actions:
        actions.append("Equipo listo para operar; mantener monitoreo.")
    return actions


def _build_match_center(snapshot: dict[str, Any]) -> dict[str, Any]:
    operations = _operations(snapshot)
    teams_by_id = {
        _safe_str(team.get("team_id")): team for team in _teams_from_entities(snapshot)
    }
    matches = []
    for match in list(operations.get("matches") or []):
        home_team = teams_by_id.get(_safe_str(match.get("home_team_id")), {})
        away_team = teams_by_id.get(_safe_str(match.get("away_team_id")), {})
        match_status = _safe_str(match.get("status") or "scheduled")
        cedula_status = _safe_str(match.get("cedula_status") or "pending")
        matches.append(
            {
                **dict(match),
                "home_team_name": home_team.get("team_name")
                or match.get("home_team_name")
                or "Local",
                "away_team_name": away_team.get("team_name")
                or match.get("away_team_name")
                or "Visitante",
                "home_readiness": _team_readiness(home_team) if home_team else None,
                "away_readiness": _team_readiness(away_team) if away_team else None,
                "match_status": match_status,
                "cedula_status": cedula_status,
                "can_close": cedula_status.lower()
                not in {"closed", "cerrada", "final"},
                "field_checklist": [
                    "Confirmar equipos presentes",
                    "Validar roster elegible",
                    "Asignar arbitro",
                    "Capturar marcador",
                    "Cerrar cedula",
                    "Subir evidencia",
                ],
            }
        )
    return {
        "title": "Match Center",
        "matches": matches[:50],
        "open_count": sum(1 for match in matches if match.get("can_close")),
        "closed_count": sum(1 for match in matches if not match.get("can_close")),
    }


def _build_roster_intelligence(snapshot: dict[str, Any]) -> dict[str, Any]:
    compliance = _soul(snapshot).get("compliance") or {}
    return {
        "title": "Roster inteligente",
        "players_count": _safe_int(compliance.get("players_count")),
        "completion_rate": float(compliance.get("completion_rate") or 0),
        "verification_rate": float(compliance.get("verification_rate") or 0),
        "incomplete_teams": list(compliance.get("incomplete_teams") or [])[:30],
        "rules": [
            "Validar documentos completos antes de matchday.",
            "Marcar CURP, edad/categoria y duplicados como bloqueantes operativos.",
            "Usar OCR como evidencia, no como verdad final sin revisión.",
        ],
    }


def _build_matchday_ops(snapshot: dict[str, Any]) -> dict[str, Any]:
    operations = _operations(snapshot)
    matches = list(operations.get("matches") or [])
    open_cedulas = [
        match
        for match in matches
        if _safe_str(match.get("cedula_status")).lower()
        not in {"closed", "cerrada", "final"}
    ]
    return {
        "title": "Matchday Ops",
        "matches_count": len(matches),
        "open_cedulas_count": len(open_cedulas),
        "next_matches": matches[:20],
        "field_actions": [
            "Check-in de equipos",
            "Validar cédula",
            "Capturar marcador",
            "Registrar incidencia",
            "Subir evidencia/foto",
        ],
    }


def _build_communications(snapshot: dict[str, Any]) -> dict[str, Any]:
    comms = _communications(snapshot)
    return {
        "title": "Comunicación oficial",
        "email_inbox_unread": _safe_int(comms.get("email_inbox_unread")),
        "whatsapp_unread": _safe_int(comms.get("whatsapp_unread")),
        "scheduled_emails": _safe_int(comms.get("scheduled_emails_count")),
        "templates_ready": _safe_int(comms.get("whatsapp_templates_active")),
        "segments": ["equipos", "sedes", "arbitros", "staff", "proveedores"],
    }


def _build_sports_crm(snapshot: dict[str, Any]) -> dict[str, Any]:
    teams = _teams_from_entities(snapshot)
    entities: dict[str, dict[str, Any]] = {}
    for team in teams:
        entity_name = _safe_str(team.get("entity_name")) or "Sin entidad"
        item = entities.setdefault(
            entity_name,
            {"entity_name": entity_name, "teams_count": 0, "players_count": 0},
        )
        item["teams_count"] += 1
        item["players_count"] += _safe_int(team.get("players_count"))
    return {
        "title": "Sports CRM",
        "entities_count": len(entities),
        "entities": sorted(
            entities.values(),
            key=lambda row: (
                -_safe_int(row.get("teams_count")),
                row.get("entity_name") or "",
            ),
        )[:50],
        "payment_status_counts": _payment_status_counts(snapshot),
    }


def _build_public_layer(snapshot: dict[str, Any]) -> dict[str, Any]:
    marketing = _marketing(snapshot)
    operations = _operations(snapshot)
    media = dict(marketing.get("media") or {})
    standings = list(operations.get("standings") or [])
    return {
        "title": "Fan/Public Layer",
        "calendar_ready": bool(operations.get("matches")),
        "standings_ready": bool(standings),
        "media_ready": bool(
            _safe_int(media.get("photos_count"))
            or _safe_int(media.get("videos_count"))
            or _safe_int(media.get("streams_count"))
        ),
        "standings": standings[:20],
        "media": media,
    }


def _build_mobile_field_app(
    matchday: dict[str, Any], roster: dict[str, Any]
) -> dict[str, Any]:
    return {
        "title": "Mobile field app",
        "offline_first_needed": bool(matchday.get("matches_count")),
        "primary_actions": [
            "Escanear equipo",
            "Ver roster",
            "Cerrar cédula",
            "Reportar incidencia",
            "Enviar comunicado",
        ],
        "blocking_counts": {
            "open_cedulas": _safe_int(matchday.get("open_cedulas_count")),
            "incomplete_teams": len(roster.get("incomplete_teams") or []),
        },
    }


def _build_ai_ops_assistant(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "AI Ops Assistant",
        "suggested_prompts": [
            "Que torneos estan en riesgo esta semana?",
            "Que equipos tienen documentos faltantes?",
            "Que partidos no tienen cedula cerrada?",
            "Que entidades necesitan seguimiento?",
            "Que comunicados debo enviar hoy?",
        ],
        "available_context": [
            "snapshot_soul",
            "roster_compliance",
            "matchday_ops",
            "communications",
            "sports_crm",
        ],
    }


def _build_global_readiness(
    *,
    team_journey: dict[str, Any],
    match_center: dict[str, Any],
    risk_radar: dict[str, Any],
    public_layer: dict[str, Any],
) -> dict[str, Any]:
    journeys = list(team_journey.get("teams") or [])
    team_scores = [
        _safe_int((journey.get("readiness") or {}).get("score")) for journey in journeys
    ]
    team_avg = round(sum(team_scores) / len(team_scores), 2) if team_scores else 0.0
    matches = list(match_center.get("matches") or [])
    closed_matches = _safe_int(match_center.get("closed_count"))
    match_score = round((closed_matches / len(matches)) * 100, 2) if matches else 0.0
    risk_penalty = min(_safe_int(risk_radar.get("high_attention_count")) * 12, 40)
    public_bonus = 10 if public_layer.get("calendar_ready") else 0
    public_bonus += 10 if public_layer.get("standings_ready") else 0
    score = max(
        0,
        min(
            100,
            round(
                (team_avg * 0.55) + (match_score * 0.25) + public_bonus - risk_penalty,
                2,
            ),
        ),
    )
    if score >= 85:
        status = "green"
    elif score >= 65:
        status = "yellow"
    else:
        status = "red"
    return {
        "title": "Readiness Score global",
        "score": score,
        "status": status,
        "components": {
            "team_readiness_avg": team_avg,
            "match_closure_score": match_score,
            "risk_penalty": risk_penalty,
            "public_bonus": public_bonus,
        },
        "readiness_by_team": [
            {
                "team_id": journey.get("team_id"),
                "team_name": journey.get("team_name"),
                "status": (journey.get("readiness") or {}).get("status"),
                "score": (journey.get("readiness") or {}).get("score"),
            }
            for journey in journeys[:20]
        ],
    }


def _build_ops_copilot(
    *,
    mission_control: dict[str, Any],
    team_journey: dict[str, Any],
    match_center: dict[str, Any],
    communications: dict[str, Any],
) -> dict[str, Any]:
    blocked_count = _safe_int(team_journey.get("blocked_count"))
    risk_count = _safe_int(team_journey.get("risk_count"))
    open_count = _safe_int(match_center.get("open_count"))
    whatsapp = _safe_int(communications.get("whatsapp_unread"))
    email = _safe_int(communications.get("email_inbox_unread"))
    return {
        "title": "Ops Copilot",
        "briefing": mission_control.get("ops_brief") or "Sin briefing activo.",
        "drafts": [
            {
                "label": "Mensaje equipos con documentos faltantes",
                "copy": (
                    f"Tenemos {blocked_count + risk_count} equipo(s) con pendientes. "
                    "Favor de completar documentos y confirmar responsable "
                    "antes del siguiente corte."
                ),
            },
            {
                "label": "Briefing matchday",
                "copy": (
                    f"Hay {open_count} partido(s) o cedula(s) abiertas. "
                    "Confirmar equipos, arbitraje, marcador y evidencia "
                    "antes de cerrar jornada."
                ),
            },
            {
                "label": "Comunicacion urgente",
                "copy": (
                    f"Pendientes: {whatsapp} WhatsApp y {email} email(s). "
                    "Priorizar mensajes de equipos bloqueados y staff de cancha."
                ),
            },
        ],
        "commands": [
            "Generar briefing operativo del dia",
            "Redactar mensaje a equipos bloqueados",
            "Listar partidos sin cedula cerrada",
            "Preparar reporte ejecutivo post-jornada",
        ],
    }


def _build_public_microsite_generator(
    *,
    command_center: dict[str, Any],
    public_layer: dict[str, Any],
) -> dict[str, Any]:
    tournament = command_center.get("tournament") or {}
    slug = _safe_str(tournament.get("slug") or tournament.get("id") or "torneo")
    sections = [
        {
            "key": "calendar",
            "label": "Calendario",
            "ready": bool(public_layer.get("calendar_ready")),
        },
        {
            "key": "standings",
            "label": "Standings",
            "ready": bool(public_layer.get("standings_ready")),
        },
        {
            "key": "media",
            "label": "Galeria/media",
            "ready": bool(public_layer.get("media_ready")),
        },
        {"key": "news", "label": "Comunicados", "ready": True},
    ]
    return {
        "title": "Public microsite generator",
        "slug": slug,
        "preview_url": f"/sports/{slug}",
        "sections": sections,
        "ready_sections": sum(1 for section in sections if section["ready"]),
        "publish_state": (
            "ready" if all(section["ready"] for section in sections[:3]) else "draft"
        ),
    }


def _build_sponsor_media_dashboard(
    snapshot: dict[str, Any],
    *,
    command_center: dict[str, Any],
    public_layer: dict[str, Any],
    sports_crm: dict[str, Any],
) -> dict[str, Any]:
    media = public_layer.get("media") or {}
    summary = snapshot.get("summary") or {}
    return {
        "title": "Sponsor/Media dashboard",
        "proof_points": [
            {"label": "Equipos", "value": _safe_int(summary.get("teams_count"))},
            {"label": "Jugadores", "value": _safe_int(summary.get("players_count"))},
            {
                "label": "Entidades",
                "value": _safe_int(sports_crm.get("entities_count")),
            },
            {"label": "Fotos", "value": _safe_int(media.get("photos_count"))},
            {"label": "Videos", "value": _safe_int(media.get("videos_count"))},
            {"label": "Streams", "value": _safe_int(media.get("streams_count"))},
        ],
        "narrative": (
            f"{(command_center.get('tournament') or {}).get('name') or 'El torneo'} "
            "ya tiene evidencia operativa lista para patrocinadores y medios."
        ),
        "export_ready": bool(public_layer.get("media_ready")),
    }


def _build_incident_center(
    *,
    risk_radar: dict[str, Any],
    team_journey: dict[str, Any],
    match_center: dict[str, Any],
) -> dict[str, Any]:
    incidents: list[dict[str, Any]] = []
    for risk in risk_radar.get("risks") or []:
        incidents.append(
            {
                "type": risk.get("code") or "risk",
                "severity": risk.get("severity") or "medium",
                "message": risk.get("message") or "",
                "source": "risk_radar",
            }
        )
    for journey in team_journey.get("teams") or []:
        readiness = journey.get("readiness") or {}
        if readiness.get("status") in {"blocked", "risk"}:
            incidents.append(
                {
                    "type": "team_readiness",
                    "severity": (
                        "high" if readiness.get("status") == "blocked" else "medium"
                    ),
                    "message": (
                        f"{journey.get('team_name') or 'Equipo'} "
                        f"esta {readiness.get('status')}."
                    ),
                    "source": "team_journey",
                }
            )
    for match in match_center.get("matches") or []:
        if match.get("can_close"):
            incidents.append(
                {
                    "type": "open_match_cedula",
                    "severity": "medium",
                    "message": (
                        f"Partido {match.get('id') or ''} " "requiere cierre de cedula."
                    ),
                    "source": "match_center",
                }
            )
    return {
        "title": "Incident Center",
        "incidents": incidents[:50],
        "open_count": len(incidents),
        "high_count": sum(1 for item in incidents if item.get("severity") == "high"),
    }


def _build_venue_ops(match_center: dict[str, Any]) -> dict[str, Any]:
    venues: dict[str, dict[str, Any]] = {}
    for match in match_center.get("matches") or []:
        field = _safe_str(
            match.get("field_number") or match.get("venue") or "Sin cancha"
        )
        item = venues.setdefault(
            field,
            {"venue": field, "matches_count": 0, "open_matches": 0, "phases": set()},
        )
        item["matches_count"] += 1
        if match.get("can_close"):
            item["open_matches"] += 1
        if _safe_str(match.get("phase")):
            item["phases"].add(_safe_str(match.get("phase")))
    rows = []
    for item in venues.values():
        rows.append(
            {
                "venue": item["venue"],
                "matches_count": item["matches_count"],
                "open_matches": item["open_matches"],
                "phases": sorted(item["phases"]),
                "checklist": [
                    "Responsable de sede",
                    "Arbitraje",
                    "Mesa de control",
                    "Botiquin/seguridad",
                    "Evidencia fotografica",
                ],
            }
        )
    return {
        "title": "Venue Ops",
        "venues": sorted(rows, key=lambda row: (-row["matches_count"], row["venue"]))[
            :30
        ],
        "venues_count": len(rows),
    }


def _build_post_tournament_report(
    *,
    command_center: dict[str, Any],
    global_readiness: dict[str, Any],
    sponsor_media: dict[str, Any],
    incident_center: dict[str, Any],
    sports_crm: dict[str, Any],
) -> dict[str, Any]:
    tournament = command_center.get("tournament") or {}
    return {
        "title": "Post-tournament report",
        "report_name": f"Reporte ejecutivo - {tournament.get('name') or 'Torneo'}",
        "sections": [
            "Resumen operativo",
            "Participacion",
            "Readiness y cumplimiento",
            "Matchday y cedulas",
            "Evidencia sponsor/media",
            "Incidencias",
            "Aprendizajes y siguientes pasos",
        ],
        "metrics": {
            "readiness_score": global_readiness.get("score"),
            "incidents_open": incident_center.get("open_count"),
            "entities_count": sports_crm.get("entities_count"),
            "media_export_ready": sponsor_media.get("export_ready"),
        },
        "export_formats": ["PDF", "Excel"],
        "ready": True,
    }


def _action_item(
    *,
    action_id: str,
    title: str,
    module: str,
    severity: str,
    owner: str,
    due: str,
    status: str,
    detail: str,
    source: str,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "title": title,
        "module": module,
        "severity": severity,
        "owner": owner,
        "due": due,
        "status": status,
        "detail": detail,
        "source": source,
    }


def _build_action_queue(
    *,
    team_journey: dict[str, Any],
    match_center: dict[str, Any],
    communications: dict[str, Any],
    incident_center: dict[str, Any],
    venue_ops: dict[str, Any],
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for journey in team_journey.get("teams") or []:
        readiness = journey.get("readiness") or {}
        if readiness.get("status") in {"blocked", "risk"}:
            severity = "high" if readiness.get("status") == "blocked" else "medium"
            actions.append(
                _action_item(
                    action_id=(
                        f"team:{journey.get('team_id') or journey.get('team_name')}"
                    ),
                    title=(
                        f"Resolver readiness de "
                        f"{journey.get('team_name') or 'equipo'}"
                    ),
                    module="Team Journey",
                    severity=severity,
                    owner=_safe_str(
                        ((journey.get("primary_manager") or {}).get("email"))
                    )
                    or "Operaciones",
                    due="today",
                    status="open",
                    detail="; ".join(journey.get("next_actions") or []),
                    source="team_journey",
                )
            )
    for match in match_center.get("matches") or []:
        if match.get("can_close"):
            actions.append(
                _action_item(
                    action_id=f"match:{match.get('id') or match.get('match_date')}",
                    title="Cerrar cédula / preparar partido",
                    module="Match Center",
                    severity="medium",
                    owner="Matchday Ops",
                    due="today",
                    status="open",
                    detail=(
                        f"{match.get('home_team_name') or 'Local'} vs "
                        f"{match.get('away_team_name') or 'Visitante'}"
                    ),
                    source="match_center",
                )
            )
    unread_whatsapp = _safe_int(communications.get("whatsapp_unread"))
    unread_email = _safe_int(communications.get("email_inbox_unread"))
    if unread_whatsapp or unread_email:
        actions.append(
            _action_item(
                action_id="communications:unread",
                title="Atender comunicación oficial pendiente",
                module="Comunicación oficial",
                severity="medium",
                owner="Comunicaciones",
                due="today",
                status="open",
                detail=f"{unread_whatsapp} WhatsApp; {unread_email} email(s).",
                source="communications",
            )
        )
    for incident in incident_center.get("incidents") or []:
        if incident.get("severity") == "high":
            actions.append(
                _action_item(
                    action_id=f"incident:{incident.get('type')}",
                    title="Resolver incidente crítico",
                    module="Incident Center",
                    severity="high",
                    owner="Operaciones",
                    due="today",
                    status="open",
                    detail=_safe_str(incident.get("message")),
                    source="incident_center",
                )
            )
    for venue in venue_ops.get("venues") or []:
        if _safe_int(venue.get("open_matches")):
            actions.append(
                _action_item(
                    action_id=f"venue:{venue.get('venue')}",
                    title=f"Revisar sede/cancha {venue.get('venue')}",
                    module="Venue Ops",
                    severity="low",
                    owner="Sedes",
                    due="next_24h",
                    status="open",
                    detail=f"{venue.get('open_matches')} partido(s) abiertos.",
                    source="venue_ops",
                )
            )
    severity_order = {"high": 0, "medium": 1, "low": 2}
    actions = sorted(
        actions,
        key=lambda item: (
            severity_order.get(_safe_str(item.get("severity")), 9),
            _safe_str(item.get("module")),
            _safe_str(item.get("title")),
        ),
    )
    return {
        "title": "Action Queue",
        "actions": actions[:100],
        "open_count": len(actions),
        "high_count": sum(1 for item in actions if item.get("severity") == "high"),
        "medium_count": sum(1 for item in actions if item.get("severity") == "medium"),
        "low_count": sum(1 for item in actions if item.get("severity") == "low"),
        "read_only": True,
    }


def _build_one_click_ops_brief(
    *,
    command_center: dict[str, Any],
    action_queue: dict[str, Any],
    global_readiness: dict[str, Any],
    match_center: dict[str, Any],
    communications: dict[str, Any],
    incident_center: dict[str, Any],
) -> dict[str, Any]:
    tournament = command_center.get("tournament") or {}
    title = f"Brief operativo - {tournament.get('name') or 'Torneo'}"
    top_actions = list(action_queue.get("actions") or [])[:5]
    lines = [
        title,
        (
            f"Readiness global: {global_readiness.get('score')} "
            f"({global_readiness.get('status')})."
        ),
        (
            f"Acciones abiertas: {action_queue.get('open_count')} "
            f"(altas: {action_queue.get('high_count')})."
        ),
        f"Partidos/cédulas abiertas: {match_center.get('open_count')}.",
        (
            "Comunicación pendiente: "
            f"{communications.get('whatsapp_unread')} WhatsApp; "
            f"{communications.get('email_inbox_unread')} email(s)."
        ),
        f"Incidentes abiertos: {incident_center.get('open_count')}.",
    ]
    if top_actions:
        lines.append("Prioridades:")
        lines.extend(
            f"- [{item.get('severity')}] {item.get('title')} - {item.get('owner')}"
            for item in top_actions
        )
    else:
        lines.append("Sin acciones críticas abiertas. Mantener monitoreo.")
    return {
        "title": "One-click Ops Brief",
        "brief_title": title,
        "plain_text": "\n".join(lines),
        "whatsapp_text": " | ".join(lines[:6]),
        "email_subject": title,
        "email_body": "\n".join(lines),
        "pdf_ready": True,
        "export_targets": ["WhatsApp", "Email", "PDF"],
        "priority_actions": top_actions,
    }


def build_sports_platform_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build the full sports platform UX projection without mutating source data."""

    command_center = _build_command_center(snapshot)
    team_portal = _build_team_portal(snapshot)
    roster = _build_roster_intelligence(snapshot)
    matchday = _build_matchday_ops(snapshot)
    communications = _build_communications(snapshot)
    sports_crm = _build_sports_crm(snapshot)
    public_layer = _build_public_layer(snapshot)
    mobile_field_app = _build_mobile_field_app(matchday, roster)
    ai_ops_assistant = _build_ai_ops_assistant(snapshot)
    risks = list((_soul(snapshot).get("risks") or []))
    risk_radar = {
        "title": "Risk Radar",
        "risks": risks,
        "risk_count": len(risks),
        "critical_count": sum(
            1 for risk in risks if risk.get("severity") == "critical"
        ),
        "high_attention_count": sum(
            1
            for risk in risks
            if risk.get("severity") in {"critical", "high", "medium"}
        ),
    }
    mission_control = _build_sports_mission_control(
        snapshot,
        team_portal=team_portal,
        matchday=matchday,
        risk_radar=risk_radar,
        communications=communications,
    )
    team_journey = _build_team_journeys(snapshot)
    match_center = _build_match_center(snapshot)
    global_readiness = _build_global_readiness(
        team_journey=team_journey,
        match_center=match_center,
        risk_radar=risk_radar,
        public_layer=public_layer,
    )
    ops_copilot = _build_ops_copilot(
        mission_control=mission_control,
        team_journey=team_journey,
        match_center=match_center,
        communications=communications,
    )
    public_microsite = _build_public_microsite_generator(
        command_center=command_center,
        public_layer=public_layer,
    )
    sponsor_media = _build_sponsor_media_dashboard(
        snapshot,
        command_center=command_center,
        public_layer=public_layer,
        sports_crm=sports_crm,
    )
    incident_center = _build_incident_center(
        risk_radar=risk_radar,
        team_journey=team_journey,
        match_center=match_center,
    )
    venue_ops = _build_venue_ops(match_center)
    post_tournament_report = _build_post_tournament_report(
        command_center=command_center,
        global_readiness=global_readiness,
        sponsor_media=sponsor_media,
        incident_center=incident_center,
        sports_crm=sports_crm,
    )
    action_queue = _build_action_queue(
        team_journey=team_journey,
        match_center=match_center,
        communications=communications,
        incident_center=incident_center,
        venue_ops=venue_ops,
    )
    ops_brief = _build_one_click_ops_brief(
        command_center=command_center,
        action_queue=action_queue,
        global_readiness=global_readiness,
        match_center=match_center,
        communications=communications,
        incident_center=incident_center,
    )
    return {
        "ok": True,
        "read_only": True,
        "source": "tournament_soul_snapshot",
        "summary": {
            "teams": (command_center["cards"][0] or {}).get("value"),
            "players": (command_center["cards"][1] or {}).get("value"),
            "matches": (command_center["cards"][2] or {}).get("value"),
            "risk_count": risk_radar["risk_count"],
            "team_actions": team_portal["action_needed_count"],
        },
        "mission_control": mission_control,
        "command_center": command_center,
        "team_journey": team_journey,
        "match_center": match_center,
        "action_queue": action_queue,
        "ops_brief": ops_brief,
        "global_readiness": global_readiness,
        "ops_copilot": ops_copilot,
        "public_microsite": public_microsite,
        "sponsor_media": sponsor_media,
        "incident_center": incident_center,
        "venue_ops": venue_ops,
        "post_tournament_report": post_tournament_report,
        "team_portal": team_portal,
        "roster_intelligence": roster,
        "matchday_ops": matchday,
        "communications": communications,
        "risk_radar": risk_radar,
        "sports_crm": sports_crm,
        "public_layer": public_layer,
        "mobile_field_app": mobile_field_app,
        "ai_ops_assistant": ai_ops_assistant,
    }
