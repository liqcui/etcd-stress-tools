#!/bin/bash

# Set strict error handling
set -euo pipefail

# Script configuration and defaults
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/etcd-load-tools.log}"
readonly ETCD_NAMESPACE="${ETCD_NAMESPACE:-openshift-etcd}"
readonly ETCD_TOOLS_REPO="https://github.com/peterducai/etcd-tools.git"
readonly ETCD_TOOLS_DIR="${SCRIPT_DIR}/etcd-tools"

# Test configurations
readonly FIO_CONFIG=(
    "quay.io/peterducai/openshift-etcd-suite:latest"
    "fio"
    "/test"
    "--privileged"
)

readonly ETCD_PERF_CONFIG=(
    "quay.io/openshift-scale/etcd-perf"
    ""
    "/var/lib/etcd"
    ""
)

# Initialize variables with defaults
CASE_NUMBER=""

# Simple logging function
log() {
    local level="$1"
    shift
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    case "$level" in
        ERROR)
            echo "[$timestamp] [ERROR] $*" | tee -a "$LOG_FILE" >&2
            ;;
        *)
            echo "[$timestamp] [$level] $*" | tee -a "$LOG_FILE"
            ;;
    esac
}

# Check prerequisites
check_prerequisites() {
    local missing=()
    for cmd in oc git bash; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        log ERROR "Missing commands: ${missing[*]}"
        return 1
    fi
    
    if ! oc whoami >/dev/null 2>&1; then
        log ERROR "Not logged into OpenShift. Run 'oc login' first"
        return 1
    fi
}

# Setup etcd-tools repository
setup_etcd_tools() {
    if [[ -d "$ETCD_TOOLS_DIR" ]]; then
        log INFO "Updating etcd-tools repository"
        (cd "$ETCD_TOOLS_DIR" && git pull origin main 2>/dev/null) || log WARN "Failed to update repository"
    else
        log INFO "Cloning etcd-tools repository"
        git clone "$ETCD_TOOLS_REPO" "$ETCD_TOOLS_DIR" || return 1
    fi
    
    local analyzer="$ETCD_TOOLS_DIR/etcd-analyzer.sh"
    [[ -f "$analyzer" ]] || { log ERROR "etcd-analyzer.sh not found"; return 1; }
    chmod +x "$analyzer"
}

# Get control plane nodes (master nodes)
get_master_nodes() {
    local nodes
    
    # Prefer control-plane label (newer clusters)
    nodes=$(oc get nodes -l node-role.kubernetes.io/control-plane -o name 2>/dev/null | sed 's|node/||') || true
    
    # Fallback to legacy master label (older clusters)
    if [[ -z "$nodes" ]]; then
        nodes=$(oc get nodes -l node-role.kubernetes.io/master -o name 2>/dev/null | sed 's|node/||') || true
    fi
    
    # If still no nodes found, fail fast
    if [[ -z "$nodes" ]]; then
        log ERROR "No control plane nodes found" >/dev/null
        return 1
    fi
    
    [[ -n "$nodes" ]] || { log ERROR "No control plane nodes found"; return 1; }
    
    # Log discovered nodes only to file; keep stdout clean for the caller
    log INFO "Found control plane nodes:" >/dev/null
    echo "$nodes" | while read -r node; do
        log INFO "  - $node" >/dev/null
    done
    
    echo "$nodes"
}

# Generic test runner
run_container_test() {
    local test_name="$1"
    local image="$2"
    local command="$3"
    local mount_path="$4"
    local extra_flags="$5"
    local node_mode="$6"  # "single" or "all"
    
    log INFO "Starting $test_name test..."
    
    local master_nodes
    master_nodes=$(get_master_nodes) || return 1
    
    if [[ "$node_mode" == "single" ]]; then
        master_nodes=$(echo "$master_nodes" | head -1)
    fi
    
    local failed_count=0
    local total_count=0
    
    while IFS= read -r node; do
        ((total_count++))
        log INFO "Running $test_name on node: $node"
        
        {
            echo "=== $test_name Test - Node: $node ==="
            date
        } | tee -a "$LOG_FILE"
        
        local cmd="podman run --rm --volume /var/lib/etcd:$mount_path:Z"
        [[ -n "$extra_flags" ]] && cmd="$cmd $extra_flags"
        cmd="$cmd $image"
        [[ -n "$command" ]] && cmd="$cmd $command"
        
        if oc debug -n "$ETCD_NAMESPACE" --quiet=true "node/$node" -- \
            chroot host bash -c "$cmd" 2>&1 | tee -a "$LOG_FILE"; then
            log INFO "$test_name completed on $node"
        else
            log ERROR "$test_name failed on $node"
            ((failed_count++))
        fi
        
        echo "" | tee -a "$LOG_FILE"
        [[ "$node_mode" == "all" ]] && sleep 2  # Brief delay between nodes
        
    done <<< "$master_nodes"
    
    log INFO "$test_name Summary: $((total_count - failed_count))/$total_count nodes successful"
    [[ $failed_count -eq 0 ]] || return 1
}

# Run etcd analyzer
run_etcd_analyzer() {
    log INFO "Running etcd analyzer..."
    
    {
        echo "=== ETCD Analyzer ==="
        date
        oc adm top node 2>/dev/null || echo "Node top unavailable"
    } | tee -a "$LOG_FILE"
    
    bash "$ETCD_TOOLS_DIR/etcd-analyzer.sh" 2>&1 | tee -a "$LOG_FILE" || return 1
    echo "" | tee -a "$LOG_FILE"
}

# Cleanup function
cleanup_project_namespace() {
    log INFO "Cleanup completed"
}

# Display usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS] <case_number|all>

ETCD Load Testing Tool

OPTIONS:
    -h, --help          Show help
    -l, --log-file FILE Set log file path
    
CASES:
    1                   FIO test (single master node)
    2                   ETCD performance test (all master nodes)
    all                 All tests

Examples:
    $0 1                # FIO test only
    $0 2                # ETCD performance test only
    $0 all              # All tests

EOF
}

# Parse arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help) usage; exit 0 ;;
            -l|--log-file)
                [[ -n "${2:-}" ]] || { log ERROR "Log file path required"; exit 1; }
                LOG_FILE="$2"
                shift 2
                ;;
            1|2|all)
                [[ -z "$CASE_NUMBER" ]] || { log ERROR "Case already specified"; exit 1; }
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
    
    [[ -n "$CASE_NUMBER" ]] || { log ERROR "Case number required"; usage; exit 1; }
}

# Main execution
main() {
    # Parse arguments first so LOG_FILE/CASE_NUMBER are finalized
    parse_arguments "$@"

    # Setup
    mkdir -p "$(dirname "$LOG_FILE")"
    {
        echo "=== ETCD Load Tools Started ==="
        date
        echo "Case: $CASE_NUMBER"
        echo "Log: $LOG_FILE"
        echo ""
    } | tee "$LOG_FILE"
    check_prerequisites || exit 1
    
    # Execute tests
    case "$CASE_NUMBER" in
        1)
            run_container_test "FIO" "${FIO_CONFIG[@]}" "single"
            ;;
        2)
            run_container_test "ETCD Performance" "${ETCD_PERF_CONFIG[@]}" "all"
            ;;
        all)
            setup_etcd_tools || exit 1
            run_container_test "FIO" "${FIO_CONFIG[@]}" "single" || exit 1
            run_container_test "ETCD Performance" "${ETCD_PERF_CONFIG[@]}" "all" || exit 1
            run_etcd_analyzer || exit 1
            cleanup_project_namespace
            ;;
        *)
            log ERROR "Invalid case: $CASE_NUMBER"
            exit 1
            ;;
    esac
    
    log INFO "Script completed successfully"
    echo "" | tee -a "$LOG_FILE"
}

# Execute
main "$@"