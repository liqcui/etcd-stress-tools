package main

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/utils/pointer"
)

// Colors for terminal output (same as etcd-stress-tools.go)
const (
	ColorRed    = "\033[0;31m"
	ColorGreen  = "\033[0;32m"
	ColorYellow = "\033[1;33m"
	ColorCyan   = "\033[0;36m"
	ColorNC     = "\033[0m" // No Color
)

// DeploymentConfig holds all configuration options
type DeploymentConfig struct {
	// Namespace settings
	TotalNamespaces       int
	NamespaceParallel     int
	NamespacePrefix       string
	NamespaceReadyTimeout time.Duration

	// Deployment settings
	DeploymentsPerNS int
	ServiceEnabled   bool

	// StatefulSet settings (OOM simulation)
	StatefulSetEnabled  bool
	StatefulSetReplicas int
	StatefulSetsPerNS   int

	// Build job settings (CI/CD simulation)
	BuildJobEnabled    bool
	BuildsPerNS        int     // Completions per job
	BuildParallelism   int     // Concurrent pods
	BuildTimeout       int     // Job timeout in seconds

	// Curl test settings
	CurlTestEnabled  bool
	CurlTestInterval int
	CurlTestCount    int

	// Resource settings
	MaxConcurrentOperations int
	CleanupOnCompletion     bool
	LogFile                 string
	LogLevel                string
}

// DeploymentTool is the main struct for deployment testing
type DeploymentTool struct {
	config    *DeploymentConfig
	clientset kubernetes.Interface
	logger    *log.Logger
}

// NewDeploymentConfig creates config from environment variables
func NewDeploymentConfig() *DeploymentConfig {
	return &DeploymentConfig{
		TotalNamespaces:         getEnvInt("TOTAL_NAMESPACES", 100),
		NamespaceParallel:       getEnvInt("NAMESPACE_PARALLEL", 10),
		NamespacePrefix:         getEnvString("NAMESPACE_PREFIX", "deploy-test"),
		NamespaceReadyTimeout:   time.Duration(getEnvInt("NAMESPACE_READY_TIMEOUT", 60)) * time.Second,
		DeploymentsPerNS:        getEnvInt("DEPLOYMENTS_PER_NS", 3),
		ServiceEnabled:          getEnvBool("SERVICE_ENABLED", false),
		StatefulSetEnabled:      getEnvBool("STATEFULSET_ENABLED", false),
		StatefulSetReplicas:     getEnvInt("STATEFULSET_REPLICAS", 3),
		StatefulSetsPerNS:       getEnvInt("STATEFULSETS_PER_NS", 1),
		BuildJobEnabled:         getEnvBool("BUILD_JOB_ENABLED", true),
		BuildsPerNS:             getEnvInt("BUILDS_PER_NS", 10),
		BuildParallelism:        getEnvInt("BUILD_PARALLELISM", 3),
		BuildTimeout:            getEnvInt("BUILD_TIMEOUT", 900),
		CurlTestEnabled:         getEnvBool("CURL_TEST_ENABLED", false),
		CurlTestInterval:        getEnvInt("CURL_TEST_INTERVAL", 30),
		CurlTestCount:           getEnvInt("CURL_TEST_COUNT", 10),
		MaxConcurrentOperations: getEnvInt("MAX_CONCURRENT_OPERATIONS", 50),
		CleanupOnCompletion:     getEnvBool("CLEANUP_ON_COMPLETION", false),
		LogFile:                 getEnvString("LOG_FILE", "deployment_test.log"),
		LogLevel:                getEnvString("LOG_LEVEL", "INFO"),
	}
}

// Helper functions for environment variables (same as etcd-stress-tools.go)
func getEnvString(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

func getEnvInt(key string, defaultValue int) int {
	if value := os.Getenv(key); value != "" {
		if intValue, err := strconv.Atoi(value); err == nil {
			return intValue
		}
	}
	return defaultValue
}

func getEnvBool(key string, defaultValue bool) bool {
	if value := os.Getenv(key); value != "" {
		if boolValue, err := strconv.ParseBool(value); err == nil {
			return boolValue
		}
	}
	return defaultValue
}

// NewDeploymentTool creates a new instance
func NewDeploymentTool(config *DeploymentConfig) (*DeploymentTool, error) {
	tool := &DeploymentTool{
		config: config,
		logger: log.New(os.Stdout, "", log.LstdFlags),
	}

	if err := tool.setupKubernetesClient(); err != nil {
		return nil, fmt.Errorf("failed to setup Kubernetes client: %w", err)
	}

	return tool, nil
}

// setupKubernetesClient initializes the Kubernetes client
func (d *DeploymentTool) setupKubernetesClient() error {
	var config *rest.Config
	var err error

	// Try in-cluster config first, then kubeconfig
	if config, err = rest.InClusterConfig(); err != nil {
		if config, err = clientcmd.BuildConfigFromFlags("", clientcmd.RecommendedHomeFile); err != nil {
			return fmt.Errorf("failed to create Kubernetes config: %w", err)
		}
		d.logInfo("Using kubeconfig file", "CONFIG")
	} else {
		d.logInfo("Using in-cluster Kubernetes configuration", "CONFIG")
	}

	// Set client configuration
	config.QPS = 50.0
	config.Burst = 100
	config.Timeout = 60 * time.Second

	d.clientset, err = kubernetes.NewForConfig(config)
	if err != nil {
		return fmt.Errorf("failed to create Kubernetes clientset: %w", err)
	}

	d.logInfo("Kubernetes client configured successfully", "CONFIG")
	return nil
}

// Logging methods (same pattern as etcd-stress-tools.go)
func (d *DeploymentTool) logInfo(message, component string) {
	timestamp := time.Now().Format("2006-01-02 15:04:05")
	fmt.Printf("%s[%s]%s %s - %s\n", ColorGreen, component, ColorNC, timestamp, message)
	d.logger.Printf("[%s] %s", component, message)
}

func (d *DeploymentTool) logWarn(message, component string) {
	timestamp := time.Now().Format("2006-01-02 15:04:05")
	fmt.Printf("%s[%s]%s %s - %s\n", ColorYellow, component, ColorNC, timestamp, message)
	d.logger.Printf("[%s] WARNING: %s", component, message)
}

func (d *DeploymentTool) logError(message, component string) {
	timestamp := time.Now().Format("2006-01-02 15:04:05")
	fmt.Printf("%s[%s]%s %s - %s\n", ColorRed, component, ColorNC, timestamp, message)
	d.logger.Printf("[%s] ERROR: %s", component, message)
}

// ensureNamespace creates namespace and waits for it to be ready
func (d *DeploymentTool) ensureNamespace(ctx context.Context, namespaceName string) error {
	// Check if namespace exists
	if _, err := d.clientset.CoreV1().Namespaces().Get(ctx, namespaceName, metav1.GetOptions{}); err == nil {
		d.logInfo(fmt.Sprintf("Namespace %s already exists", namespaceName), "NAMESPACE")
		return d.waitUntilNamespaceReady(ctx, namespaceName)
	} else if !apierrors.IsNotFound(err) {
		return fmt.Errorf("error checking namespace %s: %w", namespaceName, err)
	}

	// Create namespace
	namespace := &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{
			Name: namespaceName,
			Labels: map[string]string{
				"deployment-test": "true",
			},
		},
	}

	_, err := d.clientset.CoreV1().Namespaces().Create(ctx, namespace, metav1.CreateOptions{})
	if err != nil {
		if apierrors.IsAlreadyExists(err) {
			return d.waitUntilNamespaceReady(ctx, namespaceName)
		}
		return fmt.Errorf("failed to create namespace %s: %w", namespaceName, err)
	}

	d.logInfo(fmt.Sprintf("Created namespace %s", namespaceName), "NAMESPACE")
	return d.waitUntilNamespaceReady(ctx, namespaceName)
}

// waitUntilNamespaceReady waits for namespace to be active
func (d *DeploymentTool) waitUntilNamespaceReady(ctx context.Context, namespaceName string) error {
	timeout := time.NewTimer(d.config.NamespaceReadyTimeout)
	defer timeout.Stop()

	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-timeout.C:
			return fmt.Errorf("timeout waiting for namespace %s to be ready", namespaceName)
		case <-ticker.C:
			ns, err := d.clientset.CoreV1().Namespaces().Get(ctx, namespaceName, metav1.GetOptions{})
			if err != nil {
				if apierrors.IsNotFound(err) {
					continue
				}
				return fmt.Errorf("error checking namespace %s: %w", namespaceName, err)
			}

			if ns.Status.Phase == corev1.NamespaceActive {
				d.logInfo(fmt.Sprintf("Namespace %s is ready", namespaceName), "NAMESPACE")
				return nil
			}
		}
	}
}

// generateRandomHex generates random hex string
func generateRandomHex(length int) string {
	bytes := make([]byte, length)
	rand.Read(bytes)
	return hex.EncodeToString(bytes)
}

// createSmallConfigMap creates a small ConfigMap
func (d *DeploymentTool) createSmallConfigMap(ctx context.Context, namespace, name string) error {
	data := make(map[string]string)
	for i := 0; i < 3; i++ {
		data[fmt.Sprintf("config-%d", i)] = fmt.Sprintf("value-%d-%s", i, generateRandomHex(4))
	}

	configMap := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
			Labels: map[string]string{
				"type":            "small-configmap",
				"deployment-test": "true",
			},
		},
		Data: data,
	}

	_, err := d.clientset.CoreV1().ConfigMaps(namespace).Create(ctx, configMap, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("failed to create ConfigMap %s: %w", name, err)
	}
	return nil
}

// createSmallSecret creates a small Secret
func (d *DeploymentTool) createSmallSecret(ctx context.Context, namespace, name string) error {
	userBytes := make([]byte, 8)
	passBytes := make([]byte, 16)
	rand.Read(userBytes)
	rand.Read(passBytes)

	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
			Labels: map[string]string{
				"type":            "small-secret",
				"deployment-test": "true",
			},
		},
		Type: corev1.SecretTypeOpaque,
		Data: map[string][]byte{
			"username": []byte(base64.StdEncoding.EncodeToString([]byte(fmt.Sprintf("user-%s", hex.EncodeToString(userBytes))))),
			"password": []byte(base64.StdEncoding.EncodeToString(passBytes)),
		},
	}

	_, err := d.clientset.CoreV1().Secrets(namespace).Create(ctx, secret, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("failed to create Secret %s: %w", name, err)
	}
	return nil
}

// createService creates a ClusterIP service
func (d *DeploymentTool) createService(ctx context.Context, namespace, deploymentName string) error {
	serviceName := fmt.Sprintf("%s-svc", deploymentName)

	service := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      serviceName,
			Namespace: namespace,
			Labels: map[string]string{
				"app":             deploymentName,
				"deployment-test": "true",
			},
		},
		Spec: corev1.ServiceSpec{
			Selector: map[string]string{"app": deploymentName},
			Ports: []corev1.ServicePort{
				{
					Name:     "http",
					Port:     8080,
					Protocol: corev1.ProtocolTCP,
				},
			},
			Type: corev1.ServiceTypeClusterIP,
		},
	}

	_, err := d.clientset.CoreV1().Services(namespace).Create(ctx, service, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("failed to create Service %s: %w", serviceName, err)
	}
	return nil
}

// createDeployment creates deployment with mounted ConfigMaps and Secrets
func (d *DeploymentTool) createDeployment(ctx context.Context, namespace, name string) error {
	// Create ConfigMaps and Secrets
	cmNames := []string{fmt.Sprintf("%s-cm-0", name), fmt.Sprintf("%s-cm-1", name)}
	secretNames := []string{fmt.Sprintf("%s-secret-0", name), fmt.Sprintf("%s-secret-1", name)}

	// Create ConfigMaps
	for _, cmName := range cmNames {
		if err := d.createSmallConfigMap(ctx, namespace, cmName); err != nil {
			return err
		}
	}

	// Create Secrets
	for _, secretName := range secretNames {
		if err := d.createSmallSecret(ctx, namespace, secretName); err != nil {
			return err
		}
	}

	// Create volumes and volume mounts
	volumes := []corev1.Volume{}
	volumeMounts := []corev1.VolumeMount{}

	// Mount ConfigMaps
	for i, cmName := range cmNames {
		volumeName := fmt.Sprintf("cm-vol-%d", i)
		volumes = append(volumes, corev1.Volume{
			Name: volumeName,
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: cmName},
				},
			},
		})
		volumeMounts = append(volumeMounts, corev1.VolumeMount{
			Name:      volumeName,
			MountPath: fmt.Sprintf("/config/cm-%d", i),
		})
	}

	// Mount Secrets
	for i, secretName := range secretNames {
		volumeName := fmt.Sprintf("secret-vol-%d", i)
		volumes = append(volumes, corev1.Volume{
			Name: volumeName,
			VolumeSource: corev1.VolumeSource{
				Secret: &corev1.SecretVolumeSource{
					SecretName: secretName,
				},
			},
		})
		volumeMounts = append(volumeMounts, corev1.VolumeMount{
			Name:      volumeName,
			MountPath: fmt.Sprintf("/secrets/secret-%d", i),
		})
	}

	// Create deployment
	deployment := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
			Labels: map[string]string{
				"app":             name,
				"deployment-test": "true",
			},
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: pointer.Int32(1),
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{"app": name},
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{
						"app":             name,
						"deployment-test": "true",
					},
				},
				Spec: corev1.PodSpec{
					Containers: []corev1.Container{
						{
							Name:         "nginx",
							Image:        "quay.io/openshift-psap-qe/nginx-alpine:multiarch",
							VolumeMounts: volumeMounts,
							Ports: []corev1.ContainerPort{
								{ContainerPort: 8080},
							},
						},
					},
					Volumes: volumes,
				},
			},
		},
	}

	_, err := d.clientset.AppsV1().Deployments(namespace).Create(ctx, deployment, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("failed to create Deployment %s: %w", name, err)
	}

	// Wait for deployment to be ready
	if err := d.waitForDeploymentReady(ctx, namespace, name, 180*time.Second); err != nil {
		d.logWarn(fmt.Sprintf("Deployment %s pods not ready: %v", name, err), "DEPLOYMENT")
	}

	// Create service if enabled
	if d.config.ServiceEnabled {
		if err := d.createService(ctx, namespace, name); err != nil {
			return err
		}
	}

	return nil
}

// waitForDeploymentReady waits for deployment pods to be ready
func (d *DeploymentTool) waitForDeploymentReady(ctx context.Context, namespace, deploymentName string, timeout time.Duration) error {
	timeoutTimer := time.NewTimer(timeout)
	defer timeoutTimer.Stop()

	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-timeoutTimer.C:
			return fmt.Errorf("timeout waiting for deployment %s", deploymentName)
		case <-ticker.C:
			deployment, err := d.clientset.AppsV1().Deployments(namespace).Get(ctx, deploymentName, metav1.GetOptions{})
			if err != nil {
				if apierrors.IsNotFound(err) {
					continue
				}
				return err
			}

			specReplicas := int32(1)
			if deployment.Spec.Replicas != nil {
				specReplicas = *deployment.Spec.Replicas
			}

			if deployment.Status.ReadyReplicas == specReplicas &&
				deployment.Status.AvailableReplicas == specReplicas {
				return nil
			}
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

// createHeadlessService creates a headless service for StatefulSet
func (d *DeploymentTool) createHeadlessService(ctx context.Context, namespace, serviceName string) error {
	service := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      serviceName,
			Namespace: namespace,
			Labels: map[string]string{
				"app":             "oom-simulator",
				"deployment-test": "true",
			},
		},
		Spec: corev1.ServiceSpec{
			ClusterIP: "None", // Headless service
			Selector:  map[string]string{"app": "oom-simulator"},
			Ports: []corev1.ServicePort{
				{
					Name:     "http",
					Port:     8080,
					Protocol: corev1.ProtocolTCP,
				},
			},
		},
	}

	_, err := d.clientset.CoreV1().Services(namespace).Create(ctx, service, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("failed to create headless Service %s: %w", serviceName, err)
	}
	return nil
}

// createStatefulSet creates StatefulSet with OOM simulation
func (d *DeploymentTool) createStatefulSet(ctx context.Context, namespace, name string, replicas int) error {
	serviceName := "oom-simulator"

	// Create headless service first
	if err := d.createHeadlessService(ctx, namespace, serviceName); err != nil {
		return err
	}

	// OOM simulation Go code
	oomSimulation := `cat > /tmp/sim.go <<'EOF'
package main
import ("fmt"; "time"; "os")
func main() {
  hostname, _ := os.Hostname()
  fmt.Printf("=== StatefulSet OOM: Pod %s ===\n", hostname)
  fmt.Println("StatefulSet will recreate pod with SAME name after deletion")
  var allocs [][]byte
  for i := 0; i < 16; i++ {
    chunk := make([]byte, 5*1024*1024)
    for j := 0; j < len(chunk); j += 4096 { chunk[j] = byte(j) }
    allocs = append(allocs, chunk)
    fmt.Printf("Allocated: %d MB\n", (i+1)*5)
    time.Sleep(1 * time.Second)
  }
  select {}
}
EOF
go run /tmp/sim.go`

	statefulSet := &appsv1.StatefulSet{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
			Labels: map[string]string{
				"app":             "oom-simulator",
				"scenario":        "stateful-oom",
				"type":            "statefulset",
				"deployment-test": "true",
			},
		},
		Spec: appsv1.StatefulSetSpec{
			ServiceName: serviceName,
			Replicas:    pointer.Int32(int32(replicas)),
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{
					"app":      "oom-simulator",
					"scenario": "stateful-oom",
				},
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{
						"app":      "oom-simulator",
						"scenario": "stateful-oom",
					},
				},
				Spec: corev1.PodSpec{
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: pointer.Bool(true),
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeRuntimeDefault,
						},
					},
					RestartPolicy: corev1.RestartPolicyAlways,
					Containers: []corev1.Container{
						{
							Name:    "simulator",
							Image:   "registry.access.redhat.com/ubi9/go-toolset:latest",
							Command: []string{"/bin/bash", "-c"},
							Args:    []string{oomSimulation},
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceMemory: resource.MustParse("48Mi"),
									corev1.ResourceCPU:    resource.MustParse("100m"),
								},
								Limits: corev1.ResourceList{
									corev1.ResourceMemory: resource.MustParse("64Mi"),
									corev1.ResourceCPU:    resource.MustParse("500m"),
								},
							},
							SecurityContext: &corev1.SecurityContext{
								AllowPrivilegeEscalation: pointer.Bool(false),
								Capabilities: &corev1.Capabilities{
									Drop: []corev1.Capability{"ALL"},
								},
								RunAsNonRoot: pointer.Bool(true),
							},
							Env: []corev1.EnvVar{
								{Name: "GOCACHE", Value: "/tmp/go-build-cache"},
								{Name: "GOMODCACHE", Value: "/tmp/go-mod-cache"},
							},
						},
					},
				},
			},
		},
	}

	_, err := d.clientset.AppsV1().StatefulSets(namespace).Create(ctx, statefulSet, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("failed to create StatefulSet %s: %w", name, err)
	}

	d.logInfo(fmt.Sprintf("Created StatefulSet %s with %d replicas", name, replicas), "STATEFULSET")
	return nil
}

// createBuildJob creates a Job that simulates CI/CD build workload
func (d *DeploymentTool) createBuildJob(ctx context.Context, namespace, name string) error {
	// Realistic build script (same as Python version)
	buildScript := `#!/bin/bash
set -e

echo "================================================================"
echo "Build Job: ${JOB_NAME}"
echo "Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Namespace: ${NAMESPACE}"
echo "Pod: ${POD_NAME}"
echo "================================================================"
echo ""

# Function to simulate CPU-intensive work
cpu_intensive_work() {
    local duration=$1
    local task_name=$2
    echo "[$(date -u +%H:%M:%S)] ${task_name}..."
    
    for i in $(seq 1 $duration); do
        (dd if=/dev/zero of=/dev/null bs=1M count=10 2>/dev/null) &
        (echo "Worker $i processing..." | md5sum > /dev/null) &
        sleep 1
    done
    
    wait
    echo "[$(date -u +%H:%M:%S)] ${task_name} - DONE"
}

# Phase 1: Environment Setup
echo ""
echo "=== Phase 1: Build Environment Setup ==="
sleep $((RANDOM % 3 + 2))
echo "✓ Environment initialized"

# Phase 2: Dependency Resolution
echo ""
echo "=== Phase 2: Dependency Resolution ==="
dep_time=$((RANDOM % 10 + 5))
cpu_intensive_work $dep_time "Resolving dependencies"
echo "✓ Dependencies resolved: ${dep_time} packages"

# Phase 3: Compilation
echo ""
echo "=== Phase 3: Source Compilation ==="
compile_time=$((RANDOM % 20 + 10))
cpu_intensive_work $compile_time "Compiling source code"
echo "✓ Compilation successful: ${compile_time} source files"

# Phase 4: Unit Tests
echo ""
echo "=== Phase 4: Unit Tests ==="
test_time=$((RANDOM % 10 + 5))
cpu_intensive_work $test_time "Running unit tests"
echo "✓ Tests passed: ${test_time} test cases"

# Phase 5: Integration Tests
echo ""
echo "=== Phase 5: Integration Tests ==="
integration_time=$((RANDOM % 7 + 3))
cpu_intensive_work $integration_time "Running integration tests"
echo "✓ Integration tests passed"

# Phase 6: Packaging
echo ""
echo "=== Phase 6: Artifact Packaging ==="
package_time=$((RANDOM % 6 + 2))
sleep $package_time
echo "✓ Artifact packaged: ${JOB_NAME}.jar"

# Phase 7: Publishing
echo ""
echo "=== Phase 7: Publishing Artifacts ==="
publish_time=$((RANDOM % 3 + 2))
sleep $publish_time
echo "✓ Published to repository"

total_time=$((dep_time + compile_time + test_time + integration_time + package_time + publish_time + 10))
echo ""
echo "================================================================"
echo "BUILD SUCCESSFUL"
echo "================================================================"
echo "Total build time: ${total_time} seconds"
echo "Completed at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "================================================================"`

	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
			Labels: map[string]string{
				"app":             "build-simulator",
				"type":            "build-job",
				"deployment-test": "true",
			},
		},
		Spec: batchv1.JobSpec{
			Completions:             pointer.Int32(int32(d.config.BuildsPerNS)),
			Parallelism:             pointer.Int32(int32(d.config.BuildParallelism)),
			TTLSecondsAfterFinished: pointer.Int32(300),
			ActiveDeadlineSeconds:   pointer.Int64(int64(d.config.BuildTimeout)),
			BackoffLimit:            pointer.Int32(2),
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{
						"app":             "build-simulator",
						"type":            "build-job",
						"job-name":        name,
						"deployment-test": "true",
					},
					Annotations: map[string]string{
						"build.type": "jee-microservice",
						"build.tool": "maven",
					},
				},
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: pointer.Bool(true),
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeRuntimeDefault,
						},
					},
					Containers: []corev1.Container{
						{
							Name:    "builder",
							Image:   "registry.access.redhat.com/ubi9/ubi-minimal:latest",
							Command: []string{"/bin/bash", "-c"},
							Args:    []string{buildScript},
							Env: []corev1.EnvVar{
								{Name: "JOB_NAME", Value: name},
								{
									Name: "NAMESPACE",
									ValueFrom: &corev1.EnvVarSource{
										FieldRef: &corev1.ObjectFieldSelector{
											FieldPath: "metadata.namespace",
										},
									},
								},
								{
									Name: "POD_NAME",
									ValueFrom: &corev1.EnvVarSource{
										FieldRef: &corev1.ObjectFieldSelector{
											FieldPath: "metadata.name",
										},
									},
								},
								{Name: "MAVEN_OPTS", Value: "-Xmx400m -Xms200m"},
								{Name: "BUILD_TYPE", Value: "microservice"},
							},
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceMemory: resource.MustParse("64Mi"),
									corev1.ResourceCPU:    resource.MustParse("50m"),
								},
								Limits: corev1.ResourceList{
									corev1.ResourceMemory: resource.MustParse("256Mi"),
									corev1.ResourceCPU:    resource.MustParse("500m"),
								},
							},
							SecurityContext: &corev1.SecurityContext{
								AllowPrivilegeEscalation: pointer.Bool(false),
								Capabilities: &corev1.Capabilities{
									Drop: []corev1.Capability{"ALL"},
								},
								RunAsNonRoot: pointer.Bool(true),
							},
						},
					},
				},
			},
		},
	}

	_, err := d.clientset.BatchV1().Jobs(namespace).Create(ctx, job, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("failed to create build job %s: %w", name, err)
	}

	d.logInfo(fmt.Sprintf("Created build job %s (completions: %d, parallelism: %d)",
		name, d.config.BuildsPerNS, d.config.BuildParallelism), "BUILD_JOB")
	return nil
}

// createNamespaceResources creates all resources for a single namespace
func (d *DeploymentTool) createNamespaceResources(ctx context.Context, namespaceIdx int) (success, errors int) {
	namespaceName := fmt.Sprintf("%s-%d", d.config.NamespacePrefix, namespaceIdx)

	// Ensure namespace exists
	if err := d.ensureNamespace(ctx, namespaceName); err != nil {
		d.logError(fmt.Sprintf("Failed to create namespace %s: %v", namespaceName, err), "NAMESPACE")
		return 0, 1
	}

	semaphore := make(chan struct{}, d.config.MaxConcurrentOperations)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var successCount, errorCount int

	// Create deployments
	for i := 0; i < d.config.DeploymentsPerNS; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			deployName := fmt.Sprintf("deploy-%d", index+1)
			if err := d.createDeployment(ctx, namespaceName, deployName); err != nil {
				mu.Lock()
				errorCount++
				mu.Unlock()
				d.logWarn(fmt.Sprintf("Failed to create deployment %s: %v", deployName, err), "DEPLOYMENT")
			} else {
				mu.Lock()
				successCount++
				mu.Unlock()
			}
		}(i)
	}

	// Create StatefulSets if enabled
	if d.config.StatefulSetEnabled {
		for i := 0; i < d.config.StatefulSetsPerNS; i++ {
			wg.Add(1)
			go func(index int) {
				defer wg.Done()
				semaphore <- struct{}{}
				defer func() { <-semaphore }()

				stsName := fmt.Sprintf("stateful-oom-%d", index+1)
				if err := d.createStatefulSet(ctx, namespaceName, stsName, d.config.StatefulSetReplicas); err != nil {
					mu.Lock()
					errorCount++
					mu.Unlock()
					d.logWarn(fmt.Sprintf("Failed to create StatefulSet %s: %v", stsName, err), "STATEFULSET")
				} else {
					mu.Lock()
					successCount++
					mu.Unlock()
				}
			}(i)
		}
	}

	wg.Wait()

	// Create build jobs if enabled (matches must-gather pattern)
	if d.config.BuildJobEnabled {
		d.createBuildJobsForNamespace(ctx, namespaceName, namespaceIdx, &successCount, &errorCount)
	}

	d.logInfo(fmt.Sprintf("Created %d resources in %s (Success: %d, Errors: %d)",
		successCount, namespaceName, successCount, errorCount), "NAMESPACE")

	return successCount, errorCount
}

// createBuildJobsForNamespace creates realistic build jobs (matches Python implementation)
func (d *DeploymentTool) createBuildJobsForNamespace(ctx context.Context, namespace string, namespaceIdx int, successCount, errorCount *int) {
	// Service types and components (matches must-gather pattern)
	serviceTypes := []string{"api", "worker", "scheduler", "processor", "backend"}
	components := []string{
		"authentication", "authorization", "datastore", "cache",
		"messaging", "notification", "analytics", "reporting",
		"search", "indexer", "transformer", "validator",
		"orchestrator", "monitor", "logger", "metrics",
	}

	// Create 20 build jobs per namespace (matches must-gather observation)
	numBuilds := min(len(components), 20)
	
	var wg sync.WaitGroup
	var mu sync.Mutex
	semaphore := make(chan struct{}, d.config.MaxConcurrentOperations)

	for i := 0; i < numBuilds; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			serviceType := serviceTypes[index%len(serviceTypes)]
			component := components[index]
			major := (namespaceIdx % 3) + 1
			minor := index % 50
			patch := (namespaceIdx + index) % 10
			version := fmt.Sprintf("%d-%d-%d", major, minor, patch)

			// Job naming pattern: build-{service}-{component}-{version}-s-1
			jobName := fmt.Sprintf("build-%s-%s-%s-s-1", serviceType, component, version)

			if err := d.createBuildJob(ctx, namespace, jobName); err != nil {
				mu.Lock()
				*errorCount++
				mu.Unlock()
			} else {
				mu.Lock()
				*successCount++
				mu.Unlock()
			}

			// Stagger build creation (matches Python implementation)
			if index > 0 && index%d.config.BuildParallelism == 0 {
				time.Sleep(1 * time.Second)
			}
		}(i)
	}

	wg.Wait()
	d.logInfo(fmt.Sprintf("Created %d build jobs in %s (pattern matches must-gather: 20+ builds)", numBuilds, namespace), "BUILD_JOB")
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// createAllNamespacesAndResources creates all namespaces and resources
func (d *DeploymentTool) createAllNamespacesAndResources(ctx context.Context) {
	d.logInfo(fmt.Sprintf("Creating %d namespaces with resources", d.config.TotalNamespaces), "MAIN")
	startTime := time.Now()

	semaphore := make(chan struct{}, d.config.NamespaceParallel)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var totalSuccess, totalErrors int

	for i := 1; i <= d.config.TotalNamespaces; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			success, errors := d.createNamespaceResources(ctx, idx)

			mu.Lock()
			totalSuccess += success
			totalErrors += errors
			mu.Unlock()
		}(i)
	}

	wg.Wait()

	elapsedTime := time.Since(startTime)
	d.logInfo(fmt.Sprintf("Resource creation completed in %.2fs: Success: %d, Errors: %d",
		elapsedTime.Seconds(), totalSuccess, totalErrors), "COMPLETE")
}

// cleanupAllResources cleans up all created resources
func (d *DeploymentTool) cleanupAllResources(ctx context.Context) {
	if !d.config.CleanupOnCompletion {
		d.logInfo("Cleanup disabled", "CLEANUP")
		return
	}

	d.logInfo("Starting cleanup", "CLEANUP")

	namespaces, err := d.clientset.CoreV1().Namespaces().List(ctx, metav1.ListOptions{
		LabelSelector: "deployment-test=true",
	})
	if err != nil {
		d.logError(fmt.Sprintf("Failed to list namespaces: %v", err), "CLEANUP")
		return
	}

	d.logInfo(fmt.Sprintf("Found %d namespaces to clean up", len(namespaces.Items)), "CLEANUP")

	semaphore := make(chan struct{}, d.config.NamespaceParallel)
	var wg sync.WaitGroup
	var successful int
	var mu sync.Mutex

	for _, ns := range namespaces.Items {
		wg.Add(1)
		go func(nsName string) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			err := d.clientset.CoreV1().Namespaces().Delete(ctx, nsName, metav1.DeleteOptions{
				GracePeriodSeconds: pointer.Int64(0),
			})
			if err == nil || apierrors.IsNotFound(err) {
				mu.Lock()
				successful++
				mu.Unlock()
			} else {
				d.logWarn(fmt.Sprintf("Failed to delete namespace %s: %v", nsName, err), "CLEANUP")
			}
		}(ns.Name)
	}

	wg.Wait()
	d.logInfo(fmt.Sprintf("Deleted %d/%d namespaces", successful, len(namespaces.Items)), "CLEANUP")
}

// runTest runs the deployment test
func (d *DeploymentTool) runTest(ctx context.Context) error {
	startTime := time.Now()
	d.logInfo("Starting deployment test", "MAIN")

	d.createAllNamespacesAndResources(ctx)

	if d.config.CleanupOnCompletion {
		d.cleanupAllResources(ctx)
	}

	totalTime := time.Since(startTime)
	d.logInfo(fmt.Sprintf("Total execution time: %.2f seconds", totalTime.Seconds()), "MAIN")

	return nil
}

func main() {
	var (
		totalNamespaces     = flag.Int("total-namespaces", 0, "Total number of namespaces to create")
		namespaceParallel   = flag.Int("namespace-parallel", 0, "Number of namespaces to process in parallel")
		namespacePrefix     = flag.String("namespace-prefix", "", "Prefix for namespace names")
		deploymentsPerNS    = flag.Int("deployments-per-ns", 0, "Number of deployments per namespace")
		serviceEnabled      = flag.Bool("service-enabled", false, "Enable service creation")
		statefulSetEnabled  = flag.Bool("statefulset-enabled", false, "Enable StatefulSet creation")
		statefulSetReplicas = flag.Int("statefulset-replicas", 0, "Number of replicas per StatefulSet")
		statefulSetsPerNS   = flag.Int("statefulsets-per-ns", 0, "Number of StatefulSets per namespace")
		buildJobEnabled     = flag.Bool("build-job-enabled", false, "Enable build job creation")
		buildsPerNS         = flag.Int("builds-per-ns", 0, "Number of build completions per job")
		buildParallelism    = flag.Int("build-parallelism", 0, "Number of concurrent build pods")
		buildTimeout        = flag.Int("build-timeout", 0, "Build job timeout in seconds")
		cleanup             = flag.Bool("cleanup", false, "Enable cleanup after completion")
		help                = flag.Bool("help", false, "Show help message")
	)

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, `Kubernetes Deployment Testing Tool (Go version)

This tool creates comprehensive Kubernetes deployments, StatefulSets, and build jobs to test cluster behavior.

Based on must-gather analysis showing:
- 20+ build jobs per namespace
- Realistic build phases with CPU-intensive work  
- Process overhead: 60-85 processes per build pod
- StatefulSet with OOM simulation

Usage:
`)
		flag.PrintDefaults()
	}

	flag.Parse()

	if *help {
		flag.Usage()
		return
	}

	config := NewDeploymentConfig()

	// Override config with CLI arguments
	if *totalNamespaces != 0 {
		config.TotalNamespaces = *totalNamespaces
	}
	if *namespaceParallel != 0 {
		config.NamespaceParallel = *namespaceParallel
	}
	if *namespacePrefix != "" {
		config.NamespacePrefix = *namespacePrefix
	}
	if *deploymentsPerNS != 0 {
		config.DeploymentsPerNS = *deploymentsPerNS
	}
	if *serviceEnabled {
		config.ServiceEnabled = true
	}
	if *statefulSetEnabled {
		config.StatefulSetEnabled = true
	}
	if *statefulSetReplicas != 0 {
		config.StatefulSetReplicas = *statefulSetReplicas
	}
	if *statefulSetsPerNS != 0 {
		config.StatefulSetsPerNS = *statefulSetsPerNS
	}
	if *buildJobEnabled {
		config.BuildJobEnabled = true
	}
	if *buildsPerNS != 0 {
		config.BuildsPerNS = *buildsPerNS
	}
	if *buildParallelism != 0 {
		config.BuildParallelism = *buildParallelism
	}
	if *buildTimeout != 0 {
		config.BuildTimeout = *buildTimeout
	}
	if *cleanup {
		config.CleanupOnCompletion = true
	}

	tool, err := NewDeploymentTool(config)
	if err != nil {
		log.Fatalf("Failed to create deployment tool: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle interrupts
	go func() {
		c := make(chan os.Signal, 1)
		signal.Notify(c, os.Interrupt, syscall.SIGTERM)
		<-c
		tool.logWarn("Test interrupted by user", "MAIN")
		cancel()
		os.Exit(1)
	}()

	if err := tool.runTest(ctx); err != nil {
		tool.logError(fmt.Sprintf("Test failed: %v", err), "MAIN")
		os.Exit(1)
	}
}
