# AKS Agent MVP: Troubleshoot Kubernetes Extension on AKS

## Goal
Build an MVP command in aks-agent where the user can say an extension is unhealthy/failing and get back the extension ARM resource state.

Proposed command shape:

az aks agent troubleshoot-cluster-extension [prompt] \
  --resource-group <rg> \
  --name <aks-cluster> \
  --namespace <aks-agent-namespace> \
  --extension-name <extension-name> \
  [--cluster-type managedClusters] \
  [--mode cluster|client] \
  [--model <model>] \
  [--max-steps 10] \
  [--show-tool-output]

Prompt behavior for MVP:
- User may provide a custom prompt.
- If omitted, use a default prompt: "The extension is unhealthy/failing. Diagnose using ARM resource state."

## Why this is good MVP scope
- One clear scenario: "extension unhealthy or failing".
- No new backend service required.
- Reuses existing AKS Agent execution path (cluster mode or client mode).
- Delivers immediate value with a single high-signal diagnostic call.

## What diagnostics this AI-assisted CLI can collect (MVP)
For this MVP, collect only one data group:

1. Extension ARM resource state
- az k8s-extension show for the target extension
- provisioning state, install state, extension type, release train, version
- auto-upgrade settings and identity-related settings when present

## What to defer after MVP
- Pod, deployment, and daemonset inspection
- Namespace events and pod logs
- Deep cross-cluster correlation
- Long historical analysis in Log Analytics
- Auto-remediation or write operations
- Full policy-rule engine in the first release

## Proposed user experience
1. User runs one command with extension name and optional prompt.
2. Command maps user intent to the unhealthy/failing-extension scenario and queries ARM state.
3. Output is structured into:
   - Summary
   - Findings
   - Probable root cause
  - Recommended actions
  - Evidence (ARM fields only)

## Implementation approach

### Step 1: Add a new CLI command surface
Update command registration:
- src/aks-agent/azext_aks_agent/commands.py

Add a new custom command under aks agent, for example:
- troubleshoot-cluster-extension -> aks_agent_troubleshoot_cluster_extension

### Step 2: Add command parameters
Update parameter definitions:
- src/aks-agent/azext_aks_agent/_params.py

Suggested new args:
- positional prompt (optional)
- --extension-name
- --cluster-type (default managedClusters)
- keep existing common args: -g, -n, --namespace, --mode, --model, --max-steps, --show-tool-output

### Step 3: Add help text and examples
Update help:
- src/aks-agent/azext_aks_agent/_help.py

Include examples such as:
- Diagnose failed extension installation
- Diagnose extension pods not running
- Run non-interactive troubleshooting

### Step 4: Implement command handler in custom.py
Add new function:
- src/aks-agent/azext_aks_agent/custom.py
- function name: aks_agent_troubleshoot_cluster_extension

MVP implementation pattern:
- Reuse existing mode handling and manager setup logic from aks_agent()
- Accept optional user prompt; if missing, apply default unhealthy/failing prompt
- Constrain the prompt to ARM resource state only
- Force concise, structured output sections
- Pass prompt through existing agent_manager.exec_aks_agent(...)

This gives you focused behavior without introducing new plumbing.

## Recommended prompt template for MVP
Use a deterministic prompt constructed by the CLI handler. Example:

User statement: "<prompt or default unhealthy/failing message>"

You are troubleshooting Azure Kubernetes extension '<extension-name>' on AKS cluster '<cluster-name>' in resource group '<rg>'.
Collect only ARM diagnostics using az k8s-extension show and related ARM fields for this extension.
Return output with sections:
- Summary
- Findings
- Probable root cause
- Recommended actions (ordered)
- Evidence (ARM field snippets only)
If data is missing, state exactly what is missing and why.

## MVP guardrails
- Read-only diagnostics only.
- Do not collect Kubernetes pod, event, or log data in this MVP command.
- Always include explicit "unknown/missing" fields instead of guessing.
- Keep default max-steps lower for this command (example: 8-10).

## Testing strategy

### Unit tests
Add tests in:
- src/aks-agent/azext_aks_agent/tests/latest

Cover:
- Required args validation
- Prompt handling: user-provided prompt and default fallback prompt
- Prompt construction includes extension name and ARM-only constraints
- Mode behavior (cluster/client) remains correct
- Command flags mapping to exec path

### Manual validation matrix
1. Healthy extension
- Expect: summary says healthy and no critical findings

2. Missing extension resource
- Expect: clear "not found" diagnosis and command to verify/create

3. RBAC-restricted identity
- Expect: explicit permission failure surfaced as missing data, not silent success

## Success criteria for MVP
- One command gives meaningful diagnosis in under 2-3 minutes.
- Includes evidence from ARM extension state fields.
- Action list is specific enough for operator to execute immediately.
- Works in both cluster mode and client mode.

## Suggested phase 2 improvements
- Add pod status, namespace events, and bounded pod logs.
- Optional --output json schema for automation.
- Optional --include-logs/--no-logs flag.
- Add known-signature mapping (ImagePullBackOff, FailedScheduling, forbidden) to sharper recommendations.
- Correlate with k8s-extension policy events once available.

## Minimal delivery checklist
- Add command registration
- Add parameters
- Add help examples
- Add custom.py handler with deterministic prompt
- Add unit tests for prompt and args
- Validate end-to-end on one failing and one healthy extension
