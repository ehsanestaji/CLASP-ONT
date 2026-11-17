import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
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
import org.semanticweb.owlapi.model.OWLClassExpression;
import org.semanticweb.owlapi.model.OWLDataFactory;
import org.semanticweb.owlapi.model.OWLDisjointClassesAxiom;
import org.semanticweb.owlapi.model.OWLEntity;
import org.semanticweb.owlapi.model.OWLEquivalentClassesAxiom;
import org.semanticweb.owlapi.model.OWLObjectComplementOf;
import org.semanticweb.owlapi.model.OWLObjectIntersectionOf;
import org.semanticweb.owlapi.model.OWLOntology;
import org.semanticweb.owlapi.model.OWLOntologyLoaderConfiguration;
import org.semanticweb.owlapi.model.OWLOntologyManager;
import org.semanticweb.owlapi.model.OWLSubClassOfAxiom;
import org.semanticweb.owlapi.model.parameters.Imports;
import org.semanticweb.owlapi.reasoner.InferenceType;
import org.semanticweb.owlapi.reasoner.Node;
import org.semanticweb.owlapi.reasoner.NodeSet;
import org.semanticweb.owlapi.reasoner.OWLReasoner;

import com.clarkparsia.owlapi.modularity.locality.LocalityClass;
import com.clarkparsia.owlapi.modularity.locality.SyntacticLocalityEvaluator;
import org.semanticweb.owlapi.model.OWLObjectProperty;
import org.semanticweb.owlapi.model.OWLDataProperty;

/**
 * Headless OWL 2 EL streaming closure + radius-capped BOT Syntactic-Locality QuickXplain MUS engine.
 */
public final class ElkClosureRunner {

    private static final int MAX_LOCAL_CANDIDATE_MAPPINGS = 120;

    private static final class MappingAxiomEntry {
        final String mappingId;
        final String actionTag;
        final OWLAxiom axiom;
        final OWLClass lhs;
        final OWLClass rhs;
        final String key;

        MappingAxiomEntry(String mappingId, String actionTag, OWLAxiom axiom, OWLClass lhs, OWLClass rhs) {
            this.mappingId = mappingId;
            this.actionTag = actionTag;
            this.axiom = axiom;
            this.lhs = lhs;
            this.rhs = rhs;
            this.key = mappingId + ":" + actionTag;
        }
    }

    public static void main(String[] args) throws Exception {
        Logger.getLogger("org.semanticweb.elk").setLevel(Level.SEVERE);
        if (args.length < 2) {
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

    private static boolean matchesSigFilter(String iri, String[] filters) {
        if (filters == null || filters.length == 0) {
            return true;
        }
        for (String f : filters) {
            if (!f.isEmpty() && iri.contains(f)) {
                return true;
            }
        }
        return false;
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

    private static void extractNamedDisjoints(OWLOntology ont, File disjointOut) throws Exception {
        Set<String> seenPairs = new LinkedHashSet<>();
        for (OWLDisjointClassesAxiom dax : ont.getAxioms(AxiomType.DISJOINT_CLASSES, Imports.INCLUDED)) {
            List<OWLClass> named = new ArrayList<>();
            for (OWLClassExpression ce : dax.getClassExpressions()) {
                if (!ce.isAnonymous()) {
                    named.add(ce.asOWLClass());
                }
            }
            for (int i = 0; i < named.size(); i++) {
                for (int j = i + 1; j < named.size(); j++) {
                    String a = named.get(i).getIRI().toString();
                    String b = named.get(j).getIRI().toString();
                    if (!a.equals(b)) {
                        seenPairs.add(a.compareTo(b) < 0 ? (a + "\t" + b) : (b + "\t" + a));
                    }
                }
            }
        }
        for (OWLSubClassOfAxiom sax : ont.getAxioms(AxiomType.SUBCLASS_OF, Imports.INCLUDED)) {
            OWLClassExpression sub = sax.getSubClass();
            OWLClassExpression sup = sax.getSuperClass();
            if (!sub.isAnonymous() && sup instanceof OWLObjectComplementOf comp) {
                OWLClassExpression op = comp.getOperand();
                if (!op.isAnonymous()) {
                    String a = sub.asOWLClass().getIRI().toString();
                    String b = op.asOWLClass().getIRI().toString();
                    if (!a.equals(b)) {
                        seenPairs.add(a.compareTo(b) < 0 ? (a + "\t" + b) : (b + "\t" + a));
                    }
                }
            } else if (sup.isOWLNothing() && sub instanceof OWLObjectIntersectionOf inter) {
                List<OWLClass> named = new ArrayList<>();
                for (OWLClassExpression ce : inter.getOperands()) {
                    if (!ce.isAnonymous()) {
                        named.add(ce.asOWLClass());
                    }
                }
                if (named.size() == 2) {
                    String a = named.get(0).getIRI().toString();
                    String b = named.get(1).getIRI().toString();
                    if (!a.equals(b)) {
                        seenPairs.add(a.compareTo(b) < 0 ? (a + "\t" + b) : (b + "\t" + a));
                    }
                }
            }
        }
        try (BufferedWriter dw = writer(disjointOut)) {
            dw.write("cls_a\tcls_b\n");
            for (String pair : seenPairs) {
                dw.write(pair);
                dw.write('\n');
            }
        }
    }

    private static void runClosure(String[] args) throws Exception {
        if (args.length < 6) {
            throw new IllegalArgumentException("closure mode requires <unsat.tsv> <closure.tsv> <direct.tsv> <disjoint.tsv> <ont1.owl>...");
        }
        File unsatOut = new File(args[1]);
        File closureOut = new File(args[2]);
        File directOut = new File(args[3]);
        File disjointOut = new File(args[4]);
        List<String> ontPaths = new ArrayList<>();
        for (int i = 5; i < args.length; i++) {
            ontPaths.add(args[i]);
        }

        String filterEnv = System.getenv("CLASP_SIG_SUBSTRINGS");
        String[] sigFilters = (filterEnv != null && !filterEnv.isBlank()) ? filterEnv.split(",") : null;
        String conjOutEnv = System.getenv("CLASP_CONJ_OUT");
        boolean emitConj = (conjOutEnv != null && !conjOutEnv.isBlank());

        OWLOntologyManager manager = OWLManager.createOWLOntologyManager();
        OWLOntology merged = loadMergedBase(manager, ontPaths);
        extractNamedDisjoints(merged, disjointOut);

        OWLDataFactory df = manager.getOWLDataFactory();
        OWLClass owlThing = df.getOWLThing();
        OWLClass owlNothing = df.getOWLNothing();

        List<String> conjRows = new ArrayList<>();
        if (emitConj) {
            Map<OWLClassExpression, OWLClass> anonMap = new HashMap<>();
            List<OWLAxiom> probeAxioms = new ArrayList<>();
            int anonSeq = 0;
            for (OWLEquivalentClassesAxiom eax : merged.getAxioms(AxiomType.EQUIVALENT_CLASSES, Imports.INCLUDED)) {
                List<OWLClass> named = new ArrayList<>();
                List<OWLObjectIntersectionOf> inters = new ArrayList<>();
                for (OWLClassExpression ce : eax.getClassExpressions()) {
                    if (!ce.isAnonymous()) {
                        named.add(ce.asOWLClass());
                    } else if (ce instanceof OWLObjectIntersectionOf io) {
                        inters.add(io);
                    }
                }
                for (OWLClass target : named) {
                    for (OWLObjectIntersectionOf io : inters) {
                        List<String> conjIris = new ArrayList<>();
                        for (OWLClassExpression op : io.getOperandsAsList()) {
                            if (!op.isAnonymous()) {
                                conjIris.add(op.asOWLClass().getIRI().toString());
                            } else {
                                OWLClass probe = anonMap.get(op);
                                if (probe == null) {
                                    anonSeq++;
                                    probe = df.getOWLClass(IRI.create("urn:clasp:anon:" + anonSeq));
                                    anonMap.put(op, probe);
                                    probeAxioms.add(df.getOWLSubClassOfAxiom(op, probe));
                                }
                                conjIris.add(probe.getIRI().toString());
                            }
                        }
                        if (conjIris.size() >= 2) {
                            conjRows.add(target.getIRI().toString() + "\t" + String.join(";", conjIris));
                        }
                    }
                }
            }
            for (OWLSubClassOfAxiom sax : merged.getAxioms(AxiomType.SUBCLASS_OF, Imports.INCLUDED)) {
                if (!sax.getSuperClass().isAnonymous() && sax.getSubClass() instanceof OWLObjectIntersectionOf io) {
                    OWLClass target = sax.getSuperClass().asOWLClass();
                    List<String> conjIris = new ArrayList<>();
                    for (OWLClassExpression op : io.getOperandsAsList()) {
                        if (!op.isAnonymous()) {
                            conjIris.add(op.asOWLClass().getIRI().toString());
                        } else {
                            OWLClass probe = anonMap.get(op);
                            if (probe == null) {
                                anonSeq++;
                                probe = df.getOWLClass(IRI.create("urn:clasp:anon:" + anonSeq));
                                anonMap.put(op, probe);
                                probeAxioms.add(df.getOWLSubClassOfAxiom(op, probe));
                            }
                            conjIris.add(probe.getIRI().toString());
                        }
                    }
                    if (conjIris.size() >= 2) {
                        conjRows.add(target.getIRI().toString() + "\t" + String.join(";", conjIris));
                    }
                }
            }
            if (!probeAxioms.isEmpty()) {
                manager.addAxioms(merged, new HashSet<>(probeAxioms));
            }
            try (BufferedWriter jw = writer(new File(conjOutEnv))) {
                jw.write("target_iri\tconjuncts\n");
                for (String cr : conjRows) {
                    jw.write(cr);
                    jw.write('\n');
                }
            }
        }

        OWLReasoner reasoner = new ElkReasonerFactory().createReasoner(merged);
        try {
            reasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY);
            boolean consistent = reasoner.isConsistent();
            Set<OWLClass> allClasses = merged.getClassesInSignature(Imports.INCLUDED);
            Set<OWLClass> unsatClasses = new LinkedHashSet<>();

            if (!consistent) {
                for (OWLClass cls : allClasses) {
                    if (!cls.equals(owlThing) && !cls.equals(owlNothing)
                            && !cls.getIRI().toString().startsWith("urn:clasp:anon:")) {
                        unsatClasses.add(cls);
                    }
                }
            } else {
                Node<OWLClass> bottom = reasoner.getBottomClassNode();
                for (OWLClass cls : bottom.getEntitiesMinusBottom()) {
                    if (!cls.equals(owlThing) && !cls.equals(owlNothing)
                            && !cls.getIRI().toString().startsWith("urn:clasp:anon:")) {
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
                        Set<OWLClass> eqSet = eqNode.getEntities();

                        if (emitConj) {
                            for (OWLClass eq : eqSet) {
                                if (!eq.equals(cls) && !eq.equals(owlThing) && !eq.equals(owlNothing)
                                        && !unsatClasses.contains(eq)) {
                                    dw.write(subIri);
                                    dw.write('\t');
                                    dw.write(eq.getIRI().toString());
                                    dw.write('\n');
                                }
                            }
                        }

                        // Always emit full direct superclasses (including BFO/PATO upper roots & anon witnesses)
                        NodeSet<OWLClass> directSupers = reasoner.getSuperClasses(cls, true);
                        for (OWLClass dsup : directSupers.getFlattened()) {
                            if (!dsup.equals(owlThing) && !dsup.equals(owlNothing) && !unsatClasses.contains(dsup)) {
                                dw.write(subIri);
                                dw.write('\t');
                                dw.write(dsup.getIRI().toString());
                                dw.write('\n');
                            }
                        }

                        if (subIri.startsWith("urn:clasp:anon:")) {
                            continue;
                        }

                        // Emit transitive closure only for primary hub signature classes when filter provided
                        if (!matchesSigFilter(subIri, sigFilters)) {
                            continue;
                        }
                        for (OWLClass eq : eqSet) {
                            if (!eq.equals(owlThing) && !eq.equals(owlNothing) && !unsatClasses.contains(eq)) {
                                String eqIri = eq.getIRI().toString();
                                if (!eqIri.startsWith("urn:clasp:anon:") && matchesSigFilter(eqIri, sigFilters)) {
                                    cw.write(subIri);
                                    cw.write('\t');
                                    cw.write(eqIri);
                                    cw.write('\n');
                                }
                            }
                        }
                        NodeSet<OWLClass> supers = reasoner.getSuperClasses(cls, false);
                        for (OWLClass sup : supers.getFlattened()) {
                            if (!sup.equals(owlThing) && !sup.equals(owlNothing)
                                    && !unsatClasses.contains(sup) && !eqSet.contains(sup)) {
                                String supIri = sup.getIRI().toString();
                                if (!supIri.startsWith("urn:clasp:anon:") && matchesSigFilter(supIri, sigFilters)) {
                                    cw.write(subIri);
                                    cw.write('\t');
                                    cw.write(supIri);
                                    cw.write('\n');
                                }
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
        OWLOntology fullOnt = loadMergedBase(manager, ontPaths);
        OWLDataFactory df = manager.getOWLDataFactory();
        Set<OWLAxiom> allBaseLogical = new HashSet<>(fullOnt.getLogicalAxioms(Imports.INCLUDED));

        Map<OWLClass, List<OWLClass>> upAdj = new HashMap<>();
        for (OWLAxiom ax : allBaseLogical) {
            Set<OWLClass> sig = ax.getClassesInSignature();
            if (sig.size() >= 2 && sig.size() <= 16) {
                for (OWLClass a : sig) {
                    List<OWLClass> nbrs = upAdj.computeIfAbsent(a, k -> new ArrayList<>());
                    for (OWLClass b : sig) {
                        if (!a.equals(b)) {
                            nbrs.add(b);
                        }
                    }
                }
            }
        }

        List<MappingAxiomEntry> allMappings = new ArrayList<>();
        Map<OWLAxiom, MappingAxiomEntry> mapByAxiom = new HashMap<>();
        Map<OWLClass, List<MappingAxiomEntry>> mapByClass = new HashMap<>();
        try (BufferedReader br = reader(mappingsFile)) {
            String line = br.readLine();
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
                MappingAxiomEntry entry = new MappingAxiomEntry(mid, tag, ax, lhs, rhs);
                allMappings.add(entry);
                mapByAxiom.put(ax, entry);
                manager.addAxiom(fullOnt, ax);
                mapByClass.computeIfAbsent(lhs, k -> new ArrayList<>()).add(entry);
                mapByClass.computeIfAbsent(rhs, k -> new ArrayList<>()).add(entry);
            }
        }

        List<String[]> targets = new ArrayList<>();
        try (BufferedReader br = reader(targetsFile)) {
            String line = br.readLine();
            while ((line = br.readLine()) != null) {
                if (line.isBlank()) continue;
                String[] parts = line.split("\t", -1);
                if (parts.length >= 2) {
                    targets.add(parts);
                }
            }
        }

        // Build exact event-driven BOTTOM_BOTTOM syntactic locality index once in O(|fullOnt|)
        SyntacticLocalityEvaluator botEval = new SyntacticLocalityEvaluator(LocalityClass.BOTTOM_BOTTOM);
        List<OWLAxiom> allLogicalList = new ArrayList<>(fullOnt.getLogicalAxioms(Imports.INCLUDED));
        int nAx = allLogicalList.size();
        OWLEntity[][] axSigs = new OWLEntity[nAx][];
        Set<OWLEntity> allPropsOnly = new HashSet<>();
        for (OWLObjectProperty op : fullOnt.getObjectPropertiesInSignature(Imports.INCLUDED)) {
            allPropsOnly.add(op);
        }
        for (OWLDataProperty dp : fullOnt.getDataPropertiesInSignature(Imports.INCLUDED)) {
            allPropsOnly.add(dp);
        }

        Map<OWLEntity, List<Integer>> axIndicesByTrigger = new HashMap<>();
        List<Integer> emptyNonLocalIndices = new ArrayList<>();
        Set<OWLEntity> emptySet = Collections.emptySet();

        for (int i = 0; i < nAx; i++) {
            OWLAxiom ax = allLogicalList.get(i);
            Set<OWLEntity> sig = ax.getSignature();
            OWLEntity[] sigArr = sig.toArray(new OWLEntity[0]);
            axSigs[i] = sigArr;
            if (!botEval.isLocal(ax, emptySet)) {
                emptyNonLocalIndices.add(i);
                continue;
            }
            // Monotonicity theorem: if ax is still BOT-local under allPropsOnly, it cannot become
            // non-local until at least one non-property entity (OWLClass/OWLIndividual) enters sigma.
            boolean propsCanTrigger = !botEval.isLocal(ax, allPropsOnly);
            for (OWLEntity ent : sigArr) {
                if (propsCanTrigger || !(ent instanceof OWLObjectProperty || ent instanceof OWLDataProperty)) {
                    axIndicesByTrigger.computeIfAbsent(ent, k -> new ArrayList<>()).add(i);
                }
            }
        }

        boolean[] inModStamp = new boolean[nAx];
        List<Integer> touchedIndices = new ArrayList<>(4096);

        try (BufferedWriter out = writer(coresOutFile)) {
            out.write("target_idx\tkind\targ1\targ2\tcore_members\n");
            Set<String> globalBlockedKeys = new HashSet<>();
            for (int tIdx = 0; tIdx < targets.size(); tIdx++) {
                String[] t = targets.get(tIdx);
                String kind = t[0];
                OWLClass c1 = df.getOWLClass(IRI.create(t[1]));
                OWLClass c2 = (t.length > 2 && !t[2].isEmpty()) ? df.getOWLClass(IRI.create(t[2])) : null;

                // Quick pre-skip: if c1 has a single incident mapping that is already globally blocked, check fast
                touchedIndices.clear();
                Set<OWLEntity> sigma = new HashSet<>(256);
                Deque<OWLEntity> workQ = new ArrayDeque<>(256);
                if (sigma.add(c1)) {
                    workQ.add(c1);
                }
                for (int empIdx : emptyNonLocalIndices) {
                    if (!inModStamp[empIdx]) {
                        inModStamp[empIdx] = true;
                        touchedIndices.add(empIdx);
                        for (OWLEntity e2 : axSigs[empIdx]) {
                            if (sigma.add(e2)) {
                                workQ.add(e2);
                            }
                        }
                    }
                }
                while (!workQ.isEmpty()) {
                    OWLEntity ent = workQ.poll();
                    List<Integer> candAx = axIndicesByTrigger.get(ent);
                    if (candAx == null) continue;
                    for (int axIdx : candAx) {
                        if (!inModStamp[axIdx]) {
                            OWLAxiom ax = allLogicalList.get(axIdx);
                            if (!botEval.isLocal(ax, sigma)) {
                                inModStamp[axIdx] = true;
                                touchedIndices.add(axIdx);
                                for (OWLEntity e2 : axSigs[axIdx]) {
                                    if (sigma.add(e2)) {
                                        workQ.add(e2);
                                    }
                                }
                            }
                        }
                    }
                }

                Set<OWLAxiom> botModule = new HashSet<>(touchedIndices.size() * 2 + 4);
                Set<OWLAxiom> localBase = new LinkedHashSet<>(touchedIndices.size());
                List<MappingAxiomEntry> allBotMappings = new ArrayList<>();
                for (int axIdx : touchedIndices) {
                    inModStamp[axIdx] = false; // reset stamp in O(|module|)
                    OWLAxiom ax = allLogicalList.get(axIdx);
                    botModule.add(ax);
                    if (allBaseLogical.contains(ax)) {
                        localBase.add(ax);
                    }
                    MappingAxiomEntry me = mapByAxiom.get(ax);
                    if (me != null && !globalBlockedKeys.contains(me.key)) {
                        allBotMappings.add(me);
                    }
                }

                if (allBotMappings.isEmpty()) continue;
                if (holdsFresh(localBase, Collections.emptyList(), kind, c1, c2)) {
                    continue;
                }

                // Order candidate mappings inside botModule by BFS proximity from c1 across logical axioms
                List<MappingAxiomEntry> localMappings = new ArrayList<>();
                Set<MappingAxiomEntry> seenM = new HashSet<>();
                Deque<OWLClass> bfsQ = new ArrayDeque<>();
                Set<OWLClass> seenC = new HashSet<>();
                bfsQ.add(c1);
                seenC.add(c1);
                if (c2 != null && seenC.add(c2)) {
                    bfsQ.add(c2);
                }
                while (!bfsQ.isEmpty() && localMappings.size() < MAX_LOCAL_CANDIDATE_MAPPINGS) {
                    OWLClass u = bfsQ.poll();
                    for (MappingAxiomEntry me : mapByClass.getOrDefault(u, Collections.emptyList())) {
                        if (botModule.contains(me.axiom) && !globalBlockedKeys.contains(me.key) && seenM.add(me)) {
                            localMappings.add(me);
                            if (localMappings.size() >= MAX_LOCAL_CANDIDATE_MAPPINGS) break;
                            if (seenC.add(me.lhs)) bfsQ.add(me.lhs);
                            if (seenC.add(me.rhs)) bfsQ.add(me.rhs);
                        }
                    }
                    for (OWLClass v : upAdj.getOrDefault(u, Collections.emptyList())) {
                        if (seenC.add(v)) {
                            bfsQ.add(v);
                        }
                    }
                }

                // Progressive radius check: closest 3 -> closest 25 -> closest 120 -> full complete botModule!
                List<MappingAxiomEntry> activePool = null;
                if (!localMappings.isEmpty()) {
                    if (localMappings.size() > 3) {
                        List<MappingAxiomEntry> r3 = localMappings.subList(0, 3);
                        if (holdsFresh(localBase, r3, kind, c1, c2)) {
                            activePool = r3;
                        }
                    }
                    if (activePool == null && localMappings.size() > 25) {
                        List<MappingAxiomEntry> r25 = localMappings.subList(0, 25);
                        if (holdsFresh(localBase, r25, kind, c1, c2)) {
                            activePool = r25;
                        }
                    }
                    if (activePool == null && holdsFresh(localBase, localMappings, kind, c1, c2)) {
                        activePool = localMappings;
                    }
                }
                if (activePool == null) {
                    if (holdsFresh(localBase, allBotMappings, kind, c1, c2)) {
                        activePool = allBotMappings;
                    } else {
                        continue;
                    }
                }

                List<MappingAxiomEntry> core = quickXplainFresh(
                        localBase, Collections.emptyList(), false, activePool, kind, c1, c2
                );
                if (!core.isEmpty()) {
                    StringBuilder sb = new StringBuilder();
                    for (int i = 0; i < core.size(); i++) {
                        if (i > 0) sb.append(';');
                        sb.append(core.get(i).key);
                    }
                    globalBlockedKeys.add(core.get(0).key);
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
                    out.flush();
                }
            }
        }
    }

    private static List<MappingAxiomEntry> quickXplainFresh(
            Set<OWLAxiom> localBase,
            List<MappingAxiomEntry> background,
            boolean deltaNonEmpty,
            List<MappingAxiomEntry> candidates,
            String kind,
            OWLClass c1,
            OWLClass c2
    ) throws Exception {
        if (deltaNonEmpty && holdsFresh(localBase, background, kind, c1, c2)) {
            return Collections.emptyList();
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
        List<MappingAxiomEntry> delta2 = quickXplainFresh(
                localBase, bgPlusLeft, !cLeft.isEmpty(), cRight, kind, c1, c2
        );

        List<MappingAxiomEntry> bgPlusDelta2 = new ArrayList<>(background.size() + delta2.size());
        bgPlusDelta2.addAll(background);
        bgPlusDelta2.addAll(delta2);
        List<MappingAxiomEntry> delta1 = quickXplainFresh(
                localBase, bgPlusDelta2, !delta2.isEmpty(), cLeft, kind, c1, c2
        );

        List<MappingAxiomEntry> result = new ArrayList<>(delta1.size() + delta2.size());
        result.addAll(delta1);
        result.addAll(delta2);
        return result;
    }

    private static boolean holdsFresh(
            Set<OWLAxiom> localBase,
            List<MappingAxiomEntry> activeMappings,
            String kind,
            OWLClass c1,
            OWLClass c2
    ) throws Exception {
        OWLOntologyManager mgr = OWLManager.createOWLOntologyManager();
        Set<OWLAxiom> combined = new HashSet<>(localBase.size() + activeMappings.size() + 4);
        combined.addAll(localBase);
        for (MappingAxiomEntry e : activeMappings) {
            combined.add(e.axiom);
        }
        OWLOntology scratch = mgr.createOntology(combined);
        OWLReasoner r = new ElkReasonerFactory().createReasoner(scratch);
        try {
            r.precomputeInferences(InferenceType.CLASS_HIERARCHY);
            if (!r.isConsistent()) {
                return true;
            }
            if ("UNSAT".equals(kind)) {
                return !r.isSatisfiable(c1);
            }
            if (!r.isSatisfiable(c1)) {
                return true;
            }
            if (r.getEquivalentClasses(c1).contains(c2)) {
                return true;
            }
            return r.getSuperClasses(c1, false).containsEntity(c2);
        } finally {
            r.dispose();
        }
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
