"""Persist SAT package ZIP bytes to survive SAT 5008 re-download limits."""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .sync_models import SATPackageBlob

logger = logging.getLogger(__name__)

SAT_PACKAGE_MAX_DOWNLOADS = 5008


class SATPackageCache:
    async def get(self, session: AsyncSession, package_id: str) -> Optional[bytes]:
        result = await session.execute(
            select(SATPackageBlob).where(SATPackageBlob.package_id == package_id)
        )
        row = result.scalar_one_or_none()
        if not row or not row.package_bytes:
            return None
        try:
            return base64.b64decode(row.package_bytes.encode("ascii"))
        except Exception:
            logger.warning("Invalid cached package blob for %s", package_id)
            return None

    async def save(
        self,
        session: AsyncSession,
        *,
        package_id: str,
        rfc: str,
        solicitud_id: Optional[str],
        package_bytes: bytes,
        cod_estatus: Optional[int] = None,
    ) -> None:
        if not package_bytes:
            return
        encoded = base64.b64encode(package_bytes).decode("ascii")
        result = await session.execute(
            select(SATPackageBlob).where(SATPackageBlob.package_id == package_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = SATPackageBlob(
                package_id=package_id,
                rfc=rfc,
                solicitud_id=solicitud_id,
                package_bytes=encoded,
                cod_estatus=cod_estatus,
            )
            session.add(row)
        else:
            row.package_bytes = encoded
            row.rfc = rfc
            row.solicitud_id = solicitud_id
            row.cod_estatus = cod_estatus
            row.downloaded_at = datetime.utcnow()
