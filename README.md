# MCP Tino — Études de faisabilité géothermique

Serveur MCP qui collecte des données hydrogéologiques publiques (HubEau, BRGM, Géorisques) et génère des rapports de faisabilité pour des projets de géothermie de minime importance (GMI).

## Prérequis

- [Claude Desktop](https://claude.ai/download)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — gestionnaire Python

```powershell
winget install astral-sh.uv
```

## Installation

1. Télécharger `server.py` et `pyproject.toml` dans un dossier, par exemple `C:\mcp-tino`

2. Installer les dépendances :

```powershell
cd C:\mcp-tino
uv sync
```

3. Ouvrir le fichier de configuration de Claude Desktop :

```
%APPDATA%\Claude\claude_desktop_config.json
```

4. Ajouter le serveur MCP (adapter le chemin) :

```json
{
  "mcpServers": {
    "mcp-tino": {
      "command": "uv",
      "args": ["--directory", "C:\\mcp-tino", "run", "python", "server.py"]
    }
  }
}
```

5. Redémarrer Claude Desktop.

## Utilisation

Dans Claude Desktop, demander simplement :

> *Fais une étude de faisabilité géothermique pour les coordonnées 48.8566, 2.3522*

Claude collecte les données, évalue la technique recommandée (nappe ou sonde géothermique), rédige le rapport et le sauvegarde en **Word (.docx)** dans le dossier `C:\mcp-tino`. Aucune dépendance système supplémentaire n'est requise.
