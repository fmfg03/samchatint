"""Durable channel nonce consumer for a privately configured asyncpg pool.

Channel replay protection is separate from business approval and capture.
No pool, credentials, roles or grants are created by this module.
"""

from uuid import UUID


class PostgresTransportNonces:
    def __init__(self, pool):
        self._pool = pool

    async def __call__(self, nonce: UUID, expires_at: int) -> bool:
        if not isinstance(nonce, UUID) or type(expires_at) is not int:
            return False
        try:
            async with self._pool.acquire() as conn:
                if conn.is_in_transaction():
                    return False
                async with conn.transaction():
                    accepted = await conn.fetchval(
                        "SELECT ctt_staff_private." "claim_transport_nonce($1,$2)",
                        nonce,
                        expires_at,
                    )
            # Return only after the independent channel transaction commits.
            return accepted is True
        except Exception:
            return False
