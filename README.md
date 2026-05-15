# mcp-abogadoenquilmes

Servidor MCP remoto que expone:

- **Jurisprudencia argentina** (285K+ fallos: SAIJ, CSJN, JUBA, SCBA, PJN) vía la API en `juris.automaticialab.com`.
- **Causas judiciales propias** del estudio vía `causas.abogadoenquilmes.com/api/causas/mcp-list` (requiere endpoint con service-token desplegado en `siteai-app`).

Transport: **Streamable HTTP**. Auth: `Authorization: Bearer $MCP_API_KEY`.

## Endpoint MCP

`POST https://mcp.abogadoenquilmes.com/mcp`

## Tools

| Tool | Descripción |
| --- | --- |
| `search_jurisprudence` | Full-text search con filtros (jurisdicción, materia, tribunal, fecha, voces, fuente) |
| `get_fallo` | Trae un fallo por id con texto completo |
| `jurisprudence_stats` | Stats globales de la base |
| `list_voces` | Autocomplete de descriptores temáticos |
| `list_jurisprudence_filters` | Valores distintos para todos los filtros |
| `list_my_cases` | Causas activas del usuario |
| `get_my_case` | Detalle + movimientos de una causa |

## Registrarlo en Claude Code

En cualquier PC:

```bash
claude mcp add --transport http abogadoenquilmes https://mcp.abogadoenquilmes.com/mcp \
  --header "Authorization: Bearer TU_MCP_API_KEY"
```

## Run local

```bash
cp .env.example .env
# editar .env con MCP_API_KEY real
pip install -r requirements.txt
python server.py
```

Healthcheck (sin auth): `GET http://localhost:8000/health`
