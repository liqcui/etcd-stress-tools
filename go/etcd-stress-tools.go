package main

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"flag"
	"fmt"
	"log"
	"math/big"
	mathrand "math/rand"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/utils/pointer"
)

// Colors for terminal output
const (
	ColorRed    = "\033[0;31m"
	ColorGreen  = "\033[0;32m"
	ColorYellow = "\033[1;33m"
	ColorBlue   = "\033[0;34m"
	ColorCyan   = "\033[0;36m"
	ColorPurple = "\033[0;35m"
	ColorNC     = "\033[0m" // No Color
)

// Config holds all configuration options
type Config struct {
	TotalNamespaces            int
	NamespaceParallel          int
	NamespacePrefix            string
	SmallConfigMapsPerNS       int
	LargeConfigMapsPerNS       int
	LargeConfigMapSizeMB       float64
	TotalLargeConfigMapLimitGB float64
	SmallSecretsPerNS          int
	LargeSecretsPerNS          int
	CreateDeployments          bool
	DeploymentsPerNS           int
	CreateEgressFirewall       bool
	CreateNetworkPolicies      bool
	CreateANPBANP              bool
	CreateImages               bool
	MaxConcurrentOperations    int
	NamespaceReadyTimeout      time.Duration
	ResourceRetryCount         int
	ResourceRetryDelay         time.Duration
	LogLevel                   string
	CleanupOnCompletion        bool
	ListOnly                   bool
}

// ResourceStats holds statistics for listing operations
type ResourceStats struct {
	TotalPods       int
	TotalConfigMaps int
	SmallConfigMaps int
	LargeConfigMaps int
	TotalSecrets    int
	SmallSecrets    int
	LargeSecrets    int
	TotalNamespaces int
	Details         map[string]NamespaceDetails
}

// NamespaceDetails holds per-namespace resource details
type NamespaceDetails struct {
	PodCount           int
	ConfigMapCount     int
	SmallConfigMaps    int
	LargeConfigMaps    int
	SecretCount        int
	SmallSecrets       int
	LargeSecrets       int
	DeploymentCount    int
	NetworkPolicyCount int
}

// EtcdStressTools is the main struct for the stress testing tool
type EtcdStressTools struct {
	config        *Config
	clientset     kubernetes.Interface
	dynamicClient dynamic.Interface
	tenantLabels  []string
	logger        *log.Logger
}

// NewConfig creates a new Config with defaults from environment variables
func NewConfig() *Config {
	config := &Config{
		TotalNamespaces:            getEnvInt("TOTAL_NAMESPACES", 100),
		NamespaceParallel:          getEnvInt("NAMESPACE_PARALLEL", 5),
		NamespacePrefix:            getEnvString("NAMESPACE_PREFIX", "stress-test"),
		SmallConfigMapsPerNS:       10,
		LargeConfigMapsPerNS:       3,
		LargeConfigMapSizeMB:       1.0,
		TotalLargeConfigMapLimitGB: getEnvFloat("TOTAL_LARGE_CONFIGMAP_LIMIT_GB", 6.0),
		SmallSecretsPerNS:          10,
		LargeSecretsPerNS:          10,
		CreateDeployments:          getEnvBool("CREATE_DEPLOYMENTS", true),
		DeploymentsPerNS:           3,
		CreateEgressFirewall:       getEnvBool("CREATE_EGRESS_FIREWALL", true),
		CreateNetworkPolicies:      getEnvBool("CREATE_NETWORK_POLICIES", true),
		CreateANPBANP:              getEnvBool("CREATE_ANP_BANP", true),
		CreateImages:               getEnvBool("CREATE_IMAGES", true),
		MaxConcurrentOperations:    getEnvInt("MAX_CONCURRENT_OPERATIONS", 20),
		NamespaceReadyTimeout:      time.Duration(getEnvInt("NAMESPACE_READY_TIMEOUT", 60)) * time.Second,
		ResourceRetryCount:         getEnvInt("RESOURCE_RETRY_COUNT", 5),
		ResourceRetryDelay:         time.Duration(getEnvFloat("RESOURCE_RETRY_DELAY", 2.0)*1000) * time.Millisecond,
		LogLevel:                   getEnvString("LOG_LEVEL", "INFO"),
		CleanupOnCompletion:        getEnvBool("CLEANUP_ON_COMPLETION", false),
		ListOnly:                   false,
	}
	return config
}

// Helper functions for environment variables
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

func getEnvFloat(key string, defaultValue float64) float64 {
	if value := os.Getenv(key); value != "" {
		if floatValue, err := strconv.ParseFloat(value, 64); err == nil {
			return floatValue
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

// NewEtcdStressTools creates a new instance of the stress testing tool
func NewEtcdStressTools(config *Config) (*EtcdStressTools, error) {
	tool := &EtcdStressTools{
		config:       config,
		tenantLabels: []string{"tenant1", "tenant2", "tenant3"},
		logger:       log.New(os.Stdout, "", log.LstdFlags),
	}

	if err := tool.setupKubernetesClients(); err != nil {
		return nil, fmt.Errorf("failed to setup Kubernetes clients: %w", err)
	}

	return tool, nil
}

// setupKubernetesClients initializes the Kubernetes client configurations
func (e *EtcdStressTools) setupKubernetesClients() error {
	var config *rest.Config
	var err error

	if config, err = rest.InClusterConfig(); err != nil {
		if config, err = clientcmd.BuildConfigFromFlags("", clientcmd.RecommendedHomeFile); err != nil {
			return fmt.Errorf("failed to create Kubernetes config: %w", err)
		}
		e.logInfo("Using kubeconfig file", "MAIN")
	} else {
		e.logInfo("Using in-cluster Kubernetes configuration", "MAIN")
	}

	config.QPS = 50.0
	config.Burst = 100
	config.Timeout = 60 * time.Second

	config.Dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		d := &net.Dialer{
			Timeout:   30 * time.Second,
			KeepAlive: 30 * time.Second,
		}
		return d.DialContext(ctx, network, address)
	}

	e.clientset, err = kubernetes.NewForConfig(config)
	if err != nil {
		return fmt.Errorf("failed to create Kubernetes clientset: %w", err)
	}

	e.dynamicClient, err = dynamic.NewForConfig(config)
	if err != nil {
		return fmt.Errorf("failed to create dynamic client: %w", err)
	}

	e.logInfo("Kubernetes clients configured successfully", "MAIN")
	return nil
}

// Logging methods
func (e *EtcdStressTools) logInfo(message, component string) {
	timestamp := time.Now().Format("2006-01-02 15:04:05")
	fmt.Printf("%s[%s]%s %s - %s\n", ColorGreen, component, ColorNC, timestamp, message)
	e.logger.Printf("[%s] %s", component, message)
}

func (e *EtcdStressTools) logWarn(message, component string) {
	timestamp := time.Now().Format("2006-01-02 15:04:05")
	fmt.Printf("%s[%s]%s %s - %s\n", ColorYellow, component, ColorNC, timestamp, message)
	e.logger.Printf("[%s] WARNING: %s", component, message)
}

func (e *EtcdStressTools) logError(message, component string) {
	timestamp := time.Now().Format("2006-01-02 15:04:05")
	fmt.Printf("%s[%s]%s %s - %s\n", ColorRed, component, ColorNC, timestamp, message)
	e.logger.Printf("[%s] ERROR: %s", component, message)
}

// ensureNamespace creates a namespace with custom labels and waits for it to be ready
func (e *EtcdStressTools) ensureNamespace(ctx context.Context, namespaceName string, labels map[string]string) error {
	if _, err := e.clientset.CoreV1().Namespaces().Get(ctx, namespaceName, metav1.GetOptions{}); err == nil {
		e.logInfo(fmt.Sprintf("Namespace %s already exists", namespaceName), "NAMESPACE")
		return e.waitUntilNamespaceReady(ctx, namespaceName)
	} else if !apierrors.IsNotFound(err) {
		return fmt.Errorf("error checking namespace %s: %w", namespaceName, err)
	}

	if labels == nil {
		labels = make(map[string]string)
	}
	labels["stress-test"] = "true"
	labels["created-by"] = "etcd-stress-tool"

	namespace := &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{
			Name:   namespaceName,
			Labels: labels,
		},
	}

	_, err := e.clientset.CoreV1().Namespaces().Create(ctx, namespace, metav1.CreateOptions{})
	if err != nil {
		if apierrors.IsAlreadyExists(err) {
			return e.waitUntilNamespaceReady(ctx, namespaceName)
		}
		return fmt.Errorf("failed to create namespace %s: %w", namespaceName, err)
	}

	e.logInfo(fmt.Sprintf("Created namespace %s", namespaceName), "NAMESPACE")
	return e.waitUntilNamespaceReady(ctx, namespaceName)
}

// waitUntilNamespaceReady waits until the namespace is fully ready for resource creation
func (e *EtcdStressTools) waitUntilNamespaceReady(ctx context.Context, namespaceName string) error {
	timeout := time.NewTimer(e.config.NamespaceReadyTimeout)
	defer timeout.Stop()

	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-timeout.C:
			return fmt.Errorf("timeout waiting for namespace %s to be ready", namespaceName)
		case <-ticker.C:
			ns, err := e.clientset.CoreV1().Namespaces().Get(ctx, namespaceName, metav1.GetOptions{})
			if err != nil {
				if apierrors.IsNotFound(err) {
					continue
				}
				return fmt.Errorf("error checking namespace %s: %w", namespaceName, err)
			}

			if ns.Status.Phase != corev1.NamespaceActive {
				continue
			}

			testCMName := fmt.Sprintf("test-readiness-%d", time.Now().Unix())
			testCM := &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      testCMName,
					Namespace: namespaceName,
				},
				Data: map[string]string{"test": "readiness"},
			}

			if _, err := e.clientset.CoreV1().ConfigMaps(namespaceName).Create(ctx, testCM, metav1.CreateOptions{}); err != nil {
				if apierrors.IsNotFound(err) {
					continue
				}
				e.logWarn(fmt.Sprintf("Test resource creation failed in %s, but namespace appears ready: %v", namespaceName, err), "NAMESPACE")
				return nil
			}

			e.clientset.CoreV1().ConfigMaps(namespaceName).Delete(ctx, testCMName, metav1.DeleteOptions{})
			e.logInfo(fmt.Sprintf("Namespace %s is ready for resource creation", namespaceName), "NAMESPACE")
			return nil
		}
	}
}

// retryResourceOperation retries a resource operation with exponential backoff
func (e *EtcdStressTools) retryResourceOperation(ctx context.Context, operation func() error) error {
	var lastErr error

	for attempt := 0; attempt <= e.config.ResourceRetryCount; attempt++ {
		if err := operation(); err != nil {
			lastErr = err

			if apierrors.IsServerTimeout(err) ||
				apierrors.IsTooManyRequests(err) ||
				apierrors.IsServiceUnavailable(err) ||
				isConnectionError(err) {
				if attempt < e.config.ResourceRetryCount {
					baseDelay := e.config.ResourceRetryDelay * time.Duration(1<<uint(attempt))
					jitter := time.Duration(mathrand.Int63n(int64(baseDelay / 2)))
					waitTime := baseDelay + jitter

					e.logWarn(fmt.Sprintf("Retrying operation after %v (attempt %d/%d): %v",
						waitTime, attempt+1, e.config.ResourceRetryCount+1, err), "RETRY")

					select {
					case <-time.After(waitTime):
						continue
					case <-ctx.Done():
						return ctx.Err()
					}
				}
			} else if apierrors.IsNotFound(err) && strings.Contains(err.Error(), "not found") {
				if attempt < e.config.ResourceRetryCount {
					waitTime := e.config.ResourceRetryDelay * time.Duration(1<<uint(attempt))
					select {
					case <-time.After(waitTime):
						continue
					case <-ctx.Done():
						return ctx.Err()
					}
				}
			} else if apierrors.IsAlreadyExists(err) {
				return nil
			} else {
				break
			}
		} else {
			return nil
		}
	}

	return lastErr
}

// isConnectionError checks if the error is a connection-related error
func isConnectionError(err error) bool {
	if err == nil {
		return false
	}
	errStr := strings.ToLower(err.Error())
	return strings.Contains(errStr, "connection reset by peer") ||
		strings.Contains(errStr, "connection refused") ||
		strings.Contains(errStr, "timeout") ||
		strings.Contains(errStr, "eof") ||
		strings.Contains(errStr, "broken pipe") ||
		strings.Contains(errStr, "goaway") ||
		strings.Contains(errStr, "http2") ||
		strings.Contains(errStr, "unexpected error when reading response body")
}

// generateRandomData generates random hex data of specified size
func generateRandomData(sizeBytes int) string {
	bytes := make([]byte, sizeBytes/2)
	rand.Read(bytes)
	return hex.EncodeToString(bytes)
}

// createSmallConfigMaps creates 10 small ConfigMaps in namespace
func (e *EtcdStressTools) createSmallConfigMaps(ctx context.Context, namespace string) error {
	semaphore := make(chan struct{}, e.config.MaxConcurrentOperations)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var errors []error

	for i := 0; i < e.config.SmallConfigMapsPerNS; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			name := fmt.Sprintf("small-cm-%d", index+1)
			data := make(map[string]string)

			for j := 0; j < 5; j++ {
				data[fmt.Sprintf("config-%d", j)] = fmt.Sprintf("small-config-value-%d-%s", j, generateRandomData(16))
			}

			configMap := &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      name,
					Namespace: namespace,
					Labels: map[string]string{
						"type":        "small-configmap",
						"stress-test": "true",
					},
				},
				Data: data,
			}

			err := e.retryResourceOperation(ctx, func() error {
				_, err := e.clientset.CoreV1().ConfigMaps(namespace).Create(ctx, configMap, metav1.CreateOptions{})
				return err
			})

			if err != nil {
				mu.Lock()
				errors = append(errors, fmt.Errorf("failed to create small ConfigMap %s in %s: %w", name, namespace, err))
				mu.Unlock()
				e.logWarn(fmt.Sprintf("Failed to create small ConfigMap %s in %s: %v", name, namespace, err), "CONFIGMAP")
			}
		}(i)
	}

	wg.Wait()

	if len(errors) > 0 {
		return fmt.Errorf("failed to create %d small ConfigMaps", len(errors))
	}

	return nil
}

// createLargeConfigMaps creates 3 large ConfigMaps with 1MB size each
func (e *EtcdStressTools) createLargeConfigMaps(ctx context.Context, namespace string) error {
	totalLargeConfigMaps := e.config.TotalNamespaces * e.config.LargeConfigMapsPerNS
	totalSizeGB := float64(totalLargeConfigMaps) * e.config.LargeConfigMapSizeMB / 1024

	if totalSizeGB > e.config.TotalLargeConfigMapLimitGB {
		e.logWarn(fmt.Sprintf("Skipping large ConfigMap creation - would exceed %.1fGB limit", e.config.TotalLargeConfigMapLimitGB), "CONFIGMAP")
		return nil
	}

	semaphore := make(chan struct{}, e.config.MaxConcurrentOperations)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var errors []error

	for i := 0; i < e.config.LargeConfigMapsPerNS; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			name := fmt.Sprintf("large-cm-%d", index+1)
			sizeBytes := int(e.config.LargeConfigMapSizeMB * 1024 * 1024)
			dataSize := sizeBytes - 1024

			configMap := &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      name,
					Namespace: namespace,
					Labels: map[string]string{
						"type":        "large-configmap",
						"stress-test": "true",
					},
				},
				Data: map[string]string{
					"large-data": generateRandomData(dataSize),
				},
			}

			err := e.retryResourceOperation(ctx, func() error {
				_, err := e.clientset.CoreV1().ConfigMaps(namespace).Create(ctx, configMap, metav1.CreateOptions{})
				return err
			})

			if err != nil {
				mu.Lock()
				errors = append(errors, fmt.Errorf("failed to create large ConfigMap %s in %s: %w", name, namespace, err))
				mu.Unlock()
				e.logWarn(fmt.Sprintf("Failed to create large ConfigMap %s in %s: %v", name, namespace, err), "CONFIGMAP")
			}
		}(i)
	}

	wg.Wait()

	if len(errors) > 0 {
		return fmt.Errorf("failed to create %d large ConfigMaps", len(errors))
	}

	return nil
}

// createSmallSecrets creates 10 small Secrets in namespace
func (e *EtcdStressTools) createSmallSecrets(ctx context.Context, namespace string) error {
	semaphore := make(chan struct{}, e.config.MaxConcurrentOperations)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var errors []error

	for i := 0; i < e.config.SmallSecretsPerNS; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			name := fmt.Sprintf("small-secret-%d", index+1)

			userBytes := make([]byte, 8)
			passBytes := make([]byte, 16)
			tokenBytes := make([]byte, 32)
			rand.Read(userBytes)
			rand.Read(passBytes)
			rand.Read(tokenBytes)

			secret := &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{
					Name:      name,
					Namespace: namespace,
					Labels: map[string]string{
						"type":        "small-secret",
						"stress-test": "true",
					},
				},
				Type: corev1.SecretTypeOpaque,
				Data: map[string][]byte{
					"username": []byte(base64.StdEncoding.EncodeToString([]byte(fmt.Sprintf("user-%s", hex.EncodeToString(userBytes))))),
					"password": []byte(base64.StdEncoding.EncodeToString(passBytes)),
					"token":    []byte(base64.StdEncoding.EncodeToString(tokenBytes)),
				},
			}

			err := e.retryResourceOperation(ctx, func() error {
				_, err := e.clientset.CoreV1().Secrets(namespace).Create(ctx, secret, metav1.CreateOptions{})
				return err
			})

			if err != nil {
				mu.Lock()
				errors = append(errors, fmt.Errorf("failed to create small Secret %s in %s: %w", name, namespace, err))
				mu.Unlock()
				e.logWarn(fmt.Sprintf("Failed to create small Secret %s in %s: %v", name, namespace, err), "SECRET")
			}
		}(i)
	}

	wg.Wait()

	if len(errors) > 0 {
		return fmt.Errorf("failed to create %d small Secrets", len(errors))
	}

	return nil
}

// generateKeyPair generates RSA keypair and certificate
func generateKeyPair() (privateKeyPEM, publicKeyPEM, certificatePEM string, err error) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return "", "", "", err
	}

	privateKeyDER := x509.MarshalPKCS1PrivateKey(privateKey)
	privateKeyBlock := &pem.Block{
		Type:  "RSA PRIVATE KEY",
		Bytes: privateKeyDER,
	}
	privateKeyPEM = string(pem.EncodeToMemory(privateKeyBlock))

	publicKeyBytes := make([]byte, 256)
	rand.Read(publicKeyBytes)
	publicKeyPEM = base64.StdEncoding.EncodeToString(publicKeyBytes)

	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject: pkix.Name{
			CommonName: "stress-test.local",
		},
		NotBefore:   time.Now(),
		NotAfter:    time.Now().Add(365 * 24 * time.Hour),
		KeyUsage:    x509.KeyUsageKeyEncipherment | x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IPAddresses: nil,
	}

	certDER, err := x509.CreateCertificate(rand.Reader, &template, &template, &privateKey.PublicKey, privateKey)
	if err != nil {
		return "", "", "", err
	}

	certBlock := &pem.Block{
		Type:  "CERTIFICATE",
		Bytes: certDER,
	}
	certificatePEM = string(pem.EncodeToMemory(certBlock))

	return privateKeyPEM, publicKeyPEM, certificatePEM, nil
}

// createLargeSecrets creates 10 large Secrets in namespace
func (e *EtcdStressTools) createLargeSecrets(ctx context.Context, namespace string) error {
	semaphore := make(chan struct{}, e.config.MaxConcurrentOperations)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var errors []error

	for i := 0; i < e.config.LargeSecretsPerNS; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			name := fmt.Sprintf("large-secret-%d", index+1)

			privateKey, publicKey, certificate, err := generateKeyPair()
			if err != nil {
				mu.Lock()
				errors = append(errors, fmt.Errorf("failed to generate keypair for %s: %w", name, err))
				mu.Unlock()
				return
			}

			largeTokenBytes := make([]byte, 1024)
			rand.Read(largeTokenBytes)

			secret := &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{
					Name:      name,
					Namespace: namespace,
					Labels: map[string]string{
						"type":        "large-secret",
						"stress-test": "true",
					},
				},
				Type: corev1.SecretTypeTLS,
				Data: map[string][]byte{
					"tls.crt":        []byte(certificate),
					"tls.key":        []byte(privateKey),
					"ca.crt":         []byte(certificate),
					"ssh-public-key": []byte(publicKey),
					"large-token":    []byte(base64.StdEncoding.EncodeToString(largeTokenBytes)),
				},
			}

			err = e.retryResourceOperation(ctx, func() error {
				_, err := e.clientset.CoreV1().Secrets(namespace).Create(ctx, secret, metav1.CreateOptions{})
				return err
			})

			if err != nil {
				mu.Lock()
				errors = append(errors, fmt.Errorf("failed to create large Secret %s in %s: %w", name, namespace, err))
				mu.Unlock()
				e.logWarn(fmt.Sprintf("Failed to create large Secret %s in %s: %v", name, namespace, err), "SECRET")
			}
		}(i)
	}

	wg.Wait()

	if len(errors) > 0 {
		return fmt.Errorf("failed to create %d large Secrets", len(errors))
	}

	return nil
}

// waitForDeploymentReady waits for a deployment's pods to be running and ready
func (e *EtcdStressTools) waitForDeploymentReady(ctx context.Context, namespace, deploymentName string, timeout time.Duration) error {
	timeoutTimer := time.NewTimer(timeout)
	defer timeoutTimer.Stop()

	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-timeoutTimer.C:
			return fmt.Errorf("timeout waiting for deployment %s pods to be ready in namespace %s", deploymentName, namespace)
		case <-ticker.C:
			deployment, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, deploymentName, metav1.GetOptions{})
			if err != nil {
				if apierrors.IsNotFound(err) {
					continue
				}
				return fmt.Errorf("error getting deployment %s: %w", deploymentName, err)
			}

			if deployment.Status.ReadyReplicas == *deployment.Spec.Replicas &&
				deployment.Status.UpdatedReplicas == *deployment.Spec.Replicas &&
				deployment.Status.Replicas == *deployment.Spec.Replicas {

				pods, err := e.clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{
					LabelSelector: fmt.Sprintf("app=%s", deploymentName),
				})
				if err != nil {
					return fmt.Errorf("error listing pods for deployment %s: %w", deploymentName, err)
				}

				allPodsReady := true
				for _, pod := range pods.Items {
					if pod.Status.Phase != corev1.PodRunning {
						allPodsReady = false
						break
					}

					podReady := false
					for _, condition := range pod.Status.Conditions {
						if condition.Type == corev1.PodReady && condition.Status == corev1.ConditionTrue {
							podReady = true
							break
						}
					}
					if !podReady {
						allPodsReady = false
						break
					}
				}

				if allPodsReady && len(pods.Items) > 0 {
					e.logInfo(fmt.Sprintf("Deployment %s in namespace %s is ready with %d running pods",
						deploymentName, namespace, len(pods.Items)), "DEPLOYMENT")
					return nil
				}
			}
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

// createDeployments creates 5 deployments with mounted ConfigMaps and Secrets
func (e *EtcdStressTools) createDeployments(ctx context.Context, namespace string) error {
	if !e.config.CreateDeployments {
		return nil
	}

	semaphore := make(chan struct{}, e.config.MaxConcurrentOperations)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var errors []error

	for i := 0; i < e.config.DeploymentsPerNS; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			name := fmt.Sprintf("stress-deployment-%d", index+1)

			var volumes []corev1.Volume
			var volumeMounts []corev1.VolumeMount

			for j := 0; j < 5; j++ {
				cmName := fmt.Sprintf("small-cm-%d", j+1)
				volumeName := fmt.Sprintf("small-cm-%d", j)
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
					MountPath: fmt.Sprintf("/config/small-cm/%d", j),
				})
			}

			volumes = append(volumes, corev1.Volume{
				Name: "large-cm-0",
				VolumeSource: corev1.VolumeSource{
					ConfigMap: &corev1.ConfigMapVolumeSource{
						LocalObjectReference: corev1.LocalObjectReference{Name: "large-cm-1"},
					},
				},
			})
			volumeMounts = append(volumeMounts, corev1.VolumeMount{
				Name:      "large-cm-0",
				MountPath: "/config/large-cm/0",
			})

			for j := 0; j < 5; j++ {
				secretName := fmt.Sprintf("small-secret-%d", j+1)
				volumeName := fmt.Sprintf("small-secret-%d", j)
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
					MountPath: fmt.Sprintf("/secrets/small/%d", j),
				})
			}

			for j := 0; j < 5; j++ {
				secretName := fmt.Sprintf("large-secret-%d", j+1)
				volumeName := fmt.Sprintf("large-secret-%d", j)
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
					MountPath: fmt.Sprintf("/secrets/large/%d", j),
				})
			}

			deployment := &appsv1.Deployment{
				ObjectMeta: metav1.ObjectMeta{
					Name:      name,
					Namespace: namespace,
					Labels: map[string]string{
						"app":         name,
						"stress-test": "true",
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
								"app":         name,
								"stress-test": "true",
							},
						},
						Spec: corev1.PodSpec{
							Containers: []corev1.Container{
								{
									Name:         "stress-container",
									Image:        "registry.access.redhat.com/ubi8/ubi-minimal:latest",
									Command:      []string{"/bin/sleep", "infinity"},
									VolumeMounts: volumeMounts,
									Resources: corev1.ResourceRequirements{
										Requests: corev1.ResourceList{
											corev1.ResourceMemory: resource.MustParse("64Mi"),
											corev1.ResourceCPU:    resource.MustParse("50m"),
										},
										Limits: corev1.ResourceList{
											corev1.ResourceMemory: resource.MustParse("128Mi"),
											corev1.ResourceCPU:    resource.MustParse("100m"),
										},
									},
								},
							},
							Volumes: volumes,
						},
					},
				},
			}

			err := e.retryResourceOperation(ctx, func() error {
				_, err := e.clientset.AppsV1().Deployments(namespace).Create(ctx, deployment, metav1.CreateOptions{})
				return err
			})

			if err != nil {
				mu.Lock()
				errors = append(errors, fmt.Errorf("failed to create Deployment %s in %s: %w", name, namespace, err))
				mu.Unlock()
				e.logWarn(fmt.Sprintf("Failed to create Deployment %s in %s: %v", name, namespace, err), "DEPLOYMENT")
				return
			}

			// Wait for deployment pods to be ready
			waitTimeout := 120 * time.Second
			if err := e.waitForDeploymentReady(ctx, namespace, name, waitTimeout); err != nil {
				mu.Lock()
				errors = append(errors, fmt.Errorf("deployment %s created but pods not ready in %s: %w", name, namespace, err))
				mu.Unlock()
				e.logWarn(fmt.Sprintf("Deployment %s created but pods not ready in %s: %v", name, namespace, err), "DEPLOYMENT")
			}
		}(i)
	}

	wg.Wait()

	if len(errors) > 0 {
		return fmt.Errorf("failed to create %d deployments", len(errors))
	}

	return nil
}

// createEgressFirewall creates EgressFirewall policy in namespace
func (e *EtcdStressTools) createEgressFirewall(ctx context.Context, namespace string) error {
	if !e.config.CreateEgressFirewall {
		return nil
	}

	egressFirewallGVR := schema.GroupVersionResource{
		Group:    "k8s.ovn.org",
		Version:  "v1",
		Resource: "egressfirewalls",
	}

	egressFirewall := &unstructured.Unstructured{
		Object: map[string]interface{}{
			"apiVersion": "k8s.ovn.org/v1",
			"kind":       "EgressFirewall",
			"metadata": map[string]interface{}{
				"name":      "default",
				"namespace": namespace,
				"labels": map[string]interface{}{
					"stress-test": "true",
				},
			},
			"spec": map[string]interface{}{
				"egress": []map[string]interface{}{
					{"type": "Allow", "to": map[string]interface{}{"cidrSelector": "8.8.8.8/32"}},
					{"type": "Deny", "to": map[string]interface{}{"cidrSelector": "8.8.4.4/32"}},
					{"type": "Allow", "to": map[string]interface{}{"cidrSelector": "114.114.114.114/32"}},
					{"type": "Deny", "to": map[string]interface{}{"cidrSelector": "1.1.1.1/32"}},
					{"type": "Deny", "to": map[string]interface{}{"cidrSelector": "2.2.2.2/32"}},
					{"type": "Allow", "to": map[string]interface{}{"dnsName": "www.googlex.com"}},
					{"type": "Allow", "to": map[string]interface{}{"dnsName": "updates.jenkins.io"}},
					{"type": "Deny", "to": map[string]interface{}{"dnsName": "www.digitalxocean.com"}},
					{"type": "Allow", "to": map[string]interface{}{"dnsName": "www.xxxx.com"}},
					{"type": "Deny", "to": map[string]interface{}{"dnsName": "www.xxyyyyxx.com"}},
				},
			},
		},
	}

	err := e.retryResourceOperation(ctx, func() error {
		_, err := e.dynamicClient.Resource(egressFirewallGVR).Namespace(namespace).Create(ctx, egressFirewall, metav1.CreateOptions{})
		return err
	})

	if err != nil {
		return fmt.Errorf("failed to create EgressFirewall in %s: %w", namespace, err)
	}

	return nil
}

// createNetworkPolicies creates 2 NetworkPolicy objects in namespace
func (e *EtcdStressTools) createNetworkPolicies(ctx context.Context, namespace string) error {
	if !e.config.CreateNetworkPolicies {
		return nil
	}

	denyPolicy := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "deny-by-default",
			Namespace: namespace,
			Labels:    map[string]string{"stress-test": "true"},
		},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{},
			Ingress:     []networkingv1.NetworkPolicyIngressRule{},
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeIngress, networkingv1.PolicyTypeEgress},
		},
	}

	largePolicy := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "large-networkpolicy-egress-allow-dns",
			Namespace: namespace,
			Labels:    map[string]string{"stress-test": "true"},
		},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{},
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeIngress, networkingv1.PolicyTypeEgress},
			Ingress: []networkingv1.NetworkPolicyIngressRule{
				{
					From: []networkingv1.NetworkPolicyPeer{
						{
							IPBlock: &networkingv1.IPBlock{CIDR: "10.128.0.0/16"},
						},
					},
				},
				{
					From: []networkingv1.NetworkPolicyPeer{
						{
							IPBlock: &networkingv1.IPBlock{CIDR: "172.30.0.0/16"},
						},
					},
				},
			},
			Egress: []networkingv1.NetworkPolicyEgressRule{
				{
					To: []networkingv1.NetworkPolicyPeer{
						{IPBlock: &networkingv1.IPBlock{CIDR: "10.128.0.0/14"}},
						{IPBlock: &networkingv1.IPBlock{CIDR: "172.30.0.0/16"}},
						{IPBlock: &networkingv1.IPBlock{CIDR: "8.8.8.8/32"}},
						{IPBlock: &networkingv1.IPBlock{CIDR: "8.8.8.4/32"}},
						{IPBlock: &networkingv1.IPBlock{CIDR: "142.0.0.0/8"}},
						{IPBlock: &networkingv1.IPBlock{CIDR: "104.18.0.0/16"}},
					},
				},
				{
					To: []networkingv1.NetworkPolicyPeer{
						{
							NamespaceSelector: &metav1.LabelSelector{
								MatchLabels: map[string]string{
									"kubernetes.io/metadata.name": "openshift-dns",
								},
							},
							PodSelector: &metav1.LabelSelector{
								MatchLabels: map[string]string{
									"dns.operator.openshift.io/daemonset-dns": "default",
								},
							},
						},
					},
				},
			},
		},
	}

	err := e.retryResourceOperation(ctx, func() error {
		_, err := e.clientset.NetworkingV1().NetworkPolicies(namespace).Create(ctx, denyPolicy, metav1.CreateOptions{})
		return err
	})
	if err != nil {
		return fmt.Errorf("failed to create deny NetworkPolicy in %s: %w", namespace, err)
	}

	err = e.retryResourceOperation(ctx, func() error {
		_, err := e.clientset.NetworkingV1().NetworkPolicies(namespace).Create(ctx, largePolicy, metav1.CreateOptions{})
		return err
	})
	if err != nil {
		return fmt.Errorf("failed to create large NetworkPolicy in %s: %w", namespace, err)
	}

	return nil
}

// generateFakeIPs generates fake IP addresses for testing
func (e *EtcdStressTools) generateFakeIPs(count int) []string {
	ips := make([]string, count)
	for i := 0; i < count; i++ {
		octets := []int{
			[]int{10, 172, 192}[mathrand.Intn(3)],
			mathrand.Intn(255) + 1,
			mathrand.Intn(255) + 1,
			mathrand.Intn(255) + 1,
		}
		ips[i] = fmt.Sprintf("%d.%d.%d.%d/32", octets[0], octets[1], octets[2], octets[3])
	}
	return ips
}

// createBaselineAdminNetworkPolicy creates BaselineAdminNetworkPolicy (BANP)
func (e *EtcdStressTools) createBaselineAdminNetworkPolicy(ctx context.Context) error {
	banpGVR := schema.GroupVersionResource{
		Group:    "policy.networking.k8s.io",
		Version:  "v1alpha1",
		Resource: "baselineadminnetworkpolicies",
	}

	banp := &unstructured.Unstructured{
		Object: map[string]interface{}{
			"apiVersion": "policy.networking.k8s.io/v1alpha1",
			"kind":       "BaselineAdminNetworkPolicy",
			"metadata": map[string]interface{}{
				"name": "default",
				"labels": map[string]interface{}{
					"stress-test": "true",
				},
			},
			"spec": map[string]interface{}{
				"subject": map[string]interface{}{
					"namespaces": map[string]interface{}{
						"matchExpressions": []map[string]interface{}{
							{
								"key":      "anplabel",
								"operator": "In",
								"values":   []string{"anp-tenant"},
							},
						},
					},
				},
				"ingress": []map[string]interface{}{
					{
						"name":   "deny-all-ingress-from-any-ns",
						"action": "Deny",
						"from": []map[string]interface{}{
							{
								"namespaces": map[string]interface{}{},
							},
						},
					},
				},
				"egress": []map[string]interface{}{
					{
						"name":   "egress-deny-all-traffic-to-any-network",
						"action": "Deny",
						"to": []map[string]interface{}{
							{
								"networks": []string{"0.0.0.0/0"},
							},
						},
					},
					{
						"action": "Deny",
						"name":   "egress-deny-all-traffic-to-any-node",
						"to": []map[string]interface{}{
							{
								"nodes": map[string]interface{}{
									"matchExpressions": []map[string]interface{}{
										{
											"key":      "kubernetes.io/hostname",
											"operator": "Exists",
										},
									},
								},
							},
						},
					},
				},
			},
		},
	}

	_, err := e.dynamicClient.Resource(banpGVR).Create(ctx, banp, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("failed to create BaselineAdminNetworkPolicy: %w", err)
	}

	return nil
}

// createAdminNetworkPolicies creates AdminNetworkPolicy (ANP) objects
func (e *EtcdStressTools) createAdminNetworkPolicies(ctx context.Context) error {
	anpGVR := schema.GroupVersionResource{
		Group:    "policy.networking.k8s.io",
		Version:  "v1alpha1",
		Resource: "adminnetworkpolicies",
	}

	anpCount := max(1, e.config.TotalNamespaces/3)
	fakeIPs := e.generateFakeIPs(10)

	semaphore := make(chan struct{}, e.config.MaxConcurrentOperations)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var errors []error

	for i := 0; i < anpCount; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			tenant := e.tenantLabels[index%len(e.tenantLabels)]
			anpName := fmt.Sprintf("allow-traffic-anp-cidr-to-openshift-monitoring-network-%s-p%d", tenant, index+1)

			anp := &unstructured.Unstructured{
				Object: map[string]interface{}{
					"apiVersion": "policy.networking.k8s.io/v1alpha1",
					"kind":       "AdminNetworkPolicy",
					"metadata": map[string]interface{}{
						"name": anpName,
						"labels": map[string]interface{}{
							"stress-test": "true",
						},
					},
					"spec": map[string]interface{}{
						"priority": index + 1,
						"subject": map[string]interface{}{
							"namespaces": map[string]interface{}{
								"matchLabels": map[string]interface{}{
									"customer_tenant": tenant,
								},
							},
						},
						"ingress": []map[string]interface{}{
							{
								"name":   "all-ingress-from-same-tenant",
								"action": "Allow",
								"from": []map[string]interface{}{
									{
										"namespaces": map[string]interface{}{
											"matchLabels": map[string]interface{}{
												"customer_tenant": tenant,
											},
										},
									},
								},
							},
						},
						"egress": []map[string]interface{}{
							{
								"name":   "pass-egress-to-cluster-network",
								"action": "Pass",
								"ports": []map[string]interface{}{
									{"portNumber": map[string]interface{}{"port": 9093, "protocol": "TCP"}},
									{"portNumber": map[string]interface{}{"port": 9094, "protocol": "TCP"}},
								},
								"to": []map[string]interface{}{
									{"networks": []string{"10.128.0.0/14"}},
								},
							},
							{
								"name":   "allow-egress-to-openshift-monitoring-network-1",
								"action": "Allow",
								"ports": []map[string]interface{}{
									{"portNumber": map[string]interface{}{"port": 9100}},
									{"portNumber": map[string]interface{}{"port": 8080, "protocol": "TCP"}},
									{"portRange": map[string]interface{}{"start": 9201, "end": 9205, "protocol": "TCP"}},
								},
								"to": []map[string]interface{}{
									{"networks": fakeIPs[0:5]},
								},
							},
							{
								"name":   "allow-egress-to-openshift-monitoring-network-2",
								"action": "Allow",
								"ports": []map[string]interface{}{
									{"portNumber": map[string]interface{}{"port": 9100}},
									{"portNumber": map[string]interface{}{"port": 8080, "protocol": "TCP"}},
									{"portRange": map[string]interface{}{"start": 9201, "end": 9205, "protocol": "TCP"}},
								},
								"to": []map[string]interface{}{
									{"networks": fakeIPs[5:7]},
								},
							},
							{
								"name":   "deny-egress-to-openshift-monitoring-network-1",
								"action": "Deny",
								"ports": []map[string]interface{}{
									{"portNumber": map[string]interface{}{"port": 9091}},
									{"portNumber": map[string]interface{}{"port": 5432, "protocol": "TCP"}},
									{"portNumber": map[string]interface{}{"port": 60000, "protocol": "TCP"}},
									{"portNumber": map[string]interface{}{"port": 9099, "protocol": "TCP"}},
									{"portNumber": map[string]interface{}{"port": 9393, "protocol": "TCP"}},
								},
								"to": []map[string]interface{}{
									{"networks": []string{fakeIPs[7]}},
								},
							},
							{
								"name":   "allow-egress-to-dns",
								"action": "Allow",
								"to": []map[string]interface{}{
									{
										"namespaces": map[string]interface{}{
											"matchLabels": map[string]interface{}{
												"kubernetes.io/metadata.name": "openshift-dns",
											},
										},
									},
								},
							},
							{
								"name":   "allow-to-kube-apiserver",
								"action": "Allow",
								"to": []map[string]interface{}{
									{
										"namespaces": map[string]interface{}{
											"matchLabels": map[string]interface{}{
												"kubernetes.io/metadata.name": "openshift-kube-apiserver",
											},
										},
									},
								},
								"ports": []map[string]interface{}{
									{"portNumber": map[string]interface{}{"port": 6443, "protocol": "TCP"}},
								},
							},
						},
					},
				},
			}

			_, err := e.dynamicClient.Resource(anpGVR).Create(ctx, anp, metav1.CreateOptions{})
			if err != nil && !apierrors.IsAlreadyExists(err) {
				mu.Lock()
				errors = append(errors, fmt.Errorf("failed to create ANP %s: %w", anpName, err))
				mu.Unlock()
			}
		}(i)
	}

	wg.Wait()

	successCount := anpCount - len(errors)
	e.logInfo(fmt.Sprintf("Created %d/%d AdminNetworkPolicies", successCount, anpCount), "ANP")

	return nil
}

// createImages creates multiple Images using OpenShift Image API
func (e *EtcdStressTools) createImages(ctx context.Context) error {
	if !e.config.CreateImages {
		return nil
	}

	imageGVR := schema.GroupVersionResource{
		Group:    "image.openshift.io",
		Version:  "v1",
		Resource: "images",
	}

	imageCount := e.config.TotalNamespaces * 3
	semaphore := make(chan struct{}, e.config.MaxConcurrentOperations)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var successCount int

	for i := 0; i < imageCount; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			name := fmt.Sprintf("stress-image-%d", index+1)

			image := &unstructured.Unstructured{
				Object: map[string]interface{}{
					"apiVersion": "image.openshift.io/v1",
					"kind":       "Image",
					"metadata": map[string]interface{}{
						"name": name,
						"labels": map[string]interface{}{
							"stress-test": "true",
						},
					},
					"dockerImageReference": "registry.redhat.io/ubi8/ruby-27:latest",
					"dockerImageMetadata": map[string]interface{}{
						"kind":            "DockerImage",
						"apiVersion":      "1.0",
						"Id":              "",
						"ContainerConfig": map[string]interface{}{},
						"Config":          map[string]interface{}{},
					},
					"dockerImageLayers":          []interface{}{},
					"dockerImageMetadataVersion": "1.0",
				},
			}

			_, err := e.dynamicClient.Resource(imageGVR).Create(ctx, image, metav1.CreateOptions{})
			if err != nil && !apierrors.IsAlreadyExists(err) {
				e.logWarn(fmt.Sprintf("Failed to create Image %s: %v", name, err), "IMAGE")
			} else {
				mu.Lock()
				successCount++
				mu.Unlock()
			}
		}(i)
	}

	wg.Wait()

	e.logInfo(fmt.Sprintf("Created %d/%d Images", successCount, imageCount), "IMAGE")
	return nil
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// createNamespaceResources creates all resources for a single namespace
func (e *EtcdStressTools) createNamespaceResources(ctx context.Context, namespaceIdx int) (success, errors int) {
	namespaceName := fmt.Sprintf("%s-%d", e.config.NamespacePrefix, namespaceIdx)
	tenant := e.tenantLabels[namespaceIdx%len(e.tenantLabels)]

	namespaceLabels := map[string]string{
		"customer_tenant": tenant,
	}
	if namespaceIdx%4 == 0 {
		namespaceLabels["anplabel"] = "anp-tenant"
	}

	if err := e.ensureNamespace(ctx, namespaceName, namespaceLabels); err != nil {
		e.logError(fmt.Sprintf("Failed to create/verify namespace %s: %v", namespaceName, err), "NAMESPACE")
		return 0, 1
	}

	var successCount, errorCount int

	resourceFuncs := []struct {
		name string
		fn   func(context.Context, string) error
	}{
		{"small_configmaps", e.createSmallConfigMaps},
		{"large_configmaps", e.createLargeConfigMaps},
		{"small_secrets", e.createSmallSecrets},
		{"large_secrets", e.createLargeSecrets},
	}

	for _, resource := range resourceFuncs {
		if err := resource.fn(ctx, namespaceName); err != nil {
			e.logError(fmt.Sprintf("Task %s failed in %s: %v", resource.name, namespaceName, err), "RESOURCE")
			errorCount++
		} else {
			successCount++
		}
	}

	additionalFuncs := []struct {
		name string
		fn   func(context.Context, string) error
	}{
		{"egress_firewall", e.createEgressFirewall},
		{"network_policies", e.createNetworkPolicies},
		{"deployments", e.createDeployments},
	}

	for _, resource := range additionalFuncs {
		if err := resource.fn(ctx, namespaceName); err != nil {
			e.logError(fmt.Sprintf("Task %s failed in %s: %v", resource.name, namespaceName, err), "RESOURCE")
			errorCount++
		} else {
			successCount++
		}
	}

	return successCount, errorCount
}

// createAllNamespacesAndResources creates all namespaces and their resources
func (e *EtcdStressTools) createAllNamespacesAndResources(ctx context.Context) {
	e.logInfo(fmt.Sprintf("Creating %d namespaces with resources", e.config.TotalNamespaces), "MAIN")

	semaphore := make(chan struct{}, e.config.NamespaceParallel)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var totalSuccess, totalErrors int

	for i := 1; i <= e.config.TotalNamespaces; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			success, errors := e.createNamespaceResources(ctx, idx)

			mu.Lock()
			totalSuccess += success
			totalErrors += errors
			mu.Unlock()
		}(i)
	}

	wg.Wait()

	e.logInfo(fmt.Sprintf("Resource creation completed: Total Success: %d, Total Errors: %d",
		totalSuccess, totalErrors), "COMPLETE")
}

// createGlobalNetworkPolicies creates global network policies (BANP and ANP)
func (e *EtcdStressTools) createGlobalNetworkPolicies(ctx context.Context) {
	if !e.config.CreateANPBANP {
		return
	}

	e.logInfo("Creating global network policies", "NETPOL")

	if err := e.createBaselineAdminNetworkPolicy(ctx); err != nil {
		e.logWarn(fmt.Sprintf("Failed to create BANP: %v", err), "BANP")
	} else {
		e.logInfo("BaselineAdminNetworkPolicy created successfully", "BANP")
	}

	if err := e.createAdminNetworkPolicies(ctx); err != nil {
		e.logWarn(fmt.Sprintf("Failed to create ANPs: %v", err), "ANP")
	} else {
		e.logInfo("AdminNetworkPolicies created successfully", "ANP")
	}
}

// LIST FUNCTIONS

// listAllPods lists all pods across stress-test namespaces
func (e *EtcdStressTools) listAllPods(ctx context.Context) (*ResourceStats, error) {
	e.logInfo("Listing all pods in stress-test namespaces", "LIST-PODS")

	namespaces, err := e.clientset.CoreV1().Namespaces().List(ctx, metav1.ListOptions{
		LabelSelector: "stress-test=true",
	})
	if err != nil {
		return nil, fmt.Errorf("failed to list namespaces: %w", err)
	}

	stats := &ResourceStats{
		Details: make(map[string]NamespaceDetails),
	}

	var mu sync.Mutex
	var wg sync.WaitGroup
	semaphore := make(chan struct{}, e.config.NamespaceParallel)

	for _, ns := range namespaces.Items {
		wg.Add(1)
		go func(nsName string) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			pods, err := e.clientset.CoreV1().Pods(nsName).List(ctx, metav1.ListOptions{})
			if err != nil {
				e.logWarn(fmt.Sprintf("Failed to list pods in %s: %v", nsName, err), "LIST-PODS")
				return
			}

			mu.Lock()
			stats.TotalPods += len(pods.Items)
			if _, exists := stats.Details[nsName]; !exists {
				stats.Details[nsName] = NamespaceDetails{}
			}
			detail := stats.Details[nsName]
			detail.PodCount = len(pods.Items)
			stats.Details[nsName] = detail
			mu.Unlock()
		}(ns.Name)
	}

	wg.Wait()
	stats.TotalNamespaces = len(namespaces.Items)

	e.logInfo(fmt.Sprintf("Total pods found: %d across %d namespaces", stats.TotalPods, stats.TotalNamespaces), "LIST-PODS")
	return stats, nil
}

// listAllConfigMaps lists all ConfigMaps across stress-test namespaces
func (e *EtcdStressTools) listAllConfigMaps(ctx context.Context) (*ResourceStats, error) {
	e.logInfo("Listing all ConfigMaps in stress-test namespaces", "LIST-CM")

	namespaces, err := e.clientset.CoreV1().Namespaces().List(ctx, metav1.ListOptions{
		LabelSelector: "stress-test=true",
	})
	if err != nil {
		return nil, fmt.Errorf("failed to list namespaces: %w", err)
	}

	stats := &ResourceStats{
		Details: make(map[string]NamespaceDetails),
	}

	var mu sync.Mutex
	var wg sync.WaitGroup
	semaphore := make(chan struct{}, e.config.NamespaceParallel)

	for _, ns := range namespaces.Items {
		wg.Add(1)
		go func(nsName string) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			configMaps, err := e.clientset.CoreV1().ConfigMaps(nsName).List(ctx, metav1.ListOptions{})
			if err != nil {
				e.logWarn(fmt.Sprintf("Failed to list ConfigMaps in %s: %v", nsName, err), "LIST-CM")
				return
			}

			var smallCount, largeCount int
			for _, cm := range configMaps.Items {
				if cm.Labels["type"] == "small-configmap" {
					smallCount++
				} else if cm.Labels["type"] == "large-configmap" {
					largeCount++
				}
			}

			mu.Lock()
			stats.TotalConfigMaps += len(configMaps.Items)
			stats.SmallConfigMaps += smallCount
			stats.LargeConfigMaps += largeCount
			if _, exists := stats.Details[nsName]; !exists {
				stats.Details[nsName] = NamespaceDetails{}
			}
			detail := stats.Details[nsName]
			detail.ConfigMapCount = len(configMaps.Items)
			detail.SmallConfigMaps = smallCount
			detail.LargeConfigMaps = largeCount
			stats.Details[nsName] = detail
			mu.Unlock()
		}(ns.Name)
	}

	wg.Wait()
	stats.TotalNamespaces = len(namespaces.Items)

	e.logInfo(fmt.Sprintf("Total ConfigMaps: %d (Small: %d, Large: %d)", stats.TotalConfigMaps, stats.SmallConfigMaps, stats.LargeConfigMaps), "LIST-CM")
	return stats, nil
}

// listAllSecrets lists all Secrets across stress-test namespaces
func (e *EtcdStressTools) listAllSecrets(ctx context.Context) (*ResourceStats, error) {
	e.logInfo("Listing all Secrets in stress-test namespaces", "LIST-SECRET")

	namespaces, err := e.clientset.CoreV1().Namespaces().List(ctx, metav1.ListOptions{
		LabelSelector: "stress-test=true",
	})
	if err != nil {
		return nil, fmt.Errorf("failed to list namespaces: %w", err)
	}

	stats := &ResourceStats{
		Details: make(map[string]NamespaceDetails),
	}

	var mu sync.Mutex
	var wg sync.WaitGroup
	semaphore := make(chan struct{}, e.config.NamespaceParallel)

	for _, ns := range namespaces.Items {
		wg.Add(1)
		go func(nsName string) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			secrets, err := e.clientset.CoreV1().Secrets(nsName).List(ctx, metav1.ListOptions{})
			if err != nil {
				e.logWarn(fmt.Sprintf("Failed to list Secrets in %s: %v", nsName, err), "LIST-SECRET")
				return
			}

			var smallCount, largeCount int
			for _, secret := range secrets.Items {
				if secret.Labels["type"] == "small-secret" {
					smallCount++
				} else if secret.Labels["type"] == "large-secret" {
					largeCount++
				}
			}

			mu.Lock()
			stats.TotalSecrets += len(secrets.Items)
			stats.SmallSecrets += smallCount
			stats.LargeSecrets += largeCount
			if _, exists := stats.Details[nsName]; !exists {
				stats.Details[nsName] = NamespaceDetails{}
			}
			detail := stats.Details[nsName]
			detail.SecretCount = len(secrets.Items)
			detail.SmallSecrets = smallCount
			detail.LargeSecrets = largeCount
			stats.Details[nsName] = detail
			mu.Unlock()
		}(ns.Name)
	}

	wg.Wait()
	stats.TotalNamespaces = len(namespaces.Items)

	e.logInfo(fmt.Sprintf("Total Secrets: %d (Small: %d, Large: %d)", stats.TotalSecrets, stats.SmallSecrets, stats.LargeSecrets), "LIST-SECRET")
	return stats, nil
}

// listAllNetworkPolicies lists all NetworkPolicies across stress-test namespaces
func (e *EtcdStressTools) listAllNetworkPolicies(ctx context.Context) (*ResourceStats, error) {
	e.logInfo("Listing all NetworkPolicies in stress-test namespaces", "LIST-NETPOL")

	namespaces, err := e.clientset.CoreV1().Namespaces().List(ctx, metav1.ListOptions{
		LabelSelector: "stress-test=true",
	})
	if err != nil {
		return nil, fmt.Errorf("failed to list namespaces: %w", err)
	}

	stats := &ResourceStats{
		Details: make(map[string]NamespaceDetails),
	}

	var mu sync.Mutex
	var wg sync.WaitGroup
	semaphore := make(chan struct{}, e.config.NamespaceParallel)

	for _, ns := range namespaces.Items {
		wg.Add(1)
		go func(nsName string) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			netpols, err := e.clientset.NetworkingV1().NetworkPolicies(nsName).List(ctx, metav1.ListOptions{
				LabelSelector: "stress-test=true",
			})
			if err != nil {
				e.logWarn(fmt.Sprintf("Failed to list NetworkPolicies in %s: %v", nsName, err), "LIST-NETPOL")
				return
			}

			mu.Lock()
			if _, exists := stats.Details[nsName]; !exists {
				stats.Details[nsName] = NamespaceDetails{}
			}
			detail := stats.Details[nsName]
			detail.NetworkPolicyCount = len(netpols.Items)
			stats.Details[nsName] = detail
			mu.Unlock()
		}(ns.Name)
	}

	wg.Wait()
	stats.TotalNamespaces = len(namespaces.Items)

	totalNetPols := 0
	for _, detail := range stats.Details {
		totalNetPols += detail.NetworkPolicyCount
	}

	e.logInfo(fmt.Sprintf("Total NetworkPolicies: %d across %d namespaces", totalNetPols, stats.TotalNamespaces), "LIST-NETPOL")
	return stats, nil
}

// listAllImages lists all Images (OpenShift specific)
func (e *EtcdStressTools) listAllImages(ctx context.Context) (int, error) {
	e.logInfo("Listing all Images with stress-test label", "LIST-IMAGE")

	imageGVR := schema.GroupVersionResource{
		Group:    "image.openshift.io",
		Version:  "v1",
		Resource: "images",
	}

	images, err := e.dynamicClient.Resource(imageGVR).List(ctx, metav1.ListOptions{
		LabelSelector: "stress-test=true",
	})
	if err != nil {
		e.logWarn(fmt.Sprintf("Failed to list Images: %v", err), "LIST-IMAGE")
		return 0, err
	}

	imageCount := len(images.Items)
	e.logInfo(fmt.Sprintf("Total Images: %d", imageCount), "LIST-IMAGE")
	return imageCount, nil
}

// listAllAdminNetworkPolicies lists all ANP and BANP objects
func (e *EtcdStressTools) listAllAdminNetworkPolicies(ctx context.Context) (int, int, error) {
	e.logInfo("Listing AdminNetworkPolicies and BaselineAdminNetworkPolicies", "LIST-ANP")

	anpGVR := schema.GroupVersionResource{
		Group:    "policy.networking.k8s.io",
		Version:  "v1alpha1",
		Resource: "adminnetworkpolicies",
	}

	banpGVR := schema.GroupVersionResource{
		Group:    "policy.networking.k8s.io",
		Version:  "v1alpha1",
		Resource: "baselineadminnetworkpolicies",
	}

	anps, err := e.dynamicClient.Resource(anpGVR).List(ctx, metav1.ListOptions{
		LabelSelector: "stress-test=true",
	})
	anpCount := 0
	if err != nil {
		e.logWarn(fmt.Sprintf("Failed to list AdminNetworkPolicies: %v", err), "LIST-ANP")
	} else {
		anpCount = len(anps.Items)
	}

	banps, err := e.dynamicClient.Resource(banpGVR).List(ctx, metav1.ListOptions{
		LabelSelector: "stress-test=true",
	})
	banpCount := 0
	if err != nil {
		e.logWarn(fmt.Sprintf("Failed to list BaselineAdminNetworkPolicies: %v", err), "LIST-ANP")
	} else {
		banpCount = len(banps.Items)
	}

	e.logInfo(fmt.Sprintf("Total AdminNetworkPolicies: %d, BaselineAdminNetworkPolicies: %d", anpCount, banpCount), "LIST-ANP")
	return anpCount, banpCount, nil
}

// runListScenario runs the comprehensive listing scenario
func (e *EtcdStressTools) runListScenario(ctx context.Context) error {
	e.logInfo("Starting list scenario for large-scale resources", "LIST")
	startTime := time.Now()

	var wg sync.WaitGroup
	var podStats, cmStats, secretStats, netpolStats *ResourceStats
	var podErr, cmErr, secretErr, netpolErr error
	var imageCount, anpCount, banpCount int
	var imageErr, anpErr error

	wg.Add(6)
	go func() {
		defer wg.Done()
		podStats, podErr = e.listAllPods(ctx)
	}()
	go func() {
		defer wg.Done()
		cmStats, cmErr = e.listAllConfigMaps(ctx)
	}()
	go func() {
		defer wg.Done()
		secretStats, secretErr = e.listAllSecrets(ctx)
	}()
	go func() {
		defer wg.Done()
		netpolStats, netpolErr = e.listAllNetworkPolicies(ctx)
	}()
	go func() {
		defer wg.Done()
		imageCount, imageErr = e.listAllImages(ctx)
	}()
	go func() {
		defer wg.Done()
		anpCount, banpCount, anpErr = e.listAllAdminNetworkPolicies(ctx)
	}()

	wg.Wait()

	fmt.Printf("\n%s%s%s\n", ColorCyan, strings.Repeat("=", 80), ColorNC)
	fmt.Printf("%sResource Listing Summary%s\n", ColorGreen, ColorNC)
	fmt.Printf("%s%s%s\n", ColorCyan, strings.Repeat("=", 80), ColorNC)

	if podErr == nil && podStats != nil {
		fmt.Printf("\n%sPods:%s\n", ColorYellow, ColorNC)
		fmt.Printf("  Total Pods: %d\n", podStats.TotalPods)
		fmt.Printf("  Namespaces: %d\n", podStats.TotalNamespaces)
	}

	if cmErr == nil && cmStats != nil {
		fmt.Printf("\n%sConfigMaps:%s\n", ColorYellow, ColorNC)
		fmt.Printf("  Total ConfigMaps: %d\n", cmStats.TotalConfigMaps)
		fmt.Printf("  Small ConfigMaps: %d\n", cmStats.SmallConfigMaps)
		fmt.Printf("  Large ConfigMaps: %d\n", cmStats.LargeConfigMaps)
		fmt.Printf("  Namespaces: %d\n", cmStats.TotalNamespaces)
	}

	if secretErr == nil && secretStats != nil {
		fmt.Printf("\n%sSecrets:%s\n", ColorYellow, ColorNC)
		fmt.Printf("  Total Secrets: %d\n", secretStats.TotalSecrets)
		fmt.Printf("  Small Secrets: %d\n", secretStats.SmallSecrets)
		fmt.Printf("  Large Secrets: %d\n", secretStats.LargeSecrets)
		fmt.Printf("  Namespaces: %d\n", secretStats.TotalNamespaces)
	}

	if netpolErr == nil && netpolStats != nil {
		totalNetPols := 0
		for _, detail := range netpolStats.Details {
			totalNetPols += detail.NetworkPolicyCount
		}
		fmt.Printf("\n%sNetworkPolicies:%s\n", ColorYellow, ColorNC)
		fmt.Printf("  Total NetworkPolicies: %d\n", totalNetPols)
		fmt.Printf("  Namespaces: %d\n", netpolStats.TotalNamespaces)
	}

	if imageErr == nil {
		fmt.Printf("\n%sImages:%s\n", ColorYellow, ColorNC)
		fmt.Printf("  Total Images: %d\n", imageCount)
	}

	if anpErr == nil {
		fmt.Printf("\n%sAdmin Network Policies:%s\n", ColorYellow, ColorNC)
		fmt.Printf("  AdminNetworkPolicies (ANP): %d\n", anpCount)
		fmt.Printf("  BaselineAdminNetworkPolicies (BANP): %d\n", banpCount)
	}

	elapsedTime := time.Since(startTime)
	fmt.Printf("\n%sList scenario completed in %.2f seconds%s\n", ColorGreen, elapsedTime.Seconds(), ColorNC)
	fmt.Printf("%s%s%s\n\n", ColorCyan, strings.Repeat("=", 80), ColorNC)

	return nil
}

// cleanupAllResources cleans up all created resources
func (e *EtcdStressTools) cleanupAllResources(ctx context.Context) {
	if !e.config.CleanupOnCompletion {
		e.logInfo("Cleanup disabled, skipping resource cleanup", "CLEANUP")
		return
	}

	e.logInfo("Starting cleanup of all created resources", "CLEANUP")

	if e.config.CreateImages {
		imageGVR := schema.GroupVersionResource{
			Group:    "image.openshift.io",
			Version:  "v1",
			Resource: "images",
		}

		images, err := e.dynamicClient.Resource(imageGVR).List(ctx, metav1.ListOptions{
			LabelSelector: "stress-test=true",
		})
		if err == nil {
			var wg sync.WaitGroup
			for _, img := range images.Items {
				wg.Add(1)
				go func(name string) {
					defer wg.Done()
					e.dynamicClient.Resource(imageGVR).Delete(ctx, name, metav1.DeleteOptions{})
				}(img.GetName())
			}
			wg.Wait()
			e.logInfo(fmt.Sprintf("Cleaned up %d Images", len(images.Items)), "CLEANUP")
		}
	}

	namespaces, err := e.clientset.CoreV1().Namespaces().List(ctx, metav1.ListOptions{
		LabelSelector: "stress-test=true",
	})
	if err != nil {
		e.logError(fmt.Sprintf("Failed to list namespaces for cleanup: %v", err), "CLEANUP")
		return
	}

	e.logInfo(fmt.Sprintf("Found %d namespaces to clean up", len(namespaces.Items)), "CLEANUP")

	semaphore := make(chan struct{}, e.config.NamespaceParallel)
	var wg sync.WaitGroup
	var successful int
	var mu sync.Mutex

	for _, ns := range namespaces.Items {
		wg.Add(1)
		go func(nsName string) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			err := e.clientset.CoreV1().Namespaces().Delete(ctx, nsName, metav1.DeleteOptions{
				GracePeriodSeconds: pointer.Int64(0),
			})
			if err == nil || apierrors.IsNotFound(err) {
				mu.Lock()
				successful++
				mu.Unlock()
			} else {
				e.logWarn(fmt.Sprintf("Failed to delete namespace %s: %v", nsName, err), "CLEANUP")
			}
		}(ns.Name)
	}

	wg.Wait()
	e.logInfo(fmt.Sprintf("Deleted %d/%d namespaces", successful, len(namespaces.Items)), "CLEANUP")

	if e.config.CreateANPBANP {
		banpGVR := schema.GroupVersionResource{
			Group:    "policy.networking.k8s.io",
			Version:  "v1alpha1",
			Resource: "baselineadminnetworkpolicies",
		}
		e.dynamicClient.Resource(banpGVR).Delete(ctx, "default", metav1.DeleteOptions{})

		anpGVR := schema.GroupVersionResource{
			Group:    "policy.networking.k8s.io",
			Version:  "v1alpha1",
			Resource: "adminnetworkpolicies",
		}
		anps, err := e.dynamicClient.Resource(anpGVR).List(ctx, metav1.ListOptions{
			LabelSelector: "stress-test=true",
		})
		if err == nil {
			var anpWg sync.WaitGroup
			for _, anp := range anps.Items {
				anpWg.Add(1)
				go func(name string) {
					defer anpWg.Done()
					e.dynamicClient.Resource(anpGVR).Delete(ctx, name, metav1.DeleteOptions{})
				}(anp.GetName())
			}
			anpWg.Wait()
			e.logInfo(fmt.Sprintf("Cleaned up %d AdminNetworkPolicies", len(anps.Items)), "CLEANUP")
		}
	}

	e.logInfo("Cleanup completed", "CLEANUP")
}

// runComprehensiveTest runs the complete stress test
func (e *EtcdStressTools) runComprehensiveTest(ctx context.Context) error {
	startTime := time.Now()
	e.logInfo("Starting comprehensive etcd stress test", "MAIN")

	e.createGlobalNetworkPolicies(ctx)

	if err := e.createImages(ctx); err != nil {
		e.logWarn(fmt.Sprintf("Failed to create images: %v", err), "MAIN")
	}

	e.createAllNamespacesAndResources(ctx)

	elapsedTime := time.Since(startTime)
	e.logInfo(fmt.Sprintf("Test completed successfully in %.2f seconds", elapsedTime.Seconds()), "MAIN")

	e.logInfo("Running list scenario to verify created resources", "MAIN")
	if err := e.runListScenario(ctx); err != nil {
		e.logWarn(fmt.Sprintf("List scenario failed: %v", err), "MAIN")
	}

	if e.config.CleanupOnCompletion {
		e.logInfo("Starting cleanup phase", "MAIN")
		e.cleanupAllResources(ctx)
	}

	totalTime := time.Since(startTime)
	e.logInfo(fmt.Sprintf("Total execution time: %.2f seconds", totalTime.Seconds()), "MAIN")

	return nil
}

func main() {
	var (
		totalNamespaces   = flag.Int("total-namespaces", 0, "Total number of namespaces to create")
		namespaceParallel = flag.Int("namespace-parallel", 0, "Number of namespaces to process in parallel")
		namespacePrefix   = flag.String("namespace-prefix", "", "Prefix for namespace names")
		largeLimit        = flag.Float64("large-limit", 0, "Total limit for large ConfigMaps in GB")
		enableDeployments = flag.Bool("enable-deployments", false, "Enable deployment creation")
		noEgressFirewall  = flag.Bool("no-egress-firewall", false, "Disable EgressFirewall creation")
		noNetworkPolicies = flag.Bool("no-network-policies", false, "Disable NetworkPolicy creation")
		noANPBANP         = flag.Bool("no-anp-banp", false, "Disable ANP/BANP creation")
		noImages          = flag.Bool("no-images", false, "Disable Image creation")
		maxConcurrent     = flag.Int("max-concurrent", 0, "Maximum concurrent operations")
		namespaceTimeout  = flag.Int("namespace-timeout", 0, "Timeout for namespace readiness in seconds")
		retryCount        = flag.Int("retry-count", 0, "Number of retries for failed operations")
		retryDelay        = flag.Float64("retry-delay", 0, "Delay between retries in seconds")
		cleanup           = flag.Bool("cleanup", false, "Enable cleanup after completion")
		logLevel          = flag.String("log-level", "", "Set logging level")
		listOnly          = flag.Bool("list-only", false, "Only list existing resources without creating")
		help              = flag.Bool("help", false, "Show help message")
	)

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, `Optimized OpenShift etcd Stress Testing Tool

This tool creates comprehensive Kubernetes resources to stress test etcd.

Usage:
`)
		flag.PrintDefaults()
	}

	flag.Parse()

	if *help {
		flag.Usage()
		return
	}

	config := NewConfig()

	if *totalNamespaces != 0 {
		config.TotalNamespaces = *totalNamespaces
	}
	if *namespaceParallel != 0 {
		config.NamespaceParallel = *namespaceParallel
	}
	if *namespacePrefix != "" {
		config.NamespacePrefix = *namespacePrefix
	}
	if *largeLimit != 0 {
		config.TotalLargeConfigMapLimitGB = *largeLimit
	}
	if *enableDeployments {
		config.CreateDeployments = true
	}
	if *noEgressFirewall {
		config.CreateEgressFirewall = false
	}
	if *noNetworkPolicies {
		config.CreateNetworkPolicies = false
	}
	if *noANPBANP {
		config.CreateANPBANP = false
	}
	if *noImages {
		config.CreateImages = false
	}
	if *maxConcurrent != 0 {
		config.MaxConcurrentOperations = *maxConcurrent
	}
	if *namespaceTimeout != 0 {
		config.NamespaceReadyTimeout = time.Duration(*namespaceTimeout) * time.Second
	}
	if *retryCount != 0 {
		config.ResourceRetryCount = *retryCount
	}
	if *retryDelay != 0 {
		config.ResourceRetryDelay = time.Duration(*retryDelay*1000) * time.Millisecond
	}
	if *cleanup {
		config.CleanupOnCompletion = true
	}
	if *logLevel != "" {
		config.LogLevel = *logLevel
	}
	if *listOnly {
		config.ListOnly = true
	}

	tool, err := NewEtcdStressTools(config)
	if err != nil {
		log.Fatalf("Failed to create etcd stress tool: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go func() {
		c := make(chan os.Signal, 1)
		signal.Notify(c, os.Interrupt, syscall.SIGTERM)
		<-c
		tool.logWarn("Test interrupted by user", "MAIN")
		cancel()
		os.Exit(1)
	}()

	if config.ListOnly {
		if err := tool.runListScenario(ctx); err != nil {
			tool.logError(fmt.Sprintf("List scenario failed: %v", err), "MAIN")
			os.Exit(1)
		}
	} else {
		if err := tool.runComprehensiveTest(ctx); err != nil {
			tool.logError(fmt.Sprintf("Test failed: %v", err), "MAIN")
			os.Exit(1)
		}
	}
}
