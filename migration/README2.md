# synergy-k8s

GitOps monorepo for the Synergy platform. ArgoCD reads this repository and
reconciles the cluster from it. The production target is StackIT SKE;
development happens on minikube.

**All code, comments and docs in this repo are written in English.**

## Layout

```
bootstrap/          "app-of-apps" Application pointing at applicationsets/
applicationsets/    One ApplicationSet per tier; they generate the Applications
core/               Data services: k8ssandra (Cassandra), janus-graph
common-services/    cert-manager, envoy-gateway, external-secrets
management/         argocd, harbor
```

The tier order is not arbitrary: **`common-services` must be installed before
`core`**. k8ssandra's `cass-operator` subchart aborts rendering if it cannot find
the `cert-manager.io/v1` API, because its admission webhooks need certificates
issued by cert-manager.

## Wrapper chart convention

Every service is a Helm wrapper chart shaped like this:

```
<tier>/<service>/
├── Chart.yaml        dependency on the upstream chart (when there is one)
├── config.yaml       metadata read by the ApplicationSet — NOT a Helm file
├── values.yaml       local profile (minikube)
├── values-ske.yaml   StackIT SKE overrides (see "Profiles" below)
└── templates/        our own CRs and resources
```

Upstream chart values go under a key named after the dependency
(`k8ssandra-operator:`, `cert-manager:`, `argo-cd:`, ...). Anything at the top
level is our own values, consumed by `templates/`.

`config.yaml` always has the same three keys and is what the ApplicationSet's
*git files generator* uses to generate an Application:

```yaml
appName: janus-graph            # Application name in ArgoCD
sourcePath: core/janus-graph    # chart path in this repo
destinationNamespace: janus-graph
```

Adding a service = creating the folder with these files. The ApplicationSet picks
it up on the next sync; there is no need to touch `applicationsets/`.

## Local vs SKE profiles

`values.yaml` is the **local** profile. `values-ske.yaml` carries the production
overrides (StackIT StorageClasses, replicas, resources, credentials via
ExternalSecret).

**The `values-ske.yaml` files are not wired into ArgoCD yet.** The ApplicationSet
templates do not pass `helm.valueFiles`. Until that is done, apply them by hand:

```bash
helm upgrade --install <rel> <path> -f <path>/values.yaml -f <path>/values-ske.yaml
```

To wire them up: add `env: <local|ske>` to **every** `config.yaml` and
`source.helm.valueFiles: [values.yaml, values-{{ .env }}.yaml]` to each
ApplicationSet template. It has to be every one because the ApplicationSets run
with `goTemplateOptions: missingkey=error`.

## Local core bring-up (minikube)

Cassandra needs headroom; 4 CPU / 8 GB is the comfortable minimum.

```bash
minikube start --cpus=4 --memory=8192
kubectl get storageclass          # "standard" must exist
```

### 1. cert-manager (core prerequisite)

Locally only the `cert-manager.io/v1` API is needed. The StackIT DNS-01 webhook
declared by the wrapper chart is for SKE and, without credentials, would register
an unavailable APIService that breaks API discovery — so install upstream only:

```bash
helm dependency build common-services/cert-manager
helm install cert-manager common-services/cert-manager/charts/cert-manager-v1.21.1.tgz \
  -n cert-manager --create-namespace \
  --set installCRDs=true --set startupapicheck.enabled=false --wait
kubectl api-resources --api-group=cert-manager.io   # must list 4 resources
```

### 2. k8ssandra — in two phases

With plain Helm the operator and the cluster have to be split: the operator's
webhook has no endpoints yet when the `K8ssandraCluster` is applied in the same
install. (Through ArgoCD this does not happen: sync-waves 0/1 and retries handle
the ordering.)

```bash
helm dependency build core/k8ssandra

# Phase 1: CRDs + operators
helm upgrade --install k8ssandra core/k8ssandra -n k8ssandra --create-namespace \
  --set cluster.enabled=false --wait

# Phase 2: the K8ssandraCluster
# --reset-values is mandatory: Helm v4 reuses the user-supplied values from the
# previous upgrade (a change from v3), otherwise cluster.enabled stays false.
helm upgrade k8ssandra core/k8ssandra -n k8ssandra --reset-values

kubectl -n k8ssandra get cassandradatacenter,pods -w   # wait for dc1 Ready
```

On a brand-new cluster the first command may fail with
`no matches for kind "K8ssandraCluster"`: Helm installs the CRDs but does not
invalidate its discovery cache before building the manifests. Running it again
fixes it.

### 3. janus-graph

The k8ssandra chart has already created the `janus-graph` namespace and written
the `cassandra-cql-credentials` Secret there — the same superuser that the
`K8ssandraCluster` uses via `superuserSecretRef`.

```bash
kubectl -n janus-graph get secret cassandra-cql-credentials
helm upgrade --install janus-graph core/janus-graph -n janus-graph --create-namespace
kubectl -n janus-graph rollout status deploy/janus-graph
```

Startup is slow by design: the image entrypoint polls Cassandra
(`JANUS_STORAGE_TIMEOUT`) and only then opens Gremlin Server, creating the
`janusgraph` keyspace on first run. The `startupProbe` is sized for that.

If the pod ends up `OOMKilled`, it is the heap: the distribution's
`conf/jvm-11.options` ships `-Xms4096m -Xmx4096m -XX:+AlwaysPreTouch`, which
commits 4 GB at startup. The chart overrides it via `jvm.heapSize`; keep
`resources.limits.memory` at least twice that value.

### 4. Verify the integration

```bash
# keyspace created by JanusGraph in Cassandra
kubectl -n k8ssandra exec -it graph-cassandra-dc1-default-sts-0 -c cassandra -- \
  cqlsh -u janusgraph -p devonly-changeme -e "DESCRIBE KEYSPACES;"

# round-trip through Gremlin
kubectl -n janus-graph exec -it deploy/janus-graph -- bin/gremlin.sh
#   :remote connect tinkerpop.server conf/remote.yaml
#   :> g.addV('test').property('name','synergy').next()
#   :> g.V().has('name','synergy').valueMap()
```

## Credentials

Locally, the CQL superuser comes from `core/k8ssandra/values.yaml`
(`cqlCredentials`), which renders the same Secret into both the `k8ssandra` and
`janus-graph` namespaces. **The password is dev-only and in plain text in the
repo because it is local.**

On SKE, `cqlCredentials.create: false`: the Secret is materialised instead by an
ExternalSecret from StackIT Secrets Manager, with the same name and the same keys
(`username`/`password`). The JanusGraph chart is identical in both.

## Skills

`.claude/skills/kubernetes-skill/` is the
[KubeShark](https://github.com/LukasNiessen/kubernetes-skill) skill (MIT),
vendored at commit `34f93c1`. It covers manifests, Helm, GitOps and storage;
Claude Code loads it automatically. To update, copy `SKILL.md` + `references/`
from upstream again.
