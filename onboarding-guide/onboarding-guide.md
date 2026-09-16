# Onboarding Guide

Welcome to the project.

This document aims to help new team members set up the necessary access, understand the solution architecture, and get to know the existing environments.

---

# Index

1. Accesses
   - StackIT Access
     - Git Repositories Access
2. Repository Management
3. Local Cluster Setup
4. Migration Architecture
5. Clusters and Services
6. Power Schedule Automation

---

# Accesses

This section lists all the accesses that need to be requested to work on the project.

## StackIT Access

### Objective

StackIT is used for accessing and managing the Kubernetes clusters.

### Prerequisites

Before requesting access to StackIT you must have:

- Active corporate account

### How to request access

Access to STACKIT must be requested from BIORN via email (email@xxx.com).

#### Email template

```
To: email@xxx.com
Subject: STACKIT Access Request - <Name>

Hello,

I would like to request STACKIT access with the following details:

- Name:
- Corporate email:
- Project:
- Desired environment:
- Access justification:

Thank you in advance.

Best regards,
<Name>
```

### Validation

After approval validate access using the commands:

```bash
kubectl config get-contexts
kubectl cluster-info
```

### Git Repositories Access

_(section pending)_

---

# Repository Management

_(section pending)_

---

# Local Cluster Setup

## Objective

Add the STACKIT Kubernetes clusters as contexts locally so they can be managed through `kubectl` and `k9s`.

## Prerequisites


- [k9s](https://k9scli.io/) installed
- STACKIT CLI installed and authenticated:
  ```bash
  stackit auth login
  ```

## How to add a cluster context

Generate the kubeconfig for the desired cluster with the STACKIT CLI:

```bash
stackit ske kubeconfig create \
  --project-id e063b730-b91e-46fa-bf26-0929dc012a33 \
  --cluster-name <cluster-name> \
  --expiration 30d
```

This adds the cluster's context to your local kubeconfig. Repeat for each cluster you need (`core-dev`, `mgmt`, `unified`).

Once added, you can inspect the cluster with `kubectl` or launch k9s and switch context with `:ctx`, or select the cluster directly:

```bash
k9s --context <cluster-name>
```

## Important - do not change clusters directly

`kubectl`/`k9s` access to the clusters is meant for **visualization and troubleshooting**, not for making changes. It is not good practice to change cluster state directly with `kubectl` (e.g. `kubectl apply`, `kubectl edit`, `kubectl delete`).

All changes must be made through code in the repositories (manifests, Helm charts, GitOps), so they stay versioned and are applied consistently by the pipelines/ArgoCD.

Making changes directly on the cluster is only acceptable as a **last resort**, when an issue cannot be resolved through the manifests (e.g. an urgent production incident). In that case, the change must be reflected back into the repository afterwards to keep the cluster state consistent with the source of truth.

---

# Migration Architecture

_(section pending)_

---

# Clusters and Services

_(section pending)_

---

# Power Schedule Automation

## Objective

To reduce infrastructure costs, the StackIT server (`bastion-mgmt-prod`) and the SKE Kubernetes clusters (`core-dev`, `mgmt`, `unified`) are turned off outside office hours instead of running 24/7.

This is done through the `stackit-power.sh` script, which uses the official STACKIT CLI to:

- **Start** the server and wake up (`wakeup`) the clusters when the team arrives at the office.
- **Stop** the server and hibernate (`hibernate`) the clusters when the team leaves at the end of the day.

Script location: `pipeline-automation/stackit-power.sh` (in the repository).

## Usage rules

Before running the script, **always confirm the following**:

- **At arrival (turning services on):** if no one needs the environments that day (e.g. no development, testing, or demos planned), **do not turn the services on**. Keeping them off when not needed is the main source of cost savings — turning everything on "just in case" defeats the purpose.
- **At departure (turning services off):** before hibernating the clusters or stopping the server, **confirm with the team that no one is actively developing, testing, or has a running process/deployment on any of the environments**. Hibernating a cluster mid-use will interrupt whatever is running on it.
- If in doubt, ask in the team channel before shutting anything down.

## Prerequisites

- STACKIT CLI installed. See [installation guide](https://docs.stackit.cloud/developer-tools/stackit-cli/).
- Authenticated session:
  ```bash
  stackit auth login
  ```
- `PROJECT_ID` and `SERVER_ID` already configured inside the script (see the "Configuration" section at the top of the file). No need to pass the project ID manually on each command.

## How to use

```bash
# Turn everything on (server + 3 clusters)
./stackit-power.sh on

# Turn everything off (server + 3 clusters)
./stackit-power.sh off

# Check current status of everything
./stackit-power.sh status

# Act on only the server, or only the clusters
./stackit-power.sh on  --only server
./stackit-power.sh off --only clusters
```

The script prints the real output/errors from the STACKIT CLI for each resource — always check the log after running it to confirm every resource was affected successfully, since one resource can fail while the others succeed.

## Known limitation — cert-manager webhook timeout

SKE clusters cannot be hibernated if a `ValidatingWebhookConfiguration`/`MutatingWebhookConfiguration` in the cluster has a `timeoutSeconds` above Gardener's recommended limit (15s). This previously caused hibernation to fail on all three clusters, due to the cert-manager webhook being configured with a 30s timeout.

This has been fixed by adjusting the `timeoutSeconds` value to 15s in the cert-manager manifests managed via ArgoCD. If hibernation starts failing again with an error mentioning `RemediatedWebhooks`, check the webhook timeout configuration first — this is the most common cause.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Permission denied` when running the script | Script not marked as executable — run `chmod +x stackit-power.sh`, or run with `bash stackit-power.sh ...` |
| "não autenticado" / auth error | Session expired — run `stackit auth login` again |
| `RemediatedWebhooks` error on cluster hibernation | A webhook in that cluster has a `timeoutSeconds` above 15s — check with the team managing that cluster's manifests |
| `command not found: stackit` | CLI not installed or not in `PATH` — confirm with `stackit --version` |
