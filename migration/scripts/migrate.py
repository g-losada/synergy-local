#!/usr/bin/env python3
"""
Neptune -> JanusGraph migration.

Reads the JSON that Neptune returns for an openCypher query and loads it into
JanusGraph over the Gremlin Server's WebSocket endpoint.

    pip install gremlinpython
    kubectl -n janus-graph port-forward svc/janus-graph 8182:8182
    PHASE=all python3 migrate.py

Phases (PHASE):
    schema  derive property keys / labels / index from the data and create them
    load    insert vertices, then edges
    verify  counts and per-label breakdown
    all     the three above in order (default)

Input format -- Neptune's openCypher response:

    {"results": [ {"i": {"~id": "...", "~entityType": "node",
                         "~labels": ["Incident"], "~properties": {...}},
                   "r": {...relationship...}, "c": {...node...}} ]}

A node carries ~id / ~labels / ~properties; a relationship carries ~id / ~type /
~start / ~end / ~properties. One query returns both, so a single file holds
vertices and edges mixed -- they are split here on shape.

Why one block of Groovy lives inside this file
----------------------------------------------
Everything except schema creation is plain Python: parsing, transformation,
type inference, loading and verification all go through gremlinpython's bytecode
traversals.

Schema creation cannot. JanusGraph's management API (makePropertyKey,
makeVertexLabel, buildIndex) is a Java-only API with no Gremlin bytecode
equivalent, so no Python driver can express it. The only way to reach it from
outside the JVM is to submit a script the server evaluates -- which is what
SCHEMA_SCRIPT below does.

It is kept data-driven: Python decides *what* the schema should be and passes
that decision as a JSON binding; the Groovy only mechanically applies it. So the
Groovy never needs to change when the data changes.

Two things that will bite if changed carelessly
-----------------------------------------------
1. **The gremlinpython version must match the server's TinkerPop version.**
   This server runs TinkerPop 3.7.3 (`ls /opt/janusgraph/lib/gremlin-core-*`).
   gremlinpython 3.8.x compiles `iterate()` into a `discard()` step that a 3.7
   server rejects with "Could not locate method: DefaultGraphTraversal.discard()".
   See requirements.txt -- the pin is deliberate, not incidental.

2. **BATCH_SIZE is bounded by the server's `maxContentLength`**, 65536 bytes in
   janusgraph-server.yaml. Each batch is one bytecode message, so the limit is
   on bytes, not elements: 10 vertices x ~39 string properties fits, 100 does
   not, and the failure looks like "Connection was already closed" rather than
   anything mentioning size. Raise it only for narrower data.

Differences from the embedded Groovy version this replaces
----------------------------------------------------------
`storage.batch-loading` is not available here. It is a graph-open setting, and
this connects to an already-open graph on the server. Irrelevant at development
volume; for a full-volume load it costs throughput.
"""

import json
import os
import sys

from gremlin_python.driver.client import Client
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal
from gremlin_python.process.graph_traversal import __
from gremlin_python.process.traversal import Cardinality, T

# ------------------------------------------------------------------ CONFIG --

HERE = os.path.dirname(os.path.abspath(__file__))

GREMLIN_URL = os.getenv("GREMLIN_URL", "ws://localhost:8182/gremlin")
EXTRACT = os.getenv("EXTRACT_JSON", os.path.join(HERE, "..", "neptune", "responses", "extract.json"))
PHASE = os.getenv("PHASE", "all").lower()
BATCH = int(os.getenv("BATCH_SIZE", "10"))  # bounded by maxContentLength -- see module docstring
ID_PROP = os.getenv("ID_PROPERTY", "neptuneId")
LABELS_PROP = os.getenv("LABELS_PROPERTY", "neptuneLabels")       # vertices, SET
EDGE_LBL_PROP = os.getenv("EDGE_LABEL_PROPERTY", "neptuneLabel")  # edges, SINGLE

RESERVED = {"id", "label", "IN", "OUT"}

JAVA_CLASS = {
    "Boolean": "java.lang.Boolean",
    "Integer": "java.lang.Integer",
    "Long": "java.lang.Long",
    "Double": "java.lang.Double",
    "String": "java.lang.String",
}

# -------------------------------------------------------------- input layer --


def normalize(n):
    """One Neptune element -> the flat shape the rest of this script uses:
    id, label, optionally IN/OUT, plus the properties."""
    m = {"id": n["~id"]}
    if n.get("~entityType") == "relationship":
        m["label"] = n["~type"]
        m["OUT"] = n["~start"]
        m["IN"] = n["~end"]
    else:
        labels = n.get("~labels") or []
        # Joined with '::' because that is how Neptune spells multiple labels
        # over the Gremlin API; the split happens in one place downstream.
        m["label"] = "::".join(labels) if labels else "vertex"
    for k, v in (n.get("~properties") or {}).items():
        # A property genuinely called id/label/IN/OUT would collide with the
        # structural keys above; rename rather than silently drop it.
        m[f"prop_{k}" if k in RESERVED else k] = v
    return m


def collect_elements(node, out):
    """Walks a result row, which may nest elements at any depth, pulling out
    every graph element and ignoring nulls -- an OPTIONAL MATCH that found
    nothing yields "r": null, "c": null."""
    if node is None:
        return
    if isinstance(node, dict):
        if node.get("~entityType") is not None:
            out.append(normalize(node))
            return
        for v in node.values():
            collect_elements(v, out)
        return
    if isinstance(node, list):
        for v in node:
            collect_elements(v, out)


def read_elements(path):
    if not os.path.isfile(path):
        sys.exit(f"Missing input file: {path}\nExtract from Neptune first -- see ../neptune/queries.md")
    with open(path, encoding="utf-8") as f:
        parsed = json.load(f)
    if isinstance(parsed, dict) and "results" in parsed:
        rows = parsed["results"]
    elif isinstance(parsed, list):
        rows = parsed
    else:
        sys.exit(f'{path}: expected Neptune openCypher JSON, i.e. {{"results": [...]}}, or a bare array of rows.')
    out = []
    for row in rows:
        collect_elements(row, out)
    return out


def is_edge_el(el):
    return el.get("IN") is not None and el.get("OUT") is not None


def dedup_by_id(elements):
    """openCypher repeats the same node on every row (RETURN i, r, c), so
    elements arrive many times over. Keeps the first occurrence, in order."""
    seen = {}
    for i, el in enumerate(elements):
        key = str(el["id"]) if el.get("id") is not None else f"__anon_{i}"
        seen.setdefault(key, el)
    return list(seen.values())


# ------------------------------------------------------------------- types --


def value_type(v):
    # bool before int: in Python, isinstance(True, int) is True.
    if isinstance(v, bool):
        return "Boolean"
    if isinstance(v, int):
        return "Integer" if -(2**31) <= v < 2**31 else "Long"
    if isinstance(v, float):
        return "Double"
    if isinstance(v, list):
        return "List"
    return "String"


def widen(a, b):
    """Widens rather than failing when one key carries values of different
    types. Numbers widen to the roomiest numeric type; anything genuinely mixed
    becomes String, which always round-trips."""
    if a == b:
        return a
    numeric = {"Integer", "Long", "Double"}
    if a not in numeric or b not in numeric:
        return "String"
    return "Double" if "Double" in (a, b) else "Long"


def split_labels(raw):
    return [s for s in str(raw).split("::") if s]


def edge_label(raw):
    """'::' means two different things depending on the element.

    On a VERTEX it is Neptune's multi-label separator. JanusGraph allows exactly
    one label, so the first segment wins and the full set is kept in a
    SET-cardinality property.

    On an EDGE it separates the forward and reverse descriptors of a single
    relationship type -- the ServiceNow convention, e.g.
        "Parking Access Control Supports Off-street Parking::
         Off-Street Parking is supported by Parking Access Control"
    The direction is already carried by the edge, so the reverse descriptor is
    redundant for traversal. The forward one becomes the label and the original
    string is preserved in a property, so nothing is lost."""
    parts = split_labels(raw)
    return parts[0] if parts else "edge"


# ------------------------------------------------------------ schema phase --


def derive_schema(elements, is_vertex):
    spec = {}
    for el in elements:
        for k, v in el.items():
            if k in RESERVED:
                continue
            t = value_type(v)
            multi = False
            if t == "List":
                multi = True
                t = value_type(v[0]) if v else "String"
            if k not in spec:
                spec[k] = {"type": t, "multi": multi}
            else:
                spec[k]["type"] = widen(spec[k]["type"], t)
                spec[k]["multi"] = spec[k]["multi"] or multi
    return spec


def build_plan(vertex_els, edge_els):
    """Decides the whole schema in Python. The Groovy below only applies it."""
    v_spec = derive_schema(vertex_els, True)
    e_spec = derive_schema(edge_els, False)

    multi_labelled = sum(1 for el in vertex_els if len(split_labels(el["label"])) > 1)
    composite_rels = sum(1 for el in edge_els if "::" in str(el["label"]))

    keys, seen = [], set()

    def add_key(name, type_name, cardinality):
        if name in seen:
            return
        seen.add(name)
        keys.append({"name": name, "dataType": JAVA_CLASS[type_name], "cardinality": cardinality})

    add_key(ID_PROP, "String", "SINGLE")
    if multi_labelled:
        add_key(LABELS_PROP, "String", "SET")
    if composite_rels:
        add_key(EDGE_LBL_PROP, "String", "SINGLE")
    for spec, is_vertex in ((v_spec, True), (e_spec, False)):
        for name, s in spec.items():
            # Edge properties are always single-valued in Neptune.
            add_key(name, s["type"], "SET" if (s["multi"] and is_vertex) else "SINGLE")

    return {
        "propertyKeys": keys,
        "vertexLabels": sorted({l for el in vertex_els for l in split_labels(el["label"])}),
        "edgeLabels": sorted({edge_label(el["label"]) for el in edge_els}),
        "indexName": "by" + ID_PROP[0].upper() + ID_PROP[1:],
        "indexKey": ID_PROP,
        "_multiLabelled": multi_labelled,
        "_compositeRels": composite_rels,
    }


# The one piece that cannot be Python: JanusGraph's management API. Driven
# entirely by the PLAN binding, so it never changes when the data changes.
SCHEMA_SCRIPT = """
import groovy.json.JsonSlurper
import org.janusgraph.core.Cardinality
import org.janusgraph.core.Multiplicity
import org.janusgraph.core.schema.SchemaStatus
import org.janusgraph.graphdb.database.management.ManagementSystem
import org.apache.tinkerpop.gremlin.structure.Vertex

plan = new JsonSlurper().parseText(PLAN)
mgmt = graph.openManagement()
created = [keys: [], vertexLabels: [], edgeLabels: [], index: false]

plan.propertyKeys.each { k ->
    if (!mgmt.containsPropertyKey(k.name)) {
        card = (k.cardinality == 'SET') ? Cardinality.SET : Cardinality.SINGLE
        mgmt.makePropertyKey(k.name).dataType(Class.forName(k.dataType)).cardinality(card).make()
        created.keys << "${k.name} (${k.dataType.tokenize('.').last()}, ${k.cardinality})"
    }
}
plan.vertexLabels.each { l ->
    if (!mgmt.containsVertexLabel(l)) { mgmt.makeVertexLabel(l).make(); created.vertexLabels << l }
}
plan.edgeLabels.each { l ->
    if (!mgmt.containsEdgeLabel(l)) {
        mgmt.makeEdgeLabel(l).multiplicity(Multiplicity.MULTI).make()
        created.edgeLabels << l
    }
}

indexExisted = mgmt.getGraphIndex(plan.indexName) != null
if (!indexExisted) {
    // Not unique on purpose: uniqueness in JanusGraph requires
    // ConsistencyModifier.LOCK, which throttles write throughput, and Neptune
    // ids are already unique by construction.
    mgmt.buildIndex(plan.indexName, Vertex.class)
        .addKey(mgmt.getPropertyKey(plan.indexKey))
        .buildCompositeIndex()
    created.index = true
}
mgmt.commit()

// Target ENABLED, not the default REGISTERED. A composite index built on an
// empty graph goes straight to ENABLED and never reports REGISTERED, so the
// default target can only ever time out after a pointless minute of waiting.
report = ManagementSystem.awaitGraphIndexStatus(graph, plan.indexName)
             .status(SchemaStatus.ENABLED).call()

// Serialised rather than returned as a Map: Gremlin Server *iterates* results,
// so a Map comes back as one single-entry dict per key instead of one object.
groovy.json.JsonOutput.toJson(
    [keys: created.keys, vertexLabels: created.vertexLabels,
     edgeLabels: created.edgeLabels, indexCreated: created.index,
     indexEnabled: report.getSucceeded()])
"""


def apply_schema(client, plan):
    raw = client.submit(SCHEMA_SCRIPT, bindings={"PLAN": json.dumps(plan)}).all().result()
    result = json.loads(raw[0])
    print("-- schema --")
    print(f"  property keys : {len(result['keys'])} created")
    for k in result["keys"]:
        print(f"      {k}")
    print(f"  vertex labels : {len(result['vertexLabels'])} created {result['vertexLabels']}")
    print(f"  edge labels   : {len(result['edgeLabels'])} created {result['edgeLabels']}")
    state = "ENABLED" if result["indexEnabled"] else "NOT ENABLED -- needs a reindex before it is usable"
    print(f"  index         : {plan['indexName']} "
          f"{'created' if result['indexCreated'] else 'already present'} -> {state}")
    if plan["_multiLabelled"]:
        print(f"  NOTE: {plan['_multiLabelled']} vertices carry multiple Neptune labels.")
        print(f"        JanusGraph allows one label per vertex: the first segment")
        print(f"        becomes the label, all of them are kept in '{LABELS_PROP}'.")
    if plan["_compositeRels"]:
        print(f"  NOTE: {plan['_compositeRels']} edges have a forward::reverse relationship type.")
        print(f"        The forward descriptor becomes the label; the original string")
        print(f"        is kept in '{EDGE_LBL_PROP}'.")


# -------------------------------------------------------------- load phase --


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def add_properties(t, el, is_vertex):
    for k, v in el.items():
        if k in RESERVED or v is None:
            continue
        if isinstance(v, list):
            for item in v:
                if item is not None:
                    t = t.property(Cardinality.set_, k, item)
        elif is_vertex:
            t = t.property(Cardinality.single, k, v)
        else:
            t = t.property(k, v)
    return t


def load_vertices(g, elements):
    """Inserts in batches -- one round trip per batch rather than per vertex --
    then reads the id mapping back in a single query."""
    print("-- vertices --")
    loaded = skipped = 0
    for chunk in chunks(elements, BATCH):
        t = g
        emitted = 0
        for el in chunk:
            nid = el.get("id")
            if nid is None:
                skipped += 1
                continue
            labels = split_labels(el["label"])
            t = t.add_v(labels[0] if labels else "vertex")
            t = t.property(Cardinality.single, ID_PROP, str(nid))
            if len(labels) > 1:
                for l in labels:
                    t = t.property(Cardinality.set_, LABELS_PROP, l)
            t = add_properties(t, el, True)
            emitted += 1
        if emitted:
            t.iterate()
            loaded += emitted
            print(f"  vertices: {loaded}")

    # One query instead of collecting ids batch by batch, which would force a
    # round trip per vertex.
    rows = g.V().has(ID_PROP).project("nid", "vid").by(ID_PROP).by(T.id).to_list()
    id_map = {r["nid"]: r["vid"] for r in rows}
    print(f"  loaded  : {loaded}")
    if skipped:
        print(f"  skipped : {skipped} (no id)")
    return id_map


def load_edges(g, elements, id_map):
    print("-- edges --")
    loaded = 0
    orphans = []
    for chunk in chunks(elements, BATCH):
        t = g
        emitted = 0
        for el in chunk:
            eid = el.get("id")
            full = str(el["label"]) if el.get("label") is not None else "edge"
            label = edge_label(full)
            out_id = id_map.get(str(el["OUT"]))
            in_id = id_map.get(str(el["IN"]))
            if out_id is None or in_id is None:
                missing = " ".join(x for x in ("OUT" if out_id is None else "",
                                               "IN" if in_id is None else "") if x)
                orphans.append({"id": eid, "out": el["OUT"], "in": el["IN"], "missing": missing})
                continue
            t = t.V(out_id).add_e(label).to(__.V(in_id))
            if eid is not None:
                t = t.property(ID_PROP, str(eid))
            # Only when it carried a reverse descriptor -- no point storing a
            # copy of the label otherwise.
            if full != label:
                t = t.property(EDGE_LBL_PROP, full)
            t = add_properties(t, el, False)
            emitted += 1
        if emitted:
            t.iterate()
            loaded += emitted
            print(f"  edges: {loaded}")

    print(f"  loaded  : {loaded}")
    print(f"  orphans : {len(orphans)}")
    if orphans:
        print("  (endpoints outside the extract -- expected when the query was")
        print("   limited to a subset of incidents)")
        for o in orphans[:10]:
            print(f"      {o['id']}: missing {o['missing']} (OUT={o['out']} IN={o['in']})")
        if len(orphans) > 10:
            print(f"      ... and {len(orphans) - 10} more")


# ------------------------------------------------------------ verify phase --


def verify(g):
    print("-- verify --")
    print(f"  vertices : {g.V().count().next()}")
    print(f"  edges    : {g.E().count().next()}")
    print("  by vertex label:")
    vc = g.V().group_count().by(T.label).next()
    for k, v in sorted(vc.items(), key=lambda kv: -kv[1]) or []:
        print(f"      {k}: {v}")
    if not vc:
        print("      (none)")
    print("  by edge label:")
    ec = g.E().group_count().by(T.label).next()
    for k, v in sorted(ec.items(), key=lambda kv: -kv[1]) or []:
        print(f"      {k}: {v}")
    if not ec:
        print("      (none)")
    missing = g.V().has_not(ID_PROP).count().next()
    print(f"  vertices without {ID_PROP}: {missing}{'   <-- unexpected' if missing else ''}")


# --------------------------------------------------------------------- main --


def main():
    if PHASE not in ("schema", "load", "verify", "all"):
        sys.exit(f"PHASE must be one of schema|load|verify|all, got {PHASE!r}")

    print("Neptune -> JanusGraph migration")
    print(f"  server  : {GREMLIN_URL}")
    print(f"  extract : {os.path.normpath(EXTRACT)}")
    print(f"  phase   : {PHASE}")

    vertex_els, edge_els = [], []
    if PHASE != "verify":
        all_els = read_elements(EXTRACT)
        vertex_els = dedup_by_id([e for e in all_els if not is_edge_el(e)])
        edge_els = dedup_by_id([e for e in all_els if is_edge_el(e)])
        print(f"  parsed  : {len(vertex_els)} vertices, {len(edge_els)} edges "
              f"(from {len(all_els)} elements)")

    connection = DriverRemoteConnection(GREMLIN_URL, "g")
    client = Client(GREMLIN_URL, "g")
    try:
        g = traversal().with_remote(connection)

        if PHASE in ("schema", "all"):
            apply_schema(client, build_plan(vertex_els, edge_els))
        if PHASE in ("load", "all"):
            load_edges(g, edge_els, load_vertices(g, vertex_els))
        if PHASE in ("verify", "all"):
            verify(g)

        print("OK")
    finally:
        client.close()
        connection.close()


if __name__ == "__main__":
    main()
