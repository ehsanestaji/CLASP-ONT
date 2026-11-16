import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.logging.Level;
import java.util.logging.Logger;

import org.semanticweb.elk.owlapi.ElkReasonerFactory;
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.model.AxiomType;
import org.semanticweb.owlapi.model.IRI;
import org.semanticweb.owlapi.model.MissingImportHandlingStrategy;
import org.semanticweb.owlapi.model.OWLAxiom;
import org.semanticweb.owlapi.model.OWLClass;
import org.semanticweb.owlapi.model.OWLDataFactory;
import org.semanticweb.owlapi.model.OWLOntology;
import org.semanticweb.owlapi.model.OWLOntologyLoaderConfiguration;
import org.semanticweb.owlapi.model.OWLOntologyManager;
import org.semanticweb.owlapi.model.parameters.Imports;
import org.semanticweb.owlapi.reasoner.InferenceType;
import org.semanticweb.owlapi.reasoner.Node;
import org.semanticweb.owlapi.reasoner.NodeSet;
import org.semanticweb.owlapi.reasoner.OWLReasoner;

/**
 * Headless OWL 2 EL streaming closure and incremental QuickXplain MUS engine
 * compiled directly against vendor/robot.jar (OWLAPI + ELK).
 */
public final class ElkClosureRunner {

    private static final int MAX_ALT_CORES_PER_TARGET = 5;

    private static final class MappingAxiomEntry {
        final String mappingId;
        final String actionTag;
        final OWLAxiom axiom;
        final String key;

        MappingAxiomEntry(String mappingId, String actionTag, OWLAxiom axiom) {
            this.mappingId = mappingId;
            this.actionTag = actionTag;
            this.axiom = axiom;
            this.key = mappingId + ":" + actionTag;
        }
    }

    public static void main(String[] args) throws Exception {
        Logger.getLogger("org.semanticweb.elk").setLevel(Level.WARNING);
        if (args.length < 2) {
            System.err.println("Usage:");
            System.err.println("  ElkClosureRunner closure <unsat.tsv> <closure.tsv> <direct.tsv> <ont1.owl> [ont2.owl ...]");
            System.err.println("  ElkClosureRunner mus <mappings.tsv> <targets.tsv> <cores_out.tsv> <ont1.owl> [ont2.owl ...]");
            System.exit(2);
        }
        String mode = args[0];
        if ("closure".equals(mode)) {
            runClosure(args);
        } else if ("mus".equals(mode)) {
            runMus(args);
        } else {
            throw new IllegalArgumentException("Unknown mode: " + mode);
        }
    }

    private static OWLOntology loadMergedBase(OWLOntologyManager manager, List<String> ontPaths) throws Exception {
        OWLOntologyLoaderConfiguration cfg = new OWLOntologyLoaderConfiguration()
                .setMissingImportHandlingStrategy(MissingImportHandlingStrategy.SILENT);
        manager.setOntologyLoaderConfiguration(cfg);
        OWLOntology merged = manager.createOntology(IRI.create("http://clasp-ont.org/merged-hub.owl"));
        for (String p : ontPaths) {
            OWLOntology subOnt = manager.loadOntologyFromOntologyDocument(new File(p));
            for (OWLAxiom ax : subOnt.getAxioms(Imports.INCLUDED)) {
                if (ax.isLogicalAxiom()) {
                    AxiomType<?> t = ax.getAxiomType();
                    if (t != AxiomType.CLASS_ASSERTION
                            && t != AxiomType.OBJECT_PROPERTY_ASSERTION
                            && t != AxiomType.DATA_PROPERTY_ASSERTION
                            && t != AxiomType.SAME_INDIVIDUAL
                            && t != AxiomType.DIFFERENT_INDIVIDUALS) {
                        manager.addAxiom(merged, ax);
                    }
                } else if (ax.getAxiomType() == AxiomType.DECLARATION) {
                    manager.addAxiom(merged, ax);
                }
            }
            manager.removeOntology(subOnt);
        }
        return merged;
    }

    private static void runClosure(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("closure mode requires <unsat.tsv> <closure.tsv> <direct.tsv> <ont1.owl>...");
        }
        File unsatOut = new File(args[1]);
        File closureOut = new File(args[2]);
        File directOut = new File(args[3]);
        List<String> ontPaths = new ArrayList<>();
        for (int i = 4; i < args.length; i++) {
            ontPaths.add(args[i]);
        }

        OWLOntologyManager manager = OWLManager.createOWLOntologyManager();
        OWLOntology merged = loadMergedBase(manager, ontPaths);
        OWLDataFactory df = manager.getOWLDataFactory();
        OWLClass owlThing = df.getOWLThing();
        OWLClass owlNothing = df.getOWLNothing();

        OWLReasoner reasoner = new ElkReasonerFactory().createReasoner(merged);
        try {
            reasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY);
            boolean consistent = reasoner.isConsistent();
            Set<OWLClass> allClasses = merged.getClassesInSignature(Imports.INCLUDED);
            Set<OWLClass> unsatClasses = new LinkedHashSet<>();

            if (!consistent) {
                for (OWLClass cls : allClasses) {
                    if (!cls.equals(owlThing) && !cls.equals(owlNothing)) {
                        unsatClasses.add(cls);
                    }
                }
            } else {
                Node<OWLClass> bottom = reasoner.getBottomClassNode();
                for (OWLClass cls : bottom.getEntitiesMinusBottom()) {
                    if (!cls.equals(owlThing) && !cls.equals(owlNothing)) {
                        unsatClasses.add(cls);
                    }
                }
            }

            try (BufferedWriter uw = writer(unsatOut)) {
                uw.write("class_iri\n");
                for (OWLClass u : unsatClasses) {
                    uw.write(u.getIRI().toString());
                    uw.write('\n');
                }
            }

            try (BufferedWriter cw = writer(closureOut);
                 BufferedWriter dw = writer(directOut)) {
                cw.write("sub_iri\tsup_iri\n");
                dw.write("sub_iri\tdirect_sup_iri\n");
                if (consistent) {
                    for (OWLClass cls : allClasses) {
                        if (cls.equals(owlThing) || cls.equals(owlNothing) || unsatClasses.contains(cls)) {
                            continue;
                        }
                        String subIri = cls.getIRI().toString();
                        Node<OWLClass> eqNode = reasoner.getEquivalentClasses(cls);
                        for (OWLClass eq : eqNode.getEntities()) {
                            if (!eq.equals(owlThing) && !eq.equals(owlNothing) && !unsatClasses.contains(eq)) {
                                cw.write(subIri);
                                cw.write('\t');
                                cw.write(eq.getIRI().toString());
                                cw.write('\n');
                            }
                        }
                        NodeSet<OWLClass> supers = reasoner.getSuperClasses(cls, false);
                        for (OWLClass sup : supers.getFlattened()) {
                            if (!sup.equals(owlThing) && !sup.equals(owlNothing) && !unsatClasses.contains(sup)) {
                                cw.write(subIri);
                                cw.write('\t');
                                cw.write(sup.getIRI().toString());
                                cw.write('\n');
                            }
                        }
                        NodeSet<OWLClass> directSupers = reasoner.getSuperClasses(cls, true);
                        for (OWLClass dsup : directSupers.getFlattened()) {
                            if (!dsup.equals(owlThing) && !dsup.equals(owlNothing) && !unsatClasses.contains(dsup)) {
                                dw.write(subIri);
                                dw.write('\t');
                                dw.write(dsup.getIRI().toString());
                                dw.write('\n');
                            }
                        }
                    }
                }
            }
        } finally {
            reasoner.dispose();
        }
    }

    private static void runMus(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("mus mode requires <mappings.tsv> <targets.tsv> <cores_out.tsv> <ont1.owl>...");
        }
        File mappingsFile = new File(args[1]);
        File targetsFile = new File(args[2]);
        File coresOutFile = new File(args[3]);
        List<String> ontPaths = new ArrayList<>();
        for (int i = 4; i < args.length; i++) {
            ontPaths.add(args[i]);
        }

        OWLOntologyManager manager = OWLManager.createOWLOntologyManager();
        OWLOntology merged = loadMergedBase(manager, ontPaths);
        OWLDataFactory df = manager.getOWLDataFactory();

        List<MappingAxiomEntry> allMappings = new ArrayList<>();
        try (BufferedReader br = reader(mappingsFile)) {
            String line = br.readLine(); // header: mapping_id \t action_tag \t rel \t lhs_iri \t rhs_iri
            while ((line = br.readLine()) != null) {
                if (line.isBlank()) continue;
                String[] parts = line.split("\t", -1);
                if (parts.length < 5) continue;
                String mid = parts[0];
                String tag = parts[1];
                String rel = parts[2];
                OWLClass lhs = df.getOWLClass(IRI.create(parts[3]));
                OWLClass rhs = df.getOWLClass(IRI.create(parts[4]));
                OWLAxiom ax = "equiv".equals(rel)
                        ? df.getOWLEquivalentClassesAxiom(lhs, rhs)
                        : df.getOWLSubClassOfAxiom(lhs, rhs);
                allMappings.add(new MappingAxiomEntry(mid, tag, ax));
            }
        }

        List<String[]> targets = new ArrayList<>();
        try (BufferedReader br = reader(targetsFile)) {
            String line = br.readLine(); // header: kind \t arg1 \t arg2
            while ((line = br.readLine()) != null) {
                if (line.isBlank()) continue;
                String[] parts = line.split("\t", -1);
                if (parts.length >= 2) {
                    targets.add(parts);
                }
            }
        }

        Set<OWLAxiom> currentlyActive = new LinkedHashSet<>();
        OWLReasoner reasoner = new ElkReasonerFactory().createReasoner(merged);
        try {
            reasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY);

            try (BufferedWriter out = writer(coresOutFile)) {
                out.write("target_idx\tkind\targ1\targ2\tcore_members\n");
                for (int tIdx = 0; tIdx < targets.size(); tIdx++) {
                    String[] t = targets.get(tIdx);
                    String kind = t[0];
                    OWLClass c1 = df.getOWLClass(IRI.create(t[1]));
                    OWLClass c2 = (t.length > 2 && !t[2].isEmpty()) ? df.getOWLClass(IRI.create(t[2])) : null;

                    // Verify base ontology alone does not entail target
                    syncOntologyAxioms(manager, merged, currentlyActive, Collections.emptyList());
                    reasoner.flush();
                    if (holds(reasoner, kind, c1, c2)) {
                        continue;
                    }

                    // Discover primary + alternative disjoint/parallel MUS cores via Reiter hitting exclusions
                    Set<String> blockedKeys = new HashSet<>();
                    for (int alt = 0; alt < MAX_ALT_CORES_PER_TARGET; alt++) {
                        List<MappingAxiomEntry> pool = new ArrayList<>(allMappings.size());
                        for (MappingAxiomEntry e : allMappings) {
                            if (!blockedKeys.contains(e.key)) {
                                pool.add(e);
                            }
                        }
                        if (pool.isEmpty()) break;
                        syncOntologyAxioms(manager, merged, currentlyActive, pool);
                        reasoner.flush();
                        if (!holds(reasoner, kind, c1, c2)) {
                            break;
                        }
                        List<MappingAxiomEntry> core = quickXplain(
                                manager, merged, reasoner, currentlyActive,
                                Collections.emptyList(), false, pool, kind, c1, c2
                        );
                        if (core.isEmpty()) break;

                        StringBuilder sb = new StringBuilder();
                        for (int i = 0; i < core.size(); i++) {
                            if (i > 0) sb.append(';');
                            sb.append(core.get(i).key);
                        }
                        out.write(Integer.toString(tIdx));
                        out.write('\t');
                        out.write(kind);
                        out.write('\t');
                        out.write(t[1]);
                        out.write('\t');
                        out.write(c2 != null ? t[2] : "");
                        out.write('\t');
                        out.write(sb.toString());
                        out.write('\n');

                        // Block the first element of this minimal core to uncover any parallel bridge path
                        blockedKeys.add(core.get(0).key);
                    }
                }
            }
        } finally {
            reasoner.dispose();
        }
    }

    private static List<MappingAxiomEntry> quickXplain(
            OWLOntologyManager manager,
            OWLOntology ont,
            OWLReasoner reasoner,
            Set<OWLAxiom> currentlyActive,
            List<MappingAxiomEntry> background,
            boolean deltaNonEmpty,
            List<MappingAxiomEntry> candidates,
            String kind,
            OWLClass c1,
            OWLClass c2
    ) {
        if (deltaNonEmpty) {
            syncOntologyAxioms(manager, ont, currentlyActive, background);
            reasoner.flush();
            if (holds(reasoner, kind, c1, c2)) {
                return Collections.emptyList();
            }
        }
        if (candidates.size() == 1) {
            return Collections.singletonList(candidates.get(0));
        }
        int mid = candidates.size() / 2;
        List<MappingAxiomEntry> cLeft = candidates.subList(0, mid);
        List<MappingAxiomEntry> cRight = candidates.subList(mid, candidates.size());

        List<MappingAxiomEntry> bgPlusLeft = new ArrayList<>(background.size() + cLeft.size());
        bgPlusLeft.addAll(background);
        bgPlusLeft.addAll(cLeft);
        List<MappingAxiomEntry> delta2 = quickXplain(
                manager, ont, reasoner, currentlyActive, bgPlusLeft, !cLeft.isEmpty(), cRight, kind, c1, c2
        );

        List<MappingAxiomEntry> bgPlusDelta2 = new ArrayList<>(background.size() + delta2.size());
        bgPlusDelta2.addAll(background);
        bgPlusDelta2.addAll(delta2);
        List<MappingAxiomEntry> delta1 = quickXplain(
                manager, ont, reasoner, currentlyActive, bgPlusDelta2, !delta2.isEmpty(), cLeft, kind, c1, c2
        );

        List<MappingAxiomEntry> result = new ArrayList<>(delta1.size() + delta2.size());
        result.addAll(delta1);
        result.addAll(delta2);
        return result;
    }

    private static void syncOntologyAxioms(
            OWLOntologyManager manager,
            OWLOntology ont,
            Set<OWLAxiom> currentlyActive,
            List<MappingAxiomEntry> desiredEntries
    ) {
        Set<OWLAxiom> desired = new LinkedHashSet<>(desiredEntries.size() * 2);
        for (MappingAxiomEntry e : desiredEntries) {
            desired.add(e.axiom);
        }
        List<OWLAxiom> toRemove = new ArrayList<>();
        for (OWLAxiom ax : currentlyActive) {
            if (!desired.contains(ax)) {
                toRemove.add(ax);
            }
        }
        for (OWLAxiom ax : toRemove) {
            manager.removeAxiom(ont, ax);
            currentlyActive.remove(ax);
        }
        for (OWLAxiom ax : desired) {
            if (currentlyActive.add(ax)) {
                manager.addAxiom(ont, ax);
            }
        }
    }

    private static boolean holds(OWLReasoner reasoner, String kind, OWLClass c1, OWLClass c2) {
        if (!reasoner.isConsistent()) {
            return true;
        }
        if ("UNSAT".equals(kind)) {
            return !reasoner.isSatisfiable(c1);
        }
        if (!reasoner.isSatisfiable(c1)) {
            return true;
        }
        if (reasoner.getEquivalentClasses(c1).contains(c2)) {
            return true;
        }
        return reasoner.getSuperClasses(c1, false).containsEntity(c2);
    }

    private static BufferedWriter writer(File f) throws Exception {
        if (f.getParentFile() != null) {
            f.getParentFile().mkdirs();
        }
        return new BufferedWriter(new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8));
    }

    private static BufferedReader reader(File f) throws Exception {
        return new BufferedReader(new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8));
    }
}
