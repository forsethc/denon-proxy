"""Shared e2e fixtures: discovery stack (proxy + SSDP + discovery HTTP) and full stack (proxy + HTTP API + discovery)."""
from __future__ import annotations

import asyncio
import logging

import pytest

from runtime_state import RuntimeState
from avr_connection import create_avr_connection
from avr_discovery import run_discovery_servers
from denon_proxy import DenonProxyServer, load_config_from_dict


pytestmark = pytest.mark.e2e


@pytest.fixture
def discovery_config():
    """Config for proxy + discovery only (no JSON API): VirtualAVR + SSDP + discovery HTTP."""
    return load_config_from_dict({
        "avr_host": "",
        "proxy_host": "127.0.0.1",
        "proxy_port": 0,
        "enable_http": False,
        "enable_ssdp": True,
        "ssdp_advertise_ip": "127.0.0.1",
        "ssdp_http_port": 0,
        "ssdp_friendly_name": "Test Discovery Proxy",
    })


@pytest.fixture
def discovery_logger():
    return logging.getLogger("test.discovery")


@pytest.fixture
async def discovery_stack(discovery_config, discovery_logger):
    """
    Start full stack: DenonProxyServer (VirtualAVR) + run_discovery_servers.
    Yields (proxy, ssdp_transport, http_servers). Teardown closes all.
    """
    runtime_state = RuntimeState()
    proxy = DenonProxyServer(discovery_config, discovery_logger, create_avr_connection, runtime_state)
    await proxy.start()
    ssdp_transport, http_servers = None, None
    try:
        ssdp_transport, http_servers = await run_discovery_servers(
            discovery_config, discovery_logger, proxy.avr_state, runtime_state
        )
    except Exception:
        pass
    yield proxy, ssdp_transport, http_servers
    if ssdp_transport:
        ssdp_transport.close()
    if http_servers:
        for srv in http_servers:
            srv.close()
        for srv in http_servers:
            await asyncio.wait_for(srv.wait_closed(), timeout=2.0)
    await proxy.stop()


@pytest.fixture
def full_stack_http_config():
    """Config for full stack with JSON API: proxy + HTTP + discovery (production-like)."""
    return load_config_from_dict({
        "avr_host": "",
        "proxy_host": "127.0.0.1",
        "proxy_port": 0,
        "enable_http": True,
        "http_port": 0,
        "enable_ssdp": True,
        "ssdp_advertise_ip": "127.0.0.1",
        "ssdp_http_port": 0,
        "ssdp_friendly_name": "Test Full Stack Proxy",
    })


@pytest.fixture
def full_stack_http_logger():
    return logging.getLogger("test.full_stack")


@pytest.fixture
async def full_stack_http(full_stack_http_config, full_stack_http_logger):
    """
    Start full stack: DenonProxyServer (VirtualAVR) + HTTP JSON API + run_discovery_servers.
    Yields (proxy, ssdp_transport, http_servers). Teardown closes all.
    """
    runtime_state = RuntimeState()
    proxy = DenonProxyServer(full_stack_http_config, full_stack_http_logger, create_avr_connection, runtime_state)
    await proxy.start()
    ssdp_transport, discovery_http_servers = None, None
    try:
        ssdp_transport, discovery_http_servers = await run_discovery_servers(
            full_stack_http_config, full_stack_http_logger, proxy.avr_state, runtime_state
        )
    except Exception:
        pass
    yield proxy, ssdp_transport, discovery_http_servers
    if ssdp_transport:
        ssdp_transport.close()
    if discovery_http_servers:
        for srv in discovery_http_servers:
            srv.close()
        for srv in discovery_http_servers:
            await asyncio.wait_for(srv.wait_closed(), timeout=2.0)
    await proxy.stop()
