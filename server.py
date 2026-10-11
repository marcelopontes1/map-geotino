"""
MCP Tino — Collecte de données hydrogéologiques pour études de faisabilité géothermique.
Architecture : les outils collectent des données structurées ; Claude rédige le rapport professionnel.
"""
import asyncio
import math
import httpx
from pathlib import Path
from mcp.server.mcpserver import MCPServer

app = MCPServer("mcp-tino")

BRGM_WFS     = "https://geoservices.brgm.fr/geologie"
HUBEAU_PIE   = "https://hubeau.eaufrance.fr/api/v1/niveaux_nappes"
HUBEAU_HYDRO = "https://hubeau.eaufrance.fr/api/v2/hydrometrie/referentiel/stations"
HUBEAU_QUAL  = "https://hubeau.eaufrance.fr/api/v1/qualite_nappes/analyses"
GEORISQUES   = "https://georisques.gouv.fr/api/v1"


def _bbox(lat: float, lon: float, r: float) -> str:
    d = r / 111.0
    return f"{lon - d},{lat - d},{lon + d},{lat + d}"


# ── 1. Aquifères et forages BSS (≤ 200 m) ────────────────────────────────────

@app.tool()
async def query_aquifers(latitude: float, longitude: float, radius_km: float = 5) -> str:
    """Recense tous les ouvrages BSS dans un rayon donné (profondeur ≤ 200 m).
    Retourne un tableau détaillé par ouvrage avec formation BDLISA, nature, profondeur,
    altitude et nombre de mesures piézométriques. Données brutes pour analyse experte."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(BRGM_WFS, params={
            "SERVICE": "WFS", "VERSION": "1.0.0", "REQUEST": "GetFeature",
            "typeName": "ms:BSS_EAU_POINT", "BBOX": _bbox(latitude, longitude, radius_km),
            "outputFormat": "GeoJSON", "maxFeatures": 50,
        })
        r.raise_for_status()

    features = r.json().get("features", [])
    if not features:
        return "Aucun ouvrage BSS trouvé dans ce rayon."

    wells = []
    for f in features:
        p = f["properties"]
        try:
            depth = float(p.get("prof_invest") or 999)
        except (TypeError, ValueError):
            depth = 999
        if depth <= 200:
            wells.append(p)

    if not wells:
        return "Aucun ouvrage BSS dans les 200 premiers mètres de profondeur."

    lines = [
        f"## Ouvrages BSS recensés — {len(wells)} ouvrages (rayon {radius_km} km, prof. ≤ 200 m)\n",
        "| Code BSS | Nature | Commune | Prof. (m) | Alt. (m NGF) | Formation BDLISA | Aquifère | Mesures piézo | Lien ADES |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for w in wells:
        lien = f"[ADES]({w['lien_bsseau']})" if w.get("lien_bsseau") else "—"
        lines.append(
            f"| {w.get('code_bss','?')} | {w.get('nature_pe','?')} | {w.get('commune_actuelle','?')} "
            f"| {w.get('prof_invest','?')} | {w.get('altitude','?')} "
            f"| {w.get('liste_bdlisa') or '—'} | {w.get('carac_aquifere','?')} "
            f"| {w.get('nb_mesures_piezo','0')} | {lien} |"
        )
    lines.append(f"\n*Source : Banque du Sous-Sol BRGM — {len(features)} ouvrages totaux dans le rayon, dont {len(wells)} ≤ 200 m.*")
    return "\n".join(lines)


# ── 2. Piézométrie + sens d'écoulement ───────────────────────────────────────

@app.tool()
async def query_piezometry(latitude: float, longitude: float, radius_km: float = 5) -> str:
    """Derniers niveaux piézométriques des stations HubEau dans le rayon.
    Calcule le niveau en m NGF (altitude terrain − profondeur nappe) et estime
    le sens d'écoulement par gradient vectoriel si ≥ 3 stations disponibles."""
    async with httpx.AsyncClient(timeout=30) as c:
        rs = await c.get(f"{HUBEAU_PIE}/stations", params={
            "bbox": _bbox(latitude, longitude, radius_km), "format": "json", "size": 20,
        })
        rs.raise_for_status()
        stations = [s for s in rs.json().get("data", []) if s.get("nb_mesures_piezo", 0) > 0]

        if not stations:
            return "Aucune station piézométrique avec mesures dans ce rayon."

        points: list[dict] = []
        tasks = [
            c.get(f"{HUBEAU_PIE}/chroniques", params={"code_bss": s["code_bss"], "size": 1, "sort": "desc"})
            for s in stations[:10]
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for s, resp in zip(stations[:10], responses):
            if isinstance(resp, Exception) or not resp.is_success:
                continue
            data = resp.json().get("data", [])
            if not data:
                continue
            m = data[0]
            alt = float(s.get("altitude_station") or 0)
            depth = float(m.get("profondeur_nappe") or 0)
            coords = s.get("geometry", {}).get("coordinates", [])
            points.append({
                "code": s["code_bss"],
                "commune": s.get("nom_commune", "?"),
                "lon": float(coords[0]) if coords else float(s.get("x", 0)),
                "lat": float(coords[1]) if coords else float(s.get("y", 0)),
                "altitude": alt,
                "profondeur": depth,
                "niveau_ngf": round(alt - depth, 2),
                "date": m.get("date_mesure", "?"),
            })

    if not points:
        return "Mesures piézométriques indisponibles pour les stations de ce rayon."

    lines = [
        "## Piézométrie de la nappe\n",
        "| Station | Commune | Date mesure | Alt. terrain (m NGF) | Prof. nappe (m) | Niveau piézo (m NGF) |",
        "|---|---|---|---|---|---|",
    ]
    for p in points:
        lines.append(
            f"| {p['code']} | {p['commune']} | {p['date']} "
            f"| {p['altitude']} | {p['profondeur']} | **{p['niveau_ngf']}** |"
        )

    if len(points) >= 3:
        direction = _estimate_flow_direction(points)
        lines.append(f"\n**Sens d'écoulement estimé :** {direction}")
        niveaux = [p["niveau_ngf"] for p in points]
        lines.append(f"**Variation piézométrique :** {min(niveaux):.2f} – {max(niveaux):.2f} m NGF "
                     f"(amplitude {max(niveaux) - min(niveaux):.2f} m)")
    else:
        lines.append(f"\n*Sens d'écoulement : nombre de stations insuffisant (< 3 avec mesures). "
                     f"Consulter les cartes piézométriques SIGES ou BRGM.*")

    lines.append(f"\n*Source : HubEau — données piézométriques en temps réel.*")
    return "\n".join(lines)


def _estimate_flow_direction(points: list[dict]) -> str:
    n = len(points)
    lons = [p["lon"] for p in points]
    lats = [p["lat"] for p in points]
    niveaux = [p["niveau_ngf"] for p in points]
    mean_lon, mean_lat, mean_niv = sum(lons)/n, sum(lats)/n, sum(niveaux)/n
    sxx = sum((x - mean_lon)**2 for x in lons)
    syy = sum((y - mean_lat)**2 for y in lats)
    sxy = sum((lons[i] - mean_lon) * (lats[i] - mean_lat) for i in range(n))
    sxz = sum((lons[i] - mean_lon) * (niveaux[i] - mean_niv) for i in range(n))
    syz = sum((lats[i] - mean_lat) * (niveaux[i] - mean_niv) for i in range(n))
    det = sxx * syy - sxy**2
    if abs(det) < 1e-12:
        return "Indéterminé (stations colinéaires)"
    dzdx = (syy * sxz - sxy * syz) / det
    dzdy = (sxx * syz - sxy * sxz) / det
    angle = math.degrees(math.atan2(-dzdx, -dzdy)) % 360
    cardinal = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"][round(angle / 45) % 8]
    return f"**{cardinal}** (azimut ~{angle:.0f}°)"


# ── 3. Géologie ───────────────────────────────────────────────────────────────

@app.tool()
async def query_geology(latitude: float, longitude: float, radius_km: float = 5) -> str:
    """Lithologie de surface (BRGM 1/1M) et coupe stratigraphique estimée à partir
    des forages BSS voisins (profondeur, formation). Inclut lien carte géologique 1/50 000."""
    async with httpx.AsyncClient(timeout=30) as c:
        r_litho, r_bss = await asyncio.gather(
            c.get(BRGM_WFS, params={
                "SERVICE": "WFS", "VERSION": "1.0.0", "REQUEST": "GetFeature",
                "typeName": "ms:LITHO_1M_SIMPLIFIEE",
                "BBOX": f"{longitude-0.05},{latitude-0.05},{longitude+0.05},{latitude+0.05}",
                "outputFormat": "GeoJSON", "maxFeatures": 5,
            }),
            c.get(BRGM_WFS, params={
                "SERVICE": "WFS", "VERSION": "1.0.0", "REQUEST": "GetFeature",
                "typeName": "ms:BSS_EAU_POINT", "BBOX": _bbox(latitude, longitude, radius_km),
                "outputFormat": "GeoJSON", "maxFeatures": 20,
            }),
        )

    lines = ["## Contexte géologique\n"]

    litho_feats = r_litho.json().get("features", []) if r_litho.is_success else []
    if litho_feats:
        lines.append("### Lithologie simplifiée BRGM (1/1 000 000)\n")
        seen: set[str] = set()
        for f in litho_feats:
            p = f["properties"]
            d = p.get("DESCR", "?")
            if d not in seen:
                seen.add(d)
                lines.append(f"- **{d}** ({p.get('TYPE','?')})")

    bss_feats = r_bss.json().get("features", []) if r_bss.is_success else []
    depth_sorted = sorted(
        [f["properties"] for f in bss_feats if f["properties"].get("prof_invest")],
        key=lambda p: float(p["prof_invest"] or 0), reverse=True,
    )
    if depth_sorted:
        lines.append("\n### Profils de forages BSS voisins — proxy stratigraphique\n")
        lines.append("| Code BSS | Commune | Prof. totale (m) | Formation BDLISA |")
        lines.append("|---|---|---|---|")
        for w in depth_sorted[:10]:
            lines.append(
                f"| {w.get('code_bss','?')} | {w.get('commune_actuelle','?')} "
                f"| {w.get('prof_invest','?')} | {w.get('liste_bdlisa') or '—'} |"
            )

    brgm_url = f"https://infoterre.brgm.fr/viewer/MainTileForward.do?&x={longitude}&y={latitude}&scale=50000"
    lines.append(f"\n### Carte géologique BRGM 1/50 000\n→ {brgm_url}")
    lines.append("*(Légende, échelle et orientation disponibles sur InfoTerre)*")
    return "\n".join(lines)


# ── 4. Qualité des eaux souterraines ─────────────────────────────────────────

@app.tool()
async def query_water_quality(latitude: float, longitude: float, radius_km: float = 5) -> str:
    """Qualité physico-chimique des eaux souterraines (HubEau) pour les paramètres clés
    en géothermie : fer, manganèse, pH, conductivité, calcium, magnésium.
    Crucial pour évaluer les risques de colmatage et d'entartrage."""
    # Paramètres clés géothermie (codes SANDRE vérifiés expérimentalement)
    params_cibles = {
        "1301": "Température (°C)",
        "1302": "pH",
        "1303": "Conductivité à 25°C (µS/cm)",
        "1337": "Chlorures (mg/L Cl)",
        "1338": "Sulfates (mg/L SO₄)",
        "1340": "Nitrates (mg/L NO₃)",
        "1372": "Magnésium (mg/L Mg)",
        "1374": "Calcium (mg/L Ca)",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        tasks = [
            c.get(HUBEAU_QUAL, params={
                "bbox": _bbox(latitude, longitude, radius_km),
                "code_param": code, "size": 3, "sort": "desc",
            })
            for code in params_cibles
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    results: dict[str, list] = {}
    for code, resp in zip(params_cibles, responses):
        if isinstance(resp, Exception) or not resp.is_success:
            continue
        data = resp.json().get("data", [])
        if data:
            results[code] = data

    if not results:
        return "Aucune analyse physico-chimique disponible dans ce rayon. Analyses in-situ nécessaires lors du forage."

    lines = [
        "## Qualité des eaux souterraines\n",
        "| Paramètre | Valeur | Unité | Station BSS | Date |",
        "|---|---|---|---|---|",
    ]
    for code, data in results.items():
        nom = params_cibles[code]
        for d in data[:1]:
            lines.append(
                f"| {nom} | **{d.get('resultat','?')}** | {d.get('symbole_unite','?')} "
                f"| {d.get('code_bss','?')} | {d.get('date_debut_prelevement','?')[:10] if d.get('date_debut_prelevement') else '?'} |"
            )

    lines.append(
        "\n**Seuils critiques pour la géothermie sur nappe :**\n"
        "- pH < 7,0 → eaux agressives, précautions sur les matériaux du doublet\n"
        "- Conductivité > 1 500 µS/cm → eaux chargées en sels, risque de corrosion\n"
        "- Calcium + Magnésium → TH calculable (TH > 30 °f = risque entartrage échangeurs)\n"
        "- Sulfates > 250 mg/L → risque d'entartrage et attaque du béton de cimentation\n"
        "- Nitrates > 50 mg/L → influence anthropique possible sur la nappe\n"
        "- Chlorures > 200 mg/L → eaux corrosives pour les aciers\n"
        "- **Note :** Fer et Manganèse (colmatage) non disponibles via HubEau → analyses in-situ obligatoires lors du forage"
    )
    lines.append("\n*Source : HubEau — Qualité des eaux souterraines (BRGM/Agences de l'eau).*")
    return "\n".join(lines)


# ── 5. Réglementation et risques ──────────────────────────────────────────────

@app.tool()
async def query_regulations_and_risks(latitude: float, longitude: float, radius_km: float = 5) -> str:
    """Risques naturels GASPAR, cavités, mouvements de terrain et sites industriels ICPE
    au droit du site. Inclut le cadre réglementaire GMI complet avec références décrets/arrêtés."""
    async with httpx.AsyncClient(timeout=30) as c:
        rayon_m = int(radius_km * 1000)
        r_gaspar, r_cavites, r_mvt, r_icpe = await asyncio.gather(
            c.get(f"{GEORISQUES}/gaspar/risques", params={"latlon": f"{longitude},{latitude}", "rayon": rayon_m}),
            c.get(f"{GEORISQUES}/cavites", params={"latlon": f"{longitude},{latitude}", "rayon": rayon_m}),
            c.get(f"{GEORISQUES}/mvt", params={"latlon": f"{longitude},{latitude}", "rayon": rayon_m}),
            c.get(f"{GEORISQUES}/installations_classees", params={"latlon": f"{longitude},{latitude}", "rayon": min(rayon_m, 2000), "page": 1, "page_size": 10}),
        )

    lines = ["## Contexte réglementaire et risques\n"]

    # GASPAR
    lines.append("### Risques naturels et technologiques (GASPAR)\n")
    geothermal_relevant = {"11", "116", "12", "121", "13"}
    if r_gaspar.is_success:
        all_risks: dict[str, str] = {}
        for commune in r_gaspar.json().get("data", []):
            for risk in commune.get("risques", []):
                all_risks[risk["code"]] = risk["libelle"]
        if all_risks:
            lines.append("| Code | Risque | Impact géothermie |")
            lines.append("|---|---|---|")
            for code, libelle in sorted(all_risks.items()):
                flag = "⚠️ Pertinent" if code in geothermal_relevant else "—"
                lines.append(f"| {code} | {libelle} | {flag} |")
        else:
            lines.append("*Aucun risque GASPAR recensé dans ce rayon.*")

    # Cavités
    lines.append("\n### Cavités souterraines\n")
    if r_cavites.is_success:
        cavites = r_cavites.json().get("data", [])
        if cavites:
            lines.append(f"⚠️ **{len(cavites)} cavité(s)** recensée(s) dans {radius_km} km :")
            for cv in cavites[:5]:
                dept = cv.get("departement", {})
                dept_name = dept.get("nom", "?") if isinstance(dept, dict) else str(dept)
                nom = cv.get("nom") or cv.get("identifiant", "?")
                lines.append(f"- **{nom}** ({cv.get('type','?')}) — {dept_name}")
            lines.append("→ Vérifier les zones d'exclusion de forage (arrêté du 25/06/2015).")
        else:
            lines.append("✅ Aucune cavité souterraine recensée.")

    # MVT
    lines.append("\n### Mouvements de terrain\n")
    if r_mvt.is_success:
        total = r_mvt.json().get("results", 0)
        if total > 0:
            types: dict[str, int] = {}
            for m in r_mvt.json().get("data", []):
                t = m.get("type", "Autre")
                types[t] = types.get(t, 0) + 1
            lines.append(f"⚠️ **{total} mouvement(s) de terrain** recensé(s) :")
            for t, n in sorted(types.items(), key=lambda x: -x[1]):
                lines.append(f"- {t} : {n}")
        else:
            lines.append("✅ Aucun mouvement de terrain recensé.")

    # ICPE / Sites industriels
    lines.append("\n### Sites industriels ICPE\n")
    if r_icpe.is_success:
        icpe = r_icpe.json().get("data", [])
        actifs = [s for s in icpe if s.get("etatActivite") not in ("Cessation déclarée", "Fermé")]
        if icpe:
            lines.append(f"**{len(icpe)} installation(s) classée(s)** dans 2 km ({len(actifs)} active(s)) :")
            lines.append("| Établissement | Commune | État | Seveso |")
            lines.append("|---|---|---|---|")
            for s in icpe[:5]:
                seveso = "⚠️ Oui" if s.get("statutSeveso") else "Non"
                lines.append(f"| {s.get('raisonSociale','?')} | {s.get('commune','?')} | {s.get('etatActivite','?')} | {seveso} |")
        else:
            lines.append("Aucune installation classée recensée dans un rayon de 2 km.")

    # Cadre réglementaire GMI (textes fixes)
    lines.append("""
### Cadre réglementaire — Géothermie de Minime Importance (GMI)

**Textes de référence :**
- Décret n° 78-498 du 28/03/1978 relatif aux titres de recherches et d'exploitation de géothermie
- Décret n° 2006-649 du 02/06/2006 relatif aux travaux miniers
- Décret n° 2015-15 du 08/01/2015 modifiant le décret 78-498
- Arrêté du 25/06/2015 relatif aux prescriptions générales applicables aux activités géothermiques de minime importance
- Arrêté du 25/06/2015 relatif à la qualification des entreprises de forages
- Arrêté du 25/06/2015 relatif à l'agrément d'expert en matière de GMI
- Décret n° 2016-835 du 24/06/2016 relatif à l'obligation d'assurance (garantie Aquapac)

**Critères GMI (art. 3-II du Code Minier) :**
- Profondeur < 200 m
- Débit calorifique < 500 kW
- Température de rejet < 25 °C
- Réinjection totale dans le même aquifère
- Débits prélevés/injectés < 80 m³/h

**Distances réglementaires minimales (arrêté du 25/06/2015) :**
- > 200 m d'une décharge ou installation de stockage de déchets
- > 35 m d'un réseau d'eaux usées ou d'un captage AEP
- > 35 m de zones de stockage d'hydrocarbures ou produits chimiques
- > 5 m de la limite de propriété la plus proche

**Portails de vérification réglementaire :**
| Document | Lien |
|---|---|
| SDAGE / SAGE | https://www.gesteau.fr/sage |
| Natura 2000 | https://inpn.mnhn.fr/programme/natura2000/recherche |
| ZNIEFF | https://inpn.mnhn.fr/zone/znieff/recherche |
| Captages AEP et périmètres de protection | https://www.georisques.gouv.fr |
| Zone GMI (verte/orange/rouge) | https://www.geothermies.fr/carte-gmi |
| Piézométrie interactive | https://www.siges.fr |""")

    return "\n".join(lines)


# ── 6. Hydrologie ─────────────────────────────────────────────────────────────

@app.tool()
async def query_hydrology(latitude: float, longitude: float, radius_km: float = 10) -> str:
    """Cours d'eau à proximité du site via HubEau hydrométrie.
    Identifie les exutoires naturels de la nappe et les interactions nappe-rivière."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(HUBEAU_HYDRO, params={
            "longitude": longitude, "latitude": latitude,
            "distance": int(radius_km), "format": "json", "size": 10,
        })
        r.raise_for_status()
        stations = r.json().get("data", [])

    if not stations:
        return f"Aucune station hydrométrique dans un rayon de {radius_km} km."

    rivers: dict[str, list] = {}
    for s in stations:
        name = s.get("libelle_cours_eau") or "Cours d'eau inconnu"
        rivers.setdefault(name, []).append(s)

    lines = [
        f"## Hydrologie du secteur (rayon {radius_km} km)\n",
        f"**{len(rivers)} cours d'eau** identifiés — **{len(stations)} stations** hydrométriques.\n",
        "| Cours d'eau | Station | Commune | En service | Alt. (m NGF) |",
        "|---|---|---|---|---|",
    ]
    for river, sts in sorted(rivers.items()):
        for s in sts:
            en_service = "✅" if s.get("en_service") else "❌"
            alt = s.get("altitude_ref_alti_station") or "—"
            lines.append(f"| **{river}** | {s.get('libelle_station','?')} | {s.get('libelle_commune','?')} | {en_service} | {alt} |")

    lines.append(
        "\n*Les cours d'eau représentent les exutoires naturels des nappes. "
        "Les échanges nappe-rivière (nappes alluviales notamment) influencent directement "
        "le sens d'écoulement, la piézométrie et la qualité des eaux.*"
    )
    lines.append("\n*Source : HubEau Hydrométrie.*")
    return "\n".join(lines)


# ── 7. Collecte globale (données brutes pour rédaction par Claude) ────────────

@app.tool()
async def collect_site_data(latitude: float, longitude: float, radius_km: float = 5) -> str:
    """Collecte toutes les données du site en parallèle (forages BSS, piézométrie,
    géologie, qualité des eaux, risques, hydrologie) et les retourne sous forme structurée.
    À utiliser comme base pour rédiger un rapport hydrogéologique professionnel."""
    async with httpx.AsyncClient(timeout=60) as _:
        aquifers, piezo, geology, quality, risks, hydro = await asyncio.gather(
            _safe(query_aquifers(latitude, longitude, radius_km)),
            _safe(query_piezometry(latitude, longitude, radius_km)),
            _safe(query_geology(latitude, longitude, radius_km)),
            _safe(query_water_quality(latitude, longitude, radius_km)),
            _safe(query_regulations_and_risks(latitude, longitude, radius_km)),
            _safe(query_hydrology(latitude, longitude, radius_km + 5)),
        )

    import datetime
    today = datetime.date.today().isoformat()
    return f"""# Données brutes — Site {latitude}°N {longitude}°E — {today}

> Ces données sont collectées automatiquement depuis HubEau, BRGM et Géorisques.
> Elles constituent la base documentaire pour la rédaction d'un rapport hydrogéologique professionnel.

---

{aquifers}

---

{piezo}

---

{geology}

---

{quality}

---

{risks}

---

{hydro}
"""


async def _safe(coro) -> str:
    try:
        return await coro
    except Exception as e:
        return f"*Erreur lors de la collecte : {e}*"


# ── 8. Recommandation technique nappe vs sonde ────────────────────────────────

@app.tool()
async def evaluate_technique(
    ph: float,
    conductivity_us: float,
    calcium_mg: float,
    magnesium_mg: float,
    aquifer_wells_count: int,
    min_well_depth_m: float,
    geology_dominant: str,
    cavities_count: int,
    terrain_movements_count: int,
) -> str:
    """Évalue la technique géothermique recommandée (nappe ou sonde) à partir des données collectées.

    Extraire ces valeurs depuis collect_site_data avant d'appeler cet outil :
    - ph : code SANDRE 1302
    - conductivity_us : code SANDRE 1303 (µS/cm)
    - calcium_mg : code SANDRE 1374 (mg/L)
    - magnesium_mg : code SANDRE 1372 (mg/L)
    - aquifer_wells_count : nombre d'ouvrages BSS recensés
    - min_well_depth_m : profondeur minimale des ouvrages BSS (proxy nappe superficielle)
    - geology_dominant : type dominant ex. "alluvial", "metamorphique", "sedimentaire", "cristallin"
    - cavities_count : nombre de cavités souterraines recensées
    - terrain_movements_count : nombre de mouvements de terrain recensés
    """
    th_f = (calcium_mg / 20.04 + magnesium_mg / 12.16) * 5

    score_nappe = 0
    score_sonde = 0
    points_nappe: list[str] = []
    points_sonde: list[str] = []
    vigilance: list[str] = []

    # Aquifère
    if aquifer_wells_count >= 5:
        score_nappe += 3
        points_nappe.append(f"Aquifère bien documenté ({aquifer_wells_count} ouvrages BSS)")
    elif aquifer_wells_count >= 1:
        score_nappe += 1
        vigilance.append("Peu d'ouvrages BSS — productivité à confirmer par essai de pompage")
    else:
        score_sonde += 2
        points_sonde.append("Absence d'aquifère documenté → sonde privilégiée")

    # Profondeur nappe
    if min_well_depth_m <= 30:
        score_nappe += 2
        points_nappe.append(f"Nappe accessible (profondeur ~ {min_well_depth_m:.0f} m → forage économique)")
    elif min_well_depth_m <= 60:
        score_nappe += 1
        vigilance.append(f"Profondeur modérée ({min_well_depth_m:.0f} m) → coût de forage à évaluer")
    else:
        score_sonde += 1
        points_sonde.append(f"Nappe profonde ({min_well_depth_m:.0f} m) → sonde compétitive en coût")

    # pH
    if ph >= 7.0:
        score_nappe += 1
        points_nappe.append(f"pH neutre à basique ({ph:.2f}) — eau non agressive")
    elif ph >= 6.5:
        vigilance.append(f"pH légèrement acide ({ph:.2f}) → matériaux résistants requis (inox, PEHD)")
    else:
        score_sonde += 2
        points_sonde.append(f"pH acide ({ph:.2f}) → risque de corrosion sur doublet ouvert")

    # Dureté TH
    if th_f <= 20:
        score_nappe += 1
        points_nappe.append(f"Eau douce (TH = {th_f:.1f} °f) — faible risque d'entartrage")
    elif th_f <= 35:
        vigilance.append(f"Dureté modérée (TH = {th_f:.1f} °f) → traitement anti-tartre préventif recommandé")
    else:
        score_sonde += 1
        points_sonde.append(f"Eau dure (TH = {th_f:.1f} °f) → risque d'entartrage des échangeurs")

    # Conductivité
    if conductivity_us <= 800:
        score_nappe += 1
        points_nappe.append(f"Conductivité faible ({conductivity_us:.0f} µS/cm) — pas de risque de corrosion")
    elif conductivity_us <= 1500:
        vigilance.append(f"Conductivité moyenne ({conductivity_us:.0f} µS/cm) — surveiller la corrosion")
    else:
        score_sonde += 2
        points_sonde.append(f"Conductivité élevée ({conductivity_us:.0f} µS/cm) → risque de corrosion accéléré")

    # Géologie
    geo = geology_dominant.lower()
    if any(k in geo for k in ("alluvial", "sable", "gravier", "fz")):
        score_nappe += 2
        points_nappe.append("Géologie alluviale (sables/graviers) — bonne perméabilité, nappe productive")
    elif any(k in geo for k in ("metamorphi", "cristallin", "gneiss", "granite", "schiste")):
        score_sonde += 2
        points_sonde.append("Roche cristalline ou métamorphique — bonne conductivité thermique pour sondes verticales")
    elif any(k in geo for k in ("sedimentaire", "calcaire", "craie", "grès")):
        score_nappe += 1
        points_nappe.append("Géologie sédimentaire — aquifère possible selon la perméabilité")

    # Cavités
    if cavities_count == 0:
        score_nappe += 1
    elif cavities_count <= 2:
        vigilance.append(f"{cavities_count} cavité(s) — vérifier les zones d'exclusion de forage (arrêté 25/06/2015)")
    else:
        score_sonde += 1
        points_sonde.append(f"Nombreuses cavités ({cavities_count}) — risque pour les forages profonds")

    # Mouvements de terrain
    if terrain_movements_count > 20:
        vigilance.append(f"Mouvements de terrain importants ({terrain_movements_count} occurrences) — étude géotechnique renforcée recommandée")

    # Décision
    if score_nappe > score_sonde:
        technique = "NAPPE — doublet eau-eau"
        emoji = "💧"
        conclusion = (
            f"L'ensemble des données hydrogéologiques collectées oriente favorablement vers un système "
            f"de géothermie sur **nappe** (doublet eau-eau). L'aquifère identifié présente des caractéristiques "
            f"physico-chimiques compatibles avec l'exploitation géothermique : TH calculé à {th_f:.1f} °f, "
            f"conductivité de {conductivity_us:.0f} µS/cm. Un essai de pompage sur l'ouvrage pilote permettra "
            f"de confirmer les débits exploitables et de dimensionner le doublet."
        )
    elif score_sonde > score_nappe:
        technique = "SONDE — géothermique verticale (boucle fermée)"
        emoji = "🔄"
        conclusion = (
            f"Le contexte hydrogéologique et la qualité des eaux orientent vers des **sondes géothermiques "
            f"verticales** à boucle fermée. Ce système, indépendant de la qualité de l'eau souterraine, "
            f"supprime les contraintes liées au pH ({ph:.2f}) et à la dureté (TH = {th_f:.1f} °f), "
            f"et s'adapte mieux à la géologie identifiée. Le dimensionnement sera basé sur la conductivité "
            f"thermique du sous-sol, à déterminer par essai de réponse thermique (TRT)."
        )
    else:
        technique = "À CONFIRMER — données insuffisantes pour trancher"
        emoji = "⚖️"
        conclusion = (
            f"Les données disponibles ne permettent pas de trancher clairement entre les deux techniques. "
            f"Une étude de faisabilité approfondie (essai de pompage, diagraphie, TRT) est nécessaire "
            f"avant de statuer sur l'orientation technique."
        )

    lines = [
        f"## {emoji} Technique recommandée : {technique}\n",
        f"**Score nappe : {score_nappe} pts | Score sonde : {score_sonde} pts**\n",
        "### Éléments déterminants\n",
    ]
    for p in (points_nappe if score_nappe >= score_sonde else points_sonde):
        lines.append(f"- ✅ {p}")
    if vigilance:
        lines.append("\n### Points de vigilance\n")
        for v in vigilance:
            lines.append(f"- ⚠️ {v}")
    lines.append(f"\n### Conclusion\n{conclusion}")
    lines.append(f"\n*TH calculé : ({calcium_mg:.1f}/20,04 + {magnesium_mg:.1f}/12,16) × 5 = **{th_f:.1f} °f***")
    return "\n".join(lines)


# ── 9. Conversion Markdown → Word (.docx) ────────────────────────────────────

def _add_inline(para, text: str):
    """Ajoute du texte avec formatage inline (**gras**, *italique*)."""
    import re
    parts = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            para.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            para.add_run(part[1:-1]).italic = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            run = para.add_run(part[1:-1])
            run.font.name = "Courier New"
        elif part:
            para.add_run(part)


def _set_cell_bg(cell, hex_color: str):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    cell._tc.get_or_add_tcPr().append(shd)


def _md_to_docx(content: str, path: Path):
    import re
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()
    for sec in doc.sections:
        sec.top_margin = sec.bottom_margin = Cm(2.5)
        sec.left_margin = sec.right_margin = Cm(2.5)

    lines = content.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()

        if s.startswith("### "):
            doc.add_heading(s[4:], level=3)
        elif s.startswith("## "):
            doc.add_heading(s[3:], level=2)
        elif s.startswith("# "):
            doc.add_heading(s[2:], level=1)

        elif s == "---":
            p = doc.add_paragraph()
            pPr = p._p.get_or_add_pPr()
            pBdr = OxmlElement("w:pBdr")
            bot = OxmlElement("w:bottom")
            bot.set(qn("w:val"), "single"); bot.set(qn("w:sz"), "6")
            bot.set(qn("w:space"), "1"); bot.set(qn("w:color"), "AAAAAA")
            pBdr.append(bot); pPr.append(pBdr)

        elif s.startswith("|"):
            rows_data = []
            is_header = True
            while i < len(lines) and lines[i].strip().startswith("|"):
                row_s = lines[i].strip()
                if re.match(r"^\|[-| :]+\|$", row_s):
                    is_header = False
                    i += 1
                    continue
                cells = [c.strip() for c in row_s.split("|")[1:-1]]
                rows_data.append((cells, is_header))
                if is_header:
                    is_header = False
                i += 1
            if rows_data:
                ncols = max(len(r[0]) for r in rows_data)
                tbl = doc.add_table(rows=len(rows_data), cols=ncols)
                tbl.style = "Table Grid"
                for ri, (cells, hdr) in enumerate(rows_data):
                    for ci, txt in enumerate(cells[:ncols]):
                        cell = tbl.rows[ri].cells[ci]
                        cell.text = ""
                        p = cell.paragraphs[0]
                        _add_inline(p, txt)
                        p.paragraph_format.space_before = Pt(2)
                        p.paragraph_format.space_after = Pt(2)
                        if hdr:
                            for run in p.runs:
                                run.bold = True
                                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                            _set_cell_bg(cell, "003366")
                doc.add_paragraph()
            continue

        elif s.startswith("> "):
            inner = s[2:]
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.8)
            if re.match(r"^#{1,3} ", inner):
                run = p.add_run(inner.lstrip("# ").strip())
                run.bold = True
                run.font.size = Pt(13)
                run.font.color.rgb = RGBColor(0x00, 0x33, 0x66)
            else:
                _add_inline(p, inner)

        elif s.startswith("- ") or s.startswith("* "):
            p = doc.add_paragraph(style="List Bullet")
            _add_inline(p, s[2:])

        elif re.match(r"^\d+\. ", s):
            p = doc.add_paragraph(style="List Number")
            _add_inline(p, re.sub(r"^\d+\. ", "", s))

        elif s:
            p = doc.add_paragraph()
            _add_inline(p, s)

        i += 1

    doc.save(str(path))


# ── 10. Sauvegarde du rapport rédigé par Claude ───────────────────────────────

@app.tool()
async def save_report(content: str, output_path: str = "") -> str:
    """Sauvegarde le rapport rédigé par Claude au format Word (.docx, défaut) ou Markdown (.md).
    output_path : chemin complet du fichier. Extension .md → Markdown brut, sinon → Word.
    Par défaut : dossier mcp_tino, nom auto-généré en .docx."""
    import datetime

    path = Path(output_path) if output_path else \
        Path(__file__).parent / f"rapport_{datetime.date.today().isoformat()}.docx"
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".md":
        path.write_text(content, encoding="utf-8")
        return f"✅ Rapport Markdown sauvegardé : {path} ({len(content)} caractères)"

    _md_to_docx(content, path)
    size_kb = path.stat().st_size // 1024
    return f"✅ Rapport Word sauvegardé : {path} ({size_kb} Ko)"


def main():
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
