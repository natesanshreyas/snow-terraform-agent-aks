{{/*
Expand the name of the chart.
*/}}
{{- define "snow-terraform-agent.name" -}}
{{- .Chart.Name }}
{{- end }}

{{/*
Full release name.
*/}}
{{- define "snow-terraform-agent.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "snow-terraform-agent.labels" -}}
app.kubernetes.io/name: {{ include "snow-terraform-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels used by Deployments and Services.
*/}}
{{- define "snow-terraform-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "snow-terraform-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
