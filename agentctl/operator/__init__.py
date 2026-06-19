"""The declarative surface: an `AgentDeployment` custom resource and the reconcile that drives the
control plane to match it. The one-shot reconcile (`agentctl apply -f`) ships here; a watch-loop
controller and a hosted GitHub App are thin wrappers around it (see docs/design/CRD_OPERATOR.md)."""
