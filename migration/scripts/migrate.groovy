/*
 * Neptune -> JanusGraph migration.
 *
 * Reads the JSON that Neptune returns for an openCypher query and loads it into
 * JanusGraph. Runs inside the janusgraph/janusgraph image, which already ships
 * Groovy, JsonSlurper and the JanusGraph libraries:
 *
 *   bin/gremlin.sh -e migrate.groovy
 *
 * Use run-migration.sh rather than calling this directly -- it sets JAVA_OPTIONS,
 * without which the JVM inherits the image's -Xmx4096m and is OOMKilled.
 *
 * Driven by environment variables (see CONFIG) because Gremlin Console script
 * arguments are awkward to pass through kubectl exec.
 *
 * Phases (PHASE):
 *   schema  derive property keys / labels / index from the data and create them
 *   load    insert vertices, then edges
 *   verify  counts and per-label breakdown
 *   all     the three above in order (default)
 *
 * Input format -- Neptune's openCypher response:
 *
 *   {"results": [ {"i": {"~id": "...", "~entityType": "node",
 *                        "~labels": ["Incident"], "~properties": {...}},
 *                  "r": {...relationship...}, "c": {...node...}} ]}
 *
 * A node carries ~id / ~labels / ~properties; a relationship carries ~id /
 * ~type / ~start / ~end / ~properties. One query returns both, so a single file
 * holds vertices and edges mixed -- they are split here on shape.
 *
 * Both Graph Explorer and the Neptune workbench notebook produce this, because
 * the shape follows the query language, not the tool.
 *
 * Design notes:
 *  - The schema is DERIVED FROM THE DATA, never hardcoded. That is what makes
 *    this repeatable: a different extract creates the right schema unchanged.
 *  - Neptune ids are Strings and JanusGraph assigns its own Long ids, so the
 *    Neptune id is kept in a `neptuneId` property with a composite index.
 *  - That index is deliberately NOT unique: uniqueness in JanusGraph requires
 *    ConsistencyModifier.LOCK, which throttles write throughput. Neptune ids are
 *    already unique by construction.
 *  - Edges keep their Neptune id as a property too, because JanusGraph supports
 *    custom ids on vertices only.
 */

import org.janusgraph.core.JanusGraphFactory
import org.janusgraph.core.Cardinality
import org.janusgraph.core.Multiplicity
import org.janusgraph.graphdb.database.management.ManagementSystem
import org.janusgraph.core.schema.SchemaStatus
import org.apache.tinkerpop.gremlin.structure.T
import org.apache.tinkerpop.gremlin.structure.Vertex
import org.apache.tinkerpop.gremlin.structure.VertexProperty
import org.apache.commons.configuration2.builder.fluent.Configurations
import groovy.json.JsonSlurper

// ---------------------------------------------------------------- CONFIG ----

PROPS       = System.getenv('JANUSGRAPH_PROPS') ?: '/etc/opt/janusgraph/janusgraph.properties'
EXTRACT     = System.getenv('EXTRACT_JSON')     ?: '/tmp/migration/extract.json'
EXTRACT2    = System.getenv('EXTRACT_JSON_2')   ?: ''     // optional second file
PHASE       = (System.getenv('PHASE')           ?: 'all').toLowerCase()
BATCH       = (System.getenv('BATCH_SIZE')      ?: '200') as int
BATCH_LOAD  = (System.getenv('BATCH_LOADING')   ?: 'true').toBoolean()
ID_PROP     = System.getenv('ID_PROPERTY')      ?: 'neptuneId'
LABELS_PROP = System.getenv('LABELS_PROPERTY')  ?: 'neptuneLabels'  // vertices, SET
EDGE_LBL_P  = System.getenv('EDGE_LABEL_PROPERTY') ?: 'neptuneLabel' // edges, SINGLE

// ------------------------------------------------------------ input layer ----

def isReservedKey(k) { k == 'id' || k == 'label' || k == 'IN' || k == 'OUT' }

/* Turns one Neptune element into the flat shape the rest of the script uses:
 * id, label, optionally IN/OUT, plus the properties. */
def normalize(n) {
    def m = [:]
    m['id'] = n['~id']
    if (n['~entityType'] == 'relationship') {
        m['label'] = n['~type']
        m['OUT'] = n['~start']
        m['IN']  = n['~end']
    } else {
        def labels = (n['~labels'] ?: []) as List
        // Joined with '::' because that is how Neptune spells multiple labels
        // over the Gremlin API, and the split happens in one place downstream.
        m['label'] = labels ? labels.join('::') : 'vertex'
    }
    (n['~properties'] ?: [:]).each { k, v ->
        def key = k.toString()
        // A property genuinely called id/label/IN/OUT would collide with the
        // structural keys above; rename rather than silently drop it.
        if (isReservedKey(key)) key = "prop_${key}"
        m[key] = v
    }
    return m
}

/* Walks a result row, which may nest elements at any depth (collect(), path
 * projections, ...), pulling out every graph element and ignoring nulls -- an
 * OPTIONAL MATCH that found nothing yields "r": null, "c": null. */
def collectElements(node, List out) {
    if (node == null) return
    if (node instanceof Map) {
        if (node['~entityType'] != null) { out << normalize(node); return }
        node.each { k, v -> collectElements(v, out) }
        return
    }
    if (node instanceof List) node.each { collectElements(it, out) }
}

def readElements(path) {
    def f = new File(path)
    if (!f.exists()) throw new FileNotFoundException("Missing input file: ${path}")
    def parsed = new JsonSlurper().parse(f)
    def rows
    if (parsed instanceof Map && parsed.results != null) rows = parsed.results
    else if (parsed instanceof List)                     rows = parsed
    else throw new IllegalStateException(
        "${path}: expected Neptune openCypher JSON, i.e. {\"results\": [...]}, " +
        "or a bare array of rows. Got ${parsed?.getClass()?.simpleName}.")

    def out = []
    rows.each { collectElements(it, out) }
    return out
}

def isEdgeEl(el) { el['IN'] != null && el['OUT'] != null }

/* openCypher repeats the same node on every row (RETURN i, r, c), so elements
 * arrive many times over. Keeps the first occurrence, preserving order. */
def dedupById(list) {
    def seen = new LinkedHashMap()
    int anon = 0
    list.each { el ->
        def k = el['id']?.toString()
        seen.putIfAbsent(k != null ? k : "__anon_${anon++}", el)
    }
    return seen.values() as List
}

// ----------------------------------------------------------------- types ----

def valueType(v) {
    if (v instanceof Boolean)                                          return 'Boolean'
    if (v instanceof Integer)                                          return 'Integer'
    if (v instanceof Long || v instanceof BigInteger)                  return 'Long'
    if (v instanceof Double || v instanceof Float || v instanceof BigDecimal) return 'Double'
    if (v instanceof List)                                             return 'List'
    return 'String'
}

def javaClassFor(t) {
    switch (t) {
        case 'Boolean': return Boolean.class
        case 'Integer': return Integer.class
        case 'Long':    return Long.class
        case 'Double':  return Double.class
        default:        return String.class
    }
}

/* Widens rather than failing when one key carries values of different types.
 * Numbers widen to the roomiest numeric type; anything genuinely mixed becomes
 * String, which always round-trips. */
def widen(a, b) {
    if (a == b) return a
    def numeric = ['Integer', 'Long', 'Double'] as Set
    if (!numeric.contains(a) || !numeric.contains(b)) return 'String'
    if (a == 'Double' || b == 'Double') return 'Double'
    return 'Long'
}

def splitLabels(raw) { raw.toString().split('::').findAll { it } }

/* '::' means two different things depending on the element.
 *
 * On a VERTEX it is Neptune's multi-label separator. JanusGraph allows exactly
 * one label, so the first segment wins and the full set is kept in a
 * SET-cardinality property.
 *
 * On an EDGE it separates the forward and reverse descriptors of a single
 * relationship type -- the ServiceNow convention, e.g.
 *   "Parking Access Control Supports Off-street Parking::
 *    Off-Street Parking is supported by Parking Access Control"
 * The direction is already carried by the edge, so the reverse descriptor is
 * redundant for traversal. The forward one becomes the label and the original
 * string is preserved in a property, so nothing is lost. */
def edgeLabel(raw) { def p = splitLabels(raw); p ? p[0] : 'edge' }

// ------------------------------------------------------------ schema phase --

def deriveSchema(elements, boolean isEdge) {
    def spec = [:]   // property name -> [type: ..., multi: boolean]
    elements.each { el ->
        el.each { k, v ->
            if (isReservedKey(k)) return
            def t = valueType(v)
            def multi = false
            if (t == 'List') {
                multi = true
                t = v ? valueType(v[0]) : 'String'
            }
            def prev = spec[k]
            if (prev == null) spec[k] = [type: t, multi: multi]
            else { prev.type = widen(prev.type, t); prev.multi = prev.multi || multi }
        }
    }
    return spec
}

def applySchema(graph, vertexEls, edgeEls, idProp, labelsProp, edgeLblProp) {
    def vSpec = deriveSchema(vertexEls, false)
    def eSpec = deriveSchema(edgeEls, true)

    def vLabels = vertexEls.collectMany { splitLabels(it['label']) }.toSet()
    def eLabels = edgeEls.collect { edgeLabel(it['label']) }.toSet()
    def multiLabelled = vertexEls.count { splitLabels(it['label']).size() > 1 }
    def compositeRels = edgeEls.count { it['label'].toString().contains('::') }

    def mgmt = graph.openManagement()
    def created = [keys: [], vlabels: [], elabels: [], index: false]

    if (!mgmt.containsPropertyKey(idProp)) {
        mgmt.makePropertyKey(idProp).dataType(String.class)
            .cardinality(Cardinality.SINGLE).make()
        created.keys << "${idProp} (String, SINGLE)"
    }
    if (multiLabelled && !mgmt.containsPropertyKey(labelsProp)) {
        mgmt.makePropertyKey(labelsProp).dataType(String.class)
            .cardinality(Cardinality.SET).make()
        created.keys << "${labelsProp} (String, SET)"
    }
    if (compositeRels && !mgmt.containsPropertyKey(edgeLblProp)) {
        mgmt.makePropertyKey(edgeLblProp).dataType(String.class)
            .cardinality(Cardinality.SINGLE).make()
        created.keys << "${edgeLblProp} (String, SINGLE)"
    }

    [[vSpec, true], [eSpec, false]].each { pair ->
        pair[0].each { name, s ->
            if (mgmt.containsPropertyKey(name)) return
            // Edge properties are always single-valued in Neptune.
            def card = (s.multi && pair[1]) ? Cardinality.SET : Cardinality.SINGLE
            mgmt.makePropertyKey(name).dataType(javaClassFor(s.type)).cardinality(card).make()
            created.keys << "${name} (${javaClassFor(s.type).simpleName}, ${card})"
        }
    }

    vLabels.each { l -> if (!mgmt.containsVertexLabel(l)) { mgmt.makeVertexLabel(l).make(); created.vlabels << l } }
    eLabels.each { l -> if (!mgmt.containsEdgeLabel(l))   { mgmt.makeEdgeLabel(l).multiplicity(Multiplicity.MULTI).make(); created.elabels << l } }

    def indexName = "by${idProp.capitalize()}"
    if (mgmt.getGraphIndex(indexName) == null) {
        // Not unique on purpose -- see the header comment.
        mgmt.buildIndex(indexName, Vertex.class)
            .addKey(mgmt.getPropertyKey(idProp))
            .buildCompositeIndex()
        created.index = true
    }
    mgmt.commit()

    // Target ENABLED, not the default REGISTERED. A composite index built on an
    // empty graph goes straight to ENABLED and never reports REGISTERED, so the
    // default target can only ever time out after a pointless minute of waiting.
    def report = ManagementSystem.awaitGraphIndexStatus(graph, indexName)
                     .status(SchemaStatus.ENABLED)
                     .call()

    println "-- schema --"
    println "  property keys : ${created.keys.size()} created"
    created.keys.each { println "      ${it}" }
    println "  vertex labels : ${created.vlabels.size()} created ${created.vlabels as List}"
    println "  edge labels   : ${created.elabels.size()} created ${created.elabels as List}"
    def idxState = report.getSucceeded() ? 'ENABLED' : 'NOT ENABLED -- needs a reindex before it is usable'
    println "  index         : ${indexName} ${created.index ? 'created' : 'already present'} -> ${idxState}"
    if (multiLabelled) {
        println "  NOTE: ${multiLabelled} vertices carry multiple Neptune labels."
        println "        JanusGraph allows one label per vertex: the first segment"
        println "        becomes the label, all of them are kept in '${labelsProp}'."
    }
    if (compositeRels) {
        println "  NOTE: ${compositeRels} edges have a forward::reverse relationship type."
        println "        The forward descriptor becomes the label; the original string"
        println "        is kept in '${edgeLblProp}'."
    }
}

// -------------------------------------------------------------- load phase --

def setProperty(element, key, value, boolean isVertex) {
    if (value == null) return
    if (value instanceof List) {
        value.each { item ->
            if (item != null) element.property(VertexProperty.Cardinality.set, key, item)
        }
    } else if (isVertex) {
        element.property(VertexProperty.Cardinality.single, key, value)
    } else {
        element.property(key, value)
    }
}

def loadVertices(graph, elements, idProp, labelsProp, int batch) {
    println "-- vertices --"
    def idMap = [:]        // Neptune id -> JanusGraph vertex id
    int n = 0, skipped = 0
    elements.each { el ->
        def nid = el['id']?.toString()
        if (nid == null) { skipped++; return }
        def labels = splitLabels(el['label'])
        def v = graph.addVertex(T.label, labels ? labels[0] : 'vertex')
        v.property(VertexProperty.Cardinality.single, idProp, nid)
        if (labels.size() > 1) labels.each { v.property(VertexProperty.Cardinality.set, labelsProp, it) }
        el.each { k, val -> if (!isReservedKey(k)) setProperty(v, k, val, true) }
        idMap[nid] = v.id()
        if (++n % batch == 0) { graph.tx().commit(); println "  vertices: ${n}" }
    }
    graph.tx().commit()
    println "  loaded  : ${n}"
    if (skipped) println "  skipped : ${skipped} (no id)"
    return idMap
}

/* Resolves a Neptune id to a JanusGraph vertex: first from the map built during
 * the vertex phase, falling back to the composite index so the edge phase can
 * also run on its own. */
def resolve(graph, idMap, cache, idProp, nid) {
    if (nid == null) return null
    def known = idMap[nid]
    if (known != null) {
        def it = graph.vertices(known)
        return it.hasNext() ? it.next() : null
    }
    if (cache.containsKey(nid)) return cache[nid]
    def t = graph.traversal().V().has(idProp, nid)
    def v = t.hasNext() ? t.next() : null
    cache[nid] = v
    return v
}

def loadEdges(graph, elements, idMap, idProp, edgeLblProp, int batch) {
    println "-- edges --"
    def cache = [:]
    int n = 0
    def orphans = []
    elements.each { el ->
        def eid   = el['id']?.toString()
        def full  = el['label']?.toString() ?: 'edge'
        def label = edgeLabel(full)
        def outId = el['OUT']?.toString()
        def inId  = el['IN']?.toString()

        def from = resolve(graph, idMap, cache, idProp, outId)
        def to   = resolve(graph, idMap, cache, idProp, inId)
        if (from == null || to == null) {
            orphans << [id: eid, out: outId, inn: inId,
                        missing: ((from == null ? 'OUT ' : '') + (to == null ? 'IN' : '')).trim()]
            return
        }
        def e = from.addEdge(label, to)
        if (eid != null) e.property(idProp, eid)
        // Only when it carried a reverse descriptor -- no point storing a copy
        // of the label otherwise.
        if (full != label) e.property(edgeLblProp, full)
        el.each { k, val -> if (!isReservedKey(k)) setProperty(e, k, val, false) }
        if (++n % batch == 0) { graph.tx().commit(); println "  edges: ${n}" }
    }
    graph.tx().commit()
    println "  loaded  : ${n}"
    println "  orphans : ${orphans.size()}"
    if (orphans) {
        println "  (endpoints outside the extract -- expected when the query was"
        println "   limited to a subset of incidents)"
        orphans.take(10).each { println "      ${it.id}: missing ${it.missing} (OUT=${it.out} IN=${it.inn})" }
        if (orphans.size() > 10) println "      ... and ${orphans.size() - 10} more"
    }
}

// ------------------------------------------------------------ verify phase --

def verify(graph, idProp) {
    def g = graph.traversal()
    println "-- verify --"
    println "  vertices : ${g.V().count().next()}"
    println "  edges    : ${g.E().count().next()}"
    println "  by vertex label:"
    def vc = g.V().groupCount().by(T.label).next()
    if (vc.isEmpty()) println "      (none)" else vc.sort { -it.value }.each { k, v -> println "      ${k}: ${v}" }
    println "  by edge label:"
    def ec = g.E().groupCount().by(T.label).next()
    if (ec.isEmpty()) println "      (none)" else ec.sort { -it.value }.each { k, v -> println "      ${k}: ${v}" }
    def missing = g.V().hasNot(idProp).count().next()
    println "  vertices without ${idProp}: ${missing}${missing ? '   <-- unexpected' : ''}"
}

// -------------------------------------------------------------------- main --

println "Neptune -> JanusGraph migration"
println "  properties : ${PROPS}"
println "  extract    : ${EXTRACT}${EXTRACT2 ? ' + ' + EXTRACT2 : ''}"
println "  phase      : ${PHASE}"

conf = new Configurations().properties(new File(PROPS))
if (BATCH_LOAD && PHASE != 'verify') {
    // Skips consistency checks that are pointless when loading into an empty
    // graph. Do NOT use this to load on top of existing data.
    conf.setProperty('storage.batch-loading', true)
    println "  batch-loading enabled (initial load into an empty graph)"
}

graph = JanusGraphFactory.open(conf)

try {
    vertexEls = []
    edgeEls   = []
    if (PHASE != 'verify') {
        def all = readElements(EXTRACT)
        if (EXTRACT2 && new File(EXTRACT2).exists()) all += readElements(EXTRACT2)
        vertexEls = dedupById(all.findAll { !isEdgeEl(it) })
        edgeEls   = dedupById(all.findAll { isEdgeEl(it) })
        println "  parsed     : ${vertexEls.size()} vertices, ${edgeEls.size()} edges" +
                " (from ${all.size()} elements)"
    }

    if (PHASE in ['schema', 'all']) applySchema(graph, vertexEls, edgeEls, ID_PROP, LABELS_PROP, EDGE_LBL_P)

    if (PHASE in ['load', 'all']) {
        idMap = loadVertices(graph, vertexEls, ID_PROP, LABELS_PROP, BATCH)
        loadEdges(graph, edgeEls, idMap, ID_PROP, EDGE_LBL_P, BATCH)
    }

    if (PHASE in ['verify', 'all']) verify(graph, ID_PROP)

    println "OK"
} finally {
    graph.close()
}
