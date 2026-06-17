{{/*
Expand the name of the chart.
*/}}
{{- define "joke-application.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this.
*/}}
{{- define "joke-application.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label.
*/}}
{{- define "joke-application.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "joke-application.labels" -}}
helm.sh/chart: {{ include "joke-application.chart" . }}
{{ include "joke-application.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "joke-application.selectorLabels" -}}
app.kubernetes.io/name: {{ include "joke-application.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
CNPG cluster name.
*/}}
{{- define "joke-application.dbClusterName" -}}
{{- printf "%s-db" (include "joke-application.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
CNPG-managed secret for the app database user.
CNPG automatically creates a secret named <clusterName>-app containing:
  host, port, dbname, user, password, uri, jdbc-uri
*/}}
{{- define "joke-application.dbSecretName" -}}
{{- printf "%s-app" (include "joke-application.dbClusterName" .) }}
{{- end }}
