{{- define "authentik-demo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "authentik-demo.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "authentik-demo.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "authentik-demo.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: evaluation-app
app.kubernetes.io/part-of: synergy-evaluation
{{- end -}}

{{- define "authentik-demo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "authentik-demo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "authentik-demo.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "authentik-demo.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
The issuer with exactly one trailing slash. authentik publishes the "iss" claim
with a trailing slash, and both the discovery lookup and the JWKS URL are built
by appending to it, so normalising here avoids a double slash that would fail
issuer validation.
*/}}
{{- define "authentik-demo.issuer" -}}
{{- printf "%s/" (trimSuffix "/" .Values.oidc.issuer) -}}
{{- end -}}
