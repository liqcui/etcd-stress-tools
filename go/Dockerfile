# Multi-stage build for smaller final image
FROM golang:1.21-alpine AS builder

# Install build dependencies
RUN apk add --no-cache git ca-certificates tzdata

# Set working directory
WORKDIR /app

# Copy go mod files first for better caching
COPY go.mod go.sum ./

# Download dependencies
RUN go mod download

# Copy source code
COPY etcd-stress-tools.go ./

# Build the binary with optimizations
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -ldflags='-w -s -extldflags "-static"' \
    -a -installsuffix cgo \
    -o etcd-stress-tools .

# Final stage - minimal runtime image
FROM scratch

# Copy CA certificates for HTTPS requests
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

# Copy timezone data
COPY --from=builder /usr/share/zoneinfo /usr/share/zoneinfo

# Copy the binary
COPY --from=builder /app/etcd-stress-tools /usr/local/bin/etcd-stress-tools

# Create a non-root user (even though scratch doesn't have users, this documents intent)
USER 65534:65534

# Set entrypoint
ENTRYPOINT ["/usr/local/bin/etcd-stress-tools"]

# Default command arguments
CMD ["--help"]

# Labels for metadata
LABEL maintainer="Platform Team" \
      description="etcd Stress Testing Tool for Kubernetes/OpenShift" \
      version="1.0.0" \
      source="https://github.com/your-org/etcd-stress-tools"

# Health check (optional - requires shell which scratch doesn't have)
# HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
#   CMD ["/usr/local/bin/etcd-stress-tools", "--help"] || exit 1
