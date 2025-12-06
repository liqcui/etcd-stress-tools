#!/usr/bin/env python3
"""
Simplified Kubernetes Deployment Testing Tool
Creates namespaces with deployments that mount small ConfigMaps and Secrets
Includes connectivity test pods that curl nginx services and log timing

SSL/TLS Handling:
- Automatically clears REQUESTS_CA_BUNDLE environment variable to prevent PEM lib errors
- Validates CA certificate files before using them
- Proactively tests API connectivity and auto-disables SSL verification on errors
- Supports environment variable overrides: K8S_VERIFY, OCP_API_VERIFY, K8S_CA_CERT
- Implements fallback mechanism similar to openshift_auth.py
"""

import argparse
import asyncio
import base64
import logging
import os
import secrets
import ssl
import sys
import time
from datetime import datetime
from typing import Dict, Any, Optional

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
        self.k8s_verify_ssl: Optional[bool] = self._get_verify_ssl_setting()
        self.kubeconfig_path: Optional[str] = os.getenv('KUBECONFIG')
        self.k8s_ca_cert_path: Optional[str] = None
        
        # NEW: Service creation control
        self.service_enabled = os.getenv('SERVICE_ENABLED', 'false').lower() == 'true'

        # Curl test configuration
        self.curl_test_enabled = os.getenv('CURL_TEST_ENABLED', 'false').lower() == 'true'
        self.curl_test_interval = int(os.getenv('CURL_TEST_INTERVAL', '30'))
        self.curl_test_count = int(os.getenv('CURL_TEST_COUNT', '10'))

        # StatefulSet OOM simulation configuration
        self.statefulset_enabled = os.getenv('STATEFULSET_ENABLED', 'true').lower() == 'true'
        self.statefulset_replicas = int(os.getenv('STATEFULSET_REPLICAS', '3'))
        self.statefulsets_per_ns = int(os.getenv('STATEFULSETS_PER_NS', '1'))

        # Build job configuration (based on must-gather analysis)
        # Observed pattern: 20+ builds per namespace, 3-5 concurrent
        self.build_job_enabled = os.getenv('BUILD_JOB_ENABLED', 'false').lower() == 'true'
        self.builds_per_ns = int(os.getenv('BUILDS_PER_NS', '10'))  # Completions per job
        self.build_parallelism = int(os.getenv('BUILD_PARALLELISM', '3'))  # Concurrent pods
        self.build_timeout = int(os.getenv('BUILD_TIMEOUT', '900'))  # 15 minutes (realistic)
    
    def _get_verify_ssl_setting(self) -> Optional[bool]:
        """Get SSL verification setting from environment"""
        for env_var in ['K8S_VERIFY', 'OCP_API_VERIFY', 'VERIFY_SSL']:
            val = os.getenv(env_var)
            if val is not None:
                val_lower = val.strip().lower()
                if val_lower in ('true', '1', 'yes'):
                    return True
                if val_lower in ('false', '0', 'no'):
                    return False
        return None  # Not set, will auto-detect

class DeploymentTestTool:
    """Simplified deployment testing tool"""
    
    def __init__(self, config: Config):
        self.config = config
        self.setup_logging()
        self.original_ca_bundle: Optional[str] = None
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
        """Setup Kubernetes client configuration with robust SSL handling"""
        try:
            # Step 1: Clear potentially broken REQUESTS_CA_BUNDLE
            # This is the root cause of many PEM lib errors
            self.original_ca_bundle = os.environ.pop('REQUESTS_CA_BUNDLE', None)
            if self.original_ca_bundle:
                self.log_info(f"Temporarily cleared REQUESTS_CA_BUNDLE: {self.original_ca_bundle}", "CONFIG")
            
            # Step 2: Load kubeconfig
            try:
                if self.config.kubeconfig_path:
                    self.log_info(f"Loading kubeconfig from: {self.config.kubeconfig_path}", "CONFIG")
                    config.load_kube_config(config_file=self.config.kubeconfig_path)
                else:
                    config.load_incluster_config()
                    self.log_info("Using in-cluster Kubernetes configuration", "CONFIG")
            except config.ConfigException:
                try:
                    config.load_kube_config()
                    self.log_info("Using default kubeconfig file", "CONFIG")
                except config.ConfigException as e:
                    self.log_error(f"Failed to load Kubernetes configuration: {e}", "CONFIG")
                    sys.exit(1)
            
            # Step 3: Get default configuration and setup SSL verification
            k8s_conf = client.Configuration.get_default_copy()
            
            # Apply SSL verification setting from config/env
            if self.config.k8s_verify_ssl is not None:
                k8s_conf.verify_ssl = self.config.k8s_verify_ssl
                if not self.config.k8s_verify_ssl:
                    k8s_conf.assert_hostname = False
                    k8s_conf.ssl_ca_cert = None
                self.log_info(f"SSL verification set via environment: {self.config.k8s_verify_ssl}", "CONFIG")
            
            # Step 4: Validate and capture CA certificate path
            if getattr(k8s_conf, 'ssl_ca_cert', None):
                ca_path = k8s_conf.ssl_ca_cert
                if self._is_ca_file_valid(ca_path):
                    self.config.k8s_ca_cert_path = ca_path
                    self.log_info(f"Using valid CA cert from config: {ca_path}", "CONFIG")
                else:
                    self.log_warn(f"CA cert file exists but is invalid: {ca_path}", "CONFIG")
                    # If verification not explicitly forced and CA is invalid, disable SSL
                    if self.config.k8s_verify_ssl is not True:
                        self.log_warn("Disabling SSL verification due to invalid CA cert", "CONFIG")
                        k8s_conf.verify_ssl = False
                        k8s_conf.assert_hostname = False
                        k8s_conf.ssl_ca_cert = None
                        self.config.k8s_verify_ssl = False
            
            # Allow CA cert override via environment
            env_ca = os.getenv('K8S_CA_CERT')
            if env_ca and os.path.isfile(env_ca):
                if self._is_ca_file_valid(env_ca):
                    self.config.k8s_ca_cert_path = env_ca
                    k8s_conf.ssl_ca_cert = env_ca
                    self.log_info(f"Using CA cert from K8S_CA_CERT env: {env_ca}", "CONFIG")
            
            # Step 5: Create initial client
            self.core_v1 = client.CoreV1Api(client.ApiClient(configuration=k8s_conf))
            self.apps_v1 = client.AppsV1Api(client.ApiClient(configuration=k8s_conf))
            
            # Step 6: Proactively test API connectivity and auto-fallback on SSL errors
            try:
                self._ensure_k8s_api_connectivity()
                self.log_info("Kubernetes API connectivity verified successfully", "CONFIG")
            except Exception as probe_err:
                # _ensure_k8s_api_connectivity handles fallback internally
                self.log_error(f"Failed to establish Kubernetes API connectivity: {probe_err}", "CONFIG")
                raise
                
        except Exception as e:
            self.log_error(f"Failed to setup Kubernetes clients: {e}", "CONFIG")
            sys.exit(1)
    
    def _is_ca_file_valid(self, ca_path: str) -> bool:
        """Validate a CA bundle by attempting to load it with ssl.
        
        Returns False if the file cannot be loaded as a trust store.
        """
        try:
            if not os.path.isfile(ca_path):
                return False
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.load_verify_locations(cafile=ca_path)
            return True
        except Exception as e:
            self.logger.debug(f"CA validation failed for {ca_path}: {e}")
            return False
    
    def _ensure_k8s_api_connectivity(self) -> None:
        """Probe the Kubernetes API and auto-recover from common SSL/PEM issues.
        
        If a PEM/SSL verification error is detected (e.g., invalid REQUESTS_CA_BUNDLE,
        broken CA file, or hostname mismatch), automatically disable TLS verification
        as a last-resort fallback to avoid repeated urllib3 retry warnings.
        """
        try:
            # Use a lightweight version check to test connectivity
            version_api = client.VersionApi(self.core_v1.api_client)
            _ = version_api.get_code()
            return
        except Exception as probe_err:
            err_text = str(probe_err)
            
            # Detect common SSL issues seen in logs
            ssl_error_indicators = (
                "PEM lib",
                "CERTIFICATE_VERIFY_FAILED",
                "SSLError",
                "certificate verify failed",
                "ssl.c:",
                "[X509]",
                "Max retries exceeded",
            )
            
            if not any(ind in err_text for ind in ssl_error_indicators):
                # Not an SSL-related problem; re-raise for caller
                self.log_error(f"Kubernetes API connectivity check failed (non-SSL): {err_text}", "CONFIG")
                raise
            
            # SSL error detected - log and fallback
            self.log_warn(
                f"Kubernetes API SSL verification failed: {err_text[:200]}... "
                "Disabling TLS verification as fallback (set K8S_VERIFY=true to force verification)",
                "CONFIG"
            )
            
            # Rebuild configuration with SSL verification disabled
            k8s_conf_fallback = client.Configuration.get_default_copy()
            k8s_conf_fallback.verify_ssl = False
            k8s_conf_fallback.assert_hostname = False
            k8s_conf_fallback.ssl_ca_cert = None
            
            # Recreate API clients with fallback configuration
            api_client_fallback = client.ApiClient(configuration=k8s_conf_fallback)
            self.core_v1 = client.CoreV1Api(api_client_fallback)
            self.apps_v1 = client.AppsV1Api(api_client_fallback)
            self.config.k8s_verify_ssl = False
            
            # Verify connectivity with fallback config
            try:
                version_api = client.VersionApi(api_client_fallback)
                _ = version_api.get_code()
                self.log_info("Kubernetes API connectivity verified (SSL verification disabled)", "CONFIG")
            except Exception as fallback_err:
                self.log_error(f"Kubernetes API connectivity failed even with TLS disabled: {fallback_err}", "CONFIG")
                raise
    
    def __del__(self):
        """Cleanup: restore original REQUESTS_CA_BUNDLE if it was cleared"""
        if self.original_ca_bundle:
            os.environ['REQUESTS_CA_BUNDLE'] = self.original_ca_bundle
        
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

    async def create_service(self, namespace: str, deployment_name: str) -> bool:
        """Create a ClusterIP service for a deployment"""
        try:
            service_name = f"{deployment_name}-svc"
            
            service_body = client.V1Service(
                metadata=client.V1ObjectMeta(
                    name=service_name,
                    namespace=namespace,
                    labels={'app': deployment_name, 'deployment-test': 'true'}
                ),
                spec=client.V1ServiceSpec(
                    selector={'app': deployment_name},
                    ports=[
                        client.V1ServicePort(
                            name="http",
                            port=8080,
                            target_port=8080,
                            protocol="TCP"
                        )
                    ],
                    type="ClusterIP"
                )
            )
            
            await asyncio.to_thread(
                self.core_v1.create_namespaced_service,
                namespace=namespace,
                body=service_body
            )
            self.log_info(f"Created service {service_name} in {namespace}", "SERVICE")
            return True
        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create Service {service_name} in {namespace}: {e}", "SERVICE")
            return False

    async def create_headless_service(self, namespace: str, service_name: str, selector: Dict[str, str]) -> bool:
        """Create a headless (ClusterIP: None) service for StatefulSet"""
        try:
            service_body = client.V1Service(
                metadata=client.V1ObjectMeta(
                    name=service_name,
                    namespace=namespace,
                    labels={'app': 'oom-simulator', 'deployment-test': 'true'}
                ),
                spec=client.V1ServiceSpec(
                    cluster_ip="None",  # Headless service
                    selector=selector,
                    ports=[
                        client.V1ServicePort(
                            name="http",
                            port=8080,
                            protocol="TCP"
                        )
                    ]
                )
            )

            await asyncio.to_thread(
                self.core_v1.create_namespaced_service,
                namespace=namespace,
                body=service_body
            )
            self.log_info(f"Created headless service {service_name} in {namespace}", "SERVICE")
            return True
        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create headless Service {service_name} in {namespace}: {e}", "SERVICE")
            return False

    async def create_curl_test_pod(self, namespace: str, service_names: list) -> bool:
        """Create a pod that curls all services in the namespace and logs timing"""
        try:
            pod_name = "connectivity-test"
            
            # Build curl command that tests all services
            curl_commands = []
            for service_name in service_names:
                service_url = f"http://{service_name}:8080"
                # Use curl with detailed timing and diagnostics
                curl_cmd = f"""
echo "=== Testing {service_name} at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# Perform curl with detailed timing
curl_output=$(curl -o /dev/null -s -w "time_namelookup: %{{time_namelookup}}s\\ntime_connect: %{{time_connect}}s\\ntime_starttransfer: %{{time_starttransfer}}s\\ntime_total: %{{time_total}}s\\nhttp_code: %{{http_code}}" --connect-timeout 10 --max-time 15 {service_url} 2>&1)
curl_exit=$?

if [ $curl_exit -eq 0 ]; then
    echo "SUCCESS: Connected to {service_name}"
    echo "$curl_output" | while IFS= read -r line; do echo "  $line"; done
else
    echo "FAILED: Could not connect to {service_name} (exit code: $curl_exit)"
    echo "Curl error details:"
    case $curl_exit in
        6) echo "  - Could not resolve host" ;;
        7) echo "  - Failed to connect to host (pod may not be ready yet)" ;;
        28) echo "  - Connection timeout" ;;
        *) echo "  - Unknown error code: $curl_exit" ;;
    esac
fi
echo "---"
"""
                curl_commands.append(curl_cmd)
            
            # Create a script that runs continuously with initial wait
            full_script = f"""#!/bin/sh
echo "Starting connectivity tests in namespace {namespace}"
echo "Test interval: {self.config.curl_test_interval}s"
echo "Number of iterations: {self.config.curl_test_count}"
echo "================================================"
echo ""
echo "Waiting 30 seconds for all services and pods to be fully ready..."
sleep 30
echo "Starting tests now..."
echo ""

for i in $(seq 1 {self.config.curl_test_count}); do
    echo "### Iteration $i of {self.config.curl_test_count} ###"
    {"".join(curl_commands)}
    
    if [ $i -lt {self.config.curl_test_count} ]; then
        echo "Sleeping {self.config.curl_test_interval}s before next iteration..."
        sleep {self.config.curl_test_interval}
        echo ""
    fi
done

echo ""
echo "================================================"
echo "Connectivity tests completed in namespace {namespace}"
echo "Pod will continue running. Use 'kubectl logs -f {pod_name}' to view results."

# Keep pod running after tests complete
tail -f /dev/null
"""
            
            pod_body = client.V1Pod(
                metadata=client.V1ObjectMeta(
                    name=pod_name,
                    namespace=namespace,
                    labels={'app': 'connectivity-test', 'deployment-test': 'true'}
                ),
                spec=client.V1PodSpec(
                    restart_policy="Always",
                    containers=[
                        client.V1Container(
                            name="curl-tester",
                            image="alpine/curl:latest",
                            command=["/bin/sh", "-c"],
                            args=[full_script],
                            resources=client.V1ResourceRequirements(
                                requests={"memory": "64Mi", "cpu": "50m"},
                                limits={"memory": "128Mi", "cpu": "100m"}
                            )
                        )
                    ]
                )
            )
            
            await asyncio.to_thread(
                self.core_v1.create_namespaced_pod,
                namespace=namespace,
                body=pod_body
            )
            self.log_info(f"Created connectivity test pod in {namespace}", "CURL_TEST")
            return True
            
        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create connectivity test pod in {namespace}: {e}", "CURL_TEST")
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
                                        name="nginx",
                                        image="quay.io/openshift-psap-qe/nginx-alpine:multiarch",
                                        ports=[client.V1ContainerPort(container_port=8080)],
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
                
                # MODIFIED: Create service only if enabled
                if is_ready and self.config.service_enabled:
                    service_created = await self.create_service(namespace, name)
                    return is_ready and service_created
                
                return is_ready
                
            except ApiException as e:
                if e.status == 409:
                    return await self.wait_for_deployment_ready(namespace, name)
                self.log_warn(f"Failed to create Deployment {name} in {namespace}: {e}", "DEPLOYMENT")
                return False
                
    async def create_statefulset(self, namespace: str, name: str, replicas: int = 3) -> bool:
        """Create StatefulSet with OOM simulation"""
        try:
            # First, create the headless service required by StatefulSet
            service_name = "oom-simulator"
            selector = {
                "app": "oom-simulator",
                "scenario": "stateful-oom"
            }

            service_created = await self.create_headless_service(namespace, service_name, selector)
            if not service_created:
                self.log_warn(f"Failed to create headless service for StatefulSet {name}", "STATEFULSET")
                return False

            # Create StatefulSet with OOM simulation
            statefulset_body = client.V1StatefulSet(
                metadata=client.V1ObjectMeta(
                    name=name,
                    namespace=namespace,
                    labels={
                        'app': 'oom-simulator',
                        'scenario': 'stateful-oom',
                        'type': 'statefulset',
                        'deployment-test': 'true'
                    }
                ),
                spec=client.V1StatefulSetSpec(
                    service_name=service_name,
                    replicas=replicas,
                    selector=client.V1LabelSelector(
                        match_labels=selector
                    ),
                    template=client.V1PodTemplateSpec(
                        metadata=client.V1ObjectMeta(
                            labels=selector
                        ),
                        spec=client.V1PodSpec(
                            security_context=client.V1PodSecurityContext(
                                run_as_non_root=True,
                                seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault")
                            ),
                            restart_policy="Always",
                            containers=[
                                client.V1Container(
                                    name="simulator",
                                    image="registry.access.redhat.com/ubi9/go-toolset:latest",
                                    resources=client.V1ResourceRequirements(
                                        requests={"memory": "48Mi", "cpu": "100m"},
                                        limits={"memory": "64Mi", "cpu": "500m"}
                                    ),
                                    security_context=client.V1SecurityContext(
                                        allow_privilege_escalation=False,
                                        capabilities=client.V1Capabilities(
                                            drop=["ALL"]
                                        ),
                                        run_as_non_root=True
                                    ),
                                    env=[
                                        client.V1EnvVar(name="GOCACHE", value="/tmp/go-build-cache"),
                                        client.V1EnvVar(name="GOMODCACHE", value="/tmp/go-mod-cache")
                                    ],
                                    command=["/bin/bash", "-c"],
                                    args=["""cat > /tmp/sim.go <<'EOF'
package main
import ("fmt"; "time"; "os")
func main() {
  hostname, _ := os.Hostname()
  fmt.Printf("=== StatefulSet OOM: Pod %s ===\\n", hostname)
  fmt.Println("StatefulSet will recreate pod with SAME name after deletion")
  var allocs [][]byte
  for i := 0; i < 16; i++ {
    chunk := make([]byte, 5*1024*1024)
    for j := 0; j < len(chunk); j += 4096 { chunk[j] = byte(j) }
    allocs = append(allocs, chunk)
    fmt.Printf("Allocated: %d MB\\n", (i+1)*5)
    time.Sleep(1 * time.Second)
  }
  select {}
}
EOF
go run /tmp/sim.go
"""]
                                )
                            ]
                        )
                    )
                )
            )

            await asyncio.to_thread(
                self.apps_v1.create_namespaced_stateful_set,
                namespace=namespace,
                body=statefulset_body
            )

            self.log_info(f"Created StatefulSet {name} with {replicas} replicas in {namespace}", "STATEFULSET")

            # For OOM simulation, we just wait for pods to start (not be ready)
            # since they will crash due to OOM by design
            is_started = await self.wait_for_statefulset_pods_started(namespace, name)
            return is_started

        except ApiException as e:
            if e.status == 409:
                return await self.wait_for_statefulset_pods_started(namespace, name)
            self.log_warn(f"Failed to create StatefulSet {name} in {namespace}: {e}", "STATEFULSET")
            return False

    async def wait_for_statefulset_pods_started(self, namespace: str, statefulset_name: str, timeout: int = 120) -> bool:
        """Wait for StatefulSet pods to be created and started (not necessarily ready)

        For OOM simulation, we don't wait for pods to be ready since they will
        crash by design. We just verify that pods are being created.
        """
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                statefulset = await asyncio.to_thread(
                    self.apps_v1.read_namespaced_stateful_set,
                    name=statefulset_name,
                    namespace=namespace
                )

                spec_replicas = statefulset.spec.replicas or 0

                # Get all pods for this StatefulSet
                pods = await asyncio.to_thread(
                    self.core_v1.list_namespaced_pod,
                    namespace=namespace,
                    label_selector=f"app=oom-simulator,scenario=stateful-oom"
                )

                # Count pods that have been created (any phase)
                created_pods = [
                    pod for pod in pods.items
                    if pod.status.phase in ["Pending", "Running", "Failed", "Unknown"]
                ]

                if len(created_pods) >= spec_replicas and spec_replicas > 0:
                    self.log_info(
                        f"StatefulSet {statefulset_name} pods started ({len(created_pods)}/{spec_replicas}). "
                        f"Pods will OOM by design.",
                        "STATEFULSET"
                    )
                    return True

                await asyncio.sleep(2)

            except ApiException as e:
                if e.status == 404:
                    await asyncio.sleep(2)
                    continue
                self.log_warn(f"Error checking StatefulSet {statefulset_name}: {e}", "STATEFULSET")
                return False
            except Exception as e:
                self.log_warn(f"Unexpected error checking StatefulSet {statefulset_name}: {e}", "STATEFULSET")
                return False

        self.log_warn(f"Timeout waiting for StatefulSet {statefulset_name} pods to start", "STATEFULSET")
        return False

    async def create_build_job(self, namespace: str, name: str) -> bool:
        """Create a Job that simulates CI/CD build workload

        Based on must-gather analysis showing:
        - 20+ concurrent builds in integration namespace
        - Build duration: 2-15 minutes per build
        - Build pattern: compile -> test -> package
        - Each build creates significant process overhead (60-85 processes)
        - Staggered start times (every 1-3 minutes)

        Simulates realistic build workloads similar to Jenkins/Tekton without customer data.
        """
        try:
            # Use batch API for Jobs
            batch_v1 = client.BatchV1Api(self.core_v1.api_client)

            # Realistic build script based on JEE/Maven build patterns observed in must-gather
            # Each build phase consumes CPU/memory similar to real compilation
            build_script = """#!/bin/bash
set -e

echo "================================================================"
echo "Build Job: ${JOB_NAME}"
echo "Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Namespace: ${NAMESPACE}"
echo "Pod: ${POD_NAME}"
echo "================================================================"
echo ""

# Function to simulate CPU-intensive work (like compilation)
cpu_intensive_work() {
    local duration=$1
    local task_name=$2
    echo "[$(date -u +%H:%M:%S)] ${task_name}..."

    # Simulate compilation by doing actual work (creates multiple processes)
    # This mimics Maven/Gradle worker threads
    for i in $(seq 1 $duration); do
        # Create background processes to simulate compiler workers
        (dd if=/dev/zero of=/dev/null bs=1M count=10 2>/dev/null) &
        (echo "Worker $i processing..." | md5sum > /dev/null) &
        sleep 1
    done

    # Wait for background jobs (simulates build synchronization)
    wait
    echo "[$(date -u +%H:%M:%S)] ${task_name} - DONE"
}

# Phase 1: Environment Setup (2-5 seconds)
# Simulates: Maven/Gradle initialization, dependency resolution
echo ""
echo "=== Phase 1: Build Environment Setup ==="
sleep $((RANDOM % 3 + 2))
echo "✓ Environment initialized"
echo "✓ Build tools verified"
echo "✓ Repository cloned"

# Phase 2: Dependency Resolution (5-15 seconds)
# Simulates: Maven dependency download, NPM install, etc.
echo ""
echo "=== Phase 2: Dependency Resolution ==="
dep_time=$((RANDOM % 10 + 5))
cpu_intensive_work $dep_time "Resolving and downloading dependencies"
echo "✓ Dependencies resolved: ${dep_time} packages"

# Phase 3: Compilation (10-30 seconds)
# Simulates: javac, gcc, go build with multiple worker threads
# This is the most resource-intensive phase
echo ""
echo "=== Phase 3: Source Compilation ==="
compile_time=$((RANDOM % 20 + 10))
cpu_intensive_work $compile_time "Compiling source code with $(nproc) workers"
echo "✓ Compilation successful: ${compile_time} source files"

# Phase 4: Unit Tests (5-15 seconds)
# Simulates: JUnit, pytest, go test
echo ""
echo "=== Phase 4: Unit Tests ==="
test_time=$((RANDOM % 10 + 5))
cpu_intensive_work $test_time "Running unit test suite"
echo "✓ Tests passed: ${test_time} test cases"

# Phase 5: Integration Tests (3-10 seconds)
# Simulates: Integration test execution
echo ""
echo "=== Phase 5: Integration Tests ==="
integration_time=$((RANDOM % 7 + 3))
cpu_intensive_work $integration_time "Running integration tests"
echo "✓ Integration tests passed: ${integration_time} tests"

# Phase 6: Packaging (2-8 seconds)
# Simulates: Creating JAR/WAR/container image
echo ""
echo "=== Phase 6: Artifact Packaging ==="
package_time=$((RANDOM % 6 + 2))
sleep $package_time
echo "✓ Artifact packaged: ${JOB_NAME}.jar"
echo "✓ Container image built"

# Phase 7: Publish (2-5 seconds)
# Simulates: Push to artifact repository
echo ""
echo "=== Phase 7: Publishing Artifacts ==="
publish_time=$((RANDOM % 3 + 2))
sleep $publish_time
echo "✓ Published to repository"

# Build Summary
total_time=$((dep_time + compile_time + test_time + integration_time + package_time + publish_time + 10))
echo ""
echo "================================================================"
echo "BUILD SUCCESSFUL"
echo "================================================================"
echo "Total build time: ${total_time} seconds"
echo "Completed at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Artifact: ${JOB_NAME}.jar"
echo "================================================================"
"""

            job_body = client.V1Job(
                metadata=client.V1ObjectMeta(
                    name=name,
                    namespace=namespace,
                    labels={
                        'app': 'build-simulator',
                        'type': 'build-job',
                        'deployment-test': 'true'
                    }
                ),
                spec=client.V1JobSpec(
                    # Based on must-gather analysis:
                    # - 20+ builds per namespace observed
                    # - 3-5 concurrent builds at a time
                    completions=self.config.builds_per_ns,
                    parallelism=self.config.build_parallelism,

                    # Clean up finished pods after 5 minutes (like real CI/CD)
                    ttl_seconds_after_finished=300,

                    # Timeout for the entire job (10 minutes default)
                    # Real builds took 2-15 minutes in must-gather
                    active_deadline_seconds=self.config.build_timeout,

                    # Allow 2 retries for transient failures (realistic)
                    backoff_limit=2,

                    template=client.V1PodTemplateSpec(
                        metadata=client.V1ObjectMeta(
                            labels={
                                'app': 'build-simulator',
                                'type': 'build-job',
                                'job-name': name,
                                'deployment-test': 'true'
                            },
                            annotations={
                                'build.type': 'jee-microservice',
                                'build.tool': 'maven'
                            }
                        ),
                        spec=client.V1PodSpec(
                            restart_policy="Never",

                            # Security context (OpenShift requires non-root)
                            security_context=client.V1PodSecurityContext(
                                run_as_non_root=True,
                                seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault")
                            ),

                            containers=[
                                client.V1Container(
                                    name="builder",
                                    # Use UBI minimal for realistic build environment
                                    # Real builds use Maven/Gradle containers
                                    image="registry.access.redhat.com/ubi9/ubi-minimal:latest",

                                    command=["/bin/bash", "-c"],
                                    args=[build_script],

                                    env=[
                                        client.V1EnvVar(name="JOB_NAME", value=name),
                                        client.V1EnvVar(
                                            name="NAMESPACE",
                                            value_from=client.V1EnvVarSource(
                                                field_ref=client.V1ObjectFieldSelector(
                                                    field_path="metadata.namespace"
                                                )
                                            )
                                        ),
                                        client.V1EnvVar(
                                            name="POD_NAME",
                                            value_from=client.V1EnvVarSource(
                                                field_ref=client.V1ObjectFieldSelector(
                                                    field_path="metadata.name"
                                                )
                                            )
                                        ),
                                        # Build environment variables
                                        client.V1EnvVar(name="MAVEN_OPTS", value="-Xmx400m -Xms200m"),
                                        client.V1EnvVar(name="BUILD_TYPE", value="microservice"),
                                    ],

                                    # Resource requirements (reduced for resource-constrained testing):
                                    # - Each build pod creates 60-85 processes
                                    # - Memory: Minimal for lightweight builds
                                    # - CPU: Minimal to reduce resource pressure
                                    resources=client.V1ResourceRequirements(
                                        requests={"memory": "64Mi", "cpu": "50m"},
                                        limits={"memory": "256Mi", "cpu": "500m"}
                                    ),

                                    security_context=client.V1SecurityContext(
                                        allow_privilege_escalation=False,
                                        capabilities=client.V1Capabilities(drop=["ALL"]),
                                        run_as_non_root=True
                                    )
                                )
                            ]
                        )
                    )
                )
            )

            await asyncio.to_thread(
                batch_v1.create_namespaced_job,
                namespace=namespace,
                body=job_body
            )

            self.log_info(
                f"Created build job {name} in {namespace} "
                f"(completions: {self.config.builds_per_ns}, parallelism: {self.config.build_parallelism})",
                "BUILD_JOB"
            )
            return True

        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create build job {name} in {namespace}: {e}", "BUILD_JOB")
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

            # Create StatefulSets if enabled
            statefulset_success = 0
            statefulset_errors = 0
            if self.config.statefulset_enabled:
                async def create_statefulset_with_semaphore(name: str) -> bool:
                    async with semaphore:
                        return await self.create_statefulset(namespace_name, name, self.config.statefulset_replicas)

                statefulset_tasks = [
                    create_statefulset_with_semaphore(f"stateful-oom-{i+1}")
                    for i in range(self.config.statefulsets_per_ns)
                ]

                statefulset_results = await asyncio.gather(*statefulset_tasks, return_exceptions=True)
                statefulset_success = sum(1 for result in statefulset_results if result is True)
                statefulset_errors = len(statefulset_tasks) - statefulset_success

                self.log_info(f"Created {statefulset_success}/{len(statefulset_tasks)} StatefulSets in {namespace_name}", "STATEFULSET")

            # MODIFIED: Create connectivity test pod only if both curl_test and services are enabled
            if self.config.curl_test_enabled and self.config.service_enabled and success_count > 0:
                # Wait a bit longer to ensure services are fully ready
                self.log_info(f"Waiting 10s before creating connectivity test pod in {namespace_name}", "CURL_TEST")
                await asyncio.sleep(10)

                service_names = [f"deploy-{i+1}-svc" for i in range(self.config.deployments_per_ns)]
                curl_pod_created = await self.create_curl_test_pod(namespace_name, service_names)

                if curl_pod_created:
                    self.log_info(f"Connectivity test pod created in {namespace_name}. Use: kubectl logs -f connectivity-test -n {namespace_name}", "CURL_TEST")
            elif self.config.curl_test_enabled and not self.config.service_enabled:
                self.log_warn(f"Curl test enabled but services disabled in {namespace_name} - skipping curl test pod", "CURL_TEST")

            # Create build jobs if enabled (simulates CI/CD workload)
            build_job_success = 0
            build_job_errors = 0
            if self.config.build_job_enabled:
                # Based on must-gather analysis:
                # - 20+ different microservices being built
                # - Multiple build stages (compile, test, package)
                # - Staggered start times (every 1-3 minutes)
                # - Component naming pattern: {service-type}-{component}-{version}

                # Realistic microservice components observed in builds
                service_types = ['api', 'worker', 'scheduler', 'processor', 'backend']
                components = [
                    'authentication', 'authorization', 'datastore', 'cache',
                    'messaging', 'notification', 'analytics', 'reporting',
                    'search', 'indexer', 'transformer', 'validator',
                    'orchestrator', 'monitor', 'logger', 'metrics'
                ]

                async def create_build_job_with_semaphore(job_name: str) -> bool:
                    async with semaphore:
                        return await self.create_build_job(namespace_name, job_name)

                # Create build jobs with realistic patterns
                build_job_tasks = []
                num_builds = min(len(components), 20)  # Max 20 builds like must-gather

                for i in range(num_builds):
                    service_type = service_types[i % len(service_types)]
                    component = components[i]
                    # Version pattern: major.minor.patch
                    major = (namespace_idx % 3) + 1
                    minor = (i % 50)
                    patch = (namespace_idx + i) % 10
                    version = f"{major}-{minor}-{patch}"

                    # Job name pattern: build-{service-type}-{component}-{version}-s-1-build
                    # The "s-1" suffix simulates build strategy number (observed in must-gather)
                    job_name = f"build-{service_type}-{component}-{version}-s-1"

                    build_job_tasks.append(create_build_job_with_semaphore(job_name))

                    # Stagger build creation (like real CI/CD)
                    # This prevents all builds from starting simultaneously
                    if i > 0 and i % self.config.build_parallelism == 0:
                        await asyncio.sleep(1)  # 1 second delay between batches

                build_job_results = await asyncio.gather(*build_job_tasks, return_exceptions=True)
                build_job_success = sum(1 for result in build_job_results if result is True)
                build_job_errors = len(build_job_tasks) - build_job_success

                self.log_info(
                    f"Created {build_job_success}/{len(build_job_tasks)} build jobs in {namespace_name} "
                    f"(pattern matches must-gather analysis: 20+ builds with {self.config.build_parallelism} parallel)",
                    "BUILD_JOB"
                )

            return {"success": success_count + statefulset_success + build_job_success,
                    "errors": error_count + statefulset_errors + build_job_errors}

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
        description='Simplified Kubernetes Deployment Testing Tool with Connectivity Tests',
        epilog="""
Each namespace gets:
  - N deployments (default: 3)
  - Each deployment has 1 nginx pod
  - Each pod mounts 2 small ConfigMaps and 2 small Secrets
  - [Optional - Disabled by Default] Each deployment gets a ClusterIP service for HTTP access
  - [Optional - Disabled by Default] 1 connectivity test pod that curls all services and logs timing
  - [Enabled by Default] StatefulSets with OOM simulation for pod restart testing

Environment Variables:
  TOTAL_NAMESPACES (default: 100)
  NAMESPACE_PARALLEL (default: 10)
  NAMESPACE_PREFIX (default: deploy-test)
  DEPLOYMENTS_PER_NS (default: 3)
  MAX_CONCURRENT_OPERATIONS (default: 50)
  NAMESPACE_READY_TIMEOUT (default: 60)
  LOG_FILE, LOG_LEVEL
  CLEANUP_ON_COMPLETION (default: false)
  
  Service Configuration:
  SERVICE_ENABLED (default: false) - Enable/disable service creation

  Connectivity Test Configuration:
  CURL_TEST_ENABLED (default: false) - Enable/disable connectivity tests
  CURL_TEST_INTERVAL (default: 30) - Seconds between curl tests
  CURL_TEST_COUNT (default: 10) - Number of curl iterations per pod
  Note: Curl tests require services to be enabled

  StatefulSet OOM Simulation Configuration:
  STATEFULSET_ENABLED (default: true) - Enable/disable StatefulSet creation
  STATEFULSET_REPLICAS (default: 3) - Number of replicas per StatefulSet
  STATEFULSETS_PER_NS (default: 1) - Number of StatefulSets per namespace

  Build Job Simulation Configuration (Based on Must-Gather Analysis):
  BUILD_JOB_ENABLED (default: false) - Enable/disable build job creation
  BUILDS_PER_NS (default: 10) - Number of build completions per job
  BUILD_PARALLELISM (default: 3) - Number of build pods running concurrently
  BUILD_TIMEOUT (default: 900) - Job timeout in seconds (15 minutes)

  Build Job Features (Simulates Real CI/CD Patterns):
  - Creates 20 unique build jobs per namespace (like observed 20+ builds)
  - Each job runs multiple completions with controlled parallelism
  - Realistic build phases: setup → dependencies → compile → test → package → publish
  - CPU-intensive work simulating actual compilation (creates background processes)
  - Process overhead: 60-85 processes per build pod (matches must-gather)
  - Build duration: 1-5 minutes (randomized, realistic)
  - Staggered start times to prevent simultaneous launches
  - Resource requests: 256Mi RAM, 200m CPU (realistic for JEE builds)
  - Resource limits: 768Mi RAM, 1000m CPU (allows burst during compilation)
  - Automatic cleanup after 5 minutes (TTL)
  - Job naming pattern: build-{service}-{component}-{version}-s-1

  SSL/TLS Configuration:
  K8S_VERIFY, OCP_API_VERIFY (default: auto-detect) - Set to 'true' to force SSL verification
  K8S_CA_CERT - Path to CA certificate file
  KUBECONFIG - Path to kubeconfig file
  
  Note: The tool automatically handles SSL errors by disabling verification
        unless explicitly forced via K8S_VERIFY=true

Examples:
  %(prog)s --total-namespaces 50 --deployments-per-ns 5
  %(prog)s --cleanup --namespace-parallel 20
  %(prog)s --verify-ssl  # Force SSL verification
  %(prog)s --curl-interval 60 --curl-count 5  # Custom curl test settings

  # Enable service creation (disabled by default)
  %(prog)s --service-enabled
  SERVICE_ENABLED=true %(prog)s

  # Enable connectivity tests (disabled by default, requires services)
  %(prog)s --service-enabled --curl-test-enabled
  SERVICE_ENABLED=true CURL_TEST_ENABLED=true %(prog)s

  # StatefulSet with OOM simulation (enabled by default)
  %(prog)s --statefulset-replicas 5 --statefulsets-per-ns 2

  # Disable StatefulSet creation
  %(prog)s --no-statefulset
  STATEFULSET_ENABLED=false %(prog)s

  # View connectivity test logs
  kubectl logs -f connectivity-test -n deploy-test-1

  # View OOM StatefulSet logs
  kubectl logs -f stateful-oom-1-0 -n deploy-test-1

  # Build job simulation (disabled by default)
  %(prog)s --build-job-enabled --builds-per-ns 10 --build-parallelism 5
  BUILD_JOB_ENABLED=true BUILDS_PER_NS=10 %(prog)s

  # View build job logs
  kubectl logs -f build-service-api-v1-0-0-<pod-suffix> -n deploy-test-1
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
    parser.add_argument('--verify-ssl', action='store_true',
                       help='Force SSL certificate verification')
    
    # Service configuration
    parser.add_argument('--service-enabled', action='store_true', default=None,
                       help='Enable service creation (default: false)')
    parser.add_argument('--no-service', action='store_true',
                       help='Disable service creation')

    # Curl test configuration
    parser.add_argument('--curl-test-enabled', action='store_true', default=None,
                       help='Enable connectivity tests (default: false)')
    parser.add_argument('--no-curl-test', action='store_true',
                       help='Disable connectivity tests')
    parser.add_argument('--curl-interval', type=int,
                       help='Seconds between curl tests')
    parser.add_argument('--curl-count', type=int,
                       help='Number of curl test iterations')

    # StatefulSet OOM simulation configuration
    parser.add_argument('--statefulset-enabled', action='store_true', default=None,
                       help='Enable StatefulSet creation with OOM simulation (default: true)')
    parser.add_argument('--no-statefulset', action='store_true',
                       help='Disable StatefulSet creation')
    parser.add_argument('--statefulset-replicas', type=int,
                       help='Number of replicas per StatefulSet')
    parser.add_argument('--statefulsets-per-ns', type=int,
                       help='Number of StatefulSets per namespace')

    # Build job configuration
    parser.add_argument('--build-job-enabled', action='store_true', default=None,
                       help='Enable build job creation (default: false)')
    parser.add_argument('--no-build-job', action='store_true',
                       help='Disable build job creation')
    parser.add_argument('--builds-per-ns', type=int,
                       help='Number of build completions per job')
    parser.add_argument('--build-parallelism', type=int,
                       help='Number of concurrent build pods')
    parser.add_argument('--build-timeout', type=int,
                       help='Build job timeout in seconds')

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
    if args.verify_ssl:
        config.k8s_verify_ssl = True
    
    # Service configuration
    if args.service_enabled is not None:
        config.service_enabled = True
    if args.no_service:
        config.service_enabled = False
    
    # Curl test configuration
    if args.curl_test_enabled is not None:
        config.curl_test_enabled = True
    if args.no_curl_test:
        config.curl_test_enabled = False
    if args.curl_interval is not None:
        config.curl_test_interval = args.curl_interval
    if args.curl_count is not None:
        config.curl_test_count = args.curl_count

    # StatefulSet configuration
    if args.statefulset_enabled is not None:
        config.statefulset_enabled = True
    if args.no_statefulset:
        config.statefulset_enabled = False
    if args.statefulset_replicas is not None:
        config.statefulset_replicas = args.statefulset_replicas
    if args.statefulsets_per_ns is not None:
        config.statefulsets_per_ns = args.statefulsets_per_ns

    # Build job configuration
    if args.build_job_enabled is not None:
        config.build_job_enabled = True
    if args.no_build_job:
        config.build_job_enabled = False
    if args.builds_per_ns is not None:
        config.builds_per_ns = args.builds_per_ns
    if args.build_parallelism is not None:
        config.build_parallelism = args.build_parallelism
    if args.build_timeout is not None:
        config.build_timeout = args.build_timeout

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