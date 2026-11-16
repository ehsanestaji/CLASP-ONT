"""Deterministic 200-class, 4-ontology synthetic biological hub for Phase 0 gate verification.

Generates:
  - 4 OWL 2 EL ontologies (TOY_ANAT, TOY_PROC, TOY_TRAIT, TOY_CHEM), 50 named classes each (200 total),
    complete with rdfs:label, oboInOwl:hasExactSynonym, IAO:0000115 definitions, disjointness axioms,
    and existential role restrictions (exists part_of.C).
  - 60 cross-ontology SSSOM correspondences:
      * 45 globally coherent & conservative biological bridges
      * 15 planted incoherence & conservativity traps spanning all 5 lattice refinement moves
        (keep_equiv, downgrade_sub, downgrade_sup, weaken_ancestor, negate_drop), including
        pairwise-valid 3/4-way compositional failures, parallel bridges, and existential propagation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from clasp_ont.ingest.sssom_io import MappingRecord, write_sssom_tsv

ANAT_NS = "http://clasp-ont.org/toy/anat#"
PROC_NS = "http://clasp-ont.org/toy/proc#"
TRAIT_NS = "http://clasp-ont.org/toy/trait#"
CHEM_NS = "http://clasp-ont.org/toy/chem#"
PART_OF_IRI = "http://purl.obolibrary.org/obo/BFO_0000050"


@dataclass(slots=True)
class ClassSpec:
    iri: str
    label: str
    definition: str
    synonyms: list[str]
    parents: list[str]
    disjoint_with: list[str]
    existential_parents: list[tuple[str, str]]  # (prop_iri, filler_iri): C <= exists R.Filler
    gci_sub_of_existential: tuple[str, str] | None = None  # (prop_iri, filler_iri): exists R.Filler <= C


@dataclass(slots=True)
class ToyHubBundle:
    ontology_paths: dict[str, Path]
    sssom_path: Path
    mappings: list[MappingRecord]
    ground_truth_actions: dict[str, str]
    trap_ids: list[str]
    namespace_hints: dict[str, str]


def _write_owl_ontology(
    ont_iri: str,
    classes: list[ClassSpec],
    out_path: Path,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0"?>\n')
        fh.write(
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
            '         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"\n'
            '         xmlns:owl="http://www.w3.org/2002/07/owl#"\n'
            '         xmlns:obo="http://purl.obolibrary.org/obo/"\n'
            '         xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">\n'
        )
        fh.write(f'  <owl:Ontology rdf:about="{xml_escape(ont_iri)}"/>\n')
        fh.write(f'  <owl:ObjectProperty rdf:about="{PART_OF_IRI}"/>\n')

        for c in classes:
            c_esc = xml_escape(c.iri)
            fh.write(f'  <owl:Class rdf:about="{c_esc}">\n')
            fh.write(f"    <rdfs:label>{xml_escape(c.label)}</rdfs:label>\n")
            fh.write(f"    <obo:IAO_0000115>{xml_escape(c.definition)}</obo:IAO_0000115>\n")
            for syn in c.synonyms:
                fh.write(f"    <oboInOwl:hasExactSynonym>{xml_escape(syn)}</oboInOwl:hasExactSynonym>\n")
            for p in c.parents:
                fh.write(f'    <rdfs:subClassOf rdf:resource="{xml_escape(p)}"/>\n')
            for dj in c.disjoint_with:
                fh.write(f'    <owl:disjointWith rdf:resource="{xml_escape(dj)}"/>\n')
            for prop_iri, filler_iri in c.existential_parents:
                fh.write("    <rdfs:subClassOf>\n")
                fh.write("      <owl:Restriction>\n")
                fh.write(f'        <owl:onProperty rdf:resource="{xml_escape(prop_iri)}"/>\n')
                fh.write(f'        <owl:someValuesFrom rdf:resource="{xml_escape(filler_iri)}"/>\n')
                fh.write("      </owl:Restriction>\n")
                fh.write("    </rdfs:subClassOf>\n")
            fh.write("  </owl:Class>\n")

            if c.gci_sub_of_existential is not None:
                prop_iri, filler_iri = c.gci_sub_of_existential
                fh.write("  <owl:Restriction>\n")
                fh.write(f'    <owl:onProperty rdf:resource="{xml_escape(prop_iri)}"/>\n')
                fh.write(f'    <owl:someValuesFrom rdf:resource="{xml_escape(filler_iri)}"/>\n')
                fh.write(f'    <rdfs:subClassOf rdf:resource="{c_esc}"/>\n')
                fh.write("  </owl:Restriction>\n")

        fh.write("</rdf:RDF>\n")
    return out_path


def _build_anat_classes() -> list[ClassSpec]:
    specs: list[ClassSpec] = [
        ClassSpec(f"{ANAT_NS}A001", "plant anatomical entity", "A material anatomical structure forming part of a vascular or non-vascular plant organism.", ["plant structure"], [], [], []),
        ClassSpec(f"{ANAT_NS}A002", "vascular tissue", "Fluid-conducting plant vascular tissue comprising xylem and phloem conducting elements.", ["conductive plant tissue"], [f"{ANAT_NS}A001"], [f"{ANAT_NS}A005", f"{ANAT_NS}A012"], []),
        ClassSpec(f"{ANAT_NS}A003", "xylem vessel element", "Specialized lignified water-conducting tracheary vascular cell subtype within xylem vascular tissue.", ["xylem tracheary vessel"], [f"{ANAT_NS}A002"], [f"{ANAT_NS}A004"], []),
        ClassSpec(f"{ANAT_NS}A004", "phloem sieve element", "Specialized photoassimilate-conducting vascular sieve tube cell subtype disjoint from xylem vessels.", ["sieve tube element"], [f"{ANAT_NS}A002"], [f"{ANAT_NS}A003"], []),
        ClassSpec(f"{ANAT_NS}A005", "epidermal tissue", "Outer dermal protective cell layer covering primary plant organs.", ["dermal plant layer"], [f"{ANAT_NS}A001"], [f"{ANAT_NS}A002", f"{ANAT_NS}A012"], []),
        ClassSpec(f"{ANAT_NS}A006", "root hair tip", "Apical polarized tip domain of an elongating root hair cell in the root epidermis.", ["root hair apex"], [f"{ANAT_NS}A005"], [f"{ANAT_NS}A007"], []),
        ClassSpec(f"{ANAT_NS}A007", "epidermal trichoblast", "Root epidermal precursor cell committed to root hair initiation.", ["hair-forming rhizodermal cell"], [f"{ANAT_NS}A005"], [f"{ANAT_NS}A006"], []),
        ClassSpec(f"{ANAT_NS}A008", "ground parenchyma tissue", "Fundamental non-vascular plant ground tissue composed of thin-walled parenchyma cells.", ["plant ground tissue"], [f"{ANAT_NS}A001"], [], []),
        ClassSpec(f"{ANAT_NS}A009", "root cortex parenchyma", "Ground parenchyma tissue region situated between the root epidermis and endodermis in plants.", ["plant root cortex"], [f"{ANAT_NS}A008"], [], []),
        ClassSpec(f"{ANAT_NS}A010", "leaf mesophyll parenchyma", "Specialized chlorophyll-rich photosynthetic ground parenchyma cell layer inside the plant leaf blade.", ["chlorenchyma mesophyll"], [f"{ANAT_NS}A008"], [], []),
        ClassSpec(f"{ANAT_NS}A011", "bundle sheath cell", "Parenchymatous sheath layer surrounding leaf vascular bundles participating in photosynthetic transport.", ["vascular bundle sheath"], [f"{ANAT_NS}A008"], [], []),
        ClassSpec(f"{ANAT_NS}A012", "reproductive organ", "Broad plant reproductive anatomical organ encompassing both male and female floral structures.", ["floral or cone organ"], [f"{ANAT_NS}A001"], [f"{ANAT_NS}A002", f"{ANAT_NS}A005"], []),
        ClassSpec(f"{ANAT_NS}A013", "floral carpel structure", "Female ovule-bearing angiosperm floral reproductive organ whorl.", ["gynoecium carpel"], [f"{ANAT_NS}A012"], [f"{ANAT_NS}A014"], []),
        ClassSpec(f"{ANAT_NS}A014", "anther tapetum", "Nutritive male microsporangial layer inside the floral stamen anther.", ["stamen tapetal layer"], [f"{ANAT_NS}A012"], [f"{ANAT_NS}A013"], []),
        ClassSpec(f"{ANAT_NS}A015", "stomatal guard cell", "Specialized paired kidney-shaped epidermal cell flanking and regulating a stomatal pore.", ["guard cell"], [f"{ANAT_NS}A005"], [], []),
        ClassSpec(f"{ANAT_NS}A016", "glandular trichome apex", "Secretory apical head of an epidermal glandular trichome accumulating volatile monoterpenes.", ["secretory trichome head"], [f"{ANAT_NS}A005"], [], []),
        ClassSpec(f"{ANAT_NS}A017", "seed anatomical structure", "Mature fertilized ovule compartment comprising seed coat and storage tissues.", ["seed part"], [f"{ANAT_NS}A001"], [], []),
        ClassSpec(f"{ANAT_NS}A018", "testa outer integument", "Protective outer seed coat layer derived from maternal ovular integuments.", ["outer seed coat"], [f"{ANAT_NS}A017"], [f"{ANAT_NS}A019"], []),
        ClassSpec(f"{ANAT_NS}A019", "endosperm aleurone", "Protein-storing peripheral endosperm cell layer surrounding the starchy endosperm.", ["aleurone layer"], [f"{ANAT_NS}A017"], [f"{ANAT_NS}A018"], []),
        ClassSpec(f"{ANAT_NS}A020", "subepidermal sclerenchyma", "Thick-walled mechanical support tissue below the epidermis.", ["hypodermal sclerenchyma"], [f"{ANAT_NS}A008"], [f"{ANAT_NS}A021"], []),
        ClassSpec(
            f"{ANAT_NS}A021",
            "guard-cell-bounded stomatal pore",
            "Intercellular stomatal aperture structurally part of a stomatal guard cell complex.",
            ["stomatal aperture pore"],
            [f"{ANAT_NS}A005"],
            [f"{ANAT_NS}A020"],
            [(PART_OF_IRI, f"{ANAT_NS}A015")],
            gci_sub_of_existential=(PART_OF_IRI, f"{ANAT_NS}A015"),
        ),
        ClassSpec(f"{ANAT_NS}A022", "epidermal cuticle matrix", "Hydrophobic cutin and wax extracellular layer coating epidermal cells.", ["cuticular membrane"], [f"{ANAT_NS}A005"], [], []),
        ClassSpec(f"{ANAT_NS}A023", "meristematic zone", "Undifferentiated proliferating plant tissue zone sustaining organogenesis.", ["plant meristem"], [f"{ANAT_NS}A001"], [], []),
        ClassSpec(f"{ANAT_NS}A024", "shoot apical meristem", "Specific dome-shaped apical meristematic subtype at the growing shoot tip.", ["SAM dome"], [f"{ANAT_NS}A023"], [], []),
        ClassSpec(f"{ANAT_NS}A025", "vascular cambium", "Lateral meristematic cylinder producing secondary xylem and phloem.", ["cambial ring"], [f"{ANAT_NS}A023"], [], []),
        ClassSpec(
            f"{ANAT_NS}A026",
            "cortical fiber cap",
            "Supportive fiber cluster structurally part of a cortical mechanical bundle.",
            ["bundle fiber cap"],
            [f"{ANAT_NS}A008"],
            [],
            [(PART_OF_IRI, f"{ANAT_NS}A027")],
        ),
        ClassSpec(f"{ANAT_NS}A027", "cortical mechanical bundle", "Structural support bundle embedded within ground cortex.", ["mechanical cortex strand"], [f"{ANAT_NS}A008"], [], []),
    ]
    # Add A028..A050 (23 clean anatomical classes) to reach exact 50 classes
    organ_names = [
        ("A028", "root apical meristem", "Apical meristematic zone at the growing root tip.", "A023"),
        ("A029", "pericycle layer", "Outermost cylinder of the vascular stele giving rise to lateral roots.", "A002"),
        ("A030", "endodermal casparian strip", "Suberized radial cell wall band sealing the root endodermis.", "A008"),
        ("A031", "leaf palisade mesophyll", "Columnar upper leaf mesophyll layer specialized for light capture.", "A010"),
        ("A032", "leaf spongy mesophyll", "Aerated lower leaf mesophyll layer facilitating gas diffusion.", "A010"),
        ("A033", "petiole vascular bundle", "Conducting vascular strand traversing the leaf stalk.", "A002"),
        ("A034", "floral petal epidermis", "Conical-celled epidermal layer of the corolla petal.", "A005"),
        ("A035", "nectary secretory tissue", "Glandular floral tissue secreting carbohydrate-rich nectar.", "A012"),
        ("A036", "pollen grain exine", "Sporopollenin-rich outer wall of the male gametophyte.", "A012"),
        ("A037", "embryo cotyledon", "Embryonic seed leaf storing reserves for seedling establishment.", "A017"),
        ("A038", "hypocotyl axis", "Embryonic stem region below the cotyledonary node.", "A017"),
        ("A039", "radicle primordium", "Embryonic root axis emerging first during seed germination.", "A017"),
        ("A040", "root cap columella", "Gravity-sensing statolith-containing central root cap region.", "A008"),
        ("A041", "secondary cell wall", "Lignified structural cell wall deposited inside the primary wall.", "A001"),
        ("A042", "plasmodesmata channel", "Cytoplasmic symplastic nanopore bridging adjacent plant cells.", "A001"),
        ("A043", "chloroplast thylakoid", "Internal photosynthetic membrane stack inside plastids.", "A001"),
        ("A044", "amyloplast organelle", "Non-pigmented starch-storing plastid in storage parenchyma.", "A001"),
        ("A045", "vacuolar tonoplast", "Semi-permeable membrane bounding the central plant vacuole.", "A001"),
        ("A046", "stem internode cortex", "Cortical parenchyma region of an elongating stem internode.", "A008"),
        ("A047", "axillary bud primordium", "Dormant or active lateral shoot bud formed in a leaf axil.", "A023"),
        ("A048", "root nodule cortex", "Specialized legume root cortical zone housing symbiotic bacteroids.", "A009"),
        ("A049", "suberin lamella", "Hydrophobic polyphenolic-lipid barrier in periderm and endodermis.", "A001"),
        ("A050", "abscission zone layer", "Specialized cell separation zone at the base of leaves and fruits.", "A008"),
    ]
    for code, lbl, defn, parent in organ_names:
        specs.append(ClassSpec(f"{ANAT_NS}{code}", lbl, defn, [f"{lbl} structure"], [f"{ANAT_NS}{parent}"], [], []))
    assert len(specs) == 50
    return specs


def _build_proc_classes() -> list[ClassSpec]:
    specs: list[ClassSpec] = [
        ClassSpec(f"{PROC_NS}P001", "plant biological process", "Any regulated molecular or cellular process occurring in a photosynthetic plant.", ["phytological process"], [], [f"{PROC_NS}P008"], []),
        ClassSpec(f"{PROC_NS}P002", "cellular morphogenesis process", "Coordinated cell polarity and wall expansion shaping plant cells.", ["cell shape morphogenesis"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P003", "root hair developmental process", "Broad developmental program governing root hair initiation and tip elongation.", ["rhizodermal hair development"], [f"{PROC_NS}P002"], [], []),
        ClassSpec(f"{PROC_NS}P004", "root hair initiation process", "Early bulge formation and trichoblast specification step of root hair development.", ["trichoblast hair initiation"], [f"{PROC_NS}P003"], [], []),
        ClassSpec(f"{PROC_NS}P005", "phytohormone signaling pathway", "Intracellular receptor and transcriptional cascade transducing plant hormone cues.", ["plant hormone signaling"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P006", "auxin signaling cascade", "TIR1/AFB-Aux/IAA-ARF signal transduction cascade mediating auxin response.", ["indole-3-acetic acid signaling"], [f"{PROC_NS}P005"], [f"{PROC_NS}P007"], []),
        ClassSpec(f"{PROC_NS}P007", "cytokinin signaling cascade", "Two-component histidine kinase phosphorelay transducing cytokinin response.", ["zeatin phosphorelay signaling"], [f"{PROC_NS}P005"], [f"{PROC_NS}P006"], []),
        # Root class with NO parent, disjoint with P001 (plant biological process) for homonym Trap 5
        ClassSpec(f"{PROC_NS}P008", "adrenal cortex steroidogenesis", "Vertebrate metazoan endocrine synthesis of corticosteroids in the mammalian adrenal cortex.", ["mammalian adrenal cortex process"], [], [f"{PROC_NS}P001"], []),
        ClassSpec(f"{PROC_NS}P009", "ground tissue maintenance", "Homeostatic metabolic maintenance of plant ground parenchyma.", ["parenchyma homeostasis"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P010", "photosynthetic tissue process", "Broad physiological carbon assimilation and gas-exchange process across leaf tissues.", ["leaf photosynthetic process"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P011", "mesophyll carbon fixation", "RuBisCO-catalyzed Calvin-Benson carboxylation within leaf mesophyll.", ["Calvin cycle fixation"], [f"{PROC_NS}P010"], [], []),
        ClassSpec(f"{PROC_NS}P012", "guard cell blue-light response", "Phototropin-mediated proton pumping driving guard cell swelling.", ["stomatal blue light opening"], [f"{PROC_NS}P010"], [], []),
        ClassSpec(f"{PROC_NS}P013", "abiotic stress physiological process", "Cellular acclimation program protecting plant cells against environmental stress.", ["environmental stress response"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P014", "drought osmotic adjustment", "Specific osmolyte accumulation process lowering water potential under water deficit.", ["dehydration osmotic acclimation"], [f"{PROC_NS}P013"], [], []),
        ClassSpec(f"{PROC_NS}P015", "salt ion homeostasis", "SOS-pathway sodium extrusion and vacuolar compartmentalization under salinity.", ["salinity Na+ homeostasis"], [f"{PROC_NS}P013"], [], []),
        ClassSpec(f"{PROC_NS}P016", "seed developmental process", "Physiological program governing seed maturation, dormancy, and germination.", ["seed lifecycle process"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P017", "germination radicle emergence", "Active post-imbibition cell elongation rupturing the testa during seed germination.", ["radicle protrusion germination"], [f"{PROC_NS}P016"], [f"{PROC_NS}P018"], []),
        ClassSpec(f"{PROC_NS}P018", "seed dormancy maintenance", "ABA-regulated metabolic arrest preventing premature seed germination.", ["dormancy arrest process"], [f"{PROC_NS}P016"], [f"{PROC_NS}P017"], []),
        ClassSpec(f"{PROC_NS}P019", "phenylpropanoid biosynthesis", "General metabolic pathway converting phenylalanine into coumarins, flavonoids, and monolignols.", ["phenylpropanoid pathway"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P020", "monolignol radical coupling", "Peroxidase/laccase oxidative polymerization of monolignols into lignin.", ["lignification coupling"], [f"{PROC_NS}P019"], [], []),
        ClassSpec(f"{PROC_NS}P021", "chalcone synthase condensation", "Committed flavonoid branch reaction forming naringenin chalcone.", ["chalcone synthesis"], [f"{PROC_NS}P019"], [], []),
        ClassSpec(f"{PROC_NS}P022", "volatile monoterpene emission", "Biosynthesis and cuticular release of volatile monoterpenes from glandular trichomes.", ["monoterpene volatilization"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P023", "epidermal and cortical turgor support", "Broad mechanical and turgor stabilization across dermal and cortical tissues.", ["tissue turgor mechanics"], [f"{PROC_NS}P001"], [], []),
        ClassSpec(f"{PROC_NS}P024", "meristem mitotic proliferation", "Cell-cycle progression sustaining stem-cell divisions across meristematic zones.", ["meristem cell division"], [f"{PROC_NS}P001"], [], []),
    ]
    extra_procs = [
        ("P025", "xylem water transpiration transport", "Cohesion-tension bulk ascent of sap through xylem vessels.", "P001"),
        ("P026", "phloem sucrose translocation", "Pressure-flow source-to-sink transport through phloem sieve tubes.", "P001"),
        ("P027", "anthocyanin vacuolar sequestration", "GST- and MATE-mediated import of anthocyanins into the central vacuole.", "P019"),
        ("P028", "flavonol UV-B photoprotection", "Accumulation of UV-absorbing flavonol glycosides in epidermal cells.", "P019"),
        ("P029", "carpel ovule fertilization", "Pollen tube guidance and double fertilization inside the floral carpel.", "P001"),
        ("P030", "tapetal programmed cell death", "Timely degeneration of anther tapetum nourishing developing microspores.", "P001"),
        ("P031", "testa mucilage extrusion", "Hydration-triggered release of pectic seed coat mucilage.", "P016"),
        ("P032", "aleurone alpha-amylase secretion", "Gibberellin-induced hydrolytic enzyme mobilization in cereal aleurone.", "P016"),
        ("P033", "cuticular wax alkane elongation", "Very-long-chain fatty acid decarbonylation forming epicuticular wax.", "P001"),
        ("P034", "vascular cambial secondary growth", "Radial division of cambial initials thickening woody stems.", "P024"),
        ("P035", "lateral root primordium emergence", "Auxin-triggered pericycle founder cell division forming lateral roots.", "P024"),
        ("P036", "casparian strip suberization", "Localized lignin and suberin deposition sealing endodermal apoplast.", "P009"),
        ("P037", "thylakoid linear electron flow", "Photosystem II to Photosystem I light-driven electron transport.", "P010"),
        ("P038", "amyloplast gravitropic sedimentation", "Statolith settling in root cap columella triggering auxin redistribution.", "P009"),
        ("P039", "secondary wall cellulose deposition", "CESA4/7/8 complex synthesis of crystalline secondary wall cellulose.", "P002"),
        ("P040", "symplastic plasmodesmal gating", "Callose turnover regulating intercellular macromolecular trafficking.", "P001"),
        ("P041", "nectar hexose secretion", "SWEET9-mediated sucrose efflux and extracellular hydrolysis in nectaries.", "P001"),
        ("P042", "sporopollenin exine assembly", "Polyketide and fatty-acyl polymerization building pollen exine.", "P001"),
        ("P043", "cotyledon reserve mobilization", "Beta-oxidation and gluconeogenesis fueling post-germinative growth.", "P016"),
        ("P044", "hypocotyl skotomorphogenic elongation", "Dark-grown axial elongation lifting cotyledons toward light.", "P002"),
        ("P045", "nodule nitrogen fixation", "Nitrogenase-catalyzed reduction of dinitrogen inside root nodules.", "P009"),
        ("P046", "organ abscission cell separation", "Pectinase-driven middle lamella dissolution at abscission zones.", "P001"),
        ("P047", "gibberellin ent-kaurene oxidation", "Plastidial and ER cytochrome P450 steps forming bioactive gibberellins.", "P005"),
        ("P048", "strigolactone axillary bud inhibition", "MAX/D14 signaling suppressing lateral bud outgrowth.", "P005"),
        ("P049", "reactive oxygen burst detoxification", "Ascorbate-glutathione cycle scavenging intracellular H2O2.", "P013"),
        ("P050", "tonoplast proton-ATPase acidification", "V-ATPase electrogenic proton pumping maintaining vacuolar pH.", "P001"),
    ]
    for code, lbl, defn, parent in extra_procs:
        specs.append(ClassSpec(f"{PROC_NS}{code}", lbl, defn, [f"{lbl} pathway"], [f"{PROC_NS}{parent}"], [], []))
    assert len(specs) == 50
    return specs


def _build_trait_classes() -> list[ClassSpec]:
    specs: list[ClassSpec] = [
        ClassSpec(f"{TRAIT_NS}T001", "plant phenotypic trait", "Measurable morphological, physiological, or biochemical characteristic of a plant.", ["agronomic or botanical trait"], [], [], []),
        ClassSpec(f"{TRAIT_NS}T002", "vascular conducting structure trait", "Broad trait encompassing both xylem water conduction and phloem sieve transport characteristics.", ["vascular conduction trait"], [f"{TRAIT_NS}T001"], [], []),
        ClassSpec(f"{TRAIT_NS}T003", "xylem hydraulic conductivity", "Specific water transport efficiency across lignified xylem vessels.", ["xylem vessel conductivity"], [f"{TRAIT_NS}T002"], [], []),
        ClassSpec(f"{TRAIT_NS}T004", "phloem sieve transport trait", "Assimilate translocation capacity of phloem sieve elements.", ["sieve element translocation"], [f"{TRAIT_NS}T002"], [], []),
        ClassSpec(f"{TRAIT_NS}T005", "flavonoid pigmentation trait", "Phenotypic coloration or UV absorbance governed by flavonoid metabolites.", ["flavonoid content trait"], [f"{TRAIT_NS}T001"], [], []),
        ClassSpec(f"{TRAIT_NS}T006", "anthocyanin pigment accumulation", "Specific red-purple anthocyanin glycoside concentration in plant organs.", ["anthocyanin coloration"], [f"{TRAIT_NS}T005"], [f"{TRAIT_NS}T007"], []),
        ClassSpec(f"{TRAIT_NS}T007", "flavonol UV absorbance trait", "Epidermal flavonol glycoside UV-shielding capacity.", ["flavonol accumulation"], [f"{TRAIT_NS}T005"], [f"{TRAIT_NS}T006"], []),
        ClassSpec(f"{TRAIT_NS}T008", "epidermal hair trait", "Morphological density and length of epidermal hairs.", ["plant hair phenotype"], [f"{TRAIT_NS}T001"], [], []),
        ClassSpec(f"{TRAIT_NS}T009", "trichoblast initiation density", "Frequency of root epidermal trichoblasts initiating root hairs.", ["root trichoblast density"], [f"{TRAIT_NS}T008"], [], []),
        ClassSpec(f"{TRAIT_NS}T010", "abiotic stress tolerance", "General plant resilience trait across drought, salinity, and thermal extremes.", ["environmental stress tolerance"], [f"{TRAIT_NS}T001"], [], []),
        ClassSpec(f"{TRAIT_NS}T011", "drought osmotic tolerance", "Capacity to sustain turgor via osmotic adjustment during water deficit.", ["drought tolerance"], [f"{TRAIT_NS}T010"], [], []),
        ClassSpec(f"{TRAIT_NS}T012", "salinity ion tolerance", "Shoot biomass retention under elevated rhizosphere NaCl.", ["salt tolerance"], [f"{TRAIT_NS}T010"], [], []),
        ClassSpec(f"{TRAIT_NS}T013", "seed vigor and dormancy trait", "Composite seed developmental quality encompassing dormancy and germination speed.", ["seed performance trait"], [f"{TRAIT_NS}T001"], [], []),
        ClassSpec(f"{TRAIT_NS}T014", "seed dormancy duration", "Length of post-harvest after-ripening required to break seed dormancy.", ["primary seed dormancy"], [f"{TRAIT_NS}T013"], [], []),
        ClassSpec(f"{TRAIT_NS}T015", "radicle emergence rate", "Speed of radicle protrusion through the seed coat upon imbibition.", ["germination emergence speed"], [f"{TRAIT_NS}T013"], [], []),
        ClassSpec(f"{TRAIT_NS}T016", "floral organ morphology", "Structural dimensions and symmetry of reproductive flower whorls.", ["flower morphology"], [f"{TRAIT_NS}T001"], [], []),
        ClassSpec(f"{TRAIT_NS}T017", "floral carpel morphology", "Specific gynoecium carpel size, ovule number, and style geometry.", ["carpel structure trait"], [f"{TRAIT_NS}T016"], [f"{TRAIT_NS}T018"], []),
        ClassSpec(f"{TRAIT_NS}T018", "stamen anther fertility", "Pollen viability and tapetal support in male floral stamens.", ["anther fertility trait"], [f"{TRAIT_NS}T016"], [f"{TRAIT_NS}T017"], []),
        # Top-level disjoint roots T019 and T021 for acronym homonym Trap 14 (ROS)
        ClassSpec(f"{TRAIT_NS}T019", "root elongation morphometric index", "Macroscopic root axis length measurement index completely distinct from biochemical species.", ["root length morphometric"], [], [f"{TRAIT_NS}T021"], []),
        ClassSpec(f"{TRAIT_NS}T020", "ROS root elongation score", "Root Organogenesis Scaling (ROS) agronomic score quantifying taproot elongation rate.", ["root organogenesis score"], [f"{TRAIT_NS}T019"], [], []),
        ClassSpec(f"{TRAIT_NS}T021", "oxidative radical burst level", "Intracellular concentration of reactive oxygen free radicals.", ["ROS radical burst"], [], [f"{TRAIT_NS}T019"], []),
        ClassSpec(f"{TRAIT_NS}T022", "meristem proliferative vigor", "General stem-cell division capacity across plant meristems.", ["meristematic growth vigor"], [f"{TRAIT_NS}T001"], [], []),
        ClassSpec(f"{TRAIT_NS}T023", "shoot apical dome size", "Diameter of the vegetative shoot apical meristem dome.", ["SAM diameter"], [f"{TRAIT_NS}T022"], [], []),
    ]
    extra_traits = [
        ("T024", "cambial wood increment", "Annual radial secondary xylem width produced by vascular cambium.", "T022"),
        ("T025", "lateral root branching density", "Number of emerged lateral roots per centimeter of primary root.", "T022"),
        ("T026", "endodermal apoplastic barrier integrity", "Exclusion efficiency of Casparian strips against apoplastic tracer dyes.", "T001"),
        ("T027", "cuticular water loss resistance", "Minimum epidermal conductance through cuticular wax.", "T010"),
        ("T028", "photosynthetic leaf capacity", "Whole-leaf net carbon assimilation capacity across photosynthetic tissues.", "T001"),
        ("T029", "mesophyll carboxylation efficiency", "Maximum RuBisCO carboxylation rate Vcmax in leaf mesophyll.", "T028"),
        ("T030", "stomatal conductance sensitivity", "Dynamic guard cell aperture responsiveness to blue light and VPD.", "T028"),
        ("T031", "testa seed coat thickness", "Micrometer thickness of protective outer seed integument.", "T013"),
        ("T032", "aleurone protein content", "Storage protein and hydrolytic enzyme reserve in grain aleurone.", "T013"),
        ("T033", "stem lodging mechanical strength", "Bending resistance conferred by subepidermal sclerenchyma and lignin.", "T001"),
        ("T034", "lignin Klason content", "Cell-wall lignin mass fraction derived from monolignol polymerization.", "T033"),
        ("T035", "secondary wall cellulose crystallinity", "Crystalline microfibril index in secondary plant cell walls.", "T033"),
        ("T036", "floral nectar sugar yield", "Total hexose and sucrose mass secreted per flower.", "T016"),
        ("T037", "pollen wall thermal stability", "Desiccation resistance of sporopollenin pollen exine.", "T016"),
        ("T038", "cotyledon emergence area", "Photosynthetic surface area of expanded seedling cotyledons.", "T013"),
        ("T039", "hypocotyl elongation length", "Seedling hypocotyl length under low-light emergence.", "T001"),
        ("T040", "root gravitropic curvature angle", "Bending angle of root tip following 90-degree gravistimulation.", "T001"),
        ("T041", "symbiotic nodule nitrogenase activity", "Acetylene reduction rate of nitrogen-fixing root nodules.", "T001"),
        ("T042", "fruit abscission retention force", "Detachment force required at the pedicel abscission zone.", "T001"),
        ("T043", "shoot tiller bud outgrowth", "Release of axillary buds into productive lateral tillers.", "T022"),
        ("T044", "plant height gibberellin response", "Internode elongation sensitivity to endogenous bioactive gibberellin.", "T001"),
        ("T045", "trichome monoterpene yield", "Essential oil monoterpene concentration in glandular trichomes.", "T001"),
        ("T046", "thylakoid quantum yield Fv/Fm", "Maximum photochemical efficiency of Photosystem II.", "T028"),
        ("T047", "root starch statolith content", "Amyloplast starch granule density in root cap columella.", "T001"),
        ("T048", "vacuolar titratable acidity", "Organic acid and proton accumulation maintained by tonoplast pumps.", "T001"),
        ("T049", "suberin periderm content", "Aliphatic suberin polyester content in protective root periderm.", "T001"),
        ("T050", "petiole vascular bundle count", "Number of discrete vascular strands in leaf petiole cross-section.", "T002"),
    ]
    for code, lbl, defn, parent in extra_traits:
        specs.append(ClassSpec(f"{TRAIT_NS}{code}", lbl, defn, [f"{lbl} phenotype"], [f"{TRAIT_NS}{parent}"], [], []))
    assert len(specs) == 50
    return specs


def _build_chem_classes() -> list[ClassSpec]:
    specs: list[ClassSpec] = [
        ClassSpec(f"{CHEM_NS}C001", "plant metabolite or chemical entity", "Small-molecule primary or secondary metabolite or structural biopolymer in plants.", ["phytochemical entity"], [], [], []),
        ClassSpec(f"{CHEM_NS}C002", "flavonoid compound", "Broad polyphenolic C6-C3-C6 secondary metabolite family including anthocyanins, flavonols, and flavones.", ["plant flavonoid"], [f"{CHEM_NS}C001"], [], []),
        ClassSpec(f"{CHEM_NS}C003", "anthocyanin glycoside", "Water-soluble vacuolar flavylium glycoside pigment imparting red to blue hues.", ["anthocyanin pigment"], [f"{CHEM_NS}C002"], [f"{CHEM_NS}C004"], []),
        ClassSpec(f"{CHEM_NS}C004", "flavonol glycoside", "3-hydroxyflavone glycoside derivative such as kaempferol or quercetin glycoside.", ["flavonol derivative"], [f"{CHEM_NS}C002"], [f"{CHEM_NS}C003"], []),
        ClassSpec(f"{CHEM_NS}C005", "phytohormone signaling molecule", "Endogenous low-concentration plant growth regulator.", ["plant hormone compound"], [f"{CHEM_NS}C001"], [], []),
        ClassSpec(f"{CHEM_NS}C006", "indole-3-acetic acid auxin", "Primary natural auxin phytohormone regulating apical dominance and root initiation.", ["IAA auxin"], [f"{CHEM_NS}C005"], [f"{CHEM_NS}C007"], []),
        ClassSpec(f"{CHEM_NS}C007", "trans-zeatin cytokinin", "Adenine-derived cytokinin phytohormone promoting shoot cell division.", ["zeatin cytokinin"], [f"{CHEM_NS}C005"], [f"{CHEM_NS}C006"], []),
        ClassSpec(f"{CHEM_NS}C008", "terpenoid metabolite", "Broad isoprenoid natural product class derived from C5 IPP/DMAPP units.", ["isoprenoid compound"], [f"{CHEM_NS}C001"], [], []),
        ClassSpec(f"{CHEM_NS}C009", "monoterpene volatile", "C10 volatile isoprenoid constituent such as limonene or pinene secreted by trichomes.", ["C10 monoterpene"], [f"{CHEM_NS}C008"], [], []),
        ClassSpec(f"{CHEM_NS}C010", "diterpene ent-kaurene", "C20 tetracyclic diterpene intermediate in gibberellin biosynthesis.", ["C20 diterpene"], [f"{CHEM_NS}C008"], [], []),
        ClassSpec(f"{CHEM_NS}C011", "phenylpropanoid metabolite", "Aromatic compound synthesized from L-phenylalanine via cinnamate.", ["hydroxycinnamate family"], [f"{CHEM_NS}C001"], [], []),
        ClassSpec(f"{CHEM_NS}C012", "lignin monolignol precursor", "Specific p-coumaryl, coniferyl, or sinapyl alcohol monomer polymerized into lignin.", ["monolignol alcohol"], [f"{CHEM_NS}C011"], [], []),
        ClassSpec(f"{CHEM_NS}C013", "sinapoyl malate ester", "UV-absorbing soluble hydroxycinnamate ester accumulated in leaf epidermis.", ["sinapate ester"], [f"{CHEM_NS}C011"], [], []),
        # Root class with NO parent for acronym homonym Trap 14 (ROS)
        ClassSpec(f"{CHEM_NS}C014", "ROS reactive oxygen species", "Chemically reactive oxygen-containing free radicals and peroxides such as superoxide and H2O2.", ["reactive oxygen radical"], [], [], []),
        ClassSpec(f"{CHEM_NS}C015", "hydrogen peroxide molecule", "Diffusible non-radical reactive oxygen signaling and oxidative molecule H2O2.", ["H2O2"], [f"{CHEM_NS}C014"], [], []),
        ClassSpec(f"{CHEM_NS}C016", "meristematic nucleotide pool", "Deoxyribonucleotide triphosphate pool fueling meristematic DNA replication.", ["dNTP mitotic pool"], [f"{CHEM_NS}C001"], [], []),
    ]
    extra_chems = [
        ("C017", "crystalline cellulose microfibril", "Beta-1,4-glucan structural polysaccharide forming cell wall load-bearing fibrils.", "C001"),
        ("C018", "polymeric guaiacyl-syringyl lignin", "Cross-linked aromatic phenylpropanoid polymer reinforcing xylem and sclerenchyma.", "C011"),
        ("C019", "aliphatic suberin polyester", "Glycerol-linked omega-hydroxy fatty acid barrier polymer in endodermis.", "C001"),
        ("C020", "epicuticular alkane wax", "Very-long-chain C29-C31 alkane lipid Waterproofing aerial cuticle.", "C001"),
        ("C021", "pectic rhamnogalacturonan mucilage", "Hydrophilic branched cell wall polysaccharide extruded from seed testa.", "C001"),
        ("C022", "sporopollenin biopolymer", "Chemically inert polyhydroxylated aliphatic-aromatic polymer of pollen exine.", "C001"),
        ("C023", "sucrose transport disaccharide", "Non-reducing glucose-fructose disaccharide translocated in phloem sap.", "C001"),
        ("C024", "nectar hexose mixture", "Extracellular D-glucose and D-fructose monosaccharide blend in floral nectar.", "C001"),
        ("C025", "amyloplast amylopectin starch", "Branched alpha-1,4/1,6-glucan reserve polymer in plastids.", "C001"),
        ("C026", "proline osmolyte", "Compatible zwitterionic amino acid accumulated during drought osmotic adjustment.", "C001"),
        ("C027", "abscisic acid sesquiterpenoid", "C15 sesquiterpenoid phytohormone inducing seed dormancy and stomatal closure.", "C005"),
        ("C028", "bioactive gibberellin GA4", "Diterpenoid acid hormone stimulating stem elongation and germination.", "C005"),
        ("C029", "strigolactone carlactone derivative", "Carotenoid-derived hormone inhibiting axillary shoot branching.", "C005"),
        ("C030", "jasmonoyl-isoleucine conjugate", "Oxylipin phytohormone activating wound and defense transcription.", "C005"),
        ("C031", "salicylic acid phenolic signal", "2-hydroxybenzoic acid signaling molecule mediating systemic acquired resistance.", "C005"),
        ("C032", "chlorophyll a magnesium chlorin", "Primary light-harvesting tetrapyrrole pigment in thylakoid reaction centers.", "C001"),
        ("C033", "beta-carotene plastidial terpene", "C40 tetraterpenoid accessory pigment and strigolactone precursor.", "C008"),
        ("C034", "callose beta-1,3-glucan", "Dynamic glucan polymer constricting plasmodesmata neck regions.", "C001"),
        ("C035", "ascorbate antioxidant anion", "Redox-buffering L-ascorbic acid cofactor scavenging hydrogen peroxide.", "C001"),
        ("C036", "glutathione tripeptide thiol", "Gamma-L-glutamyl-L-cysteinylglycine cellular redox buffer.", "C001"),
        ("C037", "naringenin chalcone intermediate", "Open-chain C15 flavonoid precursor formed by chalcone synthase.", "C002"),
        ("C038", "quercetin 3-O-rutinoside", "Flavonol disaccharide rutin conferring epidermal UV-B protection.", "C004"),
        ("C039", "cyanidin 3-O-glucoside", "Major red-magenta anthocyanin glucoside in pigmented leaves and fruits.", "C003"),
        ("C040", "coniferyl alcohol monolignol", "Guaiacyl monolignol precursor of gymnosperm and dicot xylem lignin.", "C012"),
        ("C041", "limonene cyclic monoterpene", "Volatile monocyclic C10 terpene abundant in glandular trichomes.", "C009"),
        ("C042", "leghemoglobin heme cofactor", "Oxygen-buffering heme prosthetic group in nitrogen-fixing root nodules.", "C001"),
        ("C043", "malate vacuolar dicarboxylate", "C4 organic acid anion accumulated inside the plant central vacuole.", "C001"),
        ("C044", "cutin hydroxy-fatty acid polyester", "C16/C18 esterified lipid matrix scaffolding the epidermal cuticle.", "C001"),
        ("C045", "ribulose-1,5-bisphosphate", "Five-carbon CO2 acceptor substrate of RuBisCO in mesophyll stroma.", "C001"),
        ("C046", "alpha-tocopherol plastidial quinone", "Lipid-soluble vitamin E antioxidant protecting thylakoid membranes.", "C008"),
        ("C047", "nicotianamine metal chelator", "Non-proteinogenic amino acid transporting iron and zinc in vascular sap.", "C001"),
        ("C048", "raffinose oligosaccharide", "Trisaccharide desiccation protectant accumulated in maturing seeds.", "C001"),
        ("C049", "ferulic acid wall cross-linker", "Hydroxycinnamic acid ester cross-linking arabinoxylans in cell walls.", "C011"),
        ("C050", "brassinolide polyhydroxysteroid", "C28 brassinosteroid hormone promoting vascular differentiation and cell expansion.", "C005"),
    ]
    for code, lbl, defn, parent in extra_chems:
        specs.append(ClassSpec(f"{CHEM_NS}{code}", lbl, defn, [f"{lbl} chemical"], [f"{CHEM_NS}{parent}"], [], []))
    assert len(specs) == 50
    return specs


def build_toy_hub(out_dir: Path) -> ToyHubBundle:
    """Synthesize the deterministic 200-class, 4-ontology toy biological hub and 60 SSSOM mappings."""
    ont_dir = out_dir / "ontologies"
    anat_specs = _build_anat_classes()
    proc_specs = _build_proc_classes()
    trait_specs = _build_trait_classes()
    chem_specs = _build_chem_classes()

    lbl_map: dict[str, str] = {}
    parent1_map: dict[str, str | None] = {}
    for spec_list in (anat_specs, proc_specs, trait_specs, chem_specs):
        for s in spec_list:
            lbl_map[s.iri] = s.label
            parent1_map[s.iri] = s.parents[0] if s.parents else None

    ont_paths = {
        "TOY_ANAT": _write_owl_ontology(ANAT_NS.rstrip("#"), anat_specs, ont_dir / "toy_anat.owl"),
        "TOY_PROC": _write_owl_ontology(PROC_NS.rstrip("#"), proc_specs, ont_dir / "toy_proc.owl"),
        "TOY_TRAIT": _write_owl_ontology(TRAIT_NS.rstrip("#"), trait_specs, ont_dir / "toy_trait.owl"),
        "TOY_CHEM": _write_owl_ontology(CHEM_NS.rstrip("#"), chem_specs, ont_dir / "toy_chem.owl"),
    }

    def _rec(
        mid: str,
        s_iri: str,
        pred: str,
        o_iri: str,
        conf: float,
        w_dir: str = "sub_to_obj_parent",
    ) -> MappingRecord:
        return MappingRecord(
            mapping_id=mid,
            subject_id=s_iri,
            predicate_id=pred,
            object_id=o_iri,
            confidence=conf,
            subject_label=lbl_map[s_iri],
            object_label=lbl_map[o_iri],
            subject_parent1_id=parent1_map[s_iri],
            object_parent1_id=parent1_map[o_iri],
            weaken_direction=w_dir,
        )

    # 15 Planted Incoherence & Conservativity Traps (all input as over-strong skos:exactMatch)
    traps: list[tuple[MappingRecord, str]] = [
        # 1. Disjointness incoherence: specific xylem vessel -> broad vascular trait
        (_rec("m_trap_01", f"{ANAT_NS}A003", "skos:exactMatch", f"{TRAIT_NS}T002", 0.88, "sub_to_obj_parent"), "downgrade_sub"),
        # 2. Disjointness incoherence: broad flavonoid compound -> specific anthocyanin trait
        (_rec("m_trap_02", f"{CHEM_NS}C002", "skos:exactMatch", f"{TRAIT_NS}T006", 0.87, "obj_to_sub_parent"), "downgrade_sup"),
        # 3. 3-ontology compositional Unsat (pairwise clean!): root hair tip -> root hair developmental process
        (_rec("m_trap_03", f"{ANAT_NS}A006", "skos:exactMatch", f"{PROC_NS}P003", 0.86, "sub_to_obj_parent"), "downgrade_sub"),
        # 4. Sibling hormone collision requiring weaken_ancestor to P005 (phytohormone signaling pathway)
        (_rec("m_trap_04", f"{CHEM_NS}C006", "skos:exactMatch", f"{PROC_NS}P007", 0.81, "sub_to_obj_parent"), "weaken_ancestor"),
        # 5. Cross-kingdom homonym "cortex" requiring negate_drop
        (_rec("m_trap_05", f"{ANAT_NS}A009", "skos:exactMatch", f"{PROC_NS}P008", 0.72, "sub_to_obj_parent"), "negate_drop"),
        # 6. Diamond conservativity violation: specific leaf mesophyll -> broad photosynthetic process
        (_rec("m_trap_06", f"{ANAT_NS}A010", "skos:exactMatch", f"{PROC_NS}P010", 0.89, "sub_to_obj_parent"), "downgrade_sub"),
        # 7. Diamond conservativity violation: broad abiotic stress tolerance -> specific drought adjustment
        (_rec("m_trap_07", f"{TRAIT_NS}T010", "skos:exactMatch", f"{PROC_NS}P014", 0.88, "obj_to_sub_parent"), "downgrade_sup"),
        # 8. 4-ontology compositional cycle collapsing meristem hierarchy
        (_rec("m_trap_08", f"{ANAT_NS}A024", "skos:exactMatch", f"{TRAIT_NS}T022", 0.85, "sub_to_obj_parent"), "downgrade_sub"),
        # 9. 3-ontology terpenoid conservativity violation: broad terpenoid -> specific monoterpene trichome apex
        (_rec("m_trap_09", f"{CHEM_NS}C008", "skos:exactMatch", f"{ANAT_NS}A016", 0.84, "obj_to_sub_parent"), "downgrade_sup"),
        # 10. Seed dormancy vs germination cousin collision requiring weaken_ancestor to P016
        (_rec("m_trap_10", f"{TRAIT_NS}T014", "skos:exactMatch", f"{PROC_NS}P017", 0.79, "sub_to_obj_parent"), "weaken_ancestor"),
        # 11. OWL 2 EL existential role restriction propagation (exists part_of)
        (_rec("m_trap_11", f"{ANAT_NS}A015", "skos:exactMatch", f"{PROC_NS}P023", 0.86, "sub_to_obj_parent"), "downgrade_sub"),
        # 12. Organ generality mismatch: broad reproductive organ -> specific floral carpel morphology
        (_rec("m_trap_12", f"{ANAT_NS}A012", "skos:exactMatch", f"{TRAIT_NS}T017", 0.87, "obj_to_sub_parent"), "downgrade_sup"),
        # 13. Metabolic step vs pathway: specific monolignol precursor -> broad phenylpropanoid pathway
        (_rec("m_trap_13", f"{CHEM_NS}C012", "skos:exactMatch", f"{PROC_NS}P019", 0.88, "sub_to_obj_parent"), "downgrade_sub"),
        # 14. Acronym collision "ROS" (Root Organogenesis Score vs Reactive Oxygen Species) -> negate_drop
        (_rec("m_trap_14", f"{TRAIT_NS}T020", "skos:exactMatch", f"{CHEM_NS}C014", 0.69, "sub_to_obj_parent"), "negate_drop"),
        # 15. Parallel dual-bridge conservativity alongside Trap 6: leaf mesophyll -> photosynthetic leaf capacity
        (_rec("m_trap_15", f"{ANAT_NS}A010", "skos:exactMatch", f"{TRAIT_NS}T028", 0.85, "sub_to_obj_parent"), "downgrade_sub"),
    ]

    # 45 Clean, Globally Coherent & Conservative Cross-Ontology Mappings
    clean_specs: list[tuple[str, str, str, float]] = [
        # Supporting context bridges for compositional traps (all 100% valid!)
        (f"{ANAT_NS}A004", "skos:exactMatch", f"{TRAIT_NS}T004", 0.98),
        (f"{CHEM_NS}C004", "skos:exactMatch", f"{TRAIT_NS}T007", 0.98),
        (f"{TRAIT_NS}T009", "skos:exactMatch", f"{ANAT_NS}A007", 0.97),
        (f"{PROC_NS}P004", "skos:exactMatch", f"{TRAIT_NS}T009", 0.97),
        (f"{CHEM_NS}C006", "skos:broadMatch", f"{PROC_NS}P006", 0.96),
        (f"{CHEM_NS}C007", "skos:broadMatch", f"{PROC_NS}P007", 0.96),
        (f"{ANAT_NS}A008", "skos:broadMatch", f"{PROC_NS}P009", 0.96),
        (f"{ANAT_NS}A011", "skos:broadMatch", f"{PROC_NS}P010", 0.96),
        (f"{PROC_NS}P015", "skos:broadMatch", f"{TRAIT_NS}T010", 0.96),
        (f"{ANAT_NS}A023", "skos:broadMatch", f"{PROC_NS}P024", 0.96),
        (f"{PROC_NS}P024", "skos:broadMatch", f"{CHEM_NS}C016", 0.96),
        (f"{CHEM_NS}C016", "skos:broadMatch", f"{TRAIT_NS}T022", 0.96),
        (f"{ANAT_NS}A016", "skos:broadMatch", f"{PROC_NS}P022", 0.96),
        (f"{PROC_NS}P022", "skos:broadMatch", f"{CHEM_NS}C009", 0.96),
        (f"{TRAIT_NS}T015", "skos:exactMatch", f"{PROC_NS}P017", 0.97),
        (f"{TRAIT_NS}T014", "skos:exactMatch", f"{PROC_NS}P018", 0.97),
        (f"{ANAT_NS}A027", "skos:broadMatch", f"{PROC_NS}P023", 0.96),
        (f"{ANAT_NS}A013", "skos:exactMatch", f"{TRAIT_NS}T017", 0.98),
        (f"{CHEM_NS}C013", "skos:broadMatch", f"{PROC_NS}P019", 0.96),
        (f"{CHEM_NS}C014", "skos:exactMatch", f"{TRAIT_NS}T021", 0.98),
        (f"{PROC_NS}P010", "skos:exactMatch", f"{TRAIT_NS}T028", 0.98),
        # 24 Independent clean cross-hub equivalence & subsumption alignments
        (f"{PROC_NS}P025", "skos:exactMatch", f"{TRAIT_NS}T003", 0.95),
        (f"{CHEM_NS}C023", "skos:broadMatch", f"{PROC_NS}P026", 0.94),
        (f"{CHEM_NS}C003", "skos:exactMatch", f"{PROC_NS}P027", 0.95),
        (f"{CHEM_NS}C038", "skos:broadMatch", f"{PROC_NS}P028", 0.93),
        (f"{ANAT_NS}A014", "skos:exactMatch", f"{TRAIT_NS}T018", 0.95),
        (f"{ANAT_NS}A018", "skos:exactMatch", f"{TRAIT_NS}T031", 0.95),
        (f"{ANAT_NS}A019", "skos:exactMatch", f"{TRAIT_NS}T032", 0.95),
        (f"{CHEM_NS}C021", "skos:exactMatch", f"{PROC_NS}P031", 0.94),
        (f"{ANAT_NS}A022", "skos:exactMatch", f"{PROC_NS}P033", 0.94),
        (f"{CHEM_NS}C020", "skos:broadMatch", f"{TRAIT_NS}T027", 0.93),
        (f"{ANAT_NS}A025", "skos:exactMatch", f"{PROC_NS}P034", 0.95),
        (f"{PROC_NS}P034", "skos:exactMatch", f"{TRAIT_NS}T024", 0.95),
        (f"{ANAT_NS}A029", "skos:exactMatch", f"{PROC_NS}P035", 0.94),
        (f"{PROC_NS}P035", "skos:exactMatch", f"{TRAIT_NS}T025", 0.94),
        (f"{ANAT_NS}A030", "skos:exactMatch", f"{PROC_NS}P036", 0.95),
        (f"{CHEM_NS}C019", "skos:broadMatch", f"{PROC_NS}P036", 0.93),
        (f"{ANAT_NS}A043", "skos:exactMatch", f"{PROC_NS}P037", 0.95),
        (f"{PROC_NS}P037", "skos:exactMatch", f"{TRAIT_NS}T046", 0.95),
        (f"{ANAT_NS}A040", "skos:exactMatch", f"{PROC_NS}P038", 0.94),
        (f"{PROC_NS}P038", "skos:exactMatch", f"{TRAIT_NS}T040", 0.94),
        (f"{CHEM_NS}C017", "skos:exactMatch", f"{PROC_NS}P039", 0.95),
        (f"{PROC_NS}P039", "skos:exactMatch", f"{TRAIT_NS}T035", 0.95),
        (f"{ANAT_NS}A048", "skos:exactMatch", f"{PROC_NS}P045", 0.95),
        (f"{PROC_NS}P045", "skos:exactMatch", f"{TRAIT_NS}T041", 0.95),
    ]
    assert len(clean_specs) == 45

    mappings: list[MappingRecord] = []
    gt_actions: dict[str, str] = {}
    trap_ids: list[str] = []

    for rec, gt in traps:
        mappings.append(rec)
        gt_actions[rec.mapping_id] = gt
        trap_ids.append(rec.mapping_id)

    for idx, (s_iri, pred, o_iri, conf) in enumerate(clean_specs, start=1):
        mid = f"m_clean_{idx:02d}"
        mappings.append(_rec(mid, s_iri, pred, o_iri, conf))
        gt_actions[mid] = "keep_equiv"

    sssom_path = out_dir / "toy_hub.sssom.tsv"
    write_sssom_tsv(mappings, sssom_path, mapping_set_id="https://clasp-ont.org/toy/toy_hub.sssom.tsv")

    return ToyHubBundle(
        ontology_paths=ont_paths,
        sssom_path=sssom_path,
        mappings=mappings,
        ground_truth_actions=gt_actions,
        trap_ids=trap_ids,
        namespace_hints={
            "TOY_ANAT": ANAT_NS,
            "TOY_PROC": PROC_NS,
            "TOY_TRAIT": TRAIT_NS,
            "TOY_CHEM": CHEM_NS,
        },
    )
