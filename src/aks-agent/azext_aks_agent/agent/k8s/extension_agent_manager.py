# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from typing import Optional
from azure.cli.core.azclierror import AzCLIError
from knack.log import get_logger
from azext_aks_agent.agent.k8s.aks_agent_manager import AKSAgentManager
from kubernetes.client.rest import ApiException

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
        0. Query ARM extension resource for namespace property
        1. Known extension type mappings
        2. Search for deployment with extension label
        3. Try extension-specific namespace patterns
        4. Check if namespace exists in cluster
        5. Fall back to aks-agent namespace with warning
        
        Returns:
            Detected namespace name
            
        Raises:
            AzCLIError: If unable to initialize Kubernetes client
        """
        logger.info("Attempting to detect namespace for extension '%s'", self.extension_name)
        
        # Strategy 0: Query ARM extension resource for namespace property
        try:
            namespace = self._get_namespace_from_arm_resource()
            if namespace:
                logger.info("Strategy 0 (ARM resource): Using namespace: %s", namespace)
                return namespace
        except Exception as e:
            logger.debug("Strategy 0 failed: %s", e)
        
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
            logger.info("Strategy 1 (Known mappings): Using namespace for '%s': %s", 
                       self.extension_name, namespace)
            return namespace
        
        # Strategy 2: Search deployment labels (requires k8s connection)
        try:
            namespace = self._search_deployment_namespace()
            if namespace:
                logger.info("Strategy 2 (Deployment search): Detected namespace: %s", namespace)
                return namespace
        except Exception as e:
            logger.debug("Strategy 2 failed: %s", e)
        
        # Strategy 3: Try extension-specific namespace patterns
        pattern_namespaces = [
            f"{self.extension_name}-system",
            f"{self.extension_name}",
            f"arc-{self.extension_name}",
        ]
        
        for ns in pattern_namespaces:
            try:
                if self._namespace_exists(ns):
                    logger.info("Strategy 3 (Pattern matching): Detected namespace: %s", ns)
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
    
    def _get_namespace_from_arm_resource(self) -> Optional[str]:
        """
        Query the ARM extension resource to extract the namespace.
        
        Looks for namespace in:
        - scope.cluster.release_namespace
        - namespace property
        
        Returns:
            Namespace extracted from ARM resource, or None if not found
        """
        try:
            logger.debug("Strategy 0: Querying ARM resource for extension '%s'", self.extension_name)
            
            # Import here to avoid circular imports and handle missing aks-preview gracefully
            try:
                from azext_aks_agent.custom import _get_k8s_extension_state
            except ImportError:
                logger.debug("Unable to import _get_k8s_extension_state")
                return None
            
            # Note: This requires cmd context which we don't have here.
            # Return None to fall through to next strategy.
            # The calling function in custom.py should use this approach instead.
            logger.debug("Strategy 0 requires cmd context - deferring to custom.py layer")
            return None
            
        except Exception as e:
            logger.debug("Strategy 0 ARM resource query failed: %s", e)
            return None

    def _search_deployment_namespace(self) -> Optional[str]:
        """
        Search cluster deployments for extension-related labels.
        
        Returns:
            Namespace of detected deployment, or None if not found
        """
        try:
            logger.debug("Strategy 2: Searching for deployments with extension label '%s'", 
                        self.extension_name)
            
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
        except ApiException as e:
            if e.status == 404:
                return False
            raise
        except Exception:
            return False
    
    def count_pods_in_namespace(self) -> int:
        """
        Count all pods in the extension's namespace.
        
        Returns:
            Number of pods in the namespace
            
        Raises:
            AzCLIError: If unable to list pods
        """
        try:
            logger.debug("Counting pods in namespace '%s'", self.detected_namespace)
            
            pod_list = self.core_v1.list_namespaced_pod(
                namespace=self.detected_namespace
            )
            
            pod_count = len(pod_list.items)
            logger.info("Found %d pod(s) in namespace '%s'", pod_count, self.detected_namespace)
            
            return pod_count
            
        except ApiException as e:
            if e.status == 404:
                error_msg = f"Namespace '{self.detected_namespace}' not found in cluster"
            elif e.status == 403:
                error_msg = f"Access denied when listing pods in namespace '{self.detected_namespace}'. Check RBAC permissions."
            else:
                error_msg = f"Failed to list pods in namespace '{self.detected_namespace}': {e}"
            
            logger.error(error_msg)
            raise AzCLIError(error_msg)
        
        except Exception as e:
            error_msg = f"Unexpected error counting pods in namespace '{self.detected_namespace}': {e}"
            logger.error(error_msg)
            raise AzCLIError(error_msg)
    
    def get_extension_diagnostics_prompt(
        self,
        user_prompt: Optional[str] = None,
        arm_state: Optional[dict] = None,
    ) -> str:
        """
        Build a system prompt that scopes the AI agent to this extension.

        ARM state is fetched by the CLI handler (which has credentials) and passed in here
        so the agent does not need to run 'az k8s-extension show' itself — Azure CLI commands
        are blocked in the read-only aks-agent pod environment.

        Args:
            user_prompt: Optional user-provided description of the issue.
                         Defaults to a generic unhealthy/failing prompt.
            arm_state: Pre-fetched extension ARM resource dict from the CLI handler.
                       When provided, embedded directly into the prompt as evidence.

        Returns:
            Formatted system prompt string to pass to exec_aks_agent.
        """
        import json as _json

        if not user_prompt:
            user_prompt = f"The {self.extension_name} extension is unhealthy or failing. Diagnose the issue."

        # Embed pre-fetched ARM state so the agent does not need to call az CLI
        if arm_state:
            # Surface only the most diagnostic fields to keep prompt concise
            arm_fields = {
                k: arm_state.get(k)
                for k in (
                    "name", "extension_type", "provisioning_state", "install_state",
                    "version", "release_train", "auto_upgrade_minor_version",
                    "scope", "identity", "statuses", "error_info",
                )
                if arm_state.get(k) is not None
            }
            arm_section = (
                f"ARM Resource State (pre-fetched — do NOT call 'az k8s-extension show'):\n"
                f"```json\n{_json.dumps(arm_fields, indent=2, default=str)}\n```\n"
            )
        else:
            arm_section = (
                "ARM Resource State: unavailable (could not be fetched before agent start).\n"
            )

        return (
            f"You are troubleshooting the Azure Kubernetes extension '{self.extension_name}' "
            f"on AKS cluster '{self.cluster_name}' in resource group '{self.resource_group_name}'.\n"
            f"The extension is deployed in Kubernetes namespace '{self.detected_namespace}'.\n\n"
            f"{arm_section}\n"
            f"Your task:\n"
            f"  1. Analyse the ARM state above.\n"
            f"  2. Inspect Kubernetes resources in namespace '{self.detected_namespace}': "
            f"pods, deployments, daemonsets, replicasets, events, service accounts, RBAC bindings.\n"
            f"  3. Correlate findings across both layers to identify root cause(s).\n\n"
            f"IMPORTANT: Do NOT run 'az k8s-extension show' or any other Azure CLI command — "
            f"Azure CLI is unavailable in this environment. Use only kubectl-equivalent tools.\n\n"
            f"USER REQUEST: {user_prompt}\n\n"
            f"Provide output in this structure:\n"
            f"- Summary: 2-3 sentence overview\n"
            f"- Findings: Bullet list of observations\n"
            f"- Root Cause: Most likely cause(s)\n"
            f"- Recommended Actions: Numbered remediation steps\n"
            f"- Evidence: Specific resource names, error codes, event timestamps\n\n"
            f"Be specific. If data is unavailable, state exactly what is missing and why."
        )

    def get_pods_info(self) -> dict:
        """
        Get detailed information about pods in the extension's namespace.
        
        Returns:
            Dictionary with pod count and basic information
        """
        try:
            pod_list = self.core_v1.list_namespaced_pod(
                namespace=self.detected_namespace
            )
            
            pods_by_status = {
                'Running': [],
                'Pending': [],
                'Failed': [],
                'Other': []
            }
            
            for pod in pod_list.items:
                pod_info = {
                    'name': pod.metadata.name,
                    'phase': pod.status.phase,
                    'ready': False
                }
                
                # Check if pod is ready
                if pod.status.conditions:
                    for condition in pod.status.conditions:
                        if condition.type == "Ready" and condition.status == "True":
                            pod_info['ready'] = True
                            break
                
                phase = pod.status.phase
                if phase == 'Running':
                    pods_by_status['Running'].append(pod_info)
                elif phase == 'Pending':
                    pods_by_status['Pending'].append(pod_info)
                elif phase == 'Failed':
                    pods_by_status['Failed'].append(pod_info)
                else:
                    pods_by_status['Other'].append(pod_info)
            
            return {
                'namespace': self.detected_namespace,
                'total_pods': len(pod_list.items),
                'pods_by_status': pods_by_status
            }
            
        except Exception as e:
            logger.error("Failed to get pod info: %s", e)
            raise AzCLIError(f"Failed to get pod information: {e}")
