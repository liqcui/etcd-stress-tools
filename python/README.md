# OpenShift etcd Stress Testing Tool

A high-performance, async Python tool for comprehensive Kubernetes resource creation and listing to stress test etcd performance in OpenShift clusters.

## Features

- **Modular Design**: Separate functions for each resource type
- **Maximum Concurrency**: Optimized async operations with configurable limits
- **Comprehensive Resource Creation**: ConfigMaps, Secrets, Deployments, Network Policies, and more
- **Resource Listing**: List and verify all created resources across the cluster
- **Flexible Configuration**: Environment variables and command-line options
- **Optional Cleanup**: Automated resource cleanup after testing
- **Progress Monitoring**: Real-time progress reporting with colored output

## Resources Created

### Per Namespace (100 namespaces by default)
- **10 Small ConfigMaps** (5 key-value pairs each)
- **3 Large ConfigMaps** (1MB each, respects 6GB total limit)
- **10 Small Secrets** (username, password, token)
- **10 Large Secrets** (TLS certificates, SSH keys, large tokens)
- **1 EgressFirewall** (10 rules with Allow/Deny policies)
- **2 NetworkPolicies** (deny-by-default + complex egress rules)
- **5 Deployments** (optional, with mounted ConfigMaps/Secrets)

### Cluster-Wide Resources
- **1 BaselineAdminNetworkPolicy (BANP)** (deny-all baseline)
- **33+ AdminNetworkPolicies (ANP)** (total_namespaces/3, with fake IPs)
- **500+ Images** (total_namespaces × 5, OpenShift Image resources)

## Installation

### Prerequisites
- Python 3.8+
- OpenShift/Kubernetes cluster access
- Required Python packages:

```bash
pip install kubernetes cryptography pyyaml
```

### Setup
1. Clone or download the script
2. Ensure kubeconfig is configured or running in-cluster
3. Install dependencies
4. Configure permissions (cluster-admin recommended for testing)

## Usage

### Basic Usage
```bash
# Default: 100 namespaces, 10 parallel processing
python3 etcd-stress-tools.py

# Custom configuration
python3 etcd-stress-tools.py --total-namespaces 50 --namespace-parallel 5

# Enable deployments and cleanup
python3 etcd-stress-tools.py --enable-deployments --cleanup

# Disable certain features
python3 etcd-stress-tools.py --no-images --no-anp-banp

# List existing resources only (no creation)
python3 etcd-stress-tools.py --list-only
```

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--total-namespaces` | Total number of namespaces | 100 |
| `--namespace-parallel` | Parallel namespace processing | 10 |
| `--namespace-prefix` | Namespace name prefix | stress-test |
| `--large-limit` | Large ConfigMap total limit (GB) | 6 |
| `--enable-deployments` | Create deployments with mounts | false |
| `--no-egress-firewall` | Disable EgressFirewall creation | false |
| `--no-network-policies` | Disable NetworkPolicy creation | false |
| `--no-anp-banp` | Disable ANP/BANP creation | false |
| `--no-images` | Disable Image creation | false |
| `--max-concurrent` | Max concurrent operations | 50 |
| `--namespace-timeout` | Namespace readiness timeout (seconds) | 60 |
| `--retry-count` | Number of retries for failed operations | 3 |
| `--retry-delay` | Delay between retries (seconds) | 1.0 |
| `--cleanup` | Enable cleanup after completion | false |
| `--list-only` | Only list resources without creating | false |
| `--log-level` | Logging level (DEBUG/INFO/WARNING/ERROR) | INFO |
| `--log-file` | Log file path | etcd_stress_test.log |

### Environment Variables

```bash
export TOTAL_NAMESPACES=200
export NAMESPACE_PARALLEL=15
export NAMESPACE_PREFIX="my-stress"
export TOTAL_LARGE_CONFIGMAP_LIMIT_GB=8
export CREATE_DEPLOYMENTS=true
export CREATE_EGRESS_FIREWALL=false
export CREATE_NETWORK_POLICIES=true
export CREATE_ANP_BANP=true
export CREATE_IMAGES=true
export MAX_CONCURRENT_OPERATIONS=100
export NAMESPACE_READY_TIMEOUT=60
export RESOURCE_RETRY_COUNT=3
export RESOURCE_RETRY_DELAY=1.0
export LOG_LEVEL=DEBUG
export CLEANUP_ON_COMPLETION=true
```

## Architecture

### Modular Functions

#### Resource Creation Functions
1. **create_small_configmaps()** - Creates 10 small ConfigMaps per namespace in parallel
2. **create_large_configmap()** - Creates 3 large (1MB) ConfigMaps with size limits
3. **create_small_secrets()** - Creates 10 small Secrets in parallel
4. **create_large_secrets()** - Creates 10 large Secrets with certificates
5. **create_deployments()** - Creates 3 deployments with volume mounts (optional)
6. **create_egress_firewall()** - Creates EgressFirewall with 10 rules
7. **create_network_policies()** - Creates 2 NetworkPolicies per namespace
8. **create_baseline_admin_network_policy()** - Creates cluster-wide BANP
9. **create_admin_network_policies()** - Creates ANPs with fake IPs
10. **create_images()** - Creates OpenShift Image resources

#### Resource Listing Functions
1. **list_all_pods()** - Lists all pods across stress-test namespaces
2. **list_all_configmaps()** - Lists and categorizes ConfigMaps (small/large)
3. **list_all_secrets()** - Lists and categorizes Secrets (small/large)
4. **list_all_network_policies()** - Lists NetworkPolicies with rule counts
5. **list_all_images()** - Lists cluster-wide Image resources
6. **list_all_admin_network_policies()** - Lists ANPs and BANP with priorities

### Concurrency Design

- **Namespace-level parallelism**: Process multiple namespaces simultaneously
- **Resource-level parallelism**: Create different resource types concurrently within each namespace
- **Item-level parallelism**: Create multiple items of same type in parallel
- **Configurable limits**: Semaphores prevent resource exhaustion

### Performance Optimizations

- Async/await throughout for non-blocking operations
- ThreadPoolExecutor for CPU-intensive certificate generation
- Semaphores for concurrency control
- Batch processing with progress reporting
- Exception handling with exponential backoff retry logic
- Namespace readiness verification before resource creation

## Resource Listing

### List Scenario Output

The tool automatically runs a comprehensive listing scenario after resource creation, or you can run it independently with `--list-only`:

```
================================================================================
Resource Listing Summary
================================================================================

Pods:
  Total Pods: 500
  Namespaces: 100

ConfigMaps:
  Total ConfigMaps: 1300
  Small ConfigMaps: 1000
  Large ConfigMaps: 300
  Namespaces: 100

Secrets:
  Total Secrets: 2000
  Small Secrets: 1000
  Large Secrets: 1000
  Namespaces: 100

NetworkPolicies:
  Total NetworkPolicies: 200
  Namespaces: 100

Images:
  Total Images: 500

Admin Network Policies:
  Total AdminNetworkPolicies: 33
  BaselineAdminNetworkPolicy: Found

List scenario completed in 12.45 seconds
================================================================================
```

### List Only Mode

Use `--list-only` to verify existing resources without creating new ones:

```bash
# List all stress-test resources
python3 etcd-stress-tools.py --list-only

# List with custom namespace prefix
python3 etcd-stress-tools.py --list-only --namespace-prefix my-stress
```

## Resource Specifications

### Small ConfigMap
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: small-cm-{1-10}
  labels:
    type: small-configmap
    stress-test: "true"
data:
  config-0: "small-config-value-0-{random}"
  config-1: "small-config-value-1-{random}"
  # ... 5 total entries
```

### Large ConfigMap
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: large-cm-{1-3}
  labels:
    type: large-configmap
    stress-test: "true"
data:
  large-data: "{1MB of hex data}"
```

### EgressFirewall
```yaml
apiVersion: k8s.ovn.org/v1
kind: EgressFirewall
metadata:
  name: default
spec:
  egress:
  - type: Allow
    to:
      cidrSelector: 8.8.8.8/32
  - type: Deny
    to:
      cidrSelector: 8.8.4.4/32
  # ... 10 total rules
```

### NetworkPolicy Examples
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-by-default
spec:
  podSelector: {}
  ingress: []
  policyTypes:
  - Ingress
  - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: large-networkpolicy-egress-allow-dns
spec:
  ingress:
  - from:
    - ipBlock:
        cidr: 10.128.0.0/16
  egress:
  - to:
    - ipBlock:
        cidr: 10.128.0.0/14
    # ... complex rules
```

### BaselineAdminNetworkPolicy
```yaml
apiVersion: policy.networking.k8s.io/v1alpha1
kind: BaselineAdminNetworkPolicy
metadata:
  name: default
spec:
  subject:
    namespaces:
      matchExpressions:
      - key: anplabel
        operator: In
        values: ["anp-tenant"]
  ingress:
  - name: "deny-all-ingress-from-any-ns"
    action: "Deny"
    from:
    - namespaces:
        namespaceSelector: {}
  egress:
  - name: egress-deny-all-traffic-to-any-network
    action: Deny
    to:
    - networks:
      - 0.0.0.0/0
```

## Monitoring and Logging

### Log Levels
- **DEBUG**: Detailed operation logs
- **INFO**: Progress and major events (default)
- **WARNING**: Non-fatal issues
- **ERROR**: Critical failures

### Progress Reporting
- Real-time batch completion status
- Success/error counters per batch
- Total execution time tracking
- Colored console output for easy reading
- Detailed resource listing summaries

### Log Format
```
[COMPONENT] 2024-01-01 12:00:00 - Message
```

Components: MAIN, NAMESPACE, CONFIGMAP, SECRET, DEPLOYMENT, NETPOL, EGRESSFW, ANP, BANP, IMAGE, CLEANUP, LIST-PODS, LIST-CM, LIST-SECRET, LIST-NETPOL, LIST-IMAGE, LIST-ANP

## Test Workflow

The tool follows this execution flow:

1. **Phase 1: Global Network Policies**
   - Create BaselineAdminNetworkPolicy (BANP)
   - Create AdminNetworkPolicies (ANPs)

2. **Phase 2: Images**
   - Create cluster-wide Image resources

3. **Phase 3: Namespace Creation**
   - Create all namespaces with labels
   - Wait for namespace readiness with verification

4. **Phase 4: Resource Creation**
   - Create ConfigMaps and Secrets (in parallel)
   - Create NetworkPolicies and EgressFirewalls
   - Create Deployments (if enabled)

5. **Phase 5: Verification**
   - List all created resources
   - Display comprehensive summary

6. **Phase 6: Cleanup (Optional)**
   - Delete all Images
   - Delete all stress-test namespaces
   - Delete ANPs and BANP

## Cleanup

### Automatic Cleanup
When `--cleanup` is enabled or `CLEANUP_ON_COMPLETION=true`:
1. Deletes all created Images
2. Deletes all namespaces with `stress-test=true` label
3. Deletes BANP (BaselineAdminNetworkPolicy)
4. Deletes ANPs (AdminNetworkPolicies) with `stress-test=true` label

### Manual Cleanup
```bash
# Delete all stress-test namespaces
kubectl delete namespaces -l stress-test=true

# Delete ANPs
kubectl delete adminnetworkpolicies -l stress-test=true

# Delete BANP
kubectl delete baselineadminnetworkpolicies default

# Delete Images
kubectl delete images -l stress-test=true
```

## Troubleshooting

### Common Issues

1. **Permission Denied**
   - Ensure cluster-admin or appropriate RBAC permissions
   - Check kubeconfig configuration

2. **ConfigMap Size Limits**
   - Large ConfigMaps are limited to 1MB each
   - Total large ConfigMap size limited by `TOTAL_LARGE_CONFIGMAP_LIMIT_GB`
   - Tool automatically skips creation if limits would be exceeded

3. **Resource Quotas**
   - Check namespace resource quotas
   - Adjust concurrent operations if hitting API rate limits

4. **Network Policy CRDs Missing**
   - AdminNetworkPolicy and BaselineAdminNetworkPolicy require OVN-Kubernetes
   - Use `--no-anp-banp` to disable if not available

5. **Namespace Not Ready Errors**
   - Tool waits up to 60 seconds for namespace readiness
   - Adjust `--namespace-timeout` if needed
   - Check for namespace admission webhooks

### Performance Tuning

- Adjust `--max-concurrent` based on cluster capacity
- Reduce `--namespace-parallel` if experiencing API throttling
- Increase `--retry-count` for unstable clusters
- Monitor etcd metrics during execution
- Consider running during maintenance windows for large-scale tests

## Examples

### Small Scale Test
```bash
python3 etcd-stress-tools.py \
  --total-namespaces 10 \
  --namespace-parallel 2 \
  --max-concurrent 10 \
  --cleanup
```

### Large Scale Test
```bash
python3 etcd-stress-tools.py \
  --total-namespaces 500 \
  --namespace-parallel 20 \
  --large-limit 10 \
  --max-concurrent 100 \
  --log-level DEBUG
```

### Feature-Specific Test
```bash
python3 etcd-stress-tools.py \
  --total-namespaces 50 \
  --enable-deployments \
  --no-images \
  --no-egress-firewall \
  --cleanup
```

### List Existing Resources
```bash
# List all resources without creating
python3 etcd-stress-tools.py --list-only

# List with custom prefix
python3 etcd-stress-tools.py --list-only --namespace-prefix my-test
```

### Quick Verification Test
```bash
# Create small test and verify
python3 etcd-stress-tools.py \
  --total-namespaces 5 \
  --namespace-parallel 2 \
  --no-images \
  --no-anp-banp \
  --cleanup
```
## Demo
[![asciicast](https://asciinema.org/a/745466.svg)](https://asciinema.org/a/745466)

## Requirements

### Minimum Requirements
- Python 3.8+
- kubernetes>=24.0.0
- cryptography>=3.0.0
- pyyaml>=5.0.0

### Cluster Requirements
- OpenShift 4.x or Kubernetes 1.20+
- OVN-Kubernetes (for ANP/BANP features)
- Sufficient etcd storage
- Appropriate RBAC permissions

### Recommended Resources
- 4+ CPU cores for the client machine
- 8GB+ RAM for large-scale tests
- Fast network connection to cluster
- SSD storage for etcd cluster

## License

This tool is provided as-is for testing and educational purposes. Use responsibly and ensure proper authorization before running against production clusters.