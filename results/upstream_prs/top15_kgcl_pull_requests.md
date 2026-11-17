# Upstream Ontology Crosswalk Patch Bundle (Top 15 Certified Repairs)

Generated programmatically from live `ELK` + `DuckDB` + `Clingo` CEGAR certification
(`|Unsat_Delta| = 0`, `|ConsViol| = 0`). Ready for submission to `biopragmatics/biomappings`
and OBO Foundry ontology repositories (`mondo`, `doid`, `po`, `to`, `envo`).

| Rank | Suite | Subject | Input Predicate | Object | Certified Action | KGCL Patch Command |
|---:|---|---|---|---|---|---|
| 1 | `Disease_Phenotype_Hub` | `HP:0000035` (Abnormal testis morphology) | `skos:exactMatch` (0.91) | `MP:0001146` (abnormal testis morphology) | `downgrade_sub` | `change relationship between HP:0000035 and MP:0001146 from skos:exactMatch to rdfs:subClassOf` |
| 2 | `OAEI_BioML_2026` | `NCIT:C2949` (Choroid Neoplasm) | `skos:exactMatch` (0.99) | `DOID:12759` (choroid cancer) | `downgrade_sup` | `delete edge NCIT:C2949 skos:exactMatch DOID:12759 ; create edge DOID:12759 rdfs:subClassOf NCIT:C2949` |
| 3 | `Plant_Biodiv_Hub` | `PO:0025132` (canal) | `skos:exactMatch` (0.91) | `ENVO:00000014` (canal) | `downgrade_sup` | `delete edge PO:0025132 skos:exactMatch ENVO:00000014 ; create edge ENVO:00000014 rdfs:subClassOf PO:0025132` |
| 4 | `Disease_Phenotype_Hub` | `HP:0000159` (Abnormal lip morphology) | `skos:exactMatch` (0.91) | `MP:0003769` (abnormal lip morphology) | `downgrade_sub` | `change relationship between HP:0000159 and MP:0003769 from skos:exactMatch to rdfs:subClassOf` |
| 5 | `OAEI_BioML_2026` | `NCIT:C3727` (Adenosquamous Carcinoma) | `skos:exactMatch` (0.99) | `DOID:4830` (adenosquamous carcinoma) | `downgrade_sup` | `delete edge NCIT:C3727 skos:exactMatch DOID:4830 ; create edge DOID:4830 rdfs:subClassOf NCIT:C3727` |
| 6 | `Plant_Biodiv_Hub` | `PO:0009008` (plant organ) | `skos:exactMatch` (0.91) | `ENVO:01000162` (organ) | `downgrade_sub` | `change relationship between PO:0009008 and ENVO:01000162 from skos:exactMatch to rdfs:subClassOf` |
| 7 | `Disease_Phenotype_Hub` | `HP:0000174` (Abnormal palate morphology) | `skos:exactMatch` (0.91) | `MP:0003755` (abnormal palate morphology) | `downgrade_sub` | `change relationship between HP:0000174 and MP:0003755 from skos:exactMatch to rdfs:subClassOf` |
| 8 | `OAEI_BioML_2026` | `NCIT:C3780` (Large Cell Carcinoma) | `skos:exactMatch` (0.99) | `DOID:4552` (large cell carcinoma) | `downgrade_sup` | `delete edge NCIT:C3780 skos:exactMatch DOID:4552 ; create edge DOID:4552 rdfs:subClassOf NCIT:C3780` |
| 9 | `Plant_Biodiv_Hub` | `TO:0000893` (stem shape) | `skos:exactMatch` (0.91) | `FLOPO:0000093` (stem shape) | `downgrade_sub` | `change relationship between TO:0000893 and FLOPO:0000093 from skos:exactMatch to rdfs:subClassOf` |
| 10 | `Disease_Phenotype_Hub` | `HP:0000290` (Abnormal forehead morphology) | `skos:exactMatch` (0.91) | `MP:0030031` (abnormal forehead morphology) | `downgrade_sup` | `delete edge HP:0000290 skos:exactMatch MP:0030031 ; create edge MP:0030031 rdfs:subClassOf HP:0000290` |
| 11 | `OAEI_BioML_2026` | `http://www.omim.org/phenotypicSeries/PS115200` (Dilated cardiomyopathy) | `skos:exactMatch` (0.99) | `ORDO:217604` (Dilated cardiomyopathy) | `downgrade_sup` | `delete edge http://www.omim.org/phenotypicSeries/PS115200 skos:exactMatch ORDO:217604 ; create edge ORDO:217604 rdfs:subClassOf http://www.omim.org/phenotypicSeries/PS115200` |
| 12 | `Plant_Biodiv_Hub` | `TO:1000008` (branch position) | `skos:exactMatch` (0.91) | `FLOPO:0000121` (branch position) | `downgrade_sub` | `change relationship between TO:1000008 and FLOPO:0000121 from skos:exactMatch to rdfs:subClassOf` |
| 13 | `Disease_Phenotype_Hub` | `HP:0000377` (Abnormal pinna morphology) | `skos:exactMatch` (0.91) | `MP:0002177` (abnormal outer ear morphology) | `downgrade_sub` | `change relationship between HP:0000377 and MP:0002177 from skos:exactMatch to rdfs:subClassOf` |
| 14 | `OAEI_BioML_2026` | `http://www.omim.org/phenotypicSeries/PS253600` (Muscular dystrophy, limb-girdle, autosomal recessive) | `skos:exactMatch` (0.99) | `ORDO:102015` (Autosomal recessive limb-girdle muscular dystrophy) | `downgrade_sup` | `delete edge http://www.omim.org/phenotypicSeries/PS253600 skos:exactMatch ORDO:102015 ; create edge ORDO:102015 rdfs:subClassOf http://www.omim.org/phenotypicSeries/PS253600` |
| 15 | `Plant_Biodiv_Hub` | `TO:0000951` (corolla color) | `skos:exactMatch` (0.91) | `FLOPO:0000219` (corolla color) | `downgrade_sub` | `change relationship between TO:0000951 and FLOPO:0000219 from skos:exactMatch to rdfs:subClassOf` |
