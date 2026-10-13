# Neptune → K8ssandra + JanusGraph migration

Moves graph data out of AWS Neptune into the `core/` stack.

> **Status: validated end to end.** `migrate.py` has been run against the real
> extract — 193 vertices, 209 edges, 0 orphans — and the result survives a pod
> restart. A Groovy predecessor (`migrate.groovy`) is kept for reference; the
> Python path is the supported one.

## How the data gets into the cluster

```
[Neptune on AWS]
      │  openCypher query (Graph Explorer or Jupyter)
      ▼
 extract.json                    ← a file on your disk, not in git
      │  migrate.py  (gremlinpython, WebSocket via port-forward)
      ▼
[Gremlin Server]  in the janus-graph pod
      ▼
[Cassandra]  keyspace janusgraph
```

`run-migration.sh` starts the port-forward, waits for the port, runs `migrate.py`
and tears the forward down again. You need `kubectl` and a Python virtualenv.

**What is in git:** the scripts, because they are code. **Not the extract** — it
carries real personal data and stays on your disk. `.gitignore` covers
`neptune/responses/*.json`.

## The files

| File | What it is | Where it runs |
|---|---|---|
| `neptune/queries.md` | The openCypher queries, what the graph actually looks like, and how to anonymise before saving. | Graph Explorer or Jupyter |
| `neptune/responses/` | Where `extract.json` goes. Gitignored. | — |
| `scripts/migrate.py` | The migration: derives the JanusGraph schema from the data, creates keys/labels/index, inserts vertices then edges, verifies. Python throughout, except one embedded Groovy block for schema creation — see below. | Your machine |
| `scripts/requirements.txt` | `gremlinpython` pinned to the server's TinkerPop version. The pin is load-bearing, not housekeeping. | — |
| `scripts/run-migration.sh` | Wrapper: handles the port-forward, then runs `migrate.py`. | Your machine |
| `scripts/migrate.groovy` | The previous implementation, which ran embedded inside the pod. Kept for reference. | Inside the JanusGraph pod |
| `k8s/job-migrate.yaml` | The same work as a Job with its own memory budget, pulling from S3. For SKE. | SKE cluster |
| `testdata/extract.json` | A fixture mirroring the real graph's shape: two hops, hex ids, `forward::reverse` relationship types, relationship names with spaces, string-typed numbers and dates, nodes repeated across rows, an incident with no child, and an orphan edge. **Not** stand-in Neptune data. | — |

`migrate.py` has four phases, selected by the argument to `run-migration.sh`:

- `schema` — derive and create property keys, vertex/edge labels, and the
  `neptuneId` composite index. Writes no data.
- `load` — insert vertices, then edges.
- `verify` — counts and per-label breakdown. Reads only.
- `all` — the three in order.

---

## 1. Set up, and check the stack is up

First time only:

```bash
cd migration/scripts
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On Ubuntu 24.04 the system Python has neither `pip` nor `ensurepip`, and PEP 668
blocks installing into it anyway. If `python3 -m venv .venv` fails, either
`sudo apt install python3-venv`, or create it without pip and bootstrap:

```bash
python3 -m venv --without-pip .venv
curl -sSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt
```

Then check the cluster:

```bash
kubectl -n k8ssandra get pods
kubectl -n janus-graph get pods
```

`graph-cassandra-dc1-default-sts-0` must be `2/2` and `janus-graph` `1/1`. If
minikube is stopped, `minikube start` first — Cassandra takes a few minutes.

## 2. Dry run against the fixture

Proves the loader works before real data is involved. The working directory
matters: `run-migration.sh` resolves paths relative to its own location.

```bash
cd migration/scripts
EXTRACT_JSON=../testdata/extract.json ./run-migration.sh all
```

Expected:

```
  parsed  : 6 vertices, 5 edges (from 18 elements)
-- schema --
  property keys : ... created
  vertex labels : 2 created [Incident, Item]
  edge labels   : 4 created [Incident has City Service, Parking Access Control Supports ...]
  index         : byNeptuneId created -> ENABLED
  NOTE: 3 edges have a forward::reverse relationship type.
-- vertices --
  loaded  : 6
-- edges --
  loaded  : 4
  orphans : 1
-- verify --
  vertices : 6
  edges    : 4
OK
```

Four things the fixture proves on purpose, each mirroring the real graph:

- **18 elements collapse to 6 vertices and 5 edges.** openCypher repeats the
  same node on every row of a two-hop query; the loader de-duplicates by `~id`.
- **Two hops.** Incident → Item → the Item's neighbours. A one-hop extract would
  give incidents with a single child each and no network at all.
- **`forward::reverse` relationship types** become the forward descriptor as the
  edge label, with the original string kept in `neptuneLabel`.
- **One orphan edge**, pointing at a node the query never returned — the loader
  reports it rather than crashing. Expected two hops out, where a neighbour's
  own edges reach beyond the extract.

Then wipe it before loading real data, so the counts in step 5 mean something:

```bash
kubectl -n k8ssandra exec graph-cassandra-dc1-default-sts-0 -c cassandra -- \
  cqlsh -u janusgraph -p devonly-changeme -e "DROP KEYSPACE janusgraph;"
kubectl -n janus-graph rollout restart deploy/janus-graph
kubectl -n janus-graph rollout status deploy/janus-graph
```

The keyspace is recreated empty on the next start.

## 3. Extract from Neptune

Follow [neptune/queries.md](neptune/queries.md): see what the graph looks like,
pick the incidents, extract, and note the baseline counts.

Save the result as `migration/neptune/responses/extract.json`. Nodes and
relationships in one file is expected — the loader splits them by shape.

Sanity check:

```bash
cd migration/neptune/responses
python3 - <<'EOF'
import json
d = json.load(open("extract.json"))
rows = d["results"] if isinstance(d, dict) else d
els = []
def walk(n):
    if isinstance(n, dict):
        if n.get("~entityType"): els.append(n)
        else: [walk(v) for v in n.values()]
    elif isinstance(n, list): [walk(v) for v in n]
[walk(r) for r in rows]
print(len({e["~id"] for e in els if e["~entityType"] == "node"}), "nodes,",
      len({e["~id"] for e in els if e["~entityType"] == "relationship"}), "rels")
EOF
```

## 4. Load

**Schema first, and read the output.**

```bash
cd migration/scripts
./run-migration.sh schema
```

Before continuing, check: do the vertex labels match what you expect? Are the
property datatypes sensible — a key that should be numeric showing as `String`
means mixed values arrived and the loader widened it. Does the index report
`ENABLED`?

**Then load.**

```bash
./run-migration.sh load
```

## 5. Verify

```bash
./run-migration.sh verify
```

In order:

1. `vertices` matches the node count from step 3.
2. `edges` plus `orphans` (from the load output) matches the relationship count.
3. The per-label breakdown matches Neptune's.
4. `vertices without neptuneId: 0`. Anything else is a bug.

Spot check a real incident — pick an id from your extract:

```bash
cd migration/scripts
kubectl -n janus-graph port-forward svc/janus-graph 8182:8182 &
.venv/bin/python - <<'EOF'
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal

conn = DriverRemoteConnection('ws://localhost:8182/gremlin', 'g')
g = traversal().with_remote(conn)
NID = 'PUT-AN-ID-HERE'
print("properties:", g.V().has('neptuneId', NID).value_map().next())
print("neighbours:", g.V().has('neptuneId', NID).both().values('neptuneId').to_list())
conn.close()
EOF
kill %1
```

Then prove it is really in Cassandra and not in memory:

```bash
kubectl -n janus-graph rollout restart deploy/janus-graph
kubectl -n janus-graph rollout status deploy/janus-graph
cd migration/scripts && ./run-migration.sh verify
```

Counts must be unchanged.

---

## Troubleshooting

**`Could not locate method: DefaultGraphTraversal.discard()`.** The
gremlinpython version does not match the server's TinkerPop version.
gremlinpython 3.8.x compiles `iterate()` into a `discard()` step that a 3.7
server rejects. Check what the server runs and pin to it:

```bash
kubectl -n janus-graph exec deploy/janus-graph -- ls /opt/janusgraph/lib/ | grep gremlin-core
.venv/bin/pip install -r requirements.txt
```

**`Connection was already closed` during the load.** The batch exceeded the
server's `maxContentLength` (65536 bytes in `janusgraph-server.yaml`). Each
batch is one bytecode message, so the limit is on bytes, not elements — and the
error says nothing about size. Lower it:

```bash
BATCH_SIZE=5 ./run-migration.sh load
```

**`Connection refused` on port 8182 while a port-forward appears to be
running.** A stale `kubectl port-forward` from an earlier run still holds the
local port but points at a pod that no longer exists. `run-migration.sh` reuses
an already-open port rather than fighting for it, so kill the old one:

```bash
ps -eo pid,comm,args | awk '$2=="kubectl" && /port-forward/ {print $1}' | xargs -r kill
```

**`Missing .../extract.json`.** Either put the file at
`neptune/responses/extract.json` or pass `EXTRACT_JSON=`.

**`expected Neptune openCypher JSON`.** The file is neither `{"results": [...]}`
nor a bare array of rows — most likely a partial copy-paste.

**Loading on top of existing data.** Don't. `storage.batch-loading` is on by
default and skips consistency checks that only make sense on an empty graph.
Drop the keyspace as in step 2, or pass `BATCH_LOADING=false` to load
incrementally.

## Indexing

The loader creates one composite index, on `neptuneId`, because it needs it to
resolve edge endpoints. It is deliberately **not** unique: uniqueness in
JanusGraph requires `ConsistencyModifier.LOCK`, which throttles writes, and
Neptune ids are already unique by construction.

No other index is created, so queries filtering on domain properties will do
full scans. Add those after the load with `mgmt.buildIndex(...)` and a reindex —
building them beforehand only slows the load.

## Neptune and JanusGraph disagree in four places

1. **Ids are Strings in Neptune, Longs in JanusGraph.** The Neptune id goes into
   a `neptuneId` property backed by a composite index. Edges get the same
   treatment, because JanusGraph supports custom ids on vertices only.
2. **Cardinality.** Neptune has `single` and `set`, never `list`. Edge
   properties are always single-valued.
3. **Relationship names with spaces** become edge labels that need quoting in
   every Gremlin traversal.
4. **`::` means two different things.** On a vertex it is Neptune's multi-label
   separator, and JanusGraph permits exactly one label — the first segment wins
   and the full set goes into `neptuneLabels` (SET). On an edge it separates the
   forward and reverse descriptors of a single ServiceNow relationship type, so
   the forward one becomes the label and the original string goes into
   `neptuneLabel` (SINGLE). The loader reports how many elements hit each case.

---

## SKE (full volume)

Same `migrate.py`, three differences.

**Run it as a Job.** `k8s/job-migrate.yaml` uses the same image with its own
resource budget so the load does not compete with the Gremlin Server's memory
limit. `run-migration.sh` is fine for a development extract and for `verify`,
but not for a real load. Before applying it:

```bash
kubectl -n janus-graph create configmap migrate-script \
  --from-file=migrate.py=migration/scripts/migrate.py
kubectl -n janus-graph create secret generic migration-s3 \
  --from-literal=AWS_ACCESS_KEY_ID=... --from-literal=AWS_SECRET_ACCESS_KEY=...
# edit S3_SOURCE in the manifest, then:
kubectl apply -f migration/k8s/job-migrate.yaml
kubectl -n janus-graph logs -f job/neptune-migration
```

**Decide the extraction route before anything else.** Two options:

- *Keep openCypher with pagination.* Same format, same loader, nothing new to
  build. Viable if the volume is modest.
- *Use [`neptune-export`](https://docs.aws.amazon.com/neptune/latest/userguide/export-utility.html).*
  The AWS path for large graphs, but it writes **CSV**, not this JSON, and
  **that reader does not exist** — it is scoped work, not something already
  built. It also needs an execution point inside the Neptune VPC (Neptune is
  never reachable from the internet), JDK 8 specifically, an S3 bucket, and an
  IAM role with `s3:PutObject`, `s3:PutObjectTagging`, `s3:GetObject` — plus a
  longer `rds:*` set to export from a clone, which is what you want in
  production so the live cluster is not scanned.

The node and edge counts decide. Graph Explorer's **Connection Details** panel
already shows them, so check there before committing to the heavier path.

**Size the cluster.** After the local load, measure and extrapolate by volume
ratio × replication factor, leaving ~2× headroom for compaction:

```bash
kubectl -n k8ssandra exec graph-cassandra-dc1-default-sts-0 -c cassandra -- \
  nodetool tablestats janusgraph
```

Feed that into `storage` in `core/k8ssandra/values-ske.yaml`, which still
carries a TODO. SKE runs RF 3: the load writes three times.
