#!/bin/bash
# Test the deployment status checking with a single namespace
echo "Testing deployment status checking with resource-constrained cluster..."
echo "This should show errors for pods that don't start within 300 seconds."
echo ""

# Run with just 1 namespace to see the status checking in action
go run create-deployment.go \
  --total-namespaces 1 \
  --namespace-prefix "status-test" \
  --deployments-per-ns 3 \
  2>&1 | tee /tmp/status-test-output.log

echo ""
echo "=== Test Complete ==="
echo ""
echo "Check the output above for:"
echo "1. Pod status warnings showing Phase=ContainerCreating or Pending"
echo "2. Error count > 0 if pods didn't start within 300 seconds"
echo "3. Detailed timeout messages indicating which deployments failed"
