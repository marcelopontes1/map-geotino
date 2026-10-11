# MCP Tino — Guide d'utilisation pour Claude

## Rôle de ce MCP

Ce serveur MCP collecte des données hydrogéologiques publiques (HubEau, BRGM, Géorisques) pour
étudier la faisabilité de systèmes de **géothermie de minime importance (GMI)** — notamment dans
le cadre de la transition énergétique gaz → énergie verte dans des bâtiments existants.

**Architecture :** Le MCP collecte des données brutes. Claude rédige le rapport professionnel.

---

## Workflow standard

### Étape 1 — Collecter les données

```
collect_site_data(latitude, longitude, radius_km=5)
```

Retourne en une seule requête (parallèle) : ouvrages BSS, piézométrie, géologie, qualité des
eaux, réglementation/risques, hydrologie. Durée : ~15–20 s.

### Étape 1b — Évaluer la technique géothermique

Après `collect_site_data`, extraire les valeurs clés et appeler :

```
evaluate_technique(ph, conductivity_us, calcium_mg, magnesium_mg,
                   aquifer_wells_count, min_well_depth_m, geology_dominant,
                   cavities_count, terrain_movements_count)
```

Retourne la recommandation **NAPPE** ou **SONDE** avec score, éléments déterminants et conclusion.

### Étape 2 — Rédiger le rapport professionnel

Rédiger en **phrases narratives complètes** — pas de listes sèches. Commenter chaque résultat,
relier les données entre elles, contextualiser géographiquement. Sections :

1. **Contexte géologique** — Interpréter la lithologie BRGM et les profils BSS en phrases. Nommer
   les formations avec leur code (Fz, Fy…). Expliquer leur intérêt géothermique.

2. **Hydrogéologie** — Tableau des formations + intérêt géothermique (⭐). Commenter la
   piézométrie (niveaux NGF, profondeur nappe, variation saisonnière). Déduire le sens
   d'écoulement depuis le contexte régional si < 3 stations.

3. **Qualité physicochimique** — Tableau valeurs mesurées vs seuils. Calculer le TH
   (Ca/20,04 + Mg/12,16) × 5. Commenter en phrases l'agressivité de l'eau, les risques
   d'entartrage/corrosion, mentionner que Fe/Mn nécessitent une analyse in-situ.

4. **Hydrologie** — Décrire le rôle de chaque cours d'eau (drainant en étiage / alimentant en
   crue). Expliquer le lien nappe-rivière et son impact sur la ressource géothermique.

5. **Réglementation GMI** — Citer les décrets par numéro exact. Commenter les risques identifiés
   (cavités, MVT, ICPE) en phrases. Tableau réglementaire avec liens.

6. **Orientation technique** — Intégrer le résultat de `evaluate_technique` avec la conclusion
   narrative complète.

7. **Avis de faisabilité** — Bloc citation `✅ AVIS FAVORABLE SOUS RÉSERVES` ou `❌ AVIS
   DÉFAVORABLE` avec conditions suspensives numérotées.

### Étape 3 — Sauvegarder

```
save_report(content, output_path="")
```

Sans `output_path`, sauvegarde en **Word (.docx)** dans le dossier du projet, nom auto-généré.
- Extension `.docx` (défaut) → Word avec mise en page professionnelle (titres, tableaux, gras)
- Extension `.md` → Markdown brut
- Passer un chemin absolu pour choisir l'emplacement exact.

---

## Outils disponibles individuellement

| Outil | Usage |
|---|---|
| `collect_site_data(lat, lon, r)` | Collecte tout en une fois — à appeler en premier |
| `evaluate_technique(...)` | Recommandation NAPPE vs SONDE avec score et conclusion |
| `save_report(content, path)` | Sauvegarde Word (.docx) ou Markdown |
| `query_aquifers(lat, lon, r)` | BSS — ouvrages et formations |
| `query_piezometry(lat, lon, r)` | Niveaux NGF + sens d'écoulement |
| `query_geology(lat, lon, r)` | Lithologie + profils BSS |
| `query_water_quality(lat, lon, r)` | 8 paramètres physico-chimiques |
| `query_regulations_and_risks(lat, lon, r)` | GASPAR + cavités + MVT + ICPE + textes GMI |
| `query_hydrology(lat, lon, r=10)` | Cours d'eau et stations hydrométriques |

---

## Paramètres SANDRE utilisés (vérifiés)

| Code | Paramètre | Unité | Seuil critique géothermie |
|---|---|---|---|
| 1301 | Température | °C | > 25°C → haute énergie |
| 1302 | pH | unité pH | < 7 → eaux agressives |
| 1303 | Conductivité | µS/cm | > 1 500 → corrosion |
| 1337 | Chlorures | mg/L Cl | > 200 → corrosif |
| 1338 | Sulfates | mg/L SO₄ | > 250 → entartrage/béton |
| 1340 | Nitrates | mg/L NO₃ | > 50 → anthropique |
| 1372 | Magnésium | mg/L Mg | Pour calcul TH |
| 1374 | Calcium | mg/L Ca | Pour calcul TH (> 100 → eau dure) |

**Note :** Fer (Fe) et Manganèse (Mn) ne sont pas disponibles via HubEau pour la plupart des sites.
Toujours mentionner qu'une analyse in-situ est obligatoire lors du forage.

---

## Qualité du rapport attendu

Le rapport doit ressembler à un **rapport d'hydrogéologue professionnel** — pas un résumé générique.
Exemples de référence : rapports Visiplast (21-1679), Quartus ADP (23-1935), Ville de Pantin (17-749).

- Relier les données entre elles (piézométrie ↔ cours d'eau ↔ géologie)
- Nommer les formations géologiques avec leur code (ex: Fz pour alluvions récentes)
- Citer les décrets par numéro exact
- Calculer le TH, commenter l'agressivité de l'eau
- L'avis final doit être motivé et conditionnel si nécessaire
