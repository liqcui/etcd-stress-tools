#!/usr/bin/env python3
"""
Optimized OpenShift etcd Stress Testing Tool
High-performance async Kubernetes client for comprehensive resource creation and testing
"""

import argparse
import asyncio
import base64
import logging
import os
import random
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Dict, Any, Tuple
import json

# Kubernetes imports
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# For certificate generation
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime as dt
import urllib3

# Suppress warnings for unverified HTTPS requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Colors:
    """ANSI color codes for terminal output"""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    PURPLE = '\033[0;35m'
    NC = '\033[0m'  # No Color


class Config:
    """Configuration class with comprehensive defaults"""
    def __init__(self):
        # Core namespace configuration
        self.total_namespaces = int(os.getenv('TOTAL_NAMESPACES', '100'))
        self.namespace_parallel = int(os.getenv('NAMESPACE_PARALLEL', '10'))
        self.namespace_prefix = os.getenv('NAMESPACE_PREFIX', 'stress-test')
        
        # ConfigMap and Secret counts (fixed as per requirements)
        self.small_configmaps_per_ns = 10
        self.large_configmaps_per_ns = 3
        self.large_configmap_size_mb = 1.0
        self.total_large_configmap_limit_gb = float(os.getenv('TOTAL_LARGE_CONFIGMAP_LIMIT_GB', '6'))
        
        self.small_secrets_per_ns = 10
        self.large_secrets_per_ns = 10
        
        # Deployment configuration (optional)
        self.create_deployments = os.getenv('CREATE_DEPLOYMENTS', 'true').lower() == 'true'
        self.deployments_per_ns = 3
        
        # Network policy configuration
        self.create_egress_firewall = os.getenv('CREATE_EGRESS_FIREWALL', 'true').lower() == 'true'
        self.create_network_policies = os.getenv('CREATE_NETWORK_POLICIES', 'true').lower() == 'true'
        self.create_anp_banp = os.getenv('CREATE_ANP_BANP', 'true').lower() == 'true'
        self.create_images = os.getenv('CREATE_IMAGES', 'true').lower() == 'true'
        
        # Performance tuning
        self.max_concurrent_operations = int(os.getenv('MAX_CONCURRENT_OPERATIONS', '50'))
        
        # Timeouts and retries
        self.namespace_ready_timeout = int(os.getenv('NAMESPACE_READY_TIMEOUT', '60'))
        self.resource_retry_count = int(os.getenv('RESOURCE_RETRY_COUNT', '3'))
        self.resource_retry_delay = float(os.getenv('RESOURCE_RETRY_DELAY', '1.0'))
        
        # Logging
        self.log_file = os.getenv('LOG_FILE', 'etcd_stress_test.log')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')
        
        # Cleanup
        self.cleanup_on_completion = os.getenv('CLEANUP_ON_COMPLETION', 'false').lower() == 'true'


class etcdStressTools:
    """Optimized etcd stress testing tool with modular design"""
    
    def __init__(self, config: Config):
        self.config = config
        self.setup_logging()
        self.setup_kubernetes_clients()
        self.tenant_labels = ['tenant1', 'tenant2', 'tenant3']
        
    def setup_logging(self):
        """Setup comprehensive logging configuration"""
        log_level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s',
            handlers=[
                logging.FileHandler(self.config.log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def setup_kubernetes_clients(self):
        """Setup Kubernetes client configuration with error handling"""
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
                
        # Create API clients
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.networking_v1 = client.NetworkingV1Api()
        self.custom_objects = client.CustomObjectsApi()
        
    def log_info(self, message: str, component: str = "MAIN"):
        """Enhanced logging with component identification"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"{Colors.GREEN}[{component}]{Colors.NC} {timestamp} - {message}")
        self.logger.info(f"[{component}] {message}")
        
    def log_warn(self, message: str, component: str = "MAIN"):
        """Enhanced warning logging"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"{Colors.YELLOW}[{component}]{Colors.NC} {timestamp} - {message}")
        self.logger.warning(f"[{component}] {message}")
        
    def log_error(self, message: str, component: str = "MAIN"):
        """Enhanced error logging"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"{Colors.RED}[{component}]{Colors.NC} {timestamp} - {message}")
        self.logger.error(f"[{component}] {message}")

    async def ensure_namespace(self, namespace_name: str, labels: Dict[str, str] = None) -> bool:
        """Create namespace with custom labels and wait for it to be ready"""
        try:
            # Check if namespace exists
            try:
                await asyncio.to_thread(
                    self.core_v1.read_namespace,
                    name=namespace_name
                )
                self.log_info(f"Namespace {namespace_name} already exists", "NAMESPACE")
                return await self.wait_until_namespace_ready(namespace_name)
            except ApiException as e:
                if e.status != 404:  # Some other error
                    self.log_error(f"Error checking namespace {namespace_name}: {e}", "NAMESPACE")
                    return False

            # Create namespace
            namespace_labels = labels or {}
            namespace_labels.update({
                'stress-test': 'true',
                'created-by': 'etcd-stress-tool'
            })
            
            namespace_body = client.V1Namespace(
                metadata=client.V1ObjectMeta(
                    name=namespace_name,
                    labels=namespace_labels
                )
            )
            
            await asyncio.to_thread(
                self.core_v1.create_namespace,
                body=namespace_body
            )
            
            self.log_info(f"Created namespace {namespace_name}", "NAMESPACE")
            
            # Wait for namespace to be ready
            return await self.wait_until_namespace_ready(namespace_name)
            
        except ApiException as create_e:
            if create_e.status == 409:  # Already exists
                return await self.wait_until_namespace_ready(namespace_name)
            self.log_error(f"Failed to create namespace {namespace_name}: {create_e}", "NAMESPACE")
            return False
        except Exception as e:
            self.log_error(f"Unexpected error creating namespace {namespace_name}: {e}", "NAMESPACE")
            return False

    async def wait_until_namespace_ready(self, namespace_name: str) -> bool:
        """Wait until the namespace is fully ready for resource creation"""
        deadline = time.time() + self.config.namespace_ready_timeout
        
        while time.time() < deadline:
            try:
                ns = await asyncio.to_thread(self.core_v1.read_namespace, name=namespace_name)
                
                # Check namespace phase
                phase = getattr(getattr(ns, 'status', None), 'phase', None)
                if phase != 'Active':
                    await asyncio.sleep(0.5)
                    continue
                
                # Additional check: try to create a dummy resource to verify namespace is ready
                try:
                    test_cm_name = f"test-readiness-{int(time.time())}"
                    test_cm = client.V1ConfigMap(
                        metadata=client.V1ObjectMeta(
                            name=test_cm_name,
                            namespace=namespace_name
                        ),
                        data={"test": "readiness"}
                    )
                    
                    await asyncio.to_thread(
                        self.core_v1.create_namespaced_config_map,
                        namespace=namespace_name,
                        body=test_cm
                    )
                    
                    # Clean up test resource
                    try:
                        await asyncio.to_thread(
                            self.core_v1.delete_namespaced_config_map,
                            name=test_cm_name,
                            namespace=namespace_name
                        )
                    except:
                        pass  # Ignore cleanup errors
                    
                    self.log_info(f"Namespace {namespace_name} is ready for resource creation", "NAMESPACE")
                    return True
                    
                except ApiException as test_e:
                    if test_e.status == 404:  # Namespace still not ready
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        # Other API errors during test - namespace might be ready
                        self.log_warn(f"Test resource creation failed in {namespace_name}, but namespace appears ready: {test_e}", "NAMESPACE")
                        return True
                        
            except ApiException as e:
                if e.status == 404:
                    await asyncio.sleep(0.5)
                    continue
                else:
                    self.log_warn(f"Error while waiting for namespace {namespace_name}: {e}", "NAMESPACE")
                    return False
            except Exception as e:
                self.log_warn(f"Unexpected error while waiting for namespace {namespace_name}: {e}", "NAMESPACE")
                return False
        
        self.log_warn(f"Timeout waiting for namespace {namespace_name} to be ready", "NAMESPACE")
        return False

    async def retry_resource_operation(self, operation, *args, **kwargs):
        """Retry a resource operation with exponential backoff"""
        last_exception = None
        
        for attempt in range(self.config.resource_retry_count + 1):
            try:
                return await operation(*args, **kwargs)
            except ApiException as e:
                last_exception = e
                if e.status == 404 and "not found" in str(e).lower():
                    # Namespace not found - wait and retry
                    if attempt < self.config.resource_retry_count:
                        wait_time = self.config.resource_retry_delay * (2 ** attempt)
                        await asyncio.sleep(wait_time)
                        continue
                elif e.status == 409:  # Already exists
                    return True
                else:
                    # Other errors - don't retry
                    break
            except Exception as e:
                last_exception = e
                if attempt < self.config.resource_retry_count:
                    wait_time = self.config.resource_retry_delay * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                    continue
                break
        
        # All retries exhausted
        raise last_exception

    # 2.1 Create 10 small configmaps in parallel
    async def create_small_configmaps(self, namespace: str) -> bool:
        """Create 10 small ConfigMaps in namespace in parallel"""
        async def create_small_configmap(name: str) -> bool:
            try:
                data = {
                    f"config-{i}": f"small-config-value-{i}-{secrets.token_hex(8)}"
                    for i in range(5)
                }
                
                configmap_body = client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(
                        name=name, 
                        namespace=namespace,
                        labels={'type': 'small-configmap', 'stress-test': 'true'}
                    ),
                    data=data
                )
                
                await self.retry_resource_operation(
                    asyncio.to_thread,
                    self.core_v1.create_namespaced_config_map,
                    namespace=namespace,
                    body=configmap_body
                )
                return True
            except ApiException as e:
                if e.status == 409:  # Already exists
                    return True
                self.log_warn(f"Failed to create small ConfigMap {name} in {namespace}: {e}", "CONFIGMAP")
                return False

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.config.max_concurrent_operations)
        
        async def create_with_semaphore(name: str) -> bool:
            async with semaphore:
                return await create_small_configmap(name)

        # Create 10 small configmaps in parallel
        tasks = [
            create_with_semaphore(f"small-cm-{i+1}") 
            for i in range(self.config.small_configmaps_per_ns)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for result in results if result is True)
        return success_count == len(tasks)

    # 2.2 Create 3 large configmaps with 1MB each
    async def create_large_configmap(self, namespace: str) -> bool:
        """Create 3 large ConfigMaps with 1MB size each in parallel"""
        async def create_single_large_configmap(name: str) -> bool:
            try:
                # Generate 1MB of data
                size_bytes = int(self.config.large_configmap_size_mb * 1024 * 1024)
                # Reserve space for keys and metadata
                data_size = size_bytes - 1024
                
                data = {
                    "large-data": secrets.token_hex(data_size // 2)
                }
                
                configmap_body = client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(
                        name=name,
                        namespace=namespace,
                        labels={'type': 'large-configmap', 'stress-test': 'true'}
                    ),
                    data=data
                )
                
                await self.retry_resource_operation(
                    asyncio.to_thread,
                    self.core_v1.create_namespaced_config_map,
                    namespace=namespace,
                    body=configmap_body
                )
                return True
            except ApiException as e:
                if e.status == 409:
                    return True
                self.log_warn(f"Failed to create large ConfigMap {name} in {namespace}: {e}", "CONFIGMAP")
                return False

        # Calculate if we exceed the total limit
        total_large_configmaps = self.config.total_namespaces * self.config.large_configmaps_per_ns
        total_size_gb = total_large_configmaps * self.config.large_configmap_size_mb / 1024
        
        if total_size_gb > self.config.total_large_configmap_limit_gb:
            self.log_warn(f"Skipping large ConfigMap creation - would exceed {self.config.total_large_configmap_limit_gb}GB limit", "CONFIGMAP")
            return False

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.config.max_concurrent_operations)
        
        async def create_with_semaphore(name: str) -> bool:
            async with semaphore:
                return await create_single_large_configmap(name)

        # Create 3 large configmaps in parallel
        tasks = [
            create_with_semaphore(f"large-cm-{i+1}") 
            for i in range(self.config.large_configmaps_per_ns)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for result in results if result is True)
        return success_count == len(tasks)

    # 2.3 Create 10 small secrets in parallel
    async def create_small_secrets(self, namespace: str) -> bool:
        """Create 10 small Secrets in namespace in parallel"""
        async def create_small_secret(name: str) -> bool:
            try:
                data = {
                    "username": base64.b64encode(f"user-{secrets.token_hex(4)}".encode()).decode(),
                    "password": base64.b64encode(secrets.token_bytes(16)).decode(),
                    "token": base64.b64encode(secrets.token_bytes(32)).decode()
                }
                
                secret_body = client.V1Secret(
                    metadata=client.V1ObjectMeta(
                        name=name, 
                        namespace=namespace,
                        labels={'type': 'small-secret', 'stress-test': 'true'}
                    ),
                    type="Opaque",
                    data=data
                )
                
                await self.retry_resource_operation(
                    asyncio.to_thread,
                    self.core_v1.create_namespaced_secret,
                    namespace=namespace,
                    body=secret_body
                )
                return True
            except ApiException as e:
                if e.status == 409:
                    return True
                self.log_warn(f"Failed to create small Secret {name} in {namespace}: {e}", "SECRET")
                return False

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.config.max_concurrent_operations)
        
        async def create_with_semaphore(name: str) -> bool:
            async with semaphore:
                return await create_small_secret(name)

        # Create 10 small secrets in parallel
        tasks = [
            create_with_semaphore(f"small-secret-{i+1}") 
            for i in range(self.config.small_secrets_per_ns)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for result in results if result is True)
        return success_count == len(tasks)

    # 2.4 Create 10 large secrets in parallel
    async def create_large_secrets(self, namespace: str) -> bool:
        """Create 10 large Secrets in namespace in parallel"""
        async def create_large_secret(name: str) -> bool:
            try:
                # Generate certificate and key data
                private_key, public_key = await self.generate_keypair()
                certificate = await self.generate_certificate(private_key)
                
                data = {
                    "tls.crt": certificate,
                    "tls.key": private_key,
                    "ca.crt": certificate,
                    "ssh-public-key": public_key,
                    "large-token": base64.b64encode(secrets.token_bytes(1024)).decode()
                }
                
                secret_body = client.V1Secret(
                    metadata=client.V1ObjectMeta(
                        name=name, 
                        namespace=namespace,
                        labels={'type': 'large-secret', 'stress-test': 'true'}
                    ),
                    type="kubernetes.io/tls",
                    data=data
                )
                
                await self.retry_resource_operation(
                    asyncio.to_thread,
                    self.core_v1.create_namespaced_secret,
                    namespace=namespace,
                    body=secret_body
                )
                return True
            except ApiException as e:
                if e.status == 409:
                    return True
                self.log_warn(f"Failed to create large Secret {name} in {namespace}: {e}", "SECRET")
                return False

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.config.max_concurrent_operations)
        
        async def create_with_semaphore(name: str) -> bool:
            async with semaphore:
                return await create_large_secret(name)

        # Create 10 large secrets in parallel
        tasks = [
            create_with_semaphore(f"large-secret-{i+1}") 
            for i in range(self.config.large_secrets_per_ns)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for result in results if result is True)
        return success_count == len(tasks)

    async def generate_keypair(self) -> Tuple[str, str]:
        """Generate RSA keypair asynchronously"""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            future = loop.run_in_executor(executor, self._generate_keypair_sync)
            return await future

    def _generate_keypair_sync(self) -> Tuple[str, str]:
        """Synchronous keypair generation"""
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        public_key = private_key.public_key()
        public_ssh = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH
        )
        
        return (
            base64.b64encode(private_pem).decode('utf-8'),
            base64.b64encode(public_ssh).decode('utf-8')
        )

    async def generate_certificate(self, private_key_b64: str) -> str:
        """Generate self-signed certificate"""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            future = loop.run_in_executor(executor, self._generate_certificate_sync, private_key_b64)
            return await future

    def _generate_certificate_sync(self, private_key_b64: str) -> str:
        """Synchronous certificate generation"""
        # Decode private key
        private_key_pem = base64.b64decode(private_key_b64)
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "stress-test.local"),
        ])
        
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            dt.datetime.now(dt.timezone.utc)
        ).not_valid_after(
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)
        ).sign(private_key, hashes.SHA256())
        
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        return base64.b64encode(cert_pem).decode('utf-8')

    # 2.5 Create 5 deployments with mounted configmaps and secrets
    async def create_deployments(self, namespace: str) -> bool:
        """Create 5 deployments with mounted ConfigMaps and Secrets, and wait for pods to be ready"""
        async def create_deployment(name: str) -> bool:
            try:
                # Create volume mounts for ConfigMaps and Secrets
                volumes = []
                volume_mounts = []
                
                # Mount 5 small configmaps
                for i in range(5):
                    cm_name = f"small-cm-{i+1}"
                    volume_name = f"small-cm-{i}"
                    volumes.append(client.V1Volume(
                        name=volume_name,
                        config_map=client.V1ConfigMapVolumeSource(name=cm_name)
                    ))
                    volume_mounts.append(client.V1VolumeMount(
                        name=volume_name,
                        mount_path=f"/config/small-cm/{i}"
                    ))
                
                # Mount 1 large configmap
                volumes.append(client.V1Volume(
                    name="large-cm-0",
                    config_map=client.V1ConfigMapVolumeSource(name="large-cm-1")
                ))
                volume_mounts.append(client.V1VolumeMount(
                    name="large-cm-0",
                    mount_path="/config/large-cm/0"
                ))
                
                # Mount 5 small secrets
                for i in range(5):
                    secret_name = f"small-secret-{i+1}"
                    volume_name = f"small-secret-{i}"
                    volumes.append(client.V1Volume(
                        name=volume_name,
                        secret=client.V1SecretVolumeSource(secret_name=secret_name)
                    ))
                    volume_mounts.append(client.V1VolumeMount(
                        name=volume_name,
                        mount_path=f"/secrets/small/{i}"
                    ))
                
                # Mount 5 large secrets
                for i in range(5):
                    secret_name = f"large-secret-{i+1}"
                    volume_name = f"large-secret-{i}"
                    volumes.append(client.V1Volume(
                        name=volume_name,
                        secret=client.V1SecretVolumeSource(secret_name=secret_name)
                    ))
                    volume_mounts.append(client.V1VolumeMount(
                        name=volume_name,
                        mount_path=f"/secrets/large/{i}"
                    ))
                
                # Create deployment spec
                deployment_body = client.V1Deployment(
                    metadata=client.V1ObjectMeta(
                        name=name,
                        namespace=namespace,
                        labels={'app': name, 'stress-test': 'true'}
                    ),
                    spec=client.V1DeploymentSpec(
                        replicas=1,
                        selector=client.V1LabelSelector(
                            match_labels={'app': name}
                        ),
                        template=client.V1PodTemplateSpec(
                            metadata=client.V1ObjectMeta(
                                labels={'app': name, 'stress-test': 'true'}
                            ),
                            spec=client.V1PodSpec(
                                containers=[
                                    client.V1Container(
                                        name="stress-container",
                                        image="registry.access.redhat.com/ubi8/ubi-minimal:latest",
                                        command=["/bin/sleep", "infinity"],
                                        volume_mounts=volume_mounts,
                                        resources=client.V1ResourceRequirements(
                                            requests={"memory": "64Mi", "cpu": "50m"},
                                            limits={"memory": "128Mi", "cpu": "100m"}
                                        )
                                    )
                                ],
                                volumes=volumes
                            )
                        )
                    )
                )
                
                await self.retry_resource_operation(
                    asyncio.to_thread,
                    self.apps_v1.create_namespaced_deployment,
                    namespace=namespace,
                    body=deployment_body
                )
                
                # Wait for deployment pods to be ready
                is_ready = await self.wait_for_deployment_ready(namespace, name)
                if not is_ready:
                    self.log_warn(f"Deployment {name} created but pods not ready in time", "DEPLOYMENT")
                
                return is_ready
                
            except ApiException as e:
                if e.status == 409:
                    # Already exists, check if ready
                    return await self.wait_for_deployment_ready(namespace, name)
                self.log_warn(f"Failed to create Deployment {name} in {namespace}: {e}", "DEPLOYMENT")
                return False

        if not self.config.create_deployments:
            return True

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.config.max_concurrent_operations)
        
        async def create_with_semaphore(name: str) -> bool:
            async with semaphore:
                return await create_deployment(name)

        # Create 5 deployments in parallel
        tasks = [
            create_with_semaphore(f"stress-deployment-{i+1}") 
            for i in range(self.config.deployments_per_ns)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for result in results if result is True)
        
        self.log_info(f"Created {success_count}/{len(tasks)} deployments with ready pods in {namespace}", "DEPLOYMENT")
        return success_count == len(tasks)

    async def wait_for_deployment_ready(self, namespace: str, deployment_name: str, timeout: int = 300) -> bool:
        """Wait for deployment pods to be ready"""
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                # Get deployment status
                deployment = await asyncio.to_thread(
                    self.apps_v1.read_namespaced_deployment,
                    name=deployment_name,
                    namespace=namespace
                )
                
                # Check if deployment has desired replicas
                spec_replicas = deployment.spec.replicas or 0
                status = deployment.status
                
                ready_replicas = status.ready_replicas or 0
                available_replicas = status.available_replicas or 0
                
                # Check if all replicas are ready and available
                if (ready_replicas == spec_replicas and 
                    available_replicas == spec_replicas and 
                    spec_replicas > 0):
                    
                    # Double-check pods are actually running
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
                        self.log_info(f"Deployment {deployment_name} is ready with {spec_replicas} pods running", "DEPLOYMENT")
                        return True
                
                await asyncio.sleep(2)
                
            except ApiException as e:
                if e.status == 404:
                    await asyncio.sleep(2)
                    continue
                self.log_warn(f"Error checking deployment {deployment_name} status: {e}", "DEPLOYMENT")
                return False
            except Exception as e:
                self.log_warn(f"Unexpected error checking deployment {deployment_name}: {e}", "DEPLOYMENT")
                return False
        
        self.log_warn(f"Timeout waiting for deployment {deployment_name} to be ready", "DEPLOYMENT")
        return False

    # 2.6 Create EgressFirewall policy
    async def create_egress_firewall(self, namespace: str) -> bool:
        """Create EgressFirewall policy in namespace"""
        try:
            egress_firewall_body = {
                "apiVersion": "k8s.ovn.org/v1",
                "kind": "EgressFirewall",
                "metadata": {
                    "name": "default",
                    "namespace": namespace,
                    "labels": {"stress-test": "true"}
                },
                "spec": {
                    "egress": [
                        {"type": "Allow", "to": {"cidrSelector": "8.8.8.8/32"}},
                        {"type": "Deny", "to": {"cidrSelector": "8.8.4.4/32"}},
                        {"type": "Allow", "to": {"cidrSelector": "114.114.114.114/32"}},
                        {"type": "Deny", "to": {"cidrSelector": "1.1.1.1/32"}},
                        {"type": "Deny", "to": {"cidrSelector": "2.2.2.2/32"}},
                        {"type": "Allow", "to": {"dnsName": "www.googlex.com"}},
                        {"type": "Allow", "to": {"dnsName": "updates.jenkins.io"}},
                        {"type": "Deny", "to": {"dnsName": "www.digitalxocean.com"}},
                        {"type": "Allow", "to": {"dnsName": "www.xxxx.com"}},
                        {"type": "Deny", "to": {"dnsName": "www.xxyyyyxx.com"}}
                    ]
                }
            }
            
            await self.retry_resource_operation(
                asyncio.to_thread,
                self.custom_objects.create_namespaced_custom_object,
                group="k8s.ovn.org",
                version="v1",
                namespace=namespace,
                plural="egressfirewalls",
                body=egress_firewall_body
            )
            return True
        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create EgressFirewall in {namespace}: {e}", "EGRESSFW")
            return False

    # 2.7 Create 2 NetworkPolicy objects
    async def create_network_policies(self, namespace: str) -> bool:
        """Create 2 NetworkPolicy objects in namespace"""
        try:
            # Policy 1: Deny by default
            deny_policy = client.V1NetworkPolicy(
                metadata=client.V1ObjectMeta(
                    name="deny-by-default",
                    namespace=namespace,
                    labels={"stress-test": "true"}
                ),
                spec=client.V1NetworkPolicySpec(
                    pod_selector=client.V1LabelSelector(),
                    ingress=[],
                    policy_types=["Ingress", "Egress"]
                )
            )
            
            # Policy 2: Large network policy with complex rules
            large_policy = client.V1NetworkPolicy(
                metadata=client.V1ObjectMeta(
                    name="large-networkpolicy-egress-allow-dns",
                    namespace=namespace,
                    labels={"stress-test": "true"}
                ),
                spec=client.V1NetworkPolicySpec(
                    pod_selector=client.V1LabelSelector(),
                    policy_types=["Ingress", "Egress"],
                    ingress=[
                        client.V1NetworkPolicyIngressRule(
                            _from=[
                                client.V1NetworkPolicyPeer(
                                    ip_block=client.V1IPBlock(cidr="10.128.0.0/16")
                                )
                            ]
                        ),
                        client.V1NetworkPolicyIngressRule(
                            _from=[
                                client.V1NetworkPolicyPeer(
                                    ip_block=client.V1IPBlock(cidr="172.30.0.0/16")
                                )
                            ]
                        )
                    ],
                    egress=[
                        client.V1NetworkPolicyEgressRule(
                            to=[
                                client.V1NetworkPolicyPeer(
                                    ip_block=client.V1IPBlock(cidr="10.128.0.0/14")
                                ),
                                client.V1NetworkPolicyPeer(
                                    ip_block=client.V1IPBlock(cidr="172.30.0.0/16")
                                ),
                                client.V1NetworkPolicyPeer(
                                    ip_block=client.V1IPBlock(cidr="8.8.8.8/32")
                                ),
                                client.V1NetworkPolicyPeer(
                                    ip_block=client.V1IPBlock(cidr="8.8.8.4/32")
                                ),
                                client.V1NetworkPolicyPeer(
                                    ip_block=client.V1IPBlock(cidr="142.0.0.0/8")
                                ),
                                client.V1NetworkPolicyPeer(
                                    ip_block=client.V1IPBlock(cidr="104.18.0.0/16")
                                )
                            ]
                        ),
                        client.V1NetworkPolicyEgressRule(
                            to=[
                                client.V1NetworkPolicyPeer(
                                    namespace_selector=client.V1LabelSelector(
                                        match_labels={"kubernetes.io/metadata.name": "openshift-dns"}
                                    ),
                                    pod_selector=client.V1LabelSelector(
                                        match_labels={"dns.operator.openshift.io/daemonset-dns": "default"}
                                    )
                                )
                            ]
                        )
                    ]
                )
            )
            
            # Create both policies with retry
            await self.retry_resource_operation(
                asyncio.to_thread,
                self.networking_v1.create_namespaced_network_policy,
                namespace=namespace,
                body=deny_policy
            )
            
            await self.retry_resource_operation(
                asyncio.to_thread,
                self.networking_v1.create_namespaced_network_policy,
                namespace=namespace,
                body=large_policy
            )
            
            return True
        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create NetworkPolicies in {namespace}: {e}", "NETPOL")
            return False

    def generate_fake_ips(self, count: int) -> List[str]:
        """Generate fake IP addresses for testing"""
        ips = []
        for _ in range(count):
            # Generate random private IP addresses
            octets = [
                random.choice([10, 172, 192]),
                random.randint(1, 255),
                random.randint(1, 255),
                random.randint(1, 255)
            ]
            ips.append(f"{'.'.join(map(str, octets))}/32")
        return ips

    # 4. Create cluster-scope BANP and ANP
    async def create_baseline_admin_network_policy(self) -> bool:
        """Create BaselineAdminNetworkPolicy (BANP)"""
        try:
            banp_body = {
                "apiVersion": "policy.networking.k8s.io/v1alpha1",
                "kind": "BaselineAdminNetworkPolicy",
                "metadata": {
                    "name": "default",
                    "labels": {"stress-test": "true"}
                },
                "spec": {
                    "subject": {
                        "namespaces": {
                            "matchExpressions": [
                                {
                                    "key": "anplabel",
                                    "operator": "In",
                                    "values": ["anp-tenant"]
                                }
                            ]
                        }
                    },
                    "ingress": [
                        {
                            "name": "deny-all-ingress-from-any-ns",
                            "action": "Deny",
                            "from": [
                                {
                                    "namespaces": {
                                        "namespaceSelector": {}
                                    }
                                }
                            ]
                        }
                    ],
                    "egress": [
                        {
                            "name": "egress-deny-all-traffic-to-any-network",
                            "action": "Deny",
                            "to": [
                                {
                                    "networks": ["0.0.0.0/0"]
                                }
                            ]
                        },
                        {
                            "action": "Deny",
                            "name": "egress-deny-all-traffic-to-any-node",
                            "to": [
                                {
                                    "nodes": {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/hostname",
                                                "operator": "Exists"
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ]
                }
            }
            
            await asyncio.to_thread(
                self.custom_objects.create_cluster_custom_object,
                group="policy.networking.k8s.io",
                version="v1alpha1",
                plural="baselineadminnetworkpolicies",
                body=banp_body
            )
            return True
        except ApiException as e:
            if e.status == 409:
                return True
            self.log_warn(f"Failed to create BaselineAdminNetworkPolicy: {e}", "BANP")
            return False

    async def create_admin_network_policies(self) -> bool:
        """Create AdminNetworkPolicy (ANP) objects"""
        try:
            anp_count = max(1, self.config.total_namespaces // 3)
            fake_ips = self.generate_fake_ips(10)
            
            tasks = []
            for i in range(anp_count):
                tenant = self.tenant_labels[i % len(self.tenant_labels)]
                anp_name = f"allow-traffic-anp-cidr-to-openshift-monitoring-network-{tenant}-p{i+1}"
                
                anp_body = {
                    "apiVersion": "policy.networking.k8s.io/v1alpha1",
                    "kind": "AdminNetworkPolicy",
                    "metadata": {
                        "name": anp_name,
                        "labels": {"stress-test": "true"}
                    },
                    "spec": {
                        "priority": i + 1,
                        "subject": {
                            "namespaces": {
                                "matchLabels": {
                                    "customer_tenant": tenant
                                }
                            }
                        },
                        "ingress": [
                            {
                                "name": "all-ingress-from-same-tenant",
                                "action": "Allow",
                                "from": [
                                    {
                                        "namespaces": {
                                            "matchLabels": {
                                                "customer_tenant": tenant
                                            }
                                        }
                                    }
                                ]
                            }
                        ],
                        "egress": [
                            {
                                "name": "pass-egress-to-cluster-network",
                                "action": "Pass",
                                "ports": [
                                    {"portNumber": {"port": 9093, "protocol": "TCP"}},
                                    {"portNumber": {"port": 9094, "protocol": "TCP"}}
                                ],
                                "to": [
                                    {"networks": ["10.128.0.0/14"]}
                                ]
                            },
                            {
                                "name": "allow-egress-to-openshift-monitoring-network-1",
                                "action": "Allow",
                                "ports": [
                                    {"portNumber": {"port": 9100}},
                                    {"portNumber": {"port": 8080, "protocol": "TCP"}},
                                    {"portRange": {"start": 9201, "end": 9205, "protocol": "TCP"}}
                                ],
                                "to": [
                                    {"networks": fake_ips[:5]}
                                ]
                            },
                            {
                                "name": "allow-egress-to-openshift-monitoring-network-2",
                                "action": "Allow",
                                "ports": [
                                    {"portNumber": {"port": 9100}},
                                    {"portNumber": {"port": 8080, "protocol": "TCP"}},
                                    {"portRange": {"start": 9201, "end": 9205, "protocol": "TCP"}}
                                ],
                                "to": [
                                    {"networks": fake_ips[5:7]}
                                ]
                            },
                            {
                                "name": "deny-egress-to-openshift-monitoring-network-1",
                                "action": "Deny",
                                "ports": [
                                    {"portNumber": {"port": 9091}},
                                    {"portNumber": {"port": 5432, "protocol": "TCP"}},
                                    {"portNumber": {"port": 60000, "protocol": "TCP"}},
                                    {"portNumber": {"port": 9099, "protocol": "TCP"}},
                                    {"portNumber": {"port": 9393, "protocol": "TCP"}}
                                ],
                                "to": [
                                    {"networks": [fake_ips[7]]}
                                ]
                            },
                            {
                                "name": "allow-egress-to-dns",
                                "action": "Allow",
                                "to": [
                                    {
                                        "namespaces": {
                                            "namespaceSelector": {
                                                "matchLabels": {
                                                    "kubernetes.io/metadata.name": "openshift-dns"
                                                }
                                            }
                                        }
                                    }
                                ]
                            },
                            {
                                "name": "allow-to-kube-apiserver",
                                "action": "Allow",
                                "to": [
                                    {
                                        "nodes": {
                                            "matchExpressions": [
                                                {
                                                    "key": "node-role.kubernetes.io/control-plane",
                                                    "operator": "Exists"
                                                }
                                            ]
                                        }
                                    }
                                ],
                                "ports": [
                                    {"portNumber": {"port": 6443, "protocol": "TCP"}}
                                ]
                            }
                        ]
                    }
                }
                
                # Create task for each ANP
                tasks.append(asyncio.to_thread(
                    self.custom_objects.create_cluster_custom_object,
                    group="policy.networking.k8s.io",
                    version="v1alpha1",
                    plural="adminnetworkpolicies",
                    body=anp_body
                ))
            
            # Execute all ANP creation tasks
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = sum(1 for result in results if not isinstance(result, Exception))
            
            self.log_info(f"Created {success_count}/{anp_count} AdminNetworkPolicies", "ANP")
            return success_count > 0
        except Exception as e:
            self.log_warn(f"Failed to create AdminNetworkPolicies: {e}", "ANP")
            return False

    # 6. Create Images
    async def create_images(self) -> bool:
        """Create multiple Images using OpenShift Image API"""
        if not self.config.create_images:
            return True

        async def create_image(name: str) -> bool:
            try:
                image_body = {
                    "apiVersion": "image.openshift.io/v1",
                    "kind": "Image",
                    "metadata": {
                        "name": name,
                        "labels": {"stress-test": "true"}
                    },
                    "dockerImageReference": "registry.redhat.io/ubi8/ruby-27:latest",
                    "dockerImageMetadata": {
                        "kind": "DockerImage",
                        "apiVersion": "1.0",
                        "Id": "",
                        "ContainerConfig": {},
                        "Config": {}
                    },
                    "dockerImageLayers": [],
                    "dockerImageMetadataVersion": "1.0"
                }
                
                await asyncio.to_thread(
                    self.custom_objects.create_cluster_custom_object,
                    group="image.openshift.io",
                    version="v1",
                    plural="images",
                    body=image_body
                )
                return True
            except ApiException as e:
                if e.status == 409:
                    return True
                self.log_warn(f"Failed to create Image {name}: {e}", "IMAGE")
                return False

        # Create namespace * 5 images
        image_count = self.config.total_namespaces * 5
        semaphore = asyncio.Semaphore(self.config.max_concurrent_operations)
        
        async def create_with_semaphore(name: str) -> bool:
            async with semaphore:
                return await create_image(name)

        tasks = [
            create_with_semaphore(f"stress-image-{i+1}") 
            for i in range(image_count)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for result in results if result is True)
        
        self.log_info(f"Created {success_count}/{image_count} Images", "IMAGE")
        return success_count > 0

    # 3. Create namespace resources in parallel
    async def create_namespace_resources(self, namespace_idx: int) -> Dict[str, int]:
        """Create all resources for a single namespace with proper synchronization"""
        namespace_name = f"{self.config.namespace_prefix}-{namespace_idx}"
        tenant = self.tenant_labels[namespace_idx % len(self.tenant_labels)]
        
        # Create namespace with appropriate labels
        namespace_labels = {
            'customer_tenant': tenant,
        }
        # Add anplabel to some namespaces for BANP testing
        if namespace_idx % 4 == 0:
            namespace_labels['anplabel'] = 'anp-tenant'
        
        # Step 1: Ensure namespace exists and is ready
        if not await self.ensure_namespace(namespace_name, namespace_labels):
            self.log_error(f"Failed to create/verify namespace {namespace_name}", "NAMESPACE")
            return {"errors": 1}
        
        results = {"success": 0, "errors": 0}
        
        # Step 2: Create all resource types in sequence to avoid race conditions
        # First create ConfigMaps and Secrets (required for deployments)
        resource_tasks = [
            ("small_configmaps", self.create_small_configmaps(namespace_name)),
            ("large_configmap", self.create_large_configmap(namespace_name)),
            ("small_secrets", self.create_small_secrets(namespace_name)),
            ("large_secrets", self.create_large_secrets(namespace_name)),
        ]
        
        # Execute ConfigMaps and Secrets creation first
        cm_secret_results = await asyncio.gather(*[task[1] for task in resource_tasks], return_exceptions=True)
        
        # Count CM/Secret results
        for i, result in enumerate(cm_secret_results):
            if isinstance(result, Exception):
                self.log_error(f"Task {resource_tasks[i][0]} failed in {namespace_name}: {result}", "RESOURCE")
                results["errors"] += 1
            elif result:
                results["success"] += 1
            else:
                results["errors"] += 1
        
        # Step 3: Create remaining resources that don't depend on CM/Secrets
        additional_tasks = []
        if self.config.create_egress_firewall:
            additional_tasks.append(("egress_firewall", self.create_egress_firewall(namespace_name)))
            
        if self.config.create_network_policies:
            additional_tasks.append(("network_policies", self.create_network_policies(namespace_name)))
        
        if additional_tasks:
            additional_results = await asyncio.gather(*[task[1] for task in additional_tasks], return_exceptions=True)
            
            for i, result in enumerate(additional_results):
                if isinstance(result, Exception):
                    self.log_error(f"Task {additional_tasks[i][0]} failed in {namespace_name}: {result}", "RESOURCE")
                    results["errors"] += 1
                elif result:
                    results["success"] += 1
                else:
                    results["errors"] += 1
        
        # Step 4: Create deployments last (after ConfigMaps and Secrets are ready)
        if self.config.create_deployments:
            try:
                deployment_result = await self.create_deployments(namespace_name)
                if deployment_result:
                    results["success"] += 1
                else:
                    results["errors"] += 1
            except Exception as e:
                self.log_error(f"Deployment creation failed in {namespace_name}: {e}", "DEPLOYMENT")
                results["errors"] += 1
        
        return results

    async def create_all_namespaces_and_resources(self):
        """Create all namespaces and their resources with proper coordination"""
        self.log_info(f"Creating {self.config.total_namespaces} namespaces with resources", "MAIN")
        
        # Step 1: Create all namespaces first in small batches
        self.log_info("Phase 1: Creating all namespaces", "MAIN")
        namespace_creation_semaphore = asyncio.Semaphore(self.config.namespace_parallel)
        
        async def create_namespace_only(ns_idx: int) -> bool:
            async with namespace_creation_semaphore:
                namespace_name = f"{self.config.namespace_prefix}-{ns_idx}"
                tenant = self.tenant_labels[ns_idx % len(self.tenant_labels)]
                
                namespace_labels = {
                    'customer_tenant': tenant,
                }
                if ns_idx % 4 == 0:
                    namespace_labels['anplabel'] = 'anp-tenant'
                
                return await self.ensure_namespace(namespace_name, namespace_labels)
        
        # Create all namespaces
        namespace_tasks = [
            create_namespace_only(i) 
            for i in range(1, self.config.total_namespaces + 1)
        ]
        
        namespace_results = await asyncio.gather(*namespace_tasks, return_exceptions=True)
        successful_namespaces = sum(1 for result in namespace_results if result is True)
        
        self.log_info(f"Created {successful_namespaces}/{self.config.total_namespaces} namespaces", "MAIN")
        
        # Step 2: Create resources in all namespaces
        self.log_info("Phase 2: Creating resources in namespaces", "MAIN")
        
        resource_semaphore = asyncio.Semaphore(self.config.namespace_parallel)
        
        async def create_resources_only(ns_idx: int) -> Dict[str, int]:
            async with resource_semaphore:
                namespace_name = f"{self.config.namespace_prefix}-{ns_idx}"
                
                results = {"success": 0, "errors": 0}
                
                # Create all resource types concurrently within this namespace
                resource_tasks = [
                    ("small_configmaps", self.create_small_configmaps(namespace_name)),
                    ("large_configmap", self.create_large_configmap(namespace_name)),
                    ("small_secrets", self.create_small_secrets(namespace_name)),
                    ("large_secrets", self.create_large_secrets(namespace_name)),
                ]
                
                # Add optional resources
                if self.config.create_egress_firewall:
                    resource_tasks.append(("egress_firewall", self.create_egress_firewall(namespace_name)))
                    
                if self.config.create_network_policies:
                    resource_tasks.append(("network_policies", self.create_network_policies(namespace_name)))
                
                # Execute core resource tasks
                task_results = await asyncio.gather(*[task[1] for task in resource_tasks], return_exceptions=True)
                
                # Count results
                for i, result in enumerate(task_results):
                    if isinstance(result, Exception):
                        self.log_error(f"Task {resource_tasks[i][0]} failed in {namespace_name}: {result}", "RESOURCE")
                        results["errors"] += 1
                    elif result:
                        results["success"] += 1
                    else:
                        results["errors"] += 1
                
                # Create deployments after other resources (if enabled)
                if self.config.create_deployments:
                    try:
                        deployment_result = await self.create_deployments(namespace_name)
                        if deployment_result:
                            results["success"] += 1
                        else:
                            results["errors"] += 1
                    except Exception as e:
                        self.log_error(f"Deployment creation failed in {namespace_name}: {e}", "DEPLOYMENT")
                        results["errors"] += 1
                
                return results
        
        # Create resource tasks
        resource_creation_tasks = [
            create_resources_only(i) 
            for i in range(1, self.config.total_namespaces + 1)
        ]
        
        # Process in batches for progress reporting
        batch_size = self.config.namespace_parallel
        total_success = 0
        total_errors = 0
        
        for i in range(0, len(resource_creation_tasks), batch_size):
            batch = resource_creation_tasks[i:i + batch_size]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)
            
            batch_success = 0
            batch_errors = 0
            for result in batch_results:
                if isinstance(result, dict):
                    batch_success += result.get("success", 0)
                    batch_errors += result.get("errors", 0)
                else:
                    batch_errors += 1
            
            total_success += batch_success
            total_errors += batch_errors
            
            completed = min(i + batch_size, self.config.total_namespaces)
            self.log_info(f"Processed resources for {completed}/{self.config.total_namespaces} namespaces "
                         f"(Success: {total_success}, Errors: {total_errors})", "PROGRESS")
        
        self.log_info(f"Resource creation completed: "
                     f"Total Success: {total_success}, Total Errors: {total_errors}", "COMPLETE")

    async def create_global_network_policies(self):
        """Create global network policies (BANP and ANP)"""
        if not self.config.create_anp_banp:
            return
            
        self.log_info("Creating global network policies", "NETPOL")
        
        # Create BaselineAdminNetworkPolicy
        banp_success = await self.create_baseline_admin_network_policy()
        if banp_success:
            self.log_info("BaselineAdminNetworkPolicy created successfully", "BANP")
        
        # Create AdminNetworkPolicies
        anp_success = await self.create_admin_network_policies()
        if anp_success:
            self.log_info("AdminNetworkPolicies created successfully", "ANP")

    async def cleanup_all_resources(self):
        """Clean up all created resources"""
        if not self.config.cleanup_on_completion:
            self.log_info("Cleanup disabled, skipping resource cleanup", "CLEANUP")
            return
            
        self.log_info("Starting cleanup of all created resources", "CLEANUP")
        
        try:
            # Cleanup Images
            if self.config.create_images:
                try:
                    images = await asyncio.to_thread(
                        self.custom_objects.list_cluster_custom_object,
                        group="image.openshift.io",
                        version="v1",
                        plural="images"
                    )
                    
                    image_tasks = []
                    for img in images.get('items', []):
                        if img.get('metadata', {}).get('labels', {}).get('stress-test') == 'true':
                            name = img.get('metadata', {}).get('name')
                            if name:
                                image_tasks.append(asyncio.to_thread(
                                    self.custom_objects.delete_cluster_custom_object,
                                    group="image.openshift.io",
                                    version="v1",
                                    plural="images",
                                    name=name
                                ))
                    
                    if image_tasks:
                        await asyncio.gather(*image_tasks, return_exceptions=True)
                        self.log_info(f"Cleaned up {len(image_tasks)} Images", "CLEANUP")
                except Exception as e:
                    self.log_warn(f"Failed to cleanup Images: {e}", "CLEANUP")

            # List all namespaces with our label
            namespaces = await asyncio.to_thread(self.core_v1.list_namespace)
            cleanup_namespaces = [
                ns.metadata.name for ns in namespaces.items
                if ns.metadata.labels and ns.metadata.labels.get('stress-test') == 'true'
            ]
            
            self.log_info(f"Found {len(cleanup_namespaces)} namespaces to clean up", "CLEANUP")
            
            # Delete namespaces in batches
            semaphore = asyncio.Semaphore(self.config.namespace_parallel)
            
            async def delete_namespace_with_semaphore(ns_name: str):
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
                delete_tasks = [delete_namespace_with_semaphore(ns) for ns in cleanup_namespaces]
                results = await asyncio.gather(*delete_tasks, return_exceptions=True)
                successful = sum(1 for result in results if result is True)
                self.log_info(f"Deleted {successful}/{len(cleanup_namespaces)} namespaces", "CLEANUP")
            
            # Cleanup global network policies
            if self.config.create_anp_banp:
                try:
                    # Delete BANP
                    await asyncio.to_thread(
                        self.custom_objects.delete_cluster_custom_object,
                        group="policy.networking.k8s.io",
                        version="v1alpha1",
                        plural="baselineadminnetworkpolicies",
                        name="default"
                    )
                except ApiException as e:
                    if e.status != 404:
                        self.log_warn(f"Failed to delete BANP: {e}", "CLEANUP")
                
                try:
                    # Delete ANPs
                    anps = await asyncio.to_thread(
                        self.custom_objects.list_cluster_custom_object,
                        group="policy.networking.k8s.io",
                        version="v1alpha1",
                        plural="adminnetworkpolicies"
                    )
                    
                    anp_tasks = []
                    for anp in anps.get('items', []):
                        if anp.get('metadata', {}).get('labels', {}).get('stress-test') == 'true':
                            name = anp.get('metadata', {}).get('name')
                            if name:
                                anp_tasks.append(asyncio.to_thread(
                                    self.custom_objects.delete_cluster_custom_object,
                                    group="policy.networking.k8s.io",
                                    version="v1alpha1",
                                    plural="adminnetworkpolicies",
                                    name=name
                                ))
                    
                    if anp_tasks:
                        await asyncio.gather(*anp_tasks, return_exceptions=True)
                        self.log_info(f"Cleaned up {len(anp_tasks)} AdminNetworkPolicies", "CLEANUP")
                except Exception as e:
                    self.log_warn(f"Failed to cleanup ANPs: {e}", "CLEANUP")
            
            self.log_info("Cleanup completed", "CLEANUP")
            
        except Exception as e:
            self.log_error(f"Cleanup failed: {e}", "CLEANUP")

    async def list_all_pods(self) -> Dict[str, Any]:
        """List all pods across all stress-test namespaces"""
        try:
            self.log_info("Listing all pods in stress-test namespaces", "LIST-PODS")
            
            # Get all namespaces with stress-test label
            namespaces = await asyncio.to_thread(self.core_v1.list_namespace)
            stress_namespaces = [
                ns.metadata.name for ns in namespaces.items
                if ns.metadata.labels and ns.metadata.labels.get('stress-test') == 'true'
            ]
            
            total_pods = 0
            pod_details = {}
            
            for ns in stress_namespaces:
                try:
                    pods = await asyncio.to_thread(
                        self.core_v1.list_namespaced_pod,
                        namespace=ns
                    )
                    pod_count = len(pods.items)
                    total_pods += pod_count
                    
                    pod_details[ns] = {
                        'count': pod_count,
                        'pods': [
                            {
                                'name': pod.metadata.name,
                                'phase': pod.status.phase,
                                'ready': sum(1 for c in (pod.status.container_statuses or []) if c.ready)
                            }
                            for pod in pods.items
                        ]
                    }
                except ApiException as e:
                    self.log_warn(f"Failed to list pods in {ns}: {e}", "LIST-PODS")
            
            self.log_info(f"Total pods found: {total_pods} across {len(stress_namespaces)} namespaces", "LIST-PODS")
            return {
                'total_pods': total_pods,
                'total_namespaces': len(stress_namespaces),
                'details': pod_details
            }
            
        except Exception as e:
            self.log_error(f"Failed to list pods: {e}", "LIST-PODS")
            return {'error': str(e)}

    async def list_all_configmaps(self) -> Dict[str, Any]:
        """List all configmaps across all stress-test namespaces"""
        try:
            self.log_info("Listing all ConfigMaps in stress-test namespaces", "LIST-CM")
            
            namespaces = await asyncio.to_thread(self.core_v1.list_namespace)
            stress_namespaces = [
                ns.metadata.name for ns in namespaces.items
                if ns.metadata.labels and ns.metadata.labels.get('stress-test') == 'true'
            ]
            
            total_configmaps = 0
            total_small = 0
            total_large = 0
            cm_details = {}
            
            for ns in stress_namespaces:
                try:
                    configmaps = await asyncio.to_thread(
                        self.core_v1.list_namespaced_config_map,
                        namespace=ns
                    )
                    
                    small_cms = [cm for cm in configmaps.items 
                            if cm.metadata.labels and cm.metadata.labels.get('type') == 'small-configmap']
                    large_cms = [cm for cm in configmaps.items 
                            if cm.metadata.labels and cm.metadata.labels.get('type') == 'large-configmap']
                    
                    ns_small = len(small_cms)
                    ns_large = len(large_cms)
                    
                    total_configmaps += len(configmaps.items)
                    total_small += ns_small
                    total_large += ns_large
                    
                    cm_details[ns] = {
                        'total': len(configmaps.items),
                        'small': ns_small,
                        'large': ns_large
                    }
                except ApiException as e:
                    self.log_warn(f"Failed to list ConfigMaps in {ns}: {e}", "LIST-CM")
            
            self.log_info(f"Total ConfigMaps: {total_configmaps} (Small: {total_small}, Large: {total_large})", "LIST-CM")
            return {
                'total_configmaps': total_configmaps,
                'total_small': total_small,
                'total_large': total_large,
                'total_namespaces': len(stress_namespaces),
                'details': cm_details
            }
            
        except Exception as e:
            self.log_error(f"Failed to list ConfigMaps: {e}", "LIST-CM")
            return {'error': str(e)}

    async def list_all_secrets(self) -> Dict[str, Any]:
        """List all secrets across all stress-test namespaces"""
        try:
            self.log_info("Listing all Secrets in stress-test namespaces", "LIST-SECRET")
            
            namespaces = await asyncio.to_thread(self.core_v1.list_namespace)
            stress_namespaces = [
                ns.metadata.name for ns in namespaces.items
                if ns.metadata.labels and ns.metadata.labels.get('stress-test') == 'true'
            ]
            
            total_secrets = 0
            total_small = 0
            total_large = 0
            secret_details = {}
            
            for ns in stress_namespaces:
                try:
                    secrets = await asyncio.to_thread(
                        self.core_v1.list_namespaced_secret,
                        namespace=ns
                    )
                    
                    small_secrets = [s for s in secrets.items 
                                if s.metadata.labels and s.metadata.labels.get('type') == 'small-secret']
                    large_secrets = [s for s in secrets.items 
                                if s.metadata.labels and s.metadata.labels.get('type') == 'large-secret']
                    
                    ns_small = len(small_secrets)
                    ns_large = len(large_secrets)
                    
                    total_secrets += len(secrets.items)
                    total_small += ns_small
                    total_large += ns_large
                    
                    secret_details[ns] = {
                        'total': len(secrets.items),
                        'small': ns_small,
                        'large': ns_large
                    }
                except ApiException as e:
                    self.log_warn(f"Failed to list Secrets in {ns}: {e}", "LIST-SECRET")
            
            self.log_info(f"Total Secrets: {total_secrets} (Small: {total_small}, Large: {total_large})", "LIST-SECRET")
            return {
                'total_secrets': total_secrets,
                'total_small': total_small,
                'total_large': total_large,
                'total_namespaces': len(stress_namespaces),
                'details': secret_details
            }
            
        except Exception as e:
            self.log_error(f"Failed to list Secrets: {e}", "LIST-SECRET")
            return {'error': str(e)}

    async def list_all_network_policies(self) -> Dict[str, Any]:
        """List all NetworkPolicies across all stress-test namespaces"""
        try:
            self.log_info("Listing all NetworkPolicies in stress-test namespaces", "LIST-NETPOL")
            
            namespaces = await asyncio.to_thread(self.core_v1.list_namespace)
            stress_namespaces = [
                ns.metadata.name for ns in namespaces.items
                if ns.metadata.labels and ns.metadata.labels.get('stress-test') == 'true'
            ]
            
            total_netpols = 0
            netpol_details = {}
            
            for ns in stress_namespaces:
                try:
                    netpols = await asyncio.to_thread(
                        self.networking_v1.list_namespaced_network_policy,
                        namespace=ns
                    )
                    
                    stress_netpols = [np for np in netpols.items 
                                     if np.metadata.labels and np.metadata.labels.get('stress-test') == 'true']
                    
                    ns_count = len(stress_netpols)
                    total_netpols += ns_count
                    
                    netpol_details[ns] = {
                        'count': ns_count,
                        'policies': [
                            {
                                'name': np.metadata.name,
                                'ingress_rules': len(np.spec.ingress or []),
                                'egress_rules': len(np.spec.egress or [])
                            }
                            for np in stress_netpols
                        ]
                    }
                except ApiException as e:
                    self.log_warn(f"Failed to list NetworkPolicies in {ns}: {e}", "LIST-NETPOL")
            
            self.log_info(f"Total NetworkPolicies: {total_netpols} across {len(stress_namespaces)} namespaces", "LIST-NETPOL")
            return {
                'total_network_policies': total_netpols,
                'total_namespaces': len(stress_namespaces),
                'details': netpol_details
            }
            
        except Exception as e:
            self.log_error(f"Failed to list NetworkPolicies: {e}", "LIST-NETPOL")
            return {'error': str(e)}

    async def list_all_images(self) -> Dict[str, Any]:
        """List all Images created by stress test"""
        try:
            self.log_info("Listing all Images created by stress test", "LIST-IMAGE")
            
            images = await asyncio.to_thread(
                self.custom_objects.list_cluster_custom_object,
                group="image.openshift.io",
                version="v1",
                plural="images"
            )
            
            stress_images = [
                img for img in images.get('items', [])
                if img.get('metadata', {}).get('labels', {}).get('stress-test') == 'true'
            ]
            
            image_details = [
                {
                    'name': img.get('metadata', {}).get('name'),
                    'docker_image_reference': img.get('dockerImageReference', 'N/A'),
                    'created': img.get('metadata', {}).get('creationTimestamp', 'N/A')
                }
                for img in stress_images
            ]
            
            self.log_info(f"Total Images: {len(stress_images)}", "LIST-IMAGE")
            return {
                'total_images': len(stress_images),
                'images': image_details
            }
            
        except Exception as e:
            self.log_error(f"Failed to list Images: {e}", "LIST-IMAGE")
            return {'error': str(e)}

    async def list_all_admin_network_policies(self) -> Dict[str, Any]:
        """List all AdminNetworkPolicies and BaselineAdminNetworkPolicy"""
        try:
            self.log_info("Listing AdminNetworkPolicies and BaselineAdminNetworkPolicy", "LIST-ANP")
            
            result = {
                'banp': None,
                'anps': [],
                'total_anps': 0
            }
            
            # List BaselineAdminNetworkPolicy
            try:
                banps = await asyncio.to_thread(
                    self.custom_objects.list_cluster_custom_object,
                    group="policy.networking.k8s.io",
                    version="v1alpha1",
                    plural="baselineadminnetworkpolicies"
                )
                
                stress_banps = [
                    banp for banp in banps.get('items', [])
                    if banp.get('metadata', {}).get('labels', {}).get('stress-test') == 'true'
                ]
                
                if stress_banps:
                    banp = stress_banps[0]
                    result['banp'] = {
                        'name': banp.get('metadata', {}).get('name'),
                        'ingress_rules': len(banp.get('spec', {}).get('ingress', [])),
                        'egress_rules': len(banp.get('spec', {}).get('egress', [])),
                        'created': banp.get('metadata', {}).get('creationTimestamp', 'N/A')
                    }
            except ApiException as e:
                self.log_warn(f"Failed to list BaselineAdminNetworkPolicy: {e}", "LIST-ANP")
            
            # List AdminNetworkPolicies
            try:
                anps = await asyncio.to_thread(
                    self.custom_objects.list_cluster_custom_object,
                    group="policy.networking.k8s.io",
                    version="v1alpha1",
                    plural="adminnetworkpolicies"
                )
                
                stress_anps = [
                    anp for anp in anps.get('items', [])
                    if anp.get('metadata', {}).get('labels', {}).get('stress-test') == 'true'
                ]
                
                result['total_anps'] = len(stress_anps)
                result['anps'] = [
                    {
                        'name': anp.get('metadata', {}).get('name'),
                        'priority': anp.get('spec', {}).get('priority'),
                        'ingress_rules': len(anp.get('spec', {}).get('ingress', [])),
                        'egress_rules': len(anp.get('spec', {}).get('egress', [])),
                        'created': anp.get('metadata', {}).get('creationTimestamp', 'N/A')
                    }
                    for anp in stress_anps
                ]
            except ApiException as e:
                self.log_warn(f"Failed to list AdminNetworkPolicies: {e}", "LIST-ANP")
            
            self.log_info(f"Total AdminNetworkPolicies: {result['total_anps']}, BANP: {'Found' if result['banp'] else 'Not Found'}", "LIST-ANP")
            return result
            
        except Exception as e:
            self.log_error(f"Failed to list Admin Network Policies: {e}", "LIST-ANP")
            return {'error': str(e)}

    async def run_list_scenario(self):
        """Run listing scenario for all resource types"""
        try:
            self.log_info("Starting list scenario for large-scale resources", "LIST")
            start_time = time.time()
            
            # Run all listing operations concurrently
            results = await asyncio.gather(
                self.list_all_pods(),
                self.list_all_configmaps(),
                self.list_all_secrets(),
                self.list_all_network_policies(),
                self.list_all_images(),
                self.list_all_admin_network_policies(),
                return_exceptions=True
            )
            
            pods_result, cms_result, secrets_result, netpols_result, images_result, anp_result = results
            
            # Print summary
            print(f"\n{Colors.CYAN}{'='*80}{Colors.NC}")
            print(f"{Colors.GREEN}Resource Listing Summary{Colors.NC}")
            print(f"{Colors.CYAN}{'='*80}{Colors.NC}")
            
            if isinstance(pods_result, dict) and 'total_pods' in pods_result:
                print(f"\n{Colors.YELLOW}Pods:{Colors.NC}")
                print(f"  Total Pods: {pods_result['total_pods']}")
                print(f"  Namespaces: {pods_result['total_namespaces']}")
            
            if isinstance(cms_result, dict) and 'total_configmaps' in cms_result:
                print(f"\n{Colors.YELLOW}ConfigMaps:{Colors.NC}")
                print(f"  Total ConfigMaps: {cms_result['total_configmaps']}")
                print(f"  Small ConfigMaps: {cms_result['total_small']}")
                print(f"  Large ConfigMaps: {cms_result['total_large']}")
                print(f"  Namespaces: {cms_result['total_namespaces']}")
            
            if isinstance(secrets_result, dict) and 'total_secrets' in secrets_result:
                print(f"\n{Colors.YELLOW}Secrets:{Colors.NC}")
                print(f"  Total Secrets: {secrets_result['total_secrets']}")
                print(f"  Small Secrets: {secrets_result['total_small']}")
                print(f"  Large Secrets: {secrets_result['total_large']}")
                print(f"  Namespaces: {secrets_result['total_namespaces']}")
            
            if isinstance(netpols_result, dict) and 'total_network_policies' in netpols_result:
                print(f"\n{Colors.YELLOW}NetworkPolicies:{Colors.NC}")
                print(f"  Total NetworkPolicies: {netpols_result['total_network_policies']}")
                print(f"  Namespaces: {netpols_result['total_namespaces']}")
            
            if isinstance(images_result, dict) and 'total_images' in images_result:
                print(f"\n{Colors.YELLOW}Images:{Colors.NC}")
                print(f"  Total Images: {images_result['total_images']}")
            
            if isinstance(anp_result, dict) and 'total_anps' in anp_result:
                print(f"\n{Colors.YELLOW}Admin Network Policies:{Colors.NC}")
                print(f"  Total AdminNetworkPolicies: {anp_result['total_anps']}")
                print(f"  BaselineAdminNetworkPolicy: {'Found' if anp_result.get('banp') else 'Not Found'}")
            
            elapsed_time = time.time() - start_time
            print(f"\n{Colors.GREEN}List scenario completed in {elapsed_time:.2f} seconds{Colors.NC}")
            print(f"{Colors.CYAN}{'='*80}{Colors.NC}\n")
            
            return {
                'pods': pods_result,
                'configmaps': cms_result,
                'secrets': secrets_result,
                'network_policies': netpols_result,
                'images': images_result,
                'admin_network_policies': anp_result,
                'elapsed_time': elapsed_time
            }
            
        except Exception as e:
            self.log_error(f"List scenario failed: {e}", "LIST")
            raise

    async def run_comprehensive_test(self):
        """Run the complete stress test"""
        try:
            start_time = time.time()
            self.log_info("Starting comprehensive etcd stress test", "MAIN")
            
            # Phase 1: Create global network policies first
            await self.create_global_network_policies()
            
            # Phase 2: Create Images
            await self.create_images()
            
            # Phase 3: Create all namespaces and resources
            await self.create_all_namespaces_and_resources()
            
            elapsed_time = time.time() - start_time
            self.log_info(f"Test completed successfully in {elapsed_time:.2f} seconds", "MAIN")
            
            # Phase 4: Run list scenario to verify created resources
            self.log_info("Running list scenario to verify created resources", "MAIN")
            await self.run_list_scenario()
            
            # Phase 5: Cleanup (if enabled)
            if self.config.cleanup_on_completion:
                self.log_info("Starting cleanup phase", "MAIN")
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
    """Create and configure argument parser"""
    parser = argparse.ArgumentParser(
        description='Optimized OpenShift etcd Stress Testing Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This tool creates comprehensive Kubernetes resources to stress test etcd:

Resources Created Per Namespace:
  - 10 Small ConfigMaps
  - 3 Large ConfigMaps (1MB each, with 6GB total limit)
  - 10 Small Secrets 
  - 10 Large Secrets with certificates
  - 2 Network Policies per namespace
  - 1 EgressFirewall policy per namespace
  - 5 Deployments (optional, with volume mounts)

Global Resources:
  - BaselineAdminNetworkPolicy (BANP)
  - AdminNetworkPolicy (ANP) objects (total_namespaces/3)
  - Images (total_namespaces * 5)

Environment Variables:
  TOTAL_NAMESPACES (default: 100)
  NAMESPACE_PARALLEL (default: 10)
  NAMESPACE_PREFIX (default: stress-test)
  TOTAL_LARGE_CONFIGMAP_LIMIT_GB (default: 6)
  CREATE_DEPLOYMENTS (default: true)
  CREATE_EGRESS_FIREWALL (default: true)
  CREATE_NETWORK_POLICIES (default: true)
  CREATE_ANP_BANP (default: true)
  CREATE_IMAGES (default: true)
  MAX_CONCURRENT_OPERATIONS (default: 50)
  NAMESPACE_READY_TIMEOUT (default: 60)
  RESOURCE_RETRY_COUNT (default: 3)
  RESOURCE_RETRY_DELAY (default: 1.0)
  LOG_FILE, LOG_LEVEL
  CLEANUP_ON_COMPLETION (default: false)

Examples:
  %(prog)s --total-namespaces 50 --namespace-parallel 5
  %(prog)s --large-limit 8 --enable-deployments
  %(prog)s --cleanup --no-images
        """
    )
    
    # Core configuration
    parser.add_argument('--total-namespaces', type=int, 
                       help='Total number of namespaces to create')
    parser.add_argument('--namespace-parallel', type=int,
                       help='Number of namespaces to process in parallel')
    parser.add_argument('--namespace-prefix', 
                       help='Prefix for namespace names')
    parser.add_argument('--large-limit', type=float,
                       help='Total limit for large ConfigMaps in GB')
    
    # Optional features
    parser.add_argument('--enable-deployments', action='store_true',
                       help='Enable deployment creation')
    parser.add_argument('--no-egress-firewall', action='store_true',
                       help='Disable EgressFirewall creation')
    parser.add_argument('--no-network-policies', action='store_true',
                       help='Disable NetworkPolicy creation')
    parser.add_argument('--no-anp-banp', action='store_true',
                       help='Disable ANP/BANP creation')
    parser.add_argument('--no-images', action='store_true',
                       help='Disable Image creation')
    
    # Performance tuning
    parser.add_argument('--max-concurrent', type=int,
                       help='Maximum concurrent operations')
    parser.add_argument('--namespace-timeout', type=int,
                       help='Timeout for namespace readiness in seconds')
    parser.add_argument('--retry-count', type=int,
                       help='Number of retries for failed operations')
    parser.add_argument('--retry-delay', type=float,
                       help='Delay between retries in seconds')
    
    # Control options
    parser.add_argument('--cleanup', action='store_true',
                       help='Enable cleanup after completion')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Set logging level')
    parser.add_argument('--log-file',
                       help='Log file path')
    parser.add_argument('--list-only', action='store_true',
                       help='Only list existing resources without creating')    
    return parser

async def main():
    """Main execution function"""
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Create configuration
    config = Config()
    
    # Override config with command line arguments
    if args.total_namespaces is not None:
        config.total_namespaces = args.total_namespaces
    if args.namespace_parallel is not None:
        config.namespace_parallel = args.namespace_parallel
    if args.namespace_prefix is not None:
        config.namespace_prefix = args.namespace_prefix
    if args.large_limit is not None:
        config.total_large_configmap_limit_gb = args.large_limit
    if args.enable_deployments:
        config.create_deployments = True
    if args.no_egress_firewall:
        config.create_egress_firewall = False
    if args.no_network_policies:
        config.create_network_policies = False
    if args.no_anp_banp:
        config.create_anp_banp = False
    if args.no_images:
        config.create_images = False
    if args.max_concurrent is not None:
        config.max_concurrent_operations = args.max_concurrent
    if args.namespace_timeout is not None:
        config.namespace_ready_timeout = args.namespace_timeout
    if args.retry_count is not None:
        config.resource_retry_count = args.retry_count
    if args.retry_delay is not None:
        config.resource_retry_delay = args.retry_delay
    if args.cleanup:
        config.cleanup_on_completion = True
    if args.log_level is not None:
        config.log_level = args.log_level
    if args.log_file is not None:
        config.log_file = args.log_file
    
    # Create and run the stress test tool
    tool = etcdStressTools(config)
    
    try:
        # Check if list-only mode
        if args.list_only:
            # Only run list scenario
            await tool.run_list_scenario()
        else:
            # Run comprehensive test (which now includes listing)
            await tool.run_comprehensive_test()
    except KeyboardInterrupt:
        tool.log_warn("Test interrupted by user", "MAIN")
        sys.exit(1)
    except Exception as e:
        tool.log_error(f"Test failed: {e}", "MAIN")
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())