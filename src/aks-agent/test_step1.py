#!/usr/bin/env python3
# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""
Demo script for Step 1: Detect Extension Namespace and Count Pods

This script demonstrates how the ExtensionAgentManager detects an extension's namespace
and counts pods in that namespace.

Usage:
  python3 test_step1.py <extension_name> <cluster_name> <resource_group> [--namespace <namespace>]

Example:
  python3 test_step1.py azure-policy myCluster myResourceGroup
  python3 test_step1.py app-routing myCluster myResourceGroup --namespace app-routing-system
"""

import sys
import os
import argparse
from pathlib import Path

# Add the src directory to the path
src_dir = Path(__file__).parent.parent.parent.parent / "src" / "aks-agent"
sys.path.insert(0, str(src_dir))

from azext_aks_agent.agent.k8s.extension_agent_manager import ExtensionAgentManager
from azure.cli.core.azclierror import AzCLIError


def print_header(text):
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def print_section(text):
    """Print a section header."""
    print(f"\n📋 {text}")
    print("-" * 70)


def demo_step1(extension_name, cluster_name, resource_group, extension_namespace=None, subscription_id=""):

    """
    Demonstrate Step 1: Detect Extension Namespace and Count Pods
    
    Args:
        extension_name: Name of the extension (e.g., 'azure-policy')
        cluster_name: Name of the AKS cluster
        resource_group: Resource group name
        extension_namespace: Optional explicit namespace
        subscription_id: Azure subscription ID (optional for demo)
    """
    print_header(f"Step 1: Extension Namespace Detection & Pod Counting Demo")
    print(f"\nExtension: {extension_name}")
    print(f"Cluster: {cluster_name}")
    print(f"Resource Group: {resource_group}")
    if extension_namespace:
        print(f"Provided Namespace: {extension_namespace}")
    
    try:
        print_section("Initializing ExtensionAgentManager")
        print(f"Creating manager for extension '{extension_name}'...")
        
        # Create the manager
        ext_manager = ExtensionAgentManager(
            resource_group_name=resource_group,
            cluster_name=cluster_name,
            subscription_id=subscription_id or "00000000-0000-0000-0000-000000000000",
            extension_name=extension_name,
            extension_namespace=extension_namespace,
            kubeconfig_path=None  # Uses default kubeconfig
        )
        
        print("✅ Manager initialized successfully")
        
        # Step 1a: Display detected namespace
        print_section("Detected Extension Namespace")
        detected_ns = ext_manager.detected_namespace
        print(f"🎯 Namespace: {detected_ns}")
        
        # Step 1b: Count pods in namespace
        print_section("Counting Pods in Extension Namespace")
        print(f"Querying Kubernetes for pods in namespace '{detected_ns}'...")
        
        pod_count = ext_manager.count_pods_in_namespace()
        print(f"✅ Total pods: {pod_count}")
        
        # Step 1c: Get detailed pod information
        print_section("Pod Status Breakdown")
        
        pods_info = ext_manager.get_pods_info()
        
        for status, pods in pods_info['pods_by_status'].items():
            count = len(pods)
            if count > 0:
                status_symbol = "🟢" if status == "Running" else "🟡" if status == "Pending" else "🔴" if status == "Failed" else "⚪"
                print(f"\n{status_symbol} {status}: {count} pod(s)")
                
                for pod in pods:
                    ready_str = "✓ Ready" if pod.get('ready') else "✗ Not Ready"
                    print(f"   • {pod['name']:45s} [{ready_str}]")
        
        # Summary
        print_header(f"Results Summary")
        print(f"✅ Extension Namespace: {detected_ns}")
        print(f"✅ Total Pods Found: {pod_count}")
        print(f"✅ Running Pods: {len(pods_info['pods_by_status'].get('Running', []))}")
        print(f"✅ Pending Pods: {len(pods_info['pods_by_status'].get('Pending', []))}")
        print(f"✅ Failed Pods: {len(pods_info['pods_by_status'].get('Failed', []))}")
        
        print("\n✨ Step 1 demonstration complete!\n")
        return True
        
    except AzCLIError as e:
        print(f"\n❌ AzCLI Error: {str(e)}")
        return False
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Step 1 Demo: Extension Namespace Detection and Pod Counting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Detect namespace for azure-policy extension
  python3 test_step1.py azure-policy myCluster myResourceGroup
  
  # Use explicit namespace
  python3 test_step1.py app-routing myCluster myResourceGroup --namespace app-routing-system
  
  # Use custom subscription
  python3 test_step1.py flux myCluster myResourceGroup --subscription 12345678-1234-1234-1234-123456789012
        """
    )
    
    parser.add_argument('extension_name', help='Name of the extension to test (e.g., azure-policy, app-routing)')
    parser.add_argument('cluster_name', help='Name of the AKS cluster')
    parser.add_argument('resource_group', help='Resource group containing the cluster')
    parser.add_argument('--namespace', dest='extension_namespace', default=None,
                       help='Explicit Kubernetes namespace (if not provided, will auto-detect)')
    parser.add_argument('--subscription', dest='subscription_id', default="",
                       help='Azure subscription ID')
    
    args = parser.parse_args()
    
    success = demo_step1(
        extension_name=args.extension_name,
        cluster_name=args.cluster_name,
        resource_group=args.resource_group,
        extension_namespace=args.extension_namespace,
        subscription_id=args.subscription_id
    )
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
