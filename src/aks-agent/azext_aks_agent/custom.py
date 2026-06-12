# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

# pylint: disable=too-many-lines, disable=broad-except, disable=line-too-long

import shlex
import subprocess

from azext_aks_agent.agent.aks import get_aks_credentials
from azext_aks_agent.agent.console import (
    ERROR_COLOR,
    HELP_COLOR,
    INFO_COLOR,
    SUCCESS_COLOR,
    WARNING_COLOR,
    get_console,
)
from azext_aks_agent.agent.k8s import AKSAgentManager, AKSAgentManagerClient
from azext_aks_agent.agent.k8s.aks_agent_manager import AKSAgentManagerLLMConfigBase
from azext_aks_agent.agent.llm_providers import prompt_provider_choice
from azext_aks_agent.agent.telemetry import CLITelemetryClient
from azure.cli.core.azclierror import AzCLIError
from azure.cli.core.commands.client_factory import get_subscription_id
from knack.log import get_logger
from knack.util import CLIError

logger = get_logger(__name__)


# pylint: disable=too-many-branches
def aks_agent_init(cmd,
                   client,
                   resource_group_name,
                   cluster_name,
                   ):
    """Initialize AKS agent helm deployment with LLM configuration and cluster role setup."""
    subscription_id = get_subscription_id(cmd.cli_ctx)

    kubeconfig_path = get_aks_credentials(
        client,
        resource_group_name,
        cluster_name
    )
    console = get_console()

    with CLITelemetryClient(event_type="init") as telemetry_client:
        try:
            # Prompt user to choose between cluster mode and client mode
            console.print(
                "\n🚀 Welcome to AKS Agent initialization!",
                style=f"bold {HELP_COLOR}")
            console.print(
                "\nPlease select the mode you want to use:",
                style=f"bold {HELP_COLOR}")
            console.print(
                "  1. Cluster mode - Deploys agent as a pod in your AKS cluster",
                style=INFO_COLOR)
            console.print(
                "     Uses service account and workload identity for secure access to cluster and Azure resources",
                style="dim cyan")
            console.print(
                "  2. Client mode - Runs agent locally using Docker",
                style=INFO_COLOR)
            console.print(
                "     Uses your local Azure credentials and cluster user credentials for access",
                style="dim cyan")

            while True:
                mode_choice = console.input(
                    f"\n[{HELP_COLOR}]Enter your choice (1 or 2): [/]").strip()
                if mode_choice in ['1', '2']:
                    break
                console.print("Invalid choice. Please enter 1 or 2.", style=WARNING_COLOR)

            use_client_mode = (mode_choice == '2')

            # Record the mode being used in telemetry
            telemetry_client.mode = "client" if use_client_mode else "cluster"

            if use_client_mode:
                console.print(
                    "\n✅ Client mode selected. This will set up LLM configurations on your local environment.",
                    style=f"bold {HELP_COLOR}")
                aks_agent_manager = AKSAgentManagerClient(
                    resource_group_name=resource_group_name,
                    cluster_name=cluster_name,
                    subscription_id=subscription_id,
                    kubeconfig_path=kubeconfig_path,
                )
            else:
                console.print(
                    "\n✅ Cluster mode selected. This will set up the agent deployment in your cluster.",
                    style=f"bold {HELP_COLOR}")

                # Prompt user for namespace if not provided
                console.print(
                    "\nPlease specify the namespace where the agent will be deployed.",
                    style=f"bold {HELP_COLOR}")
                while True:
                    namespace = console.input(
                        f"\n[{HELP_COLOR}]Enter namespace (e.g., 'kube-system'): [/]").strip()
                    if namespace:
                        break
                    console.print("Namespace cannot be empty. Please enter a valid namespace.", style=WARNING_COLOR)

                console.print(f"\n📦 Using namespace: {namespace}", style=INFO_COLOR)
                aks_agent_manager = AKSAgentManager(
                    resource_group_name=resource_group_name,
                    cluster_name=cluster_name,
                    namespace=namespace,
                    subscription_id=subscription_id,
                    kubeconfig_path=kubeconfig_path,
                )

            # ===== PHASE 1: LLM Configuration Setup =====
            _setup_llm_configuration(console, aks_agent_manager)

            if not use_client_mode:
                # ===== PHASE 2: Helm Deployment =====
                _setup_helm_deployment(console, aks_agent_manager)

        except Exception as e:
            console.print(f"❌ Error during initialization: {str(e)}", style=ERROR_COLOR)
            raise AzCLIError(f"Agent initialization failed: {str(e)}")


def _setup_llm_configuration(console, aks_agent_manager: AKSAgentManagerLLMConfigBase):
    """Setup LLM configuration by checking existing config and prompting user.

    Args:
        console: Console instance for output
        aks_agent_manager: AKS agent manager instance (AKSAgentManager or AKSAgentManagerClient)
    """
    # Check if LLM configuration exists by getting the model list
    model_list = aks_agent_manager.get_llm_config()

    if model_list:
        console.print(
            "LLM configuration already exists.",
            style=f"bold {HELP_COLOR}")

        # Display existing LLM configurations
        console.print("\n📋 Existing LLM Models:", style=f"bold {HELP_COLOR}")
        for model_name, model_config in model_list.items():
            console.print(f"  • {model_name}", style=INFO_COLOR)
            if "api_base" in model_config:
                console.print(f"    API Base: {model_config['api_base']}", style="cyan")
            if "api_version" in model_config:
                console.print(f"    API Version: {model_config['api_version']}", style="cyan")

        # TODO: allow the user config multiple llm configs at one time?
        user_input = console.input(
            f"\n[{HELP_COLOR}]Do you want to add/update the LLM configuration? (y/N): [/]").strip().lower()
        if user_input not in ['y', 'yes']:
            console.print("Skipping LLM configuration update.", style=f"bold {HELP_COLOR}")
        else:
            _setup_and_create_llm_config(console, aks_agent_manager)
    else:
        console.print("No existing LLM configuration found. Setting up new configuration...",
                      style=f"bold {HELP_COLOR}")
        _setup_and_create_llm_config(console, aks_agent_manager)


def _setup_helm_deployment(console, aks_agent_manager: AKSAgentManager):
    """Setup and deploy helm chart with service account configuration."""
    console.print("\n🚀 Phase 2: Helm Deployment", style=f"bold {HELP_COLOR}")

    # Check current helm deployment status
    agent_status = aks_agent_manager.get_agent_status()
    helm_status = agent_status.get("helm_status", "not_found")

    if helm_status == "deployed":
        console.print(f"✅ AKS agent helm chart is already deployed (status: {helm_status})", style=SUCCESS_COLOR)

        # Display existing service account from helm values and service account is immutable.
        service_account_name = aks_agent_manager.aks_mcp_service_account_name
        console.print(
            f"\n👤 Current service account in namespace '{aks_agent_manager.namespace}': {service_account_name}",
            style="cyan")

        # Check if using Azure Entra ID provider and show role assignment reminder
        model_list = aks_agent_manager.get_llm_config()
        if model_list and any("azure/" in model_name and not model_config.get("api_key") for model_name, model_config in model_list.items()):
            console.print(
                f"\n⚠️  IMPORTANT: If using keyless authentication with Azure OpenAI, ensure the 'Cognitive Services OpenAI User' or 'Azure AI Developer' role "
                f"is assigned to the workload identity (service account: {service_account_name}).",
                style=f"bold {INFO_COLOR}"
            )
            console.print(
                "Learn more: https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/managed-identity\n",
                style=INFO_COLOR
            )

    elif helm_status == "not_found":
        console.print(
            f"Helm chart not deployed (status: {helm_status}). Setting up deployment...",
            style=f"bold {HELP_COLOR}")

        # Prompt for service account configuration
        console.print("\n👤 Service Account Configuration", style=f"bold {HELP_COLOR}")
        console.print(
            f"The AKS agent requires a service account with appropriate Azure and Kubernetes permissions in the '{aks_agent_manager.namespace}' namespace.",
            style=INFO_COLOR)
        console.print(
            "Please ensure you have created the necessary Role and RoleBinding in your namespace for this service account.",
            style=WARNING_COLOR)
        console.print(
            "To have access to Azure resources, the service account should be annotated with "
            "'azure.workload.identity/client-id: <managed-identity-client-id>'.",
            style=WARNING_COLOR)

        # Check if using Azure Entra ID provider and show role assignment note
        model_list = aks_agent_manager.get_llm_config()
        if model_list and any("azure/" in model_name and not model_config.get("api_key") for model_name, model_config in model_list.items()):
            console.print(
                "\n⚠️  NOTE: You are using keyless authentication with Azure OpenAI. "
                "Ensure the 'Cognitive Services OpenAI User' or 'Azure AI Developer' role is assigned to the workload identity.",
                style=f"bold {INFO_COLOR}"
            )
            console.print(
                "Learn more: https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/managed-identity",
                style=INFO_COLOR
            )

        # Prompt user for service account name (required)
        while True:
            user_input = console.input(
                f"\n[{HELP_COLOR}]Enter service account name: [/]").strip()
            if user_input:
                aks_agent_manager.aks_mcp_service_account_name = user_input
                console.print(f"✅ Using service account: {user_input}", style=SUCCESS_COLOR)
                break
            console.print(
                "Service account name cannot be empty. Please enter a valid service account name.", style=WARNING_COLOR)

    else:
        # Handle non-standard helm status (failed, pending-install, pending-upgrade, etc.)
        cmd_flags = aks_agent_manager.command_flags()
        init_cmd_flags = aks_agent_manager.init_command_flags()
        console.print(
            f"⚠️  Detected unexpected helm status: {helm_status}\n"
            f"The AKS agent deployment is in an unexpected state.\n\n"
            f"To investigate, run: az aks agent --status {cmd_flags}\n"
            f"To recover:\n"
            f"  1. Clean up and reinitialize: az aks agent-cleanup {cmd_flags} && az aks agent-init {init_cmd_flags}\n"
            f"  2. Check deployment logs for more details",
            style=HELP_COLOR)
        raise AzCLIError(f"Cannot proceed with initialization due to unexpected helm status: {helm_status}")

    # Deploy if configuration changed or helm charts not deployed
    console.print("\n🚀 Deploying AKS agent (this typically takes less than 2 minutes)...", style=INFO_COLOR)
    success, error_msg = aks_agent_manager.deploy_agent()

    if success:
        console.print("✅ AKS agent deployed successfully!", style=SUCCESS_COLOR)
    else:
        console.print("❌ Failed to deploy agent", style=ERROR_COLOR)
        console.print(f"Error: {error_msg}", style=ERROR_COLOR)
        cmd_flags = aks_agent_manager.command_flags()
        console.print(
            f"Run 'az aks agent --status {cmd_flags}' to investigate the deployment issue.",
            style=INFO_COLOR)
        raise AzCLIError("Failed to deploy agent")

    # Verify deployment is ready
    console.print("Verifying deployment status...", style=INFO_COLOR)
    agent_status = aks_agent_manager.get_agent_status()
    if agent_status.get("ready", False):
        console.print("✅ AKS agent is ready and running!", style=SUCCESS_COLOR)
        console.print("\n🎉 Initialization completed successfully!", style=SUCCESS_COLOR)
    else:
        console.print(
            "⚠️  AKS agent is deployed but not yet ready. It may take a few moments to start.",
            style=WARNING_COLOR)
        if helm_status not in ["deployed", "superseded"]:
            cmd_flags = aks_agent_manager.command_flags()
            console.print(
                f"You can check the status later using 'az aks agent --status {cmd_flags}'", style="cyan")


def _setup_and_create_llm_config(console, aks_agent_manager: AKSAgentManagerLLMConfigBase):
    """Setup and create LLM configuration with user input.

    Args:
        console: Console instance for output
        aks_agent_manager: AKS agent manager instance (AKSAgentManager or AKSAgentManagerClient)
    """

    # Prompt for LLM configuration
    console.print("Please provide your LLM configuration. Type '/exit' to exit.", style=f"bold {HELP_COLOR}")

    provider = prompt_provider_choice()
    params = provider.prompt_params()

    # Validate the connection
    error, action = provider.validate_connection(params)

    if error is None:
        console.print("✅ LLM configuration validated successfully!", style=SUCCESS_COLOR)

        try:
            aks_agent_manager.save_llm_config(provider, params)
            console.print(
                "✅ LLM configuration created/updated successfully in Kubernetes cluster!",
                style=SUCCESS_COLOR)
        except Exception as e:
            console.print(f"❌ Failed to save LLM configuration: {str(e)}", style=ERROR_COLOR)
            raise AzCLIError(f"Failed to save LLM configuration: {str(e)}")

    elif error is not None and action == "retry_input":
        cmd_flags = aks_agent_manager.init_command_flags()
        raise AzCLIError(f"Please re-run `az aks agent-init {cmd_flags}` to correct the input parameters. {error}")
    else:
        raise AzCLIError(f"Please check your deployed model and network connectivity. {error}")


def _aks_agent_local_status(agent_manager: AKSAgentManagerClient):
    """Display the status of LLM configuration in client mode."""
    console = get_console()

    console.print("\n📊 Checking AKS agent status (client mode)...", style=INFO_COLOR)

    # Check Docker status
    console.print("\n🐳 Docker Status:", style="bold cyan")
    try:
        result = subprocess.run(
            ["docker", "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        docker_version = result.stdout.strip()
        console.print(f"  ✅ Docker installed: {docker_version}", style=SUCCESS_COLOR)

        # Check if Docker daemon is running
        try:
            subprocess.run(
                ["docker", "info"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5
            )
            console.print("  ✅ Docker daemon is running", style=SUCCESS_COLOR)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            console.print("  ⚠️  Docker daemon is not running", style=WARNING_COLOR)
            console.print("     Start Docker to use client mode features.", style=INFO_COLOR)
    except FileNotFoundError:
        console.print("  ❌ Docker is not installed", style=ERROR_COLOR)
        console.print("     Visit https://docs.docker.com/get-docker/ for installation instructions.", style=INFO_COLOR)
    except subprocess.TimeoutExpired:
        console.print("  ⚠️  Docker command timed out", style=WARNING_COLOR)
    except Exception as e:
        console.print(f"  ⚠️  Unable to check Docker status: {str(e)}", style=WARNING_COLOR)

    # Get LLM configuration
    model_list = agent_manager.get_llm_config()

    if model_list:
        console.print("\n📋 LLM Configurations:", style="bold cyan")
        for model_name, model_config in model_list.items():
            console.print(f"  • {model_name}", style=INFO_COLOR)
            if "api_base" in model_config:
                console.print(f"    API Base: {model_config['api_base']}", style="cyan")
            if "api_version" in model_config:
                console.print(f"    API Version: {model_config['api_version']}", style="cyan")

        console.print("\n✅ Client mode is configured and ready!", style=SUCCESS_COLOR)
    else:
        console.print("\n❌ No LLM configuration found", style=ERROR_COLOR)
        cmd_flags = agent_manager.init_command_flags()
        console.print(
            f"Run 'az aks agent-init {cmd_flags}' to set up LLM configuration.", style=INFO_COLOR)


def _aks_agent_status(agent_manager: AKSAgentManager):
    """Display the status of the AKS agent deployment."""
    console = get_console()

    console.print("\n📊 Checking AKS agent status...", style=INFO_COLOR)
    agent_status = agent_manager.get_agent_status()

    # Display helm status
    helm_status = agent_status.get("helm_status", "unknown")
    if helm_status == "deployed":
        console.print(f"\n✅ Helm Release: {helm_status}", style=SUCCESS_COLOR)
    elif helm_status == "not_found":
        console.print("\n❌ Helm Release: Not found", style=ERROR_COLOR)
        cmd_flags = agent_manager.init_command_flags()
        console.print(
            f"The AKS agent is not installed. Run 'az aks agent-init {cmd_flags}' to install.", style=INFO_COLOR)
        return
    else:
        console.print(f"\n⚠️  Helm Release: {helm_status}", style=WARNING_COLOR)

    # Display deployment status
    deployments = agent_status.get("deployments", [])
    if deployments:
        console.print("\n📦 Deployments:", style="bold cyan")
        for dep in deployments:
            ready_replicas = dep.get("ready_replicas", 0)
            replicas = dep.get("replicas", 0)
            status_color = SUCCESS_COLOR if ready_replicas == replicas and replicas > 0 else WARNING_COLOR
            console.print(f"  • {dep['name']}: {ready_replicas}/{replicas} ready", style=status_color)

    # Display pod status
    pods = agent_status.get("pods", [])
    if pods:
        console.print("\n🐳 Pods:", style="bold cyan")
        for pod in pods:
            pod_name = pod.get("name", "unknown")
            pod_phase = pod.get("phase", "unknown")
            pod_ready = pod.get("ready", False)

            if pod_ready and pod_phase == "Running":
                console.print(f"  • {pod_name}: {pod_phase} ✓", style=SUCCESS_COLOR)
            elif pod_phase == "Running":
                console.print(f"  • {pod_name}: {pod_phase} (not ready)", style=WARNING_COLOR)
            else:
                console.print(f"  • {pod_name}: {pod_phase}", style=WARNING_COLOR)

    # Display LLM configurations
    llm_configs = agent_status.get("llm_configs", [])
    if llm_configs:
        console.print("\n📋 LLM Configurations:", style="bold cyan")
        for llm_config in llm_configs:
            model_name = llm_config.get("model", "unknown")
            console.print(f"  • {model_name}", style=INFO_COLOR)
            if "api_base" in llm_config:
                console.print(f"    API Base: {llm_config['api_base']}", style="cyan")
            if "api_version" in llm_config:
                console.print(f"    API Version: {llm_config['api_version']}", style="cyan")

    # Display overall status
    if agent_status.get("ready", False):
        console.print("\n✅ AKS agent is ready and running!", style=SUCCESS_COLOR)
    else:
        console.print("\n⚠️  AKS agent is not fully ready", style=WARNING_COLOR)


def aks_agent_cleanup(
        cmd,
        client,
        resource_group_name,
        cluster_name,
        namespace,
        mode=None,
        yes=False,
):
    """Cleanup and uninstall the AKS agent."""
    with CLITelemetryClient(event_type="cleanup") as telemetry_client:
        use_client_mode = (mode == "client")

        # Record the mode being used in telemetry
        telemetry_client.mode = "client" if use_client_mode else "cluster"

        console = get_console()

        # Validate namespace requirement based on mode
        if not use_client_mode and not namespace:
            raise AzCLIError(
                "--namespace is required for cluster mode.")

        if use_client_mode and namespace:
            console.print(
                f"⚠️  Warning: --namespace '{namespace}' is specified but will be ignored in client mode.",
                style=WARNING_COLOR)

        if not yes:
            console.print(
                "\n⚠️  Warning: This will uninstall the AKS agent and delete all associated resources.",
                style=WARNING_COLOR)

            user_confirmation = console.input(
                f"\n[{WARNING_COLOR}]Are you sure you want to proceed with cleanup? (y/N): [/]").strip().lower()

            if user_confirmation not in ['y', 'yes']:
                console.print("❌ Cleanup cancelled.", style=INFO_COLOR)
                return

        console.print("\n🗑️  Starting cleanup (this typically takes a few seconds)...", style=INFO_COLOR)

        kubeconfig = get_aks_credentials(
            client,
            resource_group_name,
            cluster_name
        )
        subscription_id = get_subscription_id(cmd.cli_ctx)

        if use_client_mode:
            agent_manager = AKSAgentManagerClient(
                resource_group_name=resource_group_name,
                cluster_name=cluster_name,
                subscription_id=subscription_id,
                kubeconfig_path=kubeconfig,
            )
        else:
            agent_manager = AKSAgentManager(
                resource_group_name=resource_group_name,
                cluster_name=cluster_name,
                subscription_id=subscription_id,
                namespace=namespace,
                kubeconfig_path=kubeconfig
            )

        success = agent_manager.uninstall_agent()

        if success:
            console.print("✅ Cleanup completed successfully! All resources have been removed.", style=SUCCESS_COLOR)
        else:
            cmd_flags = agent_manager.command_flags()
            console.print(
                f"❌ Cleanup failed. Please run 'az aks agent --status {cmd_flags}' to verify cleanup completion.", style=ERROR_COLOR)


# pylint: disable=unused-argument
# pylint: disable=too-many-locals
def aks_agent(
    cmd,
    client,
    prompt,
    namespace,
    model,
    max_steps,
    resource_group_name,
    cluster_name,
    mode=None,
    no_interactive=False,
    no_echo_request=False,
    show_tool_output=False,
    refresh_toolsets=False,
    status=False,
):
    """Run AI assistant to analyze and troubleshoot Azure Kubernetes Service (AKS) clusters."""
    with CLITelemetryClient() as telemetry_client:

        subscription_id = get_subscription_id(cmd.cli_ctx)

        kubeconfig = get_aks_credentials(
            client,
            resource_group_name,
            cluster_name
        )

        # Determine which mode to use based on local config files
        use_client_mode = (mode == "client")

        # Record the mode being used in telemetry
        telemetry_client.mode = "client" if use_client_mode else "cluster"

        # Validate namespace requirement based on mode
        if not use_client_mode and not namespace:
            raise AzCLIError(
                "--namespace is required for cluster mode.")

        if use_client_mode and namespace:
            console = get_console()
            console.print(
                f"⚠️  Warning: --namespace '{namespace}' is specified but will be ignored in client mode.",
                style=WARNING_COLOR)

        if use_client_mode:
            agent_manager = AKSAgentManagerClient(
                resource_group_name=resource_group_name,
                cluster_name=cluster_name,
                subscription_id=subscription_id,
                kubeconfig_path=kubeconfig,
            )
            func_aks_agent_status = _aks_agent_local_status
        else:
            agent_manager = AKSAgentManager(
                resource_group_name=resource_group_name,
                cluster_name=cluster_name,
                namespace=namespace,
                subscription_id=subscription_id,
                kubeconfig_path=kubeconfig
            )
            func_aks_agent_status = _aks_agent_status

        if status:
            func_aks_agent_status(agent_manager)
            return

        # Only check for pods if using container mode
        if not use_client_mode:
            success, result = agent_manager.get_agent_pods()
            if not success:
                # get_agent_pods already logged the error, provide helpful message
                cmd_flags = agent_manager.init_command_flags()
                error_msg = f"Failed to find AKS agent pods: {result}\n"
                error_msg += f"The AKS agent may not be deployed. Run 'az aks agent-init {cmd_flags}' to initialize the deployment."
                raise CLIError(error_msg)

        # prepare CLI flags

        # user quoted prompt to not break the command line parsing
        flags = f'"{prompt}"' if prompt else ''
        if model:
            flags += f' --model "{model}"'
        if max_steps:
            flags += f' --max-steps {max_steps}'
        if no_interactive:
            flags += ' --no-interactive'
        if no_echo_request:
            flags += ' --no-echo-request'
        if show_tool_output:
            flags += ' --show-tool-output'
        if refresh_toolsets:
            flags += ' --refresh-toolsets'

        # Use AKSAgentManager to execute commands on the agent pod
        agent_manager.exec_aks_agent(flags)


# pylint: disable=unused-argument
def _get_k8s_extension_state(cmd, resource_group_name, cluster_name, extension_name, cluster_type):
    """
    Helper function to fetch the state of a Kubernetes extension from the k8s-extension module.
    
    Args:
        cmd: CLI command context
        resource_group_name: Azure resource group name
        cluster_name: AKS cluster name
        extension_name: Name of the extension
        cluster_type: Type of cluster (e.g., 'managedClusters')
    
    Returns:
        Extension resource object with properties like provisioning state, install state, version, etc.
    
    Raises:
        Exception if k8s-extension module is not available or extension not found
    """
    try:
        # Import the helper and constants from aks-preview module
        from azext_aks_preview._helpers import get_k8s_extension_module
        from azext_aks_preview._consts import (
            CONST_K8S_EXTENSION_CUSTOM_MOD_NAME,
            CONST_K8S_EXTENSION_CLIENT_FACTORY_MOD_NAME,
        )
    except ImportError:
        raise CLIError(
            "Please add CLI extension 'aks-preview' for cross-module k8s extension operations. "
            "Run: az extension add --name aks-preview"
        )
    
    try:
        # Get the k8s-extension module and client factory
        k8s_extension_custom_mod = get_k8s_extension_module(CONST_K8S_EXTENSION_CUSTOM_MOD_NAME)
        client_factory = get_k8s_extension_module(CONST_K8S_EXTENSION_CLIENT_FACTORY_MOD_NAME)
        k8s_client = client_factory.cf_k8s_extension_operation(cmd.cli_ctx)
        
        # Call show_k8s_extension to get the extension state
        extension = k8s_extension_custom_mod.show_k8s_extension(
            k8s_client,
            resource_group_name,
            cluster_name,
            extension_name,
            cluster_type,
        )
        return extension
    except Exception as ex:
        logger.error("Failed to retrieve k8s extension state: %s", ex)
        raise


def aks_agent_troubleshoot_cluster_extension(
    cmd,
    client,
    resource_group_name,
    cluster_name,
    extension_name,
    cluster_type,
    prompt=None,
    namespace=None,
    model=None,
    max_steps=10,
    mode=None,
    show_tool_output=False,
):
    """AI-assisted troubleshooting for a Kubernetes extension on an AKS cluster."""
    from azext_aks_agent.agent.k8s.extension_agent_manager import ExtensionAgentManager

    with CLITelemetryClient(event_type="troubleshoot_extension") as telemetry_client:
        console = get_console()

        subscription_id = get_subscription_id(cmd.cli_ctx)
        kubeconfig = get_aks_credentials(
            client,
            resource_group_name,
            cluster_name,
        )

        use_client_mode = (mode == "client")
        telemetry_client.mode = "client" if use_client_mode else "cluster"

        console.print(f"\n🔧 Troubleshooting extension: {extension_name}", style=f"bold {HELP_COLOR}")
        console.print(f"Cluster: {cluster_name} | Resource Group: {resource_group_name}", style=INFO_COLOR)

        # Step 1: Detect extension namespace + fetch ARM state for prompt
        # Both use a single _get_k8s_extension_state call — namespace detection and ARM evidence.
        arm_state: dict = {}
        extension_namespace = namespace  # user-provided takes priority

        try:
            if not extension_namespace:
                console.print("\n  Querying ARM resource for namespace and state...", style=INFO_COLOR)

            extension_resource = _get_k8s_extension_state(
                cmd, resource_group_name, cluster_name, extension_name, cluster_type,
            )
            arm_state = (
                extension_resource.as_dict()
                if hasattr(extension_resource, 'as_dict')
                else vars(extension_resource)
            )

            if not extension_namespace:
                # scope.cluster.release_namespace is the canonical field
                scope = arm_state.get('scope') or {}
                cluster_scope = scope.get('cluster') or {}
                extension_namespace = cluster_scope.get('release_namespace') or arm_state.get('namespace')

                if extension_namespace:
                    console.print(f"  ✓ Namespace from ARM: {extension_namespace}", style=SUCCESS_COLOR)
                else:
                    console.print("  • Namespace not in ARM resource, using fallback detection", style=INFO_COLOR)
        except Exception as e:
            if not extension_namespace:
                logger.debug("ARM resource query failed: %s", e)
                console.print("  • ARM resource unavailable, using fallback detection", style=INFO_COLOR)
            else:
                logger.debug("ARM state fetch failed: %s", e)

        # Create ExtensionAgentManager — handles remaining namespace detection strategies
        ext_manager = ExtensionAgentManager(
            resource_group_name=resource_group_name,
            cluster_name=cluster_name,
            subscription_id=subscription_id,
            extension_name=extension_name,
            extension_namespace=extension_namespace,
            kubeconfig_path=kubeconfig,
        )
        detected_ns = ext_manager.detected_namespace

        # Step 1 output: namespace + pod count in extension namespace
        try:
            pod_count = ext_manager.count_pods_in_namespace()
            console.print(f"  Extension namespace: {detected_ns}", style=SUCCESS_COLOR)
            console.print(f"  Pods in '{detected_ns}': {pod_count}", style=SUCCESS_COLOR)
        except AzCLIError as e:
            console.print(f"  ⚠️  Could not count pods in '{detected_ns}': {e}", style=WARNING_COLOR)

        # Step 2: Build agent manager for execution
        # The aks-agent pod lives in the aks-agent namespace, not the extension namespace.
        # We run the agent there and pass the extension context via the prompt.
        if use_client_mode:
            agent_manager = AKSAgentManagerClient(
                resource_group_name=resource_group_name,
                cluster_name=cluster_name,
                subscription_id=subscription_id,
                kubeconfig_path=kubeconfig,
            )
        else:
            # Use the aks-agent runtime namespace for pod execution checks.
            # The extension namespace is separate and only used for diagnostics context.
            agent_namespace = "aks-agent"
            agent_manager = AKSAgentManager(
                resource_group_name=resource_group_name,
                cluster_name=cluster_name,
                namespace=agent_namespace,
                subscription_id=subscription_id,
                kubeconfig_path=kubeconfig,
            )

            # Verify aks-agent pods are available before running
            success, result = agent_manager.get_agent_pods()
            if not success:
                if mode is None:
                    console.print(
                        "⚠️  AKS agent pods not found in cluster mode. Falling back to client mode.",
                        style=WARNING_COLOR,
                    )
                    agent_manager = AKSAgentManagerClient(
                        resource_group_name=resource_group_name,
                        cluster_name=cluster_name,
                        subscription_id=subscription_id,
                        kubeconfig_path=kubeconfig,
                    )
                    telemetry_client.mode = "client"
                else:
                    cmd_flags = agent_manager.init_command_flags()
                    raise CLIError(
                        f"Failed to find AKS agent pods: {result}\n"
                        f"Run 'az aks agent-init {cmd_flags}' to initialize the deployment, "
                        f"or use --mode client to run locally."
                    )

        # Step 3: Build extension-scoped prompt with pre-fetched ARM state and execute
        system_prompt = ext_manager.get_extension_diagnostics_prompt(prompt, arm_state=arm_state)

        # shlex.quote wraps the prompt in single quotes and escapes any internal
        # single quotes, handling all shell metacharacters (backticks, $(), !,
        # newlines, etc.). This is safe for bash -c (cluster mode) and also
        # round-trips correctly through shlex.split used in client mode.
        flags = shlex.quote(system_prompt)
        if model:
            flags += f' --model {shlex.quote(model)}'
        if max_steps:
            flags += f' --max-steps {max_steps}'
        if show_tool_output:
            flags += ' --show-tool-output'

        console.print("\n🤖 Starting AI-assisted diagnostics...\n", style=f"bold {HELP_COLOR}")
        agent_manager.exec_aks_agent(flags)
