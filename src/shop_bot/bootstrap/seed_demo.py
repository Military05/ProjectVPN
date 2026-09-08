from __future__ import annotations

import asyncio

import logging
from sqlalchemy import select

from shop_bot.core.config import Settings, get_settings
from shop_bot.infrastructure.persistence.sqlalchemy.engine import create_engine
from shop_bot.infrastructure.persistence.sqlalchemy.tables import node_credentials, nodes, server_endpoints, servers, tariff_specs, tariffs

logger = logging.getLogger(__name__)


async def seed_demo_data(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.is_production:
        raise RuntimeError("Demo seeding is disabled in production")
    engine = create_engine(settings)
    async with engine.begin() as conn:
        tariff_exists = await conn.scalar(select(tariffs.c.tariff_id).limit(1))
        if tariff_exists is None:
            result = await conn.execute(
                tariffs.insert().values(tariff_name="1 month").returning(tariffs.c.tariff_id)
            )
            tariff_id = result.scalar_one()
            await conn.execute(
                tariff_specs.insert().values(
                    tariff_id=tariff_id,
                    price_minor=9900,
                    currency=settings.default_currency,
                    period_days=30,
                    description="Demo tariff seeded automatically",
                    is_enabled=True,
                )
            )
            logger.info("seeded_demo_tariff tariff_id=%s", tariff_id)

        demo_node_id = None
        if settings.demo_node_auto_register:
            existing_node = await conn.execute(select(nodes).where(nodes.c.node_key == settings.demo_node_key).limit(1))
            node_row = existing_node.mappings().first()
            if node_row is None:
                result = await conn.execute(
                    nodes.insert()
                    .values(
                        node_key=settings.demo_node_key,
                        display_name=settings.demo_node_display_name,
                        api_base_url=str(settings.demo_node_api_base_url),
                        is_enabled=True,
                        status="unknown",
                    )
                    .returning(nodes.c.node_id)
                )
                demo_node_id = int(result.scalar_one())
                await conn.execute(
                    node_credentials.insert().values(
                        node_id=demo_node_id,
                        key_id=settings.demo_node_key_id,
                        shared_secret=settings.demo_node_shared_secret,
                        is_active=True,
                    )
                )
                logger.info("seeded_demo_node node_id=%s", demo_node_id)
            else:
                demo_node_id = int(node_row["node_id"])

        server_exists = await conn.scalar(select(servers.c.server_id).limit(1))
        if server_exists is None:
            result = await conn.execute(
                servers.insert()
                .values(
                    server_name="demo-server",
                    host=settings.node_agent_public_host,
                    is_enabled=True,
                )
                .returning(servers.c.server_id)
            )
            server_id = result.scalar_one()
            await conn.execute(
                server_endpoints.insert().values(
                    server_id=server_id,
                    protocol=settings.node_agent_protocol,
                    port=settings.node_agent_public_port,
                    node_id=demo_node_id,
                    local_inbound_id=settings.node_agent_inbound_id if demo_node_id is not None else None,
                    security=settings.node_agent_security,
                    sni=settings.node_agent_sni,
                    fingerprint=settings.node_agent_fingerprint,
                    public_key=settings.node_agent_public_key,
                    short_id=settings.node_agent_short_id,
                    transport_type=settings.node_agent_transport_type,
                    flow=settings.node_agent_flow,
                    encryption=settings.node_agent_encryption,
                    is_enabled=True,
                )
            )
            logger.info("seeded_demo_server server_id=%s node_id=%s", server_id, demo_node_id)
    await engine.dispose()


def main() -> None:
    asyncio.run(seed_demo_data())


if __name__ == "__main__":
    main()