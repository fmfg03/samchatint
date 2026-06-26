"""Telegram runtime for the internal gastos/documentos slice."""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from uuid import UUID

from . import documento_telegram as gastos_tg
from .documento_workflow_service import (
    DocumentoWorkflowPermissionError,
    DocumentoWorkflowValidationError,
    transition_documento_workflow,
)

logger = logging.getLogger(__name__)


class TelegramDocumentRuntime:
    """Owns Telegram document commands/callbacks for gastos/documentos."""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    async def execute_approve(self, chat_id: int, empleado: Any, doc_uuid: UUID) -> None:
        session_maker = self.gateway._resolve_auth_session_maker()
        if not session_maker:
            await self.gateway.send_message(
                chat_id, "⚠️ No hay conexión a la base de datos."
            )
            return
        try:
            async with session_maker() as session:
                await transition_documento_workflow(
                    session,
                    documento_id=doc_uuid,
                    actor_id=empleado.id,
                    action="approve",
                )
        except DocumentoWorkflowValidationError as exc:
            if exc.code == "invalid_estado":
                await self.gateway.send_message(
                    chat_id,
                    "ℹ️ Este documento ya fue procesado o no está pendiente.",
                )
            else:
                await self.gateway.send_message(chat_id, f"⚠️ {exc.message}")
            return
        except DocumentoWorkflowPermissionError as exc:
            await self.gateway.send_message(chat_id, f"⚠️ {exc.message}")
            return
        except Exception:
            logger.exception("Telegram document approve failed")
            await self.gateway.send_message(
                chat_id,
                "❌ No se pudo aprobar. Intenta de nuevo o usa la web.",
            )
            return
        await self.gateway.send_message(chat_id, "✅ Documento *aprobado* correctamente.")

    async def execute_reject(
        self,
        chat_id: int,
        empleado: Any,
        doc_uuid: UUID,
        comentario: str,
    ) -> None:
        session_maker = self.gateway._resolve_auth_session_maker()
        if not session_maker:
            await self.gateway.send_message(
                chat_id, "⚠️ No hay conexión a la base de datos."
            )
            return
        try:
            async with session_maker() as session:
                await transition_documento_workflow(
                    session,
                    documento_id=doc_uuid,
                    actor_id=empleado.id,
                    action="reject",
                    comentario=comentario or None,
                )
        except DocumentoWorkflowValidationError as exc:
            if exc.code == "invalid_estado":
                await self.gateway.send_message(
                    chat_id,
                    "ℹ️ Este documento ya fue procesado o no está pendiente.",
                )
            else:
                await self.gateway.send_message(chat_id, f"⚠️ {exc.message}")
            return
        except DocumentoWorkflowPermissionError as exc:
            await self.gateway.send_message(chat_id, f"⚠️ {exc.message}")
            return
        except Exception:
            logger.exception("Telegram document reject failed")
            await self.gateway.send_message(
                chat_id,
                "❌ No se pudo rechazar. Intenta de nuevo o usa la web.",
            )
            return
        await self.gateway.send_message(
            chat_id,
            "✅ Documento *rechazado*. El solicitante recibirá aviso si tiene Telegram.",
        )

    async def complete_pending_reject(
        self, chat_id: int, user_id: int, text: str
    ) -> None:
        uid = int(user_id)
        raw = self.gateway._gastos_reject_pending.pop(uid, None)
        if not raw:
            return
        empleado = await self.gateway._get_authorized_empleado(user_id)
        if not empleado:
            await self.gateway.send_message(
                chat_id, "⚠️ Tu Telegram no está vinculado a un empleado activo."
            )
            return
        try:
            doc_uuid = UUID(raw)
        except ValueError:
            await self.gateway.send_message(
                chat_id, "⚠️ Identificador de documento inválido."
            )
            return
        await self.execute_reject(chat_id, empleado, doc_uuid, text.strip())

    async def send_pendientes(self, chat_id: int, user_id: int) -> None:
        empleado = await self.gateway._get_authorized_empleado(user_id)
        if not empleado:
            await self.gateway.send_message(
                chat_id,
                "⚠️ Tu Telegram no está vinculado a un empleado activo.",
            )
            return
        if empleado.rol not in gastos_tg.APPROVER_QUEUE_ROLES:
            await self.gateway.send_message(
                chat_id,
                "⚠️ No tienes permisos para ver la bandeja de aprobaciones.",
            )
            return
        session_maker = self.gateway._resolve_auth_session_maker()
        if not session_maker:
            await self.gateway.send_message(
                chat_id, "⚠️ Base de datos no configurada para este bot."
            )
            return
        async with session_maker() as session:
            docs = await gastos_tg.query_pending_documentos_for_approver(
                session, empleado
            )
        if not docs:
            await self.gateway.send_message(
                chat_id, "✅ No tienes documentos pendientes por aprobar."
            )
            return
        rows: List[List[Dict[str, str]]] = []
        for doc in docs[:20]:
            label = f"{doc.numero_referencia} · {doc.tipo}"[:60]
            rows.append(
                [{"text": label, "callback_data": gastos_tg.list_detail_callback_data(doc.id)}]
            )
        await self.gateway.send_message(
            chat_id,
            f"📥 *Pendientes* ({len(docs)}). Toca un documento para ver el detalle y decidir.",
            reply_markup={"inline_keyboard": rows},
        )

    async def send_mis_solicitudes(self, chat_id: int, user_id: int) -> None:
        empleado = await self.gateway._get_authorized_empleado(user_id)
        if not empleado:
            await self.gateway.send_message(
                chat_id,
                "⚠️ Tu Telegram no está vinculado a un empleado activo.",
            )
            return
        session_maker = self.gateway._resolve_auth_session_maker()
        if not session_maker:
            await self.gateway.send_message(
                chat_id, "⚠️ Base de datos no configurada para este bot."
            )
            return
        async with session_maker() as session:
            docs = await gastos_tg.query_documentos_for_requester(session, empleado.id)
        if not docs:
            await self.gateway.send_message(
                chat_id, "No hay documentos recientes registrados a tu nombre."
            )
            return
        rows: List[List[Dict[str, str]]] = []
        for doc in docs[:20]:
            label = f"{doc.numero_referencia} · {doc.estado}"[:60]
            rows.append(
                [{"text": label, "callback_data": gastos_tg.requester_view_callback_data(doc.id)}]
            )
        await self.gateway.send_message(
            chat_id,
            f"📋 *Tus documentos* (últimos {len(docs)}). Toca uno para ver el detalle.",
            reply_markup={"inline_keyboard": rows},
        )

    async def send_solicitud_ref(self, chat_id: int, user_id: int, ref: str) -> None:
        empleado = await self.gateway._get_authorized_empleado(user_id)
        if not empleado:
            await self.gateway.send_message(
                chat_id,
                "⚠️ Tu Telegram no está vinculado a un empleado activo.",
            )
            return
        session_maker = self.gateway._resolve_auth_session_maker()
        if not session_maker:
            await self.gateway.send_message(
                chat_id, "⚠️ Base de datos no configurada para este bot."
            )
            return
        async with session_maker() as session:
            doc = await gastos_tg.find_documento_by_referencia_for_requester(
                session,
                empleado_id=empleado.id,
                referencia=ref,
            )
            if not doc:
                await self.gateway.send_message(
                    chat_id,
                    "No encontré un documento con esa referencia a tu nombre.",
                )
                return
            doc = await gastos_tg.load_documento_for_telegram(session, doc.id)
            ctx = await gastos_tg.build_documento_telegram_context(session, doc)
        text = "📄 *Tu documento*\n\n" + gastos_tg.format_documento_resumen_es(
            doc, context=ctx
        )
        await self.gateway.send_message(chat_id, text)

    async def handle_callback(self, callback_query: Dict[str, Any]) -> bool:
        data = (callback_query.get("data") or "").strip()
        parsed = gastos_tg.parse_documento_callback(data)
        if not parsed:
            return False
        prefix, doc_uuid = parsed
        callback_id = callback_query["id"]
        user_id = callback_query["from"]["id"]
        chat_id = callback_query["message"]["chat"]["id"]

        empleado = await self.gateway._get_authorized_empleado(user_id)
        if not empleado:
            await self.gateway.answer_callback_query(callback_id, "Sin acceso")
            await self.gateway.send_message(
                chat_id,
                "⚠️ Tu Telegram no está vinculado a un empleado activo.",
            )
            return True

        session_maker = self.gateway._resolve_auth_session_maker()
        if not session_maker:
            await self.gateway.answer_callback_query(callback_id, "Sin base de datos")
            return True

        if prefix == gastos_tg.CB_VIEW_REQUESTER:
            async with session_maker() as session:
                doc = await gastos_tg.load_documento_for_telegram(session, doc_uuid)
                if not doc or not gastos_tg.requester_can_view_document(empleado, doc):
                    await self.gateway.answer_callback_query(callback_id, "No disponible")
                    return True
                ctx = await gastos_tg.build_documento_telegram_context(session, doc)
                msg = "📄 *Tu documento*\n\n" + gastos_tg.format_documento_resumen_es(
                    doc, context=ctx
                )
            await self.gateway.answer_callback_query(callback_id)
            await self.gateway.send_message(chat_id, msg)
            return True

        if prefix == gastos_tg.CB_DETAIL_APPROVER:
            async with session_maker() as session:
                doc = await gastos_tg.load_documento_for_telegram(session, doc_uuid)
                if not doc or not gastos_tg.approver_can_see_document_in_queue(
                    empleado, doc
                ):
                    await self.gateway.answer_callback_query(callback_id, "No disponible")
                    return True
                ctx = await gastos_tg.build_documento_telegram_context(session, doc)
                body = gastos_tg.format_documento_resumen_es(
                    doc,
                    context=ctx,
                    include_actions_hint=True,
                )
                msg = "📋 *Detalle para aprobación*\n\n" + body
                kb = gastos_tg.approval_inline_keyboard(doc.id)
            await self.gateway.answer_callback_query(callback_id)
            await self.gateway.send_message(chat_id, msg, reply_markup=kb)
            return True

        if prefix == gastos_tg.CB_APPROVE:
            await self.gateway.answer_callback_query(callback_id, "Procesando…")
            await self.execute_approve(chat_id, empleado, doc_uuid)
            return True

        if prefix == gastos_tg.CB_REJECT:
            self.gateway._gastos_reject_pending[int(user_id)] = str(doc_uuid)
            await self.gateway.answer_callback_query(callback_id)
            await self.gateway.send_message(
                chat_id,
                "✏️ Escribe el *motivo del rechazo* en el siguiente mensaje.\n"
                "Para cancelar sin rechazar, envía `/cancel` (si no tienes otra acción pendiente del asistente).",
            )
            return True

        return False
