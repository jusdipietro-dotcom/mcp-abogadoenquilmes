"""MCP server for abogadoenquilmes.

Exposes two surfaces over a single Streamable HTTP MCP endpoint:

1. Jurisprudence — wraps the JurisArgentina FastAPI at juris.automaticialab.com.
   Lets Claude search 285K+ Argentine court rulings with full text.

2. Causas — wraps the siteai-app Next.js portal at causas.abogadoenquilmes.com
   via a service-token endpoint (`/api/causas/mcp-list`). Lets Claude inspect
   the user's own active court cases.

Auth: clients must send `Authorization: Bearer $MCP_API_KEY`. The same token
works for every PC that registers this server with Claude Code.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mcp-abogadoenquilmes")

MCP_API_KEY = os.environ.get("MCP_API_KEY", "").strip()
JURIS_API_BASE = os.environ.get("JURIS_API_BASE", "https://juris.automaticialab.com").rstrip("/")
CAUSAS_API_BASE = os.environ.get("CAUSAS_API_BASE", "https://causas.abogadoenquilmes.com").rstrip("/")
CAUSAS_SERVICE_TOKEN = os.environ.get("CAUSAS_SERVICE_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "8000"))

if not MCP_API_KEY:
    raise SystemExit("MCP_API_KEY env var is required")


mcp = FastMCP(
    "abogadoenquilmes",
    instructions=(
        "Servidor MCP del estudio Di Pietro. Expone búsqueda de jurisprudencia "
        "argentina (285K+ fallos: SAIJ, CSJN, JUBA, SCBA, PJN) y las causas "
        "judiciales activas del usuario en MEV/SCBA. Usá search_jurisprudence "
        "para investigación legal y list_my_cases para revisar el estado de "
        "expedientes propios."
    ),
)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_juris_client: httpx.AsyncClient | None = None
_causas_client: httpx.AsyncClient | None = None


def _juris() -> httpx.AsyncClient:
    global _juris_client
    if _juris_client is None:
        _juris_client = httpx.AsyncClient(
            base_url=JURIS_API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "mcp-abogadoenquilmes/1.0"},
        )
    return _juris_client


def _causas() -> httpx.AsyncClient:
    global _causas_client
    if _causas_client is None:
        headers = {"User-Agent": "mcp-abogadoenquilmes/1.0"}
        if CAUSAS_SERVICE_TOKEN:
            headers["Authorization"] = f"Bearer {CAUSAS_SERVICE_TOKEN}"
        _causas_client = httpx.AsyncClient(
            base_url=CAUSAS_API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers=headers,
        )
    return _causas_client


async def _get_json(client: httpx.AsyncClient, path: str, params: dict | None = None) -> Any:
    try:
        resp = await client.get(path, params=params)
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error calling {path}: {e}") from e
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Upstream {resp.status_code} from {path}: {resp.text[:300]}"
        )
    return resp.json()


# ---------------------------------------------------------------------------
# Jurisprudence tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_jurisprudence(
    query: str | None = None,
    jurisdiccion: str | None = None,
    materia: str | None = None,
    tribunal: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    voces: str | None = None,
    fuente: str | None = None,
    page: int = 1,
    per_page: int = 20,
    sort: str = "relevance",
) -> dict:
    """Busca en la base de jurisprudencia argentina (285K+ fallos con texto completo).

    Combina full-text search en castellano (con stemming + acentos) con filtros
    estructurados. Devuelve resultados paginados + facetas por jurisdicción/materia/fuente.

    Args:
        query: texto a buscar. Soporta comillas para frase exacta, OR, -excluir.
            Ej: '"despido injustificado" indemnización -accidente'.
        jurisdiccion: Nacional, Federal, Provincial, Local, Internacional.
        materia: Civil y Comercial, Penal, Laboral, etc. (15 categorías).
        tribunal: match parcial (ILIKE). Ej: "Cámara Civil Quilmes".
        fecha_desde: YYYY-MM-DD.
        fecha_hasta: YYYY-MM-DD.
        voces: descriptor exacto (ej: "DAÑO MORAL").
        fuente: saij, csjn, juba, pjn, sanjuan.
        page: 1-indexed.
        per_page: 1-100, default 20.
        sort: relevance | fecha_desc | fecha_asc. Si no hay query, ignora relevance.

    Returns:
        { total, page, per_page, pages, results: [{id, fuente, tribunal, jurisdiccion,
        materia, fecha, caratula, actor, demandado, voces, url_original, sumario,
        relevance}], filters: { jurisdicciones, materias, fuentes } }
    """
    params: dict[str, Any] = {"page": page, "per_page": per_page, "sort": sort}
    if query:
        params["q"] = query
    for k, v in {
        "jurisdiccion": jurisdiccion,
        "materia": materia,
        "tribunal": tribunal,
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "voces": voces,
        "fuente": fuente,
    }.items():
        if v:
            params[k] = v
    return await _get_json(_juris(), "/api/search", params)


@mcp.tool()
async def get_fallo(fallo_id: int) -> dict:
    """Trae un fallo individual con texto completo (descarga on-demand de SAIJ si falta).

    Args:
        fallo_id: ID numérico devuelto por search_jurisprudence (campo `id`).

    Returns:
        Documento completo con texto_completo, sumario, voces, magistrados, etc.
    """
    return await _get_json(_juris(), f"/api/fallo/{fallo_id}")


@mcp.tool()
async def jurisprudence_stats() -> dict:
    """Estadísticas globales de la base de jurisprudencia.

    Returns:
        { total, by_fuente, by_jurisdiccion, by_materia, fecha_min, fecha_max }
    """
    return await _get_json(_juris(), "/api/stats")


@mcp.tool()
async def list_voces(query: str, limit: int = 20) -> dict:
    """Autocompleta voces/descriptores temáticos (ej: 'DAÑO', 'DESPIDO').

    Útil para encontrar el descriptor canónico antes de filtrar con search_jurisprudence.

    Args:
        query: prefijo o substring (min 2 chars).
        limit: cantidad de matches a devolver.

    Returns:
        { results: [{ voz, count }] }
    """
    return await _get_json(_juris(), "/api/voces", {"q": query, "limit": limit})


@mcp.tool()
async def list_jurisprudence_filters() -> dict:
    """Lista todos los valores distintos disponibles para los filtros de search.

    Returns:
        { jurisdicciones, materias, fuentes, tribunales (top), departamentos }
    """
    return await _get_json(_juris(), "/api/filters")


# ---------------------------------------------------------------------------
# Causas tools (siteai-app /api/causas/mcp-list)
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_my_cases(
    search: str | None = None,
    estado: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict:
    """Lista las causas judiciales propias (suscripción activa en causas.abogadoenquilmes).

    Cada causa incluye carátula, expediente, juzgado, estado y los últimos movimientos.
    Requiere que siteai-app exponga /api/causas/mcp-list con service-token.

    Args:
        search: match parcial en caratula/nroExpediente/courtName.
        estado: "En Letra", "A Despacho", etc.
        page: 1-indexed.
        limit: 1-100.

    Returns:
        { cases: [{ id, caratula, nroExpediente, estado, courtName, fechaInicio,
        totalMovimientos, scrapedAt, movimientos }], total, page, limit, totalPages,
        lastScrapeAt, estados }
    """
    if not CAUSAS_SERVICE_TOKEN:
        raise RuntimeError(
            "CAUSAS_SERVICE_TOKEN no configurado. Endpoint /api/causas/mcp-list "
            "todavía no está deployado en siteai-app."
        )
    params: dict[str, Any] = {"page": page, "limit": limit}
    if search:
        params["search"] = search
    if estado:
        params["estado"] = estado
    return await _get_json(_causas(), "/api/causas/mcp-list", params)


@mcp.tool()
async def get_my_case(case_id: str) -> dict:
    """Trae una causa propia por id con todos sus movimientos.

    Args:
        case_id: cuid devuelto por list_my_cases (campo `id`).

    Returns:
        Causa completa con movimientos parseados (array de objetos con fecha,
        descripcion, tipo).
    """
    if not CAUSAS_SERVICE_TOKEN:
        raise RuntimeError(
            "CAUSAS_SERVICE_TOKEN no configurado. Endpoint /api/causas/mcp-list "
            "todavía no está deployado en siteai-app."
        )
    return await _get_json(_causas(), f"/api/causas/mcp-list/{case_id}")


# ---------------------------------------------------------------------------
# Auth + ASGI app
# ---------------------------------------------------------------------------

class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Block any request that doesn't carry the right Bearer token.

    Exempts /health so EasyPanel / Traefik can probe the container.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        if auth[len("Bearer "):].strip() != MCP_API_KEY:
            return JSONResponse({"error": "invalid bearer token"}, status_code=401)
        return await call_next(request)


def build_app():
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware)

    async def health(_request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "name": "mcp-abogadoenquilmes",
                "juris_base": JURIS_API_BASE,
                "causas_base": CAUSAS_API_BASE,
                "causas_token_configured": bool(CAUSAS_SERVICE_TOKEN),
            }
        )

    async def root(_request: Request) -> Response:
        return JSONResponse(
            {
                "name": "mcp-abogadoenquilmes",
                "mcp_endpoint": "/mcp",
                "transport": "streamable-http",
                "auth": "Bearer token in Authorization header",
            }
        )

    # add unauthenticated info routes so the container is debuggable from a browser
    from starlette.routing import Route

    app.router.routes.append(Route("/health", health, methods=["GET"]))
    app.router.routes.append(Route("/", root, methods=["GET"]))
    return app


app = build_app()


if __name__ == "__main__":
    import uvicorn

    logger.info("starting MCP server on :%d  juris=%s  causas=%s",
                PORT, JURIS_API_BASE, CAUSAS_API_BASE)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
