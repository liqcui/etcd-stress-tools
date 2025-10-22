#!/usr/bin/env python3
"""
Simplified Kubernetes Deployment Testing Tool
Creates namespaces with deployments that mount small ConfigMaps and Secrets
"""

import argparse
import asyncio
import base64
import logging
import os
import secrets
import sys
import time
from datetime import datetime
from typing import Dict, Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Colors:
    """ANSI color codes for terminal output"""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'


class Config:
    """Configuration class"""
    def __init__(self):
        self.total_namespaces = int(os.getenv('TOTAL_NAMESPACES', '100'))
        self.namespace_parallel = int(os.getenv('NAMESPACE_PARALLEL', '10'))
        self.namespace_prefix = os.getenv('NAMESPACE_PREFIX', 'deploy-test')
        self.deployments_per_ns = int(os.getenv('DEPLOYMENTS_PER_NS', '3'))
        self.max_concurrent_operations = int(os.getenv('MAX_CONCURRENT_OPERATIONS', '50'))
        self.namespace_ready_timeout = int(os.getenv('NAMESPACE_READY_TIMEOUT', '60'))
        self.log_file = os.getenv('LOG_FILE', 'deployment_test.log')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')
        self.cleanup_on_completion = os.getenv('CLEANUP_ON_COMPLETION', 'false').lower() == 'true'


class DeploymentTestTool:
    """Simplified deployment testing tool"""
    
    def __init__(self, config: Config):
        self.config = config
        self.setup_logging()
        self.setup_kubernetes_clients()
        
    def setup_logging(self):
        """Setup logging configuration"""
        log_level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
            handlers=[
                logging.FileHandler(self.config.log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def setup_kubernetes_clients(self):
        """Setup Kubernetes client configuration"""
        try:
            config.load_incluster_config()
            self.log_info("Using in-cluster Kubernetes configuration")
        except config.ConfigException:
            try:
                config.load_kube_config()
                self.log_info("Using kubeconfig file")
            except config.ConfigException as e:
                self.log_error(f"Failed to load Kubernetes configuration: {e}")
                sys.exit(1)
                
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        
    def log_info(self, message: str, component: str = "MAIN"):
        """Enhanced logging"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"{Colors.GREEN}[{component}]{Colors.NC} {timestamp} - {message}")
        self.logger.info(f"[{component}] {message}")
        
    def log_warn(self, message: str, component: str = "MAIN"):
        """Warning logging"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"{Colors.YELLOW}[{component}]{Colors.NC} {timestamp} - {message}")
        self.logger.warning(f"[{component}] {message}")
        
    def log_error(self, message: str, component: str = "MAIN"):
        """Error logging"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"{Colors.RED}[{component}]{Colors.NC} {timestamp} - {message}")
        self.logger.error(f"[{component}] {message}")

    async def ensure_namespace(self, namespace_name: str) -> bool:
        """Create namespace and wait for it to be ready"""
        try:
            try:
                await asyncio.to_thread(self.core_v1.read_namespace, name=namespace_name)
                self.log_info(f"Namespace {namespace_name} already exists", "NAMESPACE")
                return await self.wait_until_namespace_ready(namespace_name)
            except ApiException as e:
                if e.status != 404:
                    self.log_error(f"Error checking namespace {namespace_name}: {e}", "NAMESPACE")
                    return False

            namespace_body = client.V1Namespace(
                metadata=client.V1ObjectMeta(
                    name=namespace_name,
                    labels={'deployment-test': 'true'}
                )
            )
            
            await asyncio.to_thread(self.core_v1.create_namespace, body=namespace_body)
            self.log_info(f"Created namespace {namespace_name}", "NAMESPACE")
            return await self.wait_until_namespace_ready(namespace_name)
            
        except ApiException as create_e:
            if create_e.status == 409:
                return await self.wait_until_namespace_ready(namespace_name)
            self.log_error(f"Failed to create namespace {namespace_name}: {create_e}", "NAMESPACE")
            return False
        except Exception as e:
            self.log_error(f"Unexpected error creating namespace {namespace_name}: {e}", "NAMESPACE")
            return False

    async def wait_until_namespace_ready(self, namespace_name: str) -> bool:
        """Wait until namespace is ready"""
        deadline = time.time() + self.config.namespace_ready_timeout
        
        while time.time() < deadline:
            try:
                ns = await asyncio.to_thread(self.core_v1.read_namespace, name=namespace_name)
                phase = getattr(getattr(ns, 'status', None), 'phase', None)
                
                if phase == 'Active':
                    self.log_info(f"Namespace {namespace_name} is ready", "NAMESPACE")
                    return True
                
                await asyncio.sleep(0.5)
            except ApiException as e:
                if e.status == 404:
                    await asyncio.sleep(0.5)
                    continue
                else:
                    self.log_warn(f"Error waiting for namespace {namespace_name}: {e}", "NAMESPACE")
                    return False
            except Exception as e:
                self.log_warn(f"Unexpected error waiting for namespace {namespace_name}: {e}", "NAMESPACE")
                return False
        
        self.log_warn(f"Timeout waiting for namespace {namespace_name}", "NAMESPACE")
        return False

    async def create_small_configmap(self, namespace: str, name: str) -> bool:
        """Create a small ConfigMap"""
        try:
            data = {
                f"config-{i}": f"value-{i}-{secrets.token_hex(4)}"
                for i in range(3)
            }
            
            configmap_body = client.V1ConfigMap(
                metadata=client.V1ObjectMeta(
                    name=name, 
                    namespace=namespace,
                    labels={'type': 'small-configmap', 'deployment-test': 'true'}
                ),
                data=data
            )
            
            await asyncio.to_thread(
                self.core_v1.create_namespaced_config_map,
                namespace=namespace,
                body=configmap_body
            )
            return True
        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create ConfigMap {name} in {namespace}: {e}", "CONFIGMAP")
            return False

    async def create_small_secret(self, namespace: str, name: str) -> bool:
        """Create a small Secret"""
        try:
            data = {
                "username": base64.b64encode(f"user-{secrets.token_hex(4)}".encode()).decode(),
                "password": base64.b64encode(secrets.token_bytes(16)).decode(),
            }
            
            secret_body = client.V1Secret(
                metadata=client.V1ObjectMeta(
                    name=name, 
                    namespace=namespace,
                    labels={'type': 'small-secret', 'deployment-test': 'true'}
                ),
                type="Opaque",
                data=data
            )
            
            await asyncio.to_thread(
                self.core_v1.create_namespaced_secret,
                namespace=namespace,
                body=secret_body
            )
            return True
        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create Secret {name} in {namespace}: {e}", "SECRET")
            return False

    async def create_deployment(self, namespace: str, name: str) -> bool:
        """Create deployment with 2 ConfigMaps and 2 Secrets mounted"""
        try:
            # Create ConfigMaps and Secrets first
            cm_names = [f"{name}-cm-{i}" for i in range(2)]
            secret_names = [f"{name}-secret-{i}" for i in range(2)]
            
            # Create resources concurrently
            resource_tasks = []
            for cm_name in cm_names:
                resource_tasks.append(self.create_small_configmap(namespace, cm_name))
            for secret_name in secret_names:
                resource_tasks.append(self.create_small_secret(namespace, secret_name))
            
            results = await asyncio.gather(*resource_tasks, return_exceptions=True)
            if not all(r is True for r in results if not isinstance(r, Exception)):
                self.log_warn(f"Some resources failed for deployment {name}", "DEPLOYMENT")
            
            # Create volumes and volume mounts
            volumes = []
            volume_mounts = []
            
            # Mount 2 ConfigMaps
            for i, cm_name in enumerate(cm_names):
                volume_name = f"cm-vol-{i}"
                volumes.append(client.V1Volume(
                    name=volume_name,
                    config_map=client.V1ConfigMapVolumeSource(name=cm_name)
                ))
                volume_mounts.append(client.V1VolumeMount(
                    name=volume_name,
                    mount_path=f"/config/cm-{i}"
                ))
            
            # Mount 2 Secrets
            for i, secret_name in enumerate(secret_names):
                volume_name = f"secret-vol-{i}"
                volumes.append(client.V1Volume(
                    name=volume_name,
                    secret=client.V1SecretVolumeSource(secret_name=secret_name)
                ))
                volume_mounts.append(client.V1VolumeMount(
                    name=volume_name,
                    mount_path=f"/secrets/secret-{i}"
                ))
            
            # Create deployment
            deployment_body = client.V1Deployment(
                metadata=client.V1ObjectMeta(
                    name=name,
                    namespace=namespace,
                    labels={'app': name, 'deployment-test': 'true'}
                ),
                spec=client.V1DeploymentSpec(
                    replicas=1,
                    selector=client.V1LabelSelector(
                        match_labels={'app': name}
                    ),
                    template=client.V1PodTemplateSpec(
                        metadata=client.V1ObjectMeta(
                            labels={'app': name, 'deployment-test': 'true'}
                        ),
                        spec=client.V1PodSpec(
                            containers=[
                                client.V1Container(
                                    name="test-container",
                                    image="registry.access.redhat.com/ubi8/ubi-minimal:latest",
                                    command=["/bin/sleep", "infinity"],
                                    volume_mounts=volume_mounts,
                                    resources=client.V1ResourceRequirements(
                                        # requests={"memory": "64Mi", "cpu": "50m"},
                                        # limits={"memory": "128Mi", "cpu": "100m"}
                                    )
                                )
                            ],
                            volumes=volumes
                        )
                    )
                )
            )
            
            await asyncio.to_thread(
                self.apps_v1.create_namespaced_deployment,
                namespace=namespace,
                body=deployment_body
            )
            
            # Wait for deployment to be ready
            is_ready = await self.wait_for_deployment_ready(namespace, name)
            return is_ready
            
        except ApiException as e:
            if e.status == 409:
                return await self.wait_for_deployment_ready(namespace, name)
            self.log_warn(f"Failed to create Deployment {name} in {namespace}: {e}", "DEPLOYMENT")
            return False

    async def wait_for_deployment_ready(self, namespace: str, deployment_name: str, timeout: int = 300) -> bool:
        """Wait for deployment pods to be ready"""
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                deployment = await asyncio.to_thread(
                    self.apps_v1.read_namespaced_deployment,
                    name=deployment_name,
                    namespace=namespace
                )
                
                spec_replicas = deployment.spec.replicas or 0
                status = deployment.status
                ready_replicas = status.ready_replicas or 0
                available_replicas = status.available_replicas or 0
                
                if (ready_replicas == spec_replicas and 
                    available_replicas == spec_replicas and 
                    spec_replicas > 0):
                    
                    pods = await asyncio.to_thread(
                        self.core_v1.list_namespaced_pod,
                        namespace=namespace,
                        label_selector=f"app={deployment_name}"
                    )
                    
                    running_pods = [
                        pod for pod in pods.items
                        if pod.status.phase == "Running" and
                        all(c.ready for c in (pod.status.container_statuses or []))
                    ]
                    
                    if len(running_pods) == spec_replicas:
                        return True
                
                await asyncio.sleep(2)
                
            except ApiException as e:
                if e.status == 404:
                    await asyncio.sleep(2)
                    continue
                self.log_warn(f"Error checking deployment {deployment_name}: {e}", "DEPLOYMENT")
                return False
            except Exception as e:
                self.log_warn(f"Unexpected error checking deployment {deployment_name}: {e}", "DEPLOYMENT")
                return False
        
        self.log_warn(f"Timeout waiting for deployment {deployment_name}", "DEPLOYMENT")
        return False

    async def create_namespace_deployments(self, namespace_idx: int) -> Dict[str, int]:
        """Create deployments for a single namespace"""
        namespace_name = f"{self.config.namespace_prefix}-{namespace_idx}"
        
        # Create namespace
        if not await self.ensure_namespace(namespace_name):
            self.log_error(f"Failed to create namespace {namespace_name}", "NAMESPACE")
            return {"success": 0, "errors": 1}
        
        # Create deployments
        semaphore = asyncio.Semaphore(self.config.max_concurrent_operations)
        
        async def create_with_semaphore(name: str) -> bool:
            async with semaphore:
                return await self.create_deployment(namespace_name, name)
        
        tasks = [
            create_with_semaphore(f"deploy-{i+1}") 
            for i in range(self.config.deployments_per_ns)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for result in results if result is True)
        error_count = len(tasks) - success_count
        
        self.log_info(f"Created {success_count}/{len(tasks)} deployments in {namespace_name}", "DEPLOYMENT")
        return {"success": success_count, "errors": error_count}

    async def create_all_namespaces_and_deployments(self):
        """Create all namespaces and deployments"""
        self.log_info(f"Creating {self.config.total_namespaces} namespaces with {self.config.deployments_per_ns} deployments each", "MAIN")
        start_time = time.time()
        
        semaphore = asyncio.Semaphore(self.config.namespace_parallel)
        
        async def create_with_semaphore(ns_idx: int) -> Dict[str, int]:
            async with semaphore:
                return await self.create_namespace_deployments(ns_idx)
        
        tasks = [
            create_with_semaphore(i) 
            for i in range(1, self.config.total_namespaces + 1)
        ]
        
        # Process in batches for progress reporting
        batch_size = self.config.namespace_parallel
        total_success = 0
        total_errors = 0
        
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)
            
            for result in batch_results:
                if isinstance(result, dict):
                    total_success += result.get("success", 0)
                    total_errors += result.get("errors", 0)
                else:
                    total_errors += 1
            
            completed = min(i + batch_size, self.config.total_namespaces)
            self.log_info(f"Processed {completed}/{self.config.total_namespaces} namespaces "
                         f"(Success: {total_success}, Errors: {total_errors})", "PROGRESS")
        
        elapsed_time = time.time() - start_time
        self.log_info(f"Deployment creation completed in {elapsed_time:.2f}s: "
                     f"Success: {total_success}, Errors: {total_errors}", "COMPLETE")

    async def cleanup_all_resources(self):
        """Clean up all created resources"""
        if not self.config.cleanup_on_completion:
            self.log_info("Cleanup disabled", "CLEANUP")
            return
            
        self.log_info("Starting cleanup", "CLEANUP")
        
        try:
            namespaces = await asyncio.to_thread(self.core_v1.list_namespace)
            cleanup_namespaces = [
                ns.metadata.name for ns in namespaces.items
                if ns.metadata.labels and ns.metadata.labels.get('deployment-test') == 'true'
            ]
            
            self.log_info(f"Found {len(cleanup_namespaces)} namespaces to clean up", "CLEANUP")
            
            semaphore = asyncio.Semaphore(self.config.namespace_parallel)
            
            async def delete_with_semaphore(ns_name: str):
                async with semaphore:
                    try:
                        await asyncio.to_thread(
                            self.core_v1.delete_namespace,
                            name=ns_name,
                            grace_period_seconds=0
                        )
                        return True
                    except ApiException as e:
                        if e.status == 404:
                            return True
                        self.log_warn(f"Failed to delete namespace {ns_name}: {e}", "CLEANUP")
                        return False
            
            if cleanup_namespaces:
                delete_tasks = [delete_with_semaphore(ns) for ns in cleanup_namespaces]
                results = await asyncio.gather(*delete_tasks, return_exceptions=True)
                successful = sum(1 for result in results if result is True)
                self.log_info(f"Deleted {successful}/{len(cleanup_namespaces)} namespaces", "CLEANUP")
            
            self.log_info("Cleanup completed", "CLEANUP")
            
        except Exception as e:
            self.log_error(f"Cleanup failed: {e}", "CLEANUP")

    async def run_test(self):
        """Run the deployment test"""
        try:
            start_time = time.time()
            self.log_info("Starting deployment test", "MAIN")
            
            await self.create_all_namespaces_and_deployments()
            
            if self.config.cleanup_on_completion:
                await self.cleanup_all_resources()
            
            total_time = time.time() - start_time
            self.log_info(f"Total execution time: {total_time:.2f} seconds", "MAIN")
            
        except KeyboardInterrupt:
            self.log_warn("Test interrupted by user", "MAIN")
            raise
        except Exception as e:
            self.log_error(f"Test failed: {e}", "MAIN")
            raise


def create_argument_parser():
    """Create argument parser"""
    parser = argparse.ArgumentParser(
        description='Simplified Kubernetes Deployment Testing Tool',
        epilog="""
Each namespace gets:
  - N deployments (default: 3)
  - Each deployment has 1 pod
  - Each pod mounts 2 small ConfigMaps and 2 small Secrets

Environment Variables:
  TOTAL_NAMESPACES (default: 100)
  NAMESPACE_PARALLEL (default: 10)
  NAMESPACE_PREFIX (default: deploy-test)
  DEPLOYMENTS_PER_NS (default: 3)
  MAX_CONCURRENT_OPERATIONS (default: 50)
  NAMESPACE_READY_TIMEOUT (default: 60)
  LOG_FILE, LOG_LEVEL
  CLEANUP_ON_COMPLETION (default: false)

Examples:
  %(prog)s --total-namespaces 50 --deployments-per-ns 5
  %(prog)s --cleanup --namespace-parallel 20
        """
    )
    
    parser.add_argument('--total-namespaces', type=int,
                       help='Total number of namespaces to create')
    parser.add_argument('--namespace-parallel', type=int,
                       help='Number of namespaces to process in parallel')
    parser.add_argument('--namespace-prefix',
                       help='Prefix for namespace names')
    parser.add_argument('--deployments-per-ns', type=int,
                       help='Number of deployments per namespace')
    parser.add_argument('--max-concurrent', type=int,
                       help='Maximum concurrent operations')
    parser.add_argument('--namespace-timeout', type=int,
                       help='Timeout for namespace readiness in seconds')
    parser.add_argument('--cleanup', action='store_true',
                       help='Enable cleanup after completion')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Set logging level')
    parser.add_argument('--log-file',
                       help='Log file path')
    
    return parser


async def main():
    """Main execution function"""
    parser = create_argument_parser()
    args = parser.parse_args()
    
    config = Config()
    
    # Override config with CLI arguments
    if args.total_namespaces is not None:
        config.total_namespaces = args.total_namespaces
    if args.namespace_parallel is not None:
        config.namespace_parallel = args.namespace_parallel
    if args.namespace_prefix is not None:
        config.namespace_prefix = args.namespace_prefix
    if args.deployments_per_ns is not None:
        config.deployments_per_ns = args.deployments_per_ns
    if args.max_concurrent is not None:
        config.max_concurrent_operations = args.max_concurrent
    if args.namespace_timeout is not None:
        config.namespace_ready_timeout = args.namespace_timeout
    if args.cleanup:
        config.cleanup_on_completion = True
    if args.log_level is not None:
        config.log_level = args.log_level
    if args.log_file is not None:
        config.log_file = args.log_file
    
    tool = DeploymentTestTool(config)
    
    try:
        await tool.run_test()
    except KeyboardInterrupt:
        tool.log_warn("Test interrupted", "MAIN")
        sys.exit(1)
    except Exception as e:
        tool.log_error(f"Test failed: {e}", "MAIN")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())