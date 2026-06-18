{{- define "agentctl.fullname" -}}
{{- printf "%s-%s" .Release.Name "agentctl" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentctl.labels" -}}
app.kubernetes.io/name: agentctl
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: agentctl-{{ .Chart.Version }}
{{- end -}}

{{- define "agentctl.pgHost" -}}
{{ include "agentctl.fullname" . }}-postgres
{{- end -}}

{{/* Postgres DSN used by every service (host = the in-cluster postgres Service). */}}
{{- define "agentctl.pgDsn" -}}
postgresql://{{ .Values.postgres.user }}:{{ .Values.postgres.password }}@{{ include "agentctl.pgHost" . }}:5432/{{ .Values.postgres.db }}
{{- end -}}
