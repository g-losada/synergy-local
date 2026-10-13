# Extracting the dev subset from Neptune

The cluster is a **property graph** and the connection speaks **openCypher**.
Every query here is openCypher.

## Where to run it

Two tools, **one output format**. The shape of the JSON follows the query
language, not the tool, so both produce exactly what the loader reads:

```json
{"results": [ {"i": {"~id": "...", "~entityType": "node",
                     "~labels": ["Incident"], "~properties": {...}},
               "r": null, "c": null} ]}
```

| | **Graph Explorer** | **Jupyter (Neptune workbench)** |
|---|---|---|
| Setup | none, already open | AWS console → Neptune → Notebooks → *Open Jupyter* (same instance that serves Graph Explorer) |
| Getting the file | copy the response, paste into a file | written straight to disk by the cell |
| Repeatable | no, manual | yes, re-run the cell |
| Anonymising personal data | by hand, afterwards | in the same cell, before saving |
| Practical volume | hundreds of nodes | thousands, with pagination |
| Basis for the real migration | no | yes |

Use Graph Explorer to explore and for a quick first extract. Use Jupyter when
you want the process repeatable — which is the point of writing it down.

### Jupyter setup

```python
import json, requests

ENDPOINT = "<cluster>.cluster-xxxx.<region>.neptune.amazonaws.com"

def q(cypher):
    r = requests.post(f"https://{ENDPOINT}:8182/opencypher",
                      data={"query": cypher})
    r.raise_for_status()
    return r.json()
```

If the cluster has IAM database authentication enabled, plain `requests` is
rejected — it cannot sign the request. Use the `%%oc` magic instead, which
handles auth:

```
%%oc --store-to result
MATCH (i:Incident) RETURN i LIMIT 1
```

Then check the shape before relying on it:

```python
print(json.dumps(result, indent=1)[:500])
```

You want to see `{"results": [...]}` with `~id` and `~entityType`. If it looks
different, send it over before going further.

---

## 1. What the graph looks like

```cypher
MATCH (i:Incident)-[r]->(c)
RETURN DISTINCT type(r) AS rel, labels(c) AS childLabels, count(*) AS n
```

Result at the time of writing:

| rel | child label | count |
|---|---|---|
| `Incident has City Service` | `Item` | 13390 |
| `PART_OF_EVENT` | `ROOT_EVENT` | 53 |
| `Incident has Resource` | `Item` | 7 |
| `Incident generated incident` | `Incident` | 1 |

Three consequences:

- **Relationship names contain spaces.** They become JanusGraph edge labels, so
  traversals need quoting: `g.V().out('Incident has City Service')`.
- **An incident has exactly one child.** `Incident has City Service` mirrors the
  `city_service` property, one per incident. The interesting structure is one
  hop further out, between the `Item`s themselves — parking infrastructure
  linked to parking infrastructure. **A one-hop extract is not worth loading.**
- **Item-to-Item relationship types carry `::`**, separating a forward and a
  reverse descriptor:

  ```
  Parking Access Control Supports Off-street Parking::
  Off-Street Parking is supported by Parking Access Control
  ```

  This is the ServiceNow convention, not Neptune multi-labelling. The loader
  uses the forward descriptor as the edge label and keeps the original string in
  a `neptuneLabel` property, so nothing is lost and traversals stay readable.

## 2. Extract, two hops out

One hop gives incidents with a single child each and no network. Two hops reach
the `Item`-to-`Item` topology, which is the point of testing a graph database.

```cypher
MATCH (i:Incident)
WITH i LIMIT 10
OPTIONAL MATCH (i)-[r1]->(item)
OPTIONAL MATCH (item)-[r2]-(nb)
RETURN i, r1, item, r2, nb
LIMIT 300
```

Note the details:

- **`(item)-[r2]-(nb)` is undirected** — no arrow. Neighbours attach in both
  directions and a directed match would miss half of them.
- **`LIMIT 300` is a guard, not a preference.** Some `Item`s are hubs with 24 or
  more neighbours, so an unbounded two-hop extract can return thousands of rows.
- **Both `OPTIONAL MATCH`es matter**: the first keeps incidents with no child,
  the second keeps items with no neighbours.
- The same node repeats across many rows; the loader de-duplicates by `~id`.

To pin specific incidents instead of the first ten:

```cypher
MATCH (i:Incident)
WHERE i.reference_number IN ['INC0010007', 'INC0010024']
OPTIONAL MATCH (i)-[r1]->(item)
OPTIONAL MATCH (item)-[r2]-(nb)
RETURN i, r1, item, r2, nb
LIMIT 300
```

In Jupyter:

```python
extract = q("""
MATCH (i:Incident)
WITH i LIMIT 10
OPTIONAL MATCH (i)-[r1]->(item)
OPTIONAL MATCH (item)-[r2]-(nb)
RETURN i, r1, item, r2, nb
LIMIT 300
""")
json.dump(extract, open("extract.json", "w"), indent=1, ensure_ascii=False)
```

Check the size before going further — if it hits the limit exactly, raise it or
narrow the incidents:

```python
print(len(extract["results"]), "rows")
```

Save it as `migration/neptune/responses/extract.json`. That directory is
gitignored — the extract stays on your disk and is not versioned.

## 3. Baseline

Write these numbers down; the loader's `verify` phase is checked against them.
Counting from the saved extract is more reliable than a second query, because it
counts exactly what you are about to load:

```python
els = []
def walk(x):
    if isinstance(x, dict):
        if x.get("~entityType"): els.append(x)
        else: [walk(v) for v in x.values()]
    elif isinstance(x, list): [walk(v) for v in x]
[walk(row) for row in extract["results"]]

nodes = {e["~id"] for e in els if e["~entityType"] == "node"}
rels  = {e["~id"] for e in els if e["~entityType"] == "relationship"}
print(len(nodes), "distinct nodes,", len(rels), "distinct relationships")

from collections import Counter
print(Counter(tuple(e["~labels"]) for e in els if e["~entityType"] == "node"))
```

Some of those relationships will be orphans: two hops out, a neighbour's own
edges can point at nodes the query never returned. The loader reports them.

---

## Before the file goes anywhere

**It contains real personal data.** `affected_user` and `reported_by` hold real
names, `tenant` identifies the site. `.gitignore` keeps the file out of the
repository, but that only covers git — be deliberate about where else it
travels. To anonymise in Jupyter before saving:

```python
FAKE = {}
def anon(name):
    if not name: return name
    return FAKE.setdefault(name, f"Person {len(FAKE) + 1}")

for row in extract["results"]:
    for v in row.values():
        if isinstance(v, dict) and v.get("~entityType") == "node":
            p = v.get("~properties", {})
            for field in ("affected_user", "reported_by"):
                if field in p: p[field] = anon(p[field])
```

**Check the text encoding at source.** Values such as `Monitoriza��o`
and `Pedido de Servi�o` should read *Monitorização* and *Pedido de Serviço*.
Those are already-broken UTF-8 bytes. If the corruption is in Neptune it travels
into JanusGraph, where it becomes indistinguishable from a migration bug. Find
out which it is and write the answer down.

**Every property is a String**, including `latitude`, `active` and `open_date`.
The loader keeps them as text, faithful to the source. Numeric ordering and date
filters in JanusGraph would need a conversion pass, deliberately not part of
this migration.

---

## Then

Continue with [../README.md](../README.md).
