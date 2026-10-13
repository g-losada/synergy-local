{{/*
K8ssandraCluster name. Falls back to Release.Name when cluster.name is unset.
*/}}
{{- define "k8ssandra.clusterName" -}}
{{- .Values.cluster.name | default .Release.Name -}}
{{- end -}}

{{/*
Name of the first datacenter. Used to derive the CQL contact Service.
*/}}
{{- define "k8ssandra.dcName" -}}
{{- $dc := first .Values.cluster.datacenters -}}
{{- $dc.name -}}
{{- end -}}

{{/*
CQL contact Service created by cass-operator: <cluster>-<dc>-service.
The FQDN is required because JanusGraph runs in a different namespace.
*/}}
{{- define "k8ssandra.cqlHost" -}}
{{- printf "%s-%s-service.%s.svc.cluster.local" (include "k8ssandra.clusterName" .) (include "k8ssandra.dcName" .) .Release.Namespace -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "k8ssandra.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: synergy-core
{{- end -}}
