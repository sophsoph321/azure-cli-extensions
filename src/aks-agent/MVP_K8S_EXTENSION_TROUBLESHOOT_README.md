# AKS Agent MVP: Troubleshoot Kubernetes Extension on AKS

## Goal
Build an AI-assisted extension troubleshooting command using the same agent pattern as `az aks agent`, enabling comprehensive diagnostics across both ARM resources and Kubernetes resources in the extension's namespace.

## Proposed Command Shape

```bash
az aks agent troubleshoot-cluster-extension [prompt] \
  --extension-name <extension-name> \
  --resource-group <rg> \
  --name <aks-cluster> \
  [--extension-namespace <ext-namespace>] \
  [--cluster-type managedClusters] \
  [--mode cluster|client] \
  [--model <model>] \
  [--max-steps 10] \
  [--show-tool-output]
```

### Prompt Behavior
- User may provide a custom prompt describing the issue.
- If omitted, use a default prompt: "Diagnose why this extension is unhealthy or failing."

## Why Agent-Based Pattern is Better

### MVP Scope (Agent-Driven)
- **Reuses proven infrastructure**: `AKSAgentManager`, pod execution, tool framework
- **Comprehensive diagnostics**: AI agent inspects both ARM state AND Kubernetes resources
- **Extends naturally**: Phase 2 can add more tools without architectural changes
- **Same UX**: Parameter shape and behavior match `az aks agent`

### What Diagnostics the AI Agent Can Collect

The AI agent runs in the **extension's namespace** (not aks-agent namespace) and has access to:

#### Tier 1: ARM Resources
- Extension ARM resource state (provisioning, install state, version, release train)
- Identity configuration (managed identity, workload identity)
- Auto-upgrade settings
- Error messages from provisioning state

#### Tier 2: Kubernetes Resources (extension namespace scope)
- Extension Helm release state (if applicable)
- Deployment/DaemonSet/StatefulSet status
- Pod status and conditions
- Pod events (ImagePullBackOff, CrashLoopBackOff, etc.)
- Service account and RBAC bindings
- ConfigMaps/Secrets referenced by the extension

#### Tier 3: Cluster Context
- Node availability and resource pressure
- Extension CRDs present in cluster
- Network connectivity to Azure endpoints

### What to Defer After MVP
- Pod logs and debug container inspection
- Historical Log Analytics queries
- Cross-cluster correlation
- Auto-remediation or write operations
- Full policy-rule engine
- Time-series performance analysis

## Proposed User Experience

**User invokes one command:**
```bash
az aks agent troubleshoot-cluster-extension \
  --extension-name azure-policy \
  -g myResourceGroup \
  -n myCluster
```

**Flow:**
1. CLI detects extension's namespace (from resource OR parameter)
2. Sets up extension diagnostics pod in that namespace (cluster mode) or runs locally (client mode)
3. AI agent executes diagnostic queries against ARM and Kubernetes
4. Agent synthesizes findings across both layers
5. Output: structured report with summary, findings, root cause, actions

**Example Flow for Extension Installation Failure:**
- Agent queries: `az k8s-extension show` → sees `provisioningState: Failed`
- Agent queries: Pod status in extension namespace → sees `ImagePullBackOff`
- Agent queries: Service account permissions → sees missing RBAC
- Agent correlates and outputs: "Root cause: invalid image registry credentials + missing RBAC permissions. Actions: 1. Verify image registry credentials 2. Apply RBAC bindings"

## Implementation Approach

### Step 1: Detect Extension Namespace
Create helper to find extension's namespace:
- **Primary**: Query ARM extension resource for `scope.cluster.release_namespace` or `namespace` property
- If `--extension-namespace` provided, use it directly
- Otherwise, fallback to detection strategies (known mappings → deployment search → patterns → existence check)
- Final fallback: aks-agent namespace with warning

### Step 2: Create ExtensionAgentManager
Extend `AKSAgentManager` to handle extension-specific logic:
```python
class ExtensionAgentManager(AKSAgentManager):
    def __init__(self, resource_group_name, cluster_name, subscription_id, 
                 extension_name, extension_namespace=None, **kwargs):
        # If namespace not provided, use detection strategies
        # (ARM resource query, known mappings, deployment search, patterns, etc.)
        if not extension_namespace:
            extension_namespace = self._detect_extension_namespace(...)
        
        # Initialize parent with extension namespace
        super().__init__(resource_group_name, cluster_name, subscription_id, 
                         namespace=extension_namespace, **kwargs)
        
        self.extension_name = extension_name
```

Namespace detection strategies (in order):
1. **ARM Resource**: Query extension resource properties `scope.cluster.release_namespace` or `namespace`
2. **Known Mappings**: Static map of extension types to typical namespaces
3. **Deployment Search**: Find deployments with matching labels
4. **Pattern Matching**: Try -system, bare name, arc- variants
5. **Fallback**: Default to aks-agent with logged warning

### Step 3: Update Command Handler
The `aks_agent_troubleshoot_cluster_extension()` function mirrors `aks_agent()`:
- Accept optional user prompt
- Build deterministic system prompt with ARM + K8s constraints
- Use cluster/client mode logic similar to `aks_agent()`
- Execute on extension diagnostics pod

### Step 4: Add Command Registration & Parameters
Update:
- `commands.py`: already registered
- `_params.py`: add extension-specific args
- `_help.py`: add examples and help text

### Step 5: Create Diagnostic Tools for Agent
These tools run inside the extension diagnostics pod:
- `get_extension_arm_state`: Query `az k8s-extension show`
- `list_extension_pods`: List pods in extension namespace with filtering
- `get_pod_status`: Detailed pod/deployment/daemonset status
- `get_namespace_events`: Recent events in extension namespace
- `get_rbac_bindings`: Service account and RBAC permissions  
- `get_extension_config`: ConfigMaps/Secrets related to extension

### Step 6: Build System Prompt
Construct a prompt that scopes the AI agent appropriately:
```
You are troubleshooting Azure Kubernetes extension '{extension_name}' on AKS cluster '{cluster_name}' in resource group '{rg}'.

Available tools:
- get_extension_arm_state: Fetch ARM resource state
- list_extension_pods: List pods in the extension namespace
- get_pod_status: Get detailed pod/deployment status
- get_namespace_events: Recent events in the extension namespace
- get_rbac_bindings: Check service account and role bindings

Your task is to:
1. Collect diagnostics from both ARM and Kubernetes layers
2. Correlate findings across layers
3. Identify root cause(s)
4. Provide ordered remediation steps

USER REQUEST: {user_prompt}

Output format:
- Summary: 1-2 sentence overview
- Findings: Bullet list of observations from diagnostics
- Root Cause: Most likely cause(s)
- Recommended Actions: Numbered steps user can execute
```

## Namespace Detection Logic

Extension namespace detection uses the following strategy precedence:

1. **User-provided**: `--extension-namespace` parameter (highest priority)
2. **ARM Resource Query** (Strategy 0): Query `az k8s-extension show` and extract:
   - `scope.cluster.release_namespace`, or
   - Direct `namespace` property
3. **Known Type Mapping** (Strategy 1): Map extension types to typical namespaces:
   - `azure-policy` → `azure-policy-system`
   - `app-routing` → `app-routing-system`
   - etc.
4. **Deployment Label Search** (Strategy 2): Search cluster deployments with label `app.kubernetes.io/name={extension_name}`
5. **Pattern Matching** (Strategy 3): Try patterns like `{extension}-system`, `{extension}`, `arc-{extension}`
6. **Fallback** (Strategy 5): Default to `aks-agent` namespace with warning

This ensures robustness across different cluster configurations, with Strategy 0 providing the most canonical source of truth directly from ARM.

## MVP Guardrails
- **Read-only operations only**: No write/remediation actions in MVP
- **Focused scope**: Extension namespace only, not cluster-wide
- **Transparent about limits**: Clearly state when data is unavailable
- **Default max-steps**: 8-10 steps (lower than general `aks agent` for focus)
- **No pod logs in MVP**: Defer detailed log inspection to phase 2
- **Namespace isolation**: Diagnostics run in extension namespace, not aks-agent

## Testing Strategy

### Unit Tests
Add tests in `src/aks-agent/azext_aks_agent/tests/latest`:
- Namespace detection logic (all 5 paths)
- Prompt construction with extension context
- Mode behavior (cluster vs client)
- Parameter validation
- Error handling for missing extensions
- Agent manager initialization with extension namespace

### Integration Tests
1. **Healthy extension**: Successful diagnostics, clear summary
2. **Failed provisioning**: ARM state shows failure, agent synthesizes root cause
3. **Pod failures**: Pod status shows ImagePullBackOff, events show details
4. **Permission issues**: RBAC check fails gracefully, suggests permissions to add
5. **Missing namespace**: Fallback detection logic works
6. **Both modes**: Cluster mode finds extension pods; client mode runs locally

### Manual Validation Matrix

| Scenario | Expected Result |
|----------|-----------------|
| Healthy extension | ✅ Summary: "Extension healthy", no critical findings |
| ARM provisioning failed | ✅ Surface ARM error code, likely causes |
| Pod not running + events | ✅ Show pod status + recent events, correlate |
| RBAC permission missing | ✅ Explicit "permission denied", show required roles |
| Extension namespace not found | ✅ Try detection paths, clear fallback message |
| Cluster mode with no pods | ✅ Helpful error, suggest `agent-init` or deployment help |
| Client mode (local execution) | ✅ Queries work via local kubectl config |

## Success Criteria for MVP

✅ **Single command provides comprehensive diagnosis** in 2-3 minutes  
✅ **Correlates ARM + Kubernetes** findings into actionable insights  
✅ **Works in both cluster and client modes** seamlessly  
✅ **Namespace detection is robust** (5 fallback paths)  
✅ **Error messages are helpful** (never silent failures)  
✅ **Prompt constrains agent scope** appropriately  
✅ **Reuses all existing aks-agent infrastructure**  
✅ **Parameter shape matches `az aks agent`**  

## Phase 2+ Improvements

- **Pod logs and debug shell**: `--include-logs` flag for bounded log inspection
- **JSON output**: `--output json` for automation pipelines
- **Extension history**: Query extension versioning and upgrade history
- **Policy events**: Correlate with k8s-extension policy violation events
- **Network diagnostics**: Connectivity tests to Azure container registry, API endpoints
- **Known signatures mapping**: ImagePullBackOff → registry credential issues, etc.
- **Time-series trends**: Pod restart frequency, error patterns over time
- **Multi-extension analysis**: Probe dependencies between extensions
- **Remediation actions**: Safe write operations (credential rotation, RBAC grant, etc.)

---

## Detailed Implementation Code Examples

### 1. ExtensionAgentManager Class

Create new file: `src/aks-agent/azext_aks_agent/agent/k8s/extension_agent_manager.py`

```python
# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from typing import Optional, Tuple
from azure.cli.core.azclierror import AzCLIError
from knack.log import get_logger
from azext_aks_agent.agent.k8s.aks_agent_manager import AKSAgentManager

logger = get_logger(__name__)

class ExtensionAgentManager(AKSAgentManager):
    """
    Manager for troubleshooting Kubernetes extensions on AKS clusters.
    
    Extends AKSAgentManager to focus diagnostics on a specific extension's namespace,
    enabling the AI agent to correlate ARM resource state with Kubernetes observations.
    """
    
    def __init__(self, 
                 resource_group_name: str,
                 cluster_name: str,
                 subscription_id: str,
                 extension_name: str,
                 extension_namespace: Optional[str] = None,
                 kubeconfig_path: Optional[str] = None):
        """
        Initialize ExtensionAgentManager.
        
        Args:
            resource_group_name: Azure resource group name
            cluster_name: AKS cluster name
            subscription_id: Azure subscription ID
            extension_name: Name of the extension to troubleshoot
            extension_namespace: Kubernetes namespace of the extension.
                                If None, will attempt to detect automatically.
            kubeconfig_path: Path to kubeconfig file
        """
        self.extension_name = extension_name
        self.resource_group_name = resource_group_name
        self.cluster_name = cluster_name
        self.subscription_id = subscription_id
        
        # Detect extension namespace if not provided
        if not extension_namespace:
            extension_namespace = self._detect_extension_namespace()
        
        self.detected_namespace = extension_namespace
        
        # Initialize parent with extension namespace
        super().__init__(
            resource_group_name=resource_group_name,
            cluster_name=cluster_name,
            subscription_id=subscription_id,
            namespace=extension_namespace,
            kubeconfig_path=kubeconfig_path
        )
        
        logger.info("ExtensionAgentManager initialized for extension '%s' in namespace '%s'",
                    extension_name, extension_namespace)
    
    def _detect_extension_namespace(self) -> str:
        """
        Detect the extension's namespace using multiple strategies.
        
        Strategy precedence:
        1. Query extension resource metadata (if available)
        2. Search for deployment with extension label
        3. Check known defaults for extension type
        4. Fall back to aks-agent namespace with warning
        
        Returns:
            Detected namespace name
            
        Raises:
            AzCLIError: If namespace cannot be detected
        """
        logger.info("Attempting to detect namespace for extension '%s'", self.extension_name)
        
        # Strategy 1: Known extension mappings
        known_namespaces = {
            'azure-policy': 'azure-policy-system',
            'azure-policy-addon': 'azure-policy-system',
            'app-routing': 'app-routing-system',
            'open-service-mesh': 'osm-system',
            'monitoring': 'arc-monitoring',
            'flux': 'flux-system',
            'secrets-store-csi-driver': 'kube-system',
        }
        
        if self.extension_name in known_namespaces:
            namespace = known_namespaces[self.extension_name]
            logger.info("Using known namespace for '%s': %s", self.extension_name, namespace)
            return namespace
        
        # Strategy 2: Search deployment labels (requires k8s connection)
        try:
            namespace = self._search_deployment_namespace()
            if namespace:
                logger.info("Detected namespace via deployment search: %s", namespace)
                return namespace
        except Exception as e:
            logger.warning("Failed to search deployments: %s", e)
        
        # Strategy 3: Try extension-specific namespace patterns
        pattern_namespaces = [
            f"{self.extension_name}-system",
            f"{self.extension_name}",
            f"arc-{self.extension_name}",
        ]
        
        for ns in pattern_namespaces:
            try:
                if self._namespace_exists(ns):
                    logger.info("Detected namespace via pattern match: %s", ns)
                    return ns
            except Exception as e:
                logger.debug("Namespace pattern %s check failed: %s", ns, e)
        
        # Strategy 4: Fall back to aks-agent namespace
        default_ns = "aks-agent"
        logger.warning(
            "Could not detect extension namespace for '%s', falling back to '%s'. "
            "Provide --extension-namespace if this is incorrect.",
            self.extension_name, default_ns
        )
        return default_ns
    
    def _search_deployment_namespace(self) -> Optional[str]:
        """
        Search cluster deployments for extension-related labels.
        
        Returns:
            Namespace of detected deployment, or None if not found
        """
        try:
            # Search all namespaces for deployments with extension labels
            logger.debug("Searching for deployments with extension label '%s'", self.extension_name)
            
            deployments = self.apps_v1.list_deployment_for_all_namespaces(
                label_selector=f"app.kubernetes.io/name={self.extension_name}"
            )
            
            if deployments.items:
                namespace = deployments.items[0].metadata.namespace
                logger.debug("Found deployment in namespace: %s", namespace)
                return namespace
                
        except Exception as e:
            logger.debug("Deployment search failed: %s", e)
        
        return None
    
    def _namespace_exists(self, namespace: str) -> bool:
        """
        Check if a namespace exists in the cluster.
        
        Args:
            namespace: Namespace name to check
            
        Returns:
            True if namespace exists, False otherwise
        """
        try:
            self.core_v1.read_namespace(namespace)
            return True
        except Exception:
            return False
    
    def get_extension_diagnostics_prompt(self, user_prompt: Optional[str] = None) -> str:
        """
        Build a comprehensive system prompt for extension diagnostics.
        
        Args:
            user_prompt: Optional user-provided prompt; uses default if None
            
        Returns:
            Formatted system prompt for the AI agent
        """
        if not user_prompt:
            user_prompt = f"Diagnose why the {self.extension_name} extension is unhealthy or failing."
        
        system_prompt = f"""You are troubleshooting the Azure Kubernetes extension '{self.extension_name}' 
on AKS cluster '{self.cluster_name}' in resource group '{self.resource_group_name}'.

The extension is deployed in Kubernetes namespace '{self.detected_namespace}'.

You have access to tools for querying:
1. Extension ARM resource state (provisioning state, install state, version, identity config)
2. Kubernetes resources in the extension namespace:
   - Deployments, DaemonSets, StatefulSets
   - Pods, Services, ConfigMaps, Secrets
   - Recent events and conditions
   - Service account and RBAC bindings
3. Helm release information (if applicable)

Your task is to:
1. Collect and analyze diagnostics from both ARM and Kubernetes layers
2. Correlate findings to identify root causes
3. Provide a clear diagnosis with actionable remediation steps

USER REQUEST: {user_prompt}

Provide output in this structure:
- **Summary**: 2-3 sentence overview of the issue
- **Findings**: Bullet list of observations from diagnostics
- **Root Cause**: Most likely cause(s) of the problem
- **Recommended Actions**: Numbered remediation steps the user can execute
- **Evidence**: Referenced diagnostic data points (timestamps, resource states, etc.)

Be specific: reference actual resource names, error codes, and specific Kubernetes events.
If data is missing, state exactly what is missing and why."""
        
        return system_prompt
```

### 2. Updated custom.py Handler

Add to `src/aks-agent/azext_aks_agent/custom.py`:

```python
def aks_agent_troubleshoot_cluster_extension(
    cmd,
    client,
    resource_group_name,
    cluster_name,
    extension_name,
    prompt=None,
    extension_namespace=None,
    cluster_type="managedClusters",
    mode=None,
    model=None,
    max_steps=10,
    show_tool_output=False,
):
    """
    Troubleshoot a Kubernetes extension using AI-assisted diagnostics.
    
    This command uses the same agent pattern as 'az aks agent', but focuses
    diagnostics on the extension's namespace and ARM resource state.
    """
    from azext_aks_agent.agent.k8s.extension_agent_manager import ExtensionAgentManager
    
    with CLITelemetryClient(event_type="troubleshoot_extension") as telemetry_client:
        subscription_id = get_subscription_id(cmd.cli_ctx)
        
        kubeconfig = get_aks_credentials(
            client,
            resource_group_name,
            cluster_name
        )
        
        console = get_console()
        
        # Display header
        console.print(f"\n🔧 Troubleshooting Extension: {extension_name}", 
                      style=f"bold {HELP_COLOR}")
        console.print(f"Cluster: {cluster_name} | Resource Group: {resource_group_name}",
                      style=INFO_COLOR)
        
        # Determine mode
        use_client_mode = (mode == "client")
        telemetry_client.mode = "client" if use_client_mode else "cluster"
        
        # Validate parameters
        if not use_client_mode and not extension_namespace:
            console.print(
                "🔍 Detecting extension namespace...", style=INFO_COLOR)
        
        try:
            # Create extension agent manager
            ext_manager = ExtensionAgentManager(
                resource_group_name=resource_group_name,
                cluster_name=cluster_name,
                subscription_id=subscription_id,
                extension_name=extension_name,
                extension_namespace=extension_namespace,
                kubeconfig_path=kubeconfig
            )
            
            detected_ns = ext_manager.detected_namespace
            console.print(
                f"✓ Extension namespace: {detected_ns}", 
                style=f"{SUCCESS_COLOR}")
            
            # Build diagnostic prompt
            system_prompt = ext_manager.get_extension_diagnostics_prompt(prompt)
            
            if use_client_mode:
                console.print(
                    "\n📍 Running in client mode (local kubectl)", style=INFO_COLOR)
            else:
                # Check for extension diagnostics pod
                console.print(
                    "🔍 Checking for extension diagnostics pods...", style=INFO_COLOR)
                
                success, result = ext_manager.get_agent_pods()
                if not success:
                    console.print(
                        f"⚠️  No diagnostics pods found in namespace '{detected_ns}'",
                        style=WARNING_COLOR)
                    console.print(
                        f"   Suggestion: Deploy diagnostics pod or use --mode client",
                        style="dim")
                else:
                    console.print(
                        f"✓ Found {len(result)} pod(s) available",
                        style=SUCCESS_COLOR)
            
            # Prepare flags
            flags = f'"{system_prompt}"' if system_prompt else ''
            if model:
                flags += f' --model "{model}"'
            if max_steps:
                flags += f' --max-steps {max_steps}'
            if show_tool_output:
                flags += ' --show-tool-output'
            
            # Execute diagnostics
            console.print(
                f"\n🤖 Starting AI-assisted diagnostics...\n", 
                style=f"bold {HELP_COLOR}")
            
            ext_manager.exec_aks_agent(flags)
            
            console.print(
                f"\n✅ Diagnostics complete.", 
                style=f"bold {SUCCESS_COLOR}")
            
        except Exception as e:
            error_msg = str(e)
            console.print(
                f"\n❌ Error: {error_msg}",
                style=f"bold {ERROR_COLOR}")
            raise AzCLIError(error_msg)
```

### 3. Parameter Updates

Add to `src/aks-agent/azext_aks_agent/_params.py`:

```python
def load_arguments_troubleshoot_cluster_extension(self, _):
    """Load arguments for troubleshoot-cluster-extension command."""
    with self.argument_context('aks agent troubleshoot-cluster-extension') as ac:
        ac.argument('prompt', type=str, required=False,
                    help='Optional prompt describing the extension issue. '
                         'If omitted, uses default: "Diagnose why the extension is unhealthy."')
        
        ac.argument('extension_name',
                    options_list=['--extension-name'],
                    required=True,
                    help='Name of the extension to troubleshoot (e.g., azure-policy, app-routing)')
        
        ac.argument('extension_namespace',
                    options_list=['--extension-namespace'],
                    required=False,
                    help='Kubernetes namespace where the extension is deployed. '
                         'If not provided, will attempt to detect automatically.')
        
        ac.argument('cluster_type',
                    options_list=['--cluster-type'],
                    choices=['managedClusters', 'connectedClusters'],
                    default='managedClusters',
                    help='Type of cluster (for ARM resource path)')
        
        ac.argument('mode',
                    options_list=['--mode'],
                    choices=['cluster', 'client'],
                    required=False,
                    help='Execution mode: cluster (in-pod) or client (local). '
                         'If not specified, uses same mode as main aks-agent.')
        
        ac.argument('model',
                    options_list=['--model'],
                    required=False,
                    help='LLM model to use for analysis (e.g., gpt-4, gpt-3.5-turbo)')
        
        ac.argument('max_steps',
                    options_list=['--max-steps'],
                    type=int,
                    default=10,
                    help='Maximum number of steps for the AI agent (default: 10)')
        
        ac.argument('show_tool_output',
                    options_list=['--show-tool-output'],
                    action='store_true',
                    help='Show raw tool outputs from diagnostic queries')
```

### 4. Help Text Updates

Add to `src/aks-agent/azext_aks_agent/_help.py`:

```python
helps['aks_agent_troubleshoot_cluster_extension'] = """
    short-summary: Troubleshoot a Kubernetes extension using AI-assisted diagnostics
    long-summary: |
        Diagnose issues with Azure Kubernetes extensions. The AI agent analyzes both ARM resource state
        and Kubernetes resources in the extension's namespace to correlate findings and provide actionable
        remediation steps.
        
        The command works in both cluster mode (runs diagnostics pod in extension namespace) and client mode
        (runs diagnostics locally using your kubectl config).
    
    examples:
        - name: Troubleshoot a failing azure-policy extension
          text: |
            az aks agent troubleshoot-cluster-extension \\
              --extension-name azure-policy \\
              --resource-group myRg \\
              --name myCluster
        
        - name: Provide custom diagnostic prompt
          text: |
            az aks agent troubleshoot-cluster-extension \\
              "Extension pods are not running and events show ImagePullBackOff" \\
              --extension-name azure-policy \\
              --resource-group myRg \\
              --name myCluster
        
        - name: Run in client mode with pod logs
          text: |
            az aks agent troubleshoot-cluster-extension \\
              --extension-name app-routing \\
              --resource-group myRg \\
              --name myCluster \\
              --mode client \\
              --show-tool-output
        
        - name: Specify extension namespace explicitly
          text: |
            az aks agent troubleshoot-cluster-extension \\
              --extension-name custom-extension \\
              --extension-namespace my-custom-ns \\
              --resource-group myRg \\
              --name myCluster
"""
```

### 5. Minimal Delivery Checklist

- [ ] Create `ExtensionAgentManager` class in new file
- [ ] Update `custom.py` with `aks_agent_troubleshoot_cluster_extension()` handler
- [ ] Update `_params.py` with parameter definitions
- [ ] Update `_help.py` with help text and examples
- [ ] Update `commands.py` if not already registered
- [ ] Add unit tests in `tests/latest/`
  - [ ] Namespace detection with 5 fallback strategies
  - [ ] Prompt construction
  - [ ] Mode handling (cluster vs client)
  - [ ] Error cases
- [ ] Manual validation on real extensions (healthy + failing scenarios)
- [ ] Update main README with new command documentation
