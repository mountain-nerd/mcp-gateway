"""CLI entry point for the MCP Gateway."""

from __future__ import annotations

import logging
import sys

import click
import uvicorn

from mcp_gateway.config import load_config


@click.command()
@click.option(
    "-c", "--config",
    required=True,
    type=click.Path(exists=True),
    help="Path to the gateway YAML config file.",
)
@click.option("--host", default=None, help="Override listen host.")
@click.option("--port", default=None, type=int, help="Override listen port.")
@click.option("--log-level", default=None, help="Override log level.")
def main(config: str, host: str | None, port: int | None, log_level: str | None) -> None:
    """Start the MCP Gateway server."""
    cfg = load_config(config)

    if host:
        cfg.host = host
    if port:
        cfg.port = port
    if log_level:
        cfg.log_level = log_level

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger = logging.getLogger("mcp_gateway")
    logger.info("Starting MCP Gateway on %s:%d%s", cfg.host, cfg.port, cfg.path)
    logger.info("Upstream servers: %s", ", ".join(cfg.mcp_servers.keys()) or "(none)")

    # Import here to avoid circular imports
    from mcp_gateway.app import create_app

    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=cfg.log_level)


if __name__ == "__main__":
    main()
