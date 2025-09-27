#!/bin/bash

# OpenShift Resource Creation and Testing Script
# Optimized version with configurable parameters and better error handling

set -euo pipefail

# Configuration - Override these via environment variables
MAX_NAMESPACES=${MAX_NAMESPACES:-100}

MAX_IMAGES=${MAX_IMAGES:-100}
MULTI_IMAGES_NS=${MULTI_IMAGES_NS:-"multi-image"}
# Defaults for image generation per namespace and total namespaces
IMAGES_PER_NS=${IMAGES_PER_NS:-10}
IMAGES_MAX_NS=${IMAGES_MAX_NS:-10}

SECRETS_PER_NS=${SECRETS_PER_NS:-100}
SECRETS_MAX_NS=${SECRETS_MAX_NS:-100}
SECRETS_NS=${SECRETS_NS:-"project-sec"}

LARGE_SECRETS_PER_NS=${LARGE_SECRETS_PER_NS:-10}
LARGE_SECRETS_NAX_NS=${LARGE_SECRETS_NAX_NS:-100}
LARGE_SECRETS_NS=${LARGE_SECRETS_NS:-"project-lsec"}

CONFIGMAP_SIZE_GB=${CONFIGMAP_SIZE_GB:-1}
CONFIGMAP_NAMESPACE=${CONFIGMAP_NAMESPACE:-"project-cfg"}
# Each ConfigMap payload size unit (in blocks of 512 KiB). Default 2 -> 1MiB per ConfigMap
CONFIGMAP_PAYLOAD_SIZE=${CONFIGMAP_PAYLOAD_SIZE:-2}
# Number of ConfigMaps to create per namespace (default 10, but configurable)
CONFIGMAPS_PER_NS=${CONFIGMAPS_PER_NS:-20}
BATCH_SIZE=${BATCH_SIZE:-50}
ETCD_NAMESPACE=${ETCD_NAMESPACE:-"openshift-etcd"}

# Parallelism for namespace creation batches
NAMESPACE_PARALLEL=${NAMESPACE_PARALLEL:-10}


LOG_FILE=${LOG_FILE:-"resource_creation.log"}
# Namespace to cleanup after runs
CLEANUP_TARGET_NAMESPACE=${CLEANUP_TARGET_NAMESPACE:-"project|multi-image"}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    local level=$1
    shift
    local message="$*"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    case $level in
        INFO)  echo -e "${GREEN}[INFO]${NC} $timestamp - $message" | tee -a "$LOG_FILE" ;;
        WARN)  echo -e "${YELLOW}[WARN]${NC} $timestamp - $message" | tee -a "$LOG_FILE" ;;
        ERROR) echo -e "${RED}[ERROR]${NC} $timestamp - $message" | tee -a "$LOG_FILE" ;;
        DEBUG) echo -e "${BLUE}[DEBUG]${NC} $timestamp - $message" | tee -a "$LOG_FILE" ;;
    esac
}

# Error handling
handle_error() {
    log ERROR "Script failed at line $1. Exit code: $2"
    cleanup
    exit $2
}

trap 'handle_error $LINENO $?' ERR

# Cleanup function
cleanup() {
    log INFO "Cleaning up temporary files..."
    rm -f sshkey sshkey.pub tls.crt tls.key 2>/dev/null || true
    rm -f /tmp/etcd_data_* 2>/dev/null || true
}

# Cleanup specific namespace (default: project-x) after all runs complete
cleanup_project_namespace() {
    # Treat CLEANUP_TARGET_NAMESPACE as a prefix (default: project-). Find and delete all matching namespaces.
    local ns_prefix=${1:-$CLEANUP_TARGET_NAMESPACE}
    if [[ -z "$ns_prefix" ]]; then
        return 0
    fi

    # List namespaces starting with the prefix
    local ns_list
    ns_list=$(oc get ns --no-headers -o custom-columns=NAME:.metadata.name 2>/dev/null | grep -E "^${ns_prefix}" || true)

    # Count how many we found
    local count
    count=$(echo "$ns_list" | sed '/^\s*$/d' | wc -l | tr -d ' ')
    log INFO "Found $count namespaces with prefix ${ns_prefix}"

    if [[ "$count" -eq 0 ]]; then
        log INFO "No namespaces to clean for prefix ${ns_prefix}"
        return 0
    fi

    # Delete one by one and wait for termination
    local ns
    for ns in $ns_list; do
        log INFO "Deleting namespace: $ns"
        oc delete namespace "$ns" --ignore-not-found=true || true
        log INFO "Waiting for namespace $ns to terminate..."
        # Wait up to ~4 minutes per namespace
        for i in {1..120}; do
            if ! oc get ns "$ns" &>/dev/null; then
                log INFO "Namespace $ns deleted"
                break
            fi
            sleep 2
        done
    done
}

clean_images(){
    image_list=`oc  get images | grep testImage | awk '{print $1}'`
    for image_name in $image_list
    do
        oc delete image $image_name
    done
}
# Check if oc command is available
check_prerequisites() {
    log INFO "Checking prerequisites..."
    
    if ! command -v oc &> /dev/null; then
        log ERROR "oc command not found. Please install OpenShift CLI."
        exit 1
    fi
    
    if ! command -v bc &> /dev/null; then
        log ERROR "bc command not found. Please install bc for calculations."
        exit 1
    fi
    
    if ! oc whoami &> /dev/null; then
        log ERROR "Not logged into OpenShift cluster. Please login first."
        exit 1
    fi
    
    log INFO "Prerequisites check passed"
}

# Check etcd endpoint health
check_etcd_health() {
    log INFO "Checking etcd endpoint health..."
    
    local etcd_pods
    etcd_pods=$(oc -n $ETCD_NAMESPACE get pods -lapp=etcd --no-headers| awk '{print $1}')
    
    if [[ -z "$etcd_pods" ]]; then
        log WARN "No etcd pods found with 'etcd-ip' pattern"
        return 1
    fi
    
    for pod in $etcd_pods; do
        log DEBUG "Checking health for pod: $pod"
        if oc -n "$ETCD_NAMESPACE" exec "$pod" -- etcdctl endpoint health; then
            log INFO "Pod $pod is healthy"
        else
            log WARN "Pod $pod health check failed"
        fi
    done
}

# Create namespaces in batches
create_namespaces() {
    local max_namespaces=$1
    log INFO "Creating $max_namespaces namespaces in parallel (batch size: $NAMESPACE_PARALLEL); creating 10 ConfigMaps per namespace in parallel"

    local i=1
    while (( i <= max_namespaces )); do
        local -a ns_pids=()
        local batch_end=$(( i + NAMESPACE_PARALLEL - 1 ))
        if (( batch_end > max_namespaces )); then batch_end=$max_namespaces; fi

        local ns_idx
        for ((ns_idx=i; ns_idx<=batch_end; ns_idx++)); do
            local namespace_name="project-$ns_idx"
            (
                if oc get ns "$namespace_name" &>/dev/null; then
                    log DEBUG "Namespace $namespace_name already exists"
                else
                    if oc create namespace "$namespace_name" >/dev/null 2>&1; then
                        log DEBUG "Created namespace: $namespace_name"
                    else
                        log WARN "Failed to create namespace: $namespace_name"
                    fi
                fi

                # Create 10 configmaps in parallel inside the namespace
                local -a cm_pids=()
                local cm_idx
                for cm_idx in {1..10}; do
                    (
                        # If CM exists, skip; otherwise create a small dummy CM
                        if oc -n "$namespace_name" get configmap "init-cm-$cm_idx" >/dev/null 2>&1; then
                            log DEBUG "ConfigMap init-cm-$cm_idx already exists in $namespace_name"
                            exit 0
                        fi
                        oc -n "$namespace_name" create configmap "init-cm-$cm_idx" \
                            --from-literal=dummy-key="dummy-value-$cm_idx" >/dev/null 2>&1 && \
                        log DEBUG "Created configmap init-cm-$cm_idx in $namespace_name" || \
                        log WARN "Failed to create configmap init-cm-$cm_idx in $namespace_name"
                    ) &
                    cm_pids+=("$!")
                done

                # Wait for all CM creations in this namespace
                local p
                for p in "${cm_pids[@]}"; do
                    wait "$p" 2>/dev/null || true
                done
            ) &
            ns_pids+=("$!")
        done

        # Wait for this batch of namespaces to finish
        local p
        for p in "${ns_pids[@]}"; do
            wait "$p" 2>/dev/null || true
        done

        log INFO "Processed $batch_end/$max_namespaces namespaces (batch completed)"
        i=$(( batch_end + 1 ))
    done

    log INFO "Namespace creation completed"
    date | tee -a "$LOG_FILE"
    oc adm top node | tee -a "$LOG_FILE"
    check_etcd_health
}

# Create large ConfigMaps for etcd load testing
create_configmaps() {
    local size_gb=$1
    local ns_prefix=$2

    # Derive sizes in bytes; enforce < 1MiB per ConfigMap to satisfy API limits (~1MiB total object size)
    local cm_blocks=$CONFIGMAP_PAYLOAD_SIZE
    if (( cm_blocks <= 0 )); then
        cm_blocks=1
    fi
    # Cap to 1 block (512KiB) if exceeding ~1.5M safe threshold
    local computed_bytes=$(( 512 * 1024 * cm_blocks ))
    if (( computed_bytes > 1500 * 1024 )); then
        log WARN "CONFIGMAP payload ~${computed_bytes} bytes may exceed API limits; capping to 512KiB"
        cm_blocks=1
        computed_bytes=$(( 512 * 1024 * cm_blocks ))
    fi
    local per_cm_bytes=$computed_bytes
    local total_size_bytes=$(( size_gb * 1024 * 1024 * 1024 ))
    if (( per_cm_bytes <= 0 )); then
        log ERROR "Invalid CONFIGMAP_PAYLOAD_SIZE=$CONFIGMAP_PAYLOAD_SIZE"
        return 1
    fi

    # Calculate total number of ConfigMaps (ceil division)
    local num_objects=$(( (total_size_bytes + per_cm_bytes - 1) / per_cm_bytes ))
    local per_ns=$CONFIGMAPS_PER_NS
    if (( per_ns <= 0 )); then
        per_ns=10
    fi
    # Calculate namespaces needed (ceil)
    local num_namespaces=$(( (num_objects + per_ns - 1) / per_ns ))

    log INFO "Creating ConfigMaps: target=~${size_gb}GB, per_cm=$((per_cm_bytes/1024)) KiB, total_objects=$num_objects, per_namespace=$per_ns, namespaces=$num_namespaces (prefix: $ns_prefix)"
    log WARN "This will create $num_objects ConfigMaps across $num_namespaces namespaces. Ensure this is not a production cluster!"

    # Safety pause
    log INFO "Starting in 5 seconds... Press Ctrl+C to cancel"
    sleep 5

    local created_total=0
    local idx=1
    for ((ns_idx=1; ns_idx<=num_namespaces; ns_idx++)); do
        local ns_name="${ns_prefix}-${ns_idx}"
        if ! oc get namespace "$ns_name" &>/dev/null; then
            log INFO "Creating namespace: $ns_name"
            if ! oc create namespace "$ns_name" >/dev/null; then
                log ERROR "Failed to create namespace: $ns_name"
                return 1
            fi
        fi

        local remaining=$(( num_objects - created_total ))
        local to_create=$per_ns
        if (( to_create > remaining )); then
            to_create=$remaining
        fi

        log INFO "Creating $to_create ConfigMaps in $ns_name (remaining: $remaining)"

        # Parallel creation with batch control
        local -a pids=()
        local -a names=()
        local cm
        for ((cm=1; cm<=to_create; cm++)); do
            local temp_file="/tmp/etcd_data_${ns_idx}_${cm}"
            local cm_name="etcd-load-test-cm-${idx}"
            (
                dd if=/dev/urandom of="$temp_file" bs=512k count=$cm_blocks status=none 2>/dev/null && \
                oc -n "$ns_name" create configmap "$cm_name" --from-file="data=$temp_file" >/dev/null && \
                rm -f "$temp_file" && \
                log DEBUG "Created ConfigMap: $cm_name in $ns_name" || \
                { rm -f "$temp_file"; log ERROR "Failed to create ConfigMap: $cm_name in $ns_name"; }
            ) &
            pids+=("$!")
            names+=("$cm_name")
            ((idx++))

            if (( ${#pids[@]} >= BATCH_SIZE )); then
                local i
                for i in "${!pids[@]}"; do
                    if ! wait "${pids[$i]}" 2>/dev/null; then
                        log WARN "Background creation failed for ${names[$i]} in $ns_name"
                    fi
                done
                pids=()
                names=()
            fi
        done

        # Wait remaining pids for this namespace
        if (( ${#pids[@]} > 0 )); then
            local i
            for i in "${!pids[@]}"; do
                if ! wait "${pids[$i]}" 2>/dev/null; then
                    log WARN "Background creation failed for ${names[$i]} in $ns_name"
                fi
            done
        fi

        created_total=$(( created_total + to_create ))
        log INFO "Namespace $ns_name complete ($created_total/$num_objects objects)"
    done

    log INFO "ConfigMap creation completed: $created_total/$num_objects objects across $num_namespaces namespaces"
    check_etcd_health
}

# Create multiple images
create_images() {
    # Supports 3 calling modes:
    # 1) create_images            -> uses IMAGES_PER_NS and IMAGES_MAX_NS across namespaces prefixed by MULTI_IMAGES_NS
    # 2) create_images TOTAL      -> spreads TOTAL across namespaces with IMAGES_PER_NS per ns (caps by IMAGES_MAX_NS)
    # 3) create_images PER MAX [PREFIX] -> explicit control over per-ns count, number of namespaces, and prefix
    local arg1=${1:-}
    local arg2=${2:-}
    local ns_prefix=${3:-$MULTI_IMAGES_NS}

    local per_ns
    local max_ns
    if [[ -n "$arg1" && -z "$arg2" ]]; then
        # Interpret as TOTAL images to create
        local total_images=$arg1
        per_ns=$IMAGES_PER_NS
        # ceil(total/per_ns)
        max_ns=$(( (total_images + per_ns - 1) / per_ns ))
        # Cap by IMAGES_MAX_NS if configured smaller
        if (( max_ns > IMAGES_MAX_NS )); then
            max_ns=$IMAGES_MAX_NS
        fi
    elif [[ -n "$arg1" && -n "$arg2" ]]; then
        per_ns=$arg1
        max_ns=$arg2
    else
        per_ns=$IMAGES_PER_NS
        max_ns=$IMAGES_MAX_NS
    fi

    log INFO "Creating $per_ns images per namespace across $max_ns namespaces (prefix: $ns_prefix)"

    cat<<'EOF' >/tmp/template_image.yaml
apiVersion: template.openshift.io/v1
kind: Template
metadata:
  name: img-template
objects:
  - kind: Image
    apiVersion: image.openshift.io/v1
    metadata:
      name: "${NAME}"
      creationTimestamp:
    dockerImageReference: registry.redhat.io/ubi8/ruby-27:latest
    dockerImageMetadata:
      kind: DockerImage
      apiVersion: '1.0'
      Id: ''
      ContainerConfig: {}
      Config: {}
    dockerImageLayers: []
    dockerImageMetadataVersion: '1.0'
parameters:
  - name: NAME
EOF

    # Create across namespaces
    for ((ns_idx=1; ns_idx<=max_ns; ns_idx++)); do
        local ns_name="${ns_prefix}-${ns_idx}"
        if ! oc get ns "$ns_name" &>/dev/null; then
            log INFO "Creating namespace: $ns_name"
            oc create ns "$ns_name"
        fi

        local -a pids=()
        local -a names=()
        local created=0

        for ((i=1; i<=per_ns; i++)); do
            local img_name="testImage-${ns_idx}-${i}"
            (
                oc -n "$ns_name" process -f /tmp/template_image.yaml -p NAME="$img_name" \
                | oc -n "$ns_name" create -f - 2>/dev/null && \
                log DEBUG "Created image: $img_name in $ns_name" || \
                log WARN "Failed to create image: $img_name in $ns_name"
            ) &
            pids+=("$!")
            names+=("$img_name")
            ((created++))

            if (( ${#pids[@]} >= BATCH_SIZE )); then
                local idx
                for idx in "${!pids[@]}"; do
                    if ! wait "${pids[$idx]}" 2>/dev/null; then
                        log WARN "Background image creation failed for ${names[$idx]} in $ns_name"
                    fi
                done
                pids=()
                names=()
            fi
        done

        # Wait remaining pids
        if (( ${#pids[@]} > 0 )); then
            local idx
            for idx in "${!pids[@]}"; do
                if ! wait "${pids[$idx]}" 2>/dev/null; then
                    log WARN "Background image creation failed for ${names[$idx]} in $ns_name"
                fi
            done
        fi

        log INFO "Completed images in namespace $ns_name ($ns_idx/$max_ns)"
    done

    log INFO "Image creation completed"
    check_etcd_health
}

# Create secrets in multiple namespaces
create_secrets() {

    local max_secrets=${1:-$SECRETS_PER_NS}
    local max_namespaces=${2:-$SECRETS_MAX_NS}
    local sec_namespace=${3:-$SECRETS_NS}
    log INFO "Creating $max_secrets secrets in each $sec_namespace, total $max_namespaces namespaces (parallel batches of $BATCH_SIZE)..."
    
    for ((i=1; i<=max_namespaces; i++)); do
        local namespace="$sec_namespace-$i"
        
        if oc create namespace "$namespace" 2>/dev/null; then
            log DEBUG "Created namespace: $namespace"
        else
            log DEBUG "Namespace $namespace already exists or creation failed"
        fi
        
        # Create secrets in parallel with batch concurrency control
        local -a pids=()
        local -a names=()
        local created=0
        for ((j=1; j<=max_secrets; j++)); do
            local sec_name="small-secret-$j"
            (
                oc -n "$namespace" create secret generic "$sec_name" \
                    --from-literal=key1=supersecret \
                    --from-literal=key2=topsecret 2>/dev/null && \
                log DEBUG "Created secret $sec_name in $namespace" || \
                log WARN "Failed to create secret $sec_name in $namespace"
            ) &
            pids+=("$!")
            names+=("$sec_name")
            ((created++))
            
            if (( ${#pids[@]} >= BATCH_SIZE )); then
                local idx
                for idx in "${!pids[@]}"; do
                    if ! wait "${pids[$idx]}" 2>/dev/null; then
                        log WARN "Background creation failed for ${names[$idx]} in $namespace"
                    fi
                done
                pids=()
                names=()
            fi
        done
        
        # Wait for remaining background jobs for this namespace
        if (( ${#pids[@]} > 0 )); then
            local idx
            for idx in "${!pids[@]}"; do
                if ! wait "${pids[$idx]}" 2>/dev/null; then
                    log WARN "Background creation failed for ${names[$idx]} in $namespace"
                fi
            done
        fi
        
        log INFO "Completed secrets for namespace $namespace ($i/$max_namespaces)"
    done
    
    log INFO "Secret creation completed"
}

# Create large secrets
create_large_secrets() {
    local max_secrets=${1:-$LARGE_SECRETS_PER_NS}
    local max_namespaces=${2:-$LARGE_SECRETS_NAX_NS}
    local sec_namespace=${3:-$LARGE_SECRETS_NS}
    log INFO "Creating LargeSecretsPerNS=$LARGE_SECRETS_PER_NS, LargeSecretsMaxNS=$LARGE_SECRETS_NAX_NS in $sec_namespace-x namespaces (parallel batches of $BATCH_SIZE)..."

    # Generate materials once and reuse across namespaces
    log INFO "Generating SSH key..."
    ssh-keygen -t rsa -b 4096 -f sshkey -N '' -q
    local ssh_private_key
    local ssh_public_key
    ssh_private_key=$(base64 -w 0 < sshkey)
    ssh_public_key=$(base64 -w 0 < sshkey.pub)

    log INFO "Generating token..."
    local token_value
    token_value=$(openssl rand -hex 32 | base64 -w 0)

    log INFO "Generating self-signed certificate..."
    openssl req -x509 -newkey rsa:4096 -nodes -keyout tls.key -out tls.crt \
        -days 365 -subj "/CN=mydomain.com"
    local certificate
    local private_key
    certificate=$(base64 -w 0 < tls.crt)
    private_key=$(base64 -w 0 < tls.key)

    # Iterate namespaces
    for ((ns_idx=1; ns_idx<=max_namespaces; ns_idx++)); do
        local namespace="${sec_namespace}-${ns_idx}"
        if ! oc get ns "$namespace" &>/dev/null; then
            log INFO "Creating namespace: $namespace"
            oc create ns "$namespace"
        fi

        local -a pids=()
        local -a names=()
        local created=0
        local base_name="large-secret"

        for ((i=1; i<=max_secrets; i++)); do
            local sec_name="${base_name}-${i}"
            (
                oc create secret generic "$sec_name" -n "$namespace" \
                    --from-literal=ssh-private-key="$ssh_private_key" \
                    --from-literal=ssh-public-key="$ssh_public_key" \
                    --from-literal=token="$token_value" \
                    --from-literal=tls.crt="$certificate" \
                    --from-literal=tls.key="$private_key" 2>/dev/null && \
                log DEBUG "Created large secret: $sec_name in $namespace" || \
                log WARN "Failed to create large secret: $sec_name in $namespace"
            ) &
            pids+=("$!")
            names+=("$sec_name")
            ((created++))

            if (( ${#pids[@]} >= BATCH_SIZE )); then
                local idx
                for idx in "${!pids[@]}"; do
                    if ! wait "${pids[$idx]}" 2>/dev/null; then
                        log WARN "Background creation failed for ${names[$idx]} in $namespace"
                    fi
                done
                pids=()
                names=()
            fi
        done

        # Wait any remaining
        if (( ${#pids[@]} > 0 )); then
            local idx
            for idx in "${!pids[@]}"; do
                if ! wait "${pids[$idx]}" 2>/dev/null; then
                    log WARN "Background creation failed for ${names[$idx]} in $namespace"
                fi
            done
        fi

        log INFO "Completed large secrets for namespace $namespace ($ns_idx/$max_namespaces)"
    done

    cleanup
    log INFO "Large secret creation completed"
    check_etcd_health
}

# Clone etcd-tools if needed
setup_etcd_tools() {
    if [[ ! -d "etcd-tools" ]]; then
        log INFO "Cloning etcd-tools repository..."
        if git clone https://github.com/peterducai/etcd-tools.git; then
            log INFO "etcd-tools cloned successfully"
            sleep 10
        else
            log ERROR "Failed to clone etcd-tools repository"
            return 1
        fi
    else
        log INFO "etcd-tools directory already exists"
    fi
}

# Run etcd analyzer
run_etcd_analyzer() {
    if [[ -f "etcd-tools/etcd-analyzer.sh" ]]; then
        log INFO "Running etcd analyzer..."
        date | tee -a "$LOG_FILE"
        oc adm top node | tee -a "$LOG_FILE"
        date | tee -a "$LOG_FILE"
        bash etcd-tools/etcd-analyzer.sh | tee -a "$LOG_FILE"
        date | tee -a "$LOG_FILE"
    else
        log WARN "etcd-analyzer.sh not found"
    fi
}

# Run FIO test
run_fio_test() {
    log INFO "Starting FIO test..."
    
    local master_node
    master_node=$(oc get node --no-headers | grep master | awk '{print $1}' | tail -1)
    
    if [[ -z "$master_node" ]]; then
        log ERROR "No master node found"
        return 1
    fi
    
    log INFO "Running FIO test on master node: $master_node"
    oc debug -n "$ETCD_NAMESPACE" --quiet=true "node/$master_node" -- \
        chroot host bash -c "podman run --privileged --volume /var/lib/etcd:/test quay.io/peterducai/openshift-etcd-suite:latest fio" | tee -a "$LOG_FILE"
}

# Display usage information
usage() {
    cat << EOF
Usage: $0 [OPTIONS] CASE_NUMBER

OpenShift Resource Creation and Testing Script

CASE_NUMBER:
    1    Create namespaces with configmaps
    2    Create multiple images
    3    Create secrets in multiple namespaces
    4    Create large secrets
    5    Create large ConfigMaps for etcd load testing
    6    Run etcd analyzer
    7    Run FIO test
    all  Run all cases sequentially

OPTIONS:
    -h, --help                          Show this help message
    -p, --max-namespaces NUM            Maximum number of namespaces (default: $MAX_NAMESPACES)
    -i, --max-images NUM                Maximum number of images (default: $MAX_IMAGES)
    -s, --secrets-per-namespace NUM     Secrets per namespace (default: $MAX_SECRETS_PER_NAMESPACE)
    -n, --secret-namespaces NUM         Number of secret namespaces (default: $MAX_SECRET_NAMESPACES)
    -l, --large-secrets NUM             Number of large secrets (default: $LARGE_SECRET)
    -c, --configmap-size GB             ConfigMap total size in GB (default: $CONFIGMAP_SIZE_GB)
    --configmap-namespace NAME          ConfigMap namespace (default: $CONFIGMAP_NAMESPACE)
    -b, --batch-size NUM                Batch size for operations (default: $BATCH_SIZE)
    -o, --log-file FILE                 Log file path (default: $LOG_FILE)

Environment Variables:
    MAX_NAMESPACES, MAX_IMAGES, MAX_SECRETS_PER_NAMESPACE, MAX_SECRET_NAMESPACES,
    MAX_LARGE_SECRETS, CONFIGMAP_SIZE_GB, CONFIGMAP_NAMESPACE, BATCH_SIZE, 
    ETCD_NAMESPACE, MULTI_IMAGES_NS, SECRET_NAMESPACE, LOG_FILE

Examples:
    $0 1                                # Create default number of namespaces
    $0 --max-namespaces 200 1           # Create 200 namespaces
    $0 --max-images 5000 2              # Create 5000 images
    $0 --configmap-size 10 5            # Create 10GB of ConfigMaps
    $0 all                              # Run all test cases
EOF
}

# Parse command line arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                usage
                exit 0
                ;;
            -p|--max-namespaces)
                MAX_NAMESPACES="$2"
                shift 2
                ;;
            -i|--max-images)
                MAX_IMAGES="$2"
                shift 2
                ;;
            -s|--secrets-per-namespace)
                MAX_SECRETS_PER_NAMESPACE="$2"
                shift 2
                ;;
            -n|--secret-namespaces)
                MAX_SECRET_NAMESPACES="$2"
                shift 2
                ;;
            -l|--large-secrets)
                MAX_LARGE_SECRETS="$2"
                shift 2
                ;;
            -c|--configmap-size)
                CONFIGMAP_SIZE_GB="$2"
                shift 2
                ;;
            --configmap-namespace)
                CONFIGMAP_NAMESPACE="$2"
                shift 2
                ;;
            -b|--batch-size)
                BATCH_SIZE="$2"
                shift 2
                ;;
            -o|--log-file)
                LOG_FILE="$2"
                shift 2
                ;;
            [1-7]|all)
                CASE_NUMBER="$1"
                shift
                ;;
            *)
                log ERROR "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    
    if [[ -z "${CASE_NUMBER:-}" ]]; then
        log ERROR "Case number is required"
        usage
        exit 1
    fi
}

# Main execution function
main() {
    parse_arguments "$@"
    
    log INFO "Starting OpenShift resource creation script"
    log INFO "Configuration:\n Namespaces=$MAX_NAMESPACES. Images=$MAX_IMAGES. ConfigMapSize=${CONFIGMAP_SIZE_GB}GB, BatchSize=$BATCH_SIZE"
 
    check_prerequisites
    
    case "$CASE_NUMBER" in
        1)
            create_namespaces "$MAX_NAMESPACES"
            ;;
        2)
            create_images "$IMAGES_PER_NS" $IMAGES_MAX_NS
            ;;
        3)
            create_secrets "$SECRETS_PER_NS" "$SECRETS_MAX_NS"
            ;;
        4)
            create_large_secrets $LARGE_SECRETS_PERNS $LARGE_SECRETS_MAXNUM
            ;;
        5)
            create_configmaps "$CONFIGMAP_SIZE_GB" "$CONFIGMAP_NAMESPACE"
            ;;
        6)
            setup_etcd_tools
            run_etcd_analyzer
            ;;
        7)
            run_fio_test
            ;;
        all)
            cleanup_project_namespace
            create_namespaces "$MAX_NAMESPACES"
            create_images $IMAGES_PER_NS $IMAGES_MAX_NS
            create_secrets "$SECRETS_PER_NS" "$SECRETS_MAX_NS"
            create_large_secrets $LARGE_SECRETS_PER_NS $LARGE_SECRETS_NAX_NS
            create_configmaps "$CONFIGMAP_SIZE_GB" "$CONFIGMAP_NAMESPACE"
            run_fio_test 
            setup_etcd_tools           
            # run_etcd_analyzer
            cleanup_project_namespace
            ;;
        *)
            log ERROR "Invalid case number: $CASE_NUMBER"
            usage
            exit 1
            ;;
    esac
    
    log INFO "Script execution completed successfully"
    # Attempt namespace cleanup at the very end
    cleanup_project_namespace "$CLEANUP_TARGET_NAMESPACE"
    clean_images
}

# Execute main function with all arguments
main "$@"