# macOS Binary Execution Issue - Solutions

## Problem

When running the compiled Go binary on macOS, you may encounter:

```
dyld[8852]: missing LC_UUID load command in /path/to/create-deployment
dyld[8852]: missing LC_UUID load command
Abort trap: 6
```

This is a **macOS code signing issue** related to how Go 1.22+ generates binaries on Apple Silicon.

---

## Solutions (Choose One)

### ✅ Solution 1: Use `go run` (Recommended for Development)

**Easiest and most reliable** - no binary execution issues:

```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go

# Run directly with go run
go run create-deployment.go --help
go run create-deployment.go --total-namespaces 5

# Set environment variables as usual
BUILD_PARALLELISM=5 go run create-deployment.go --total-namespaces 3
```

**Pros**:
- ✅ No code signing issues
- ✅ Always uses latest code changes
- ✅ No binary to maintain

**Cons**:
- Takes 1-2 seconds to compile on each run (negligible for long-running tests)

---

### ✅ Solution 2: Use Wrapper Script

We've created a wrapper script that uses `go run` internally:

```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go

# Use the wrapper script
./run-create-deployment.sh --help
./run-create-deployment.sh --total-namespaces 5

# Works with environment variables
BUILD_PARALLELISM=5 ./run-create-deployment.sh --total-namespaces 3
```

**Pros**:
- ✅ Simple to use (like a regular binary)
- ✅ No code signing issues
- ✅ Easy to remember

**Cons**:
- Same compilation overhead as `go run`

---

### ✅ Solution 3: Build with Specific Flags

Try building with linker flags that may help:

```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go

# Option A: Strip debug info
go build -ldflags="-w -s" -o create-deployment create-deployment.go

# Option B: Minimal build
go build -ldflags="-w" -o create-deployment create-deployment.go

# Option C: With explicit UUID
go build -ldflags="-w -s -buildid=" -o create-deployment create-deployment.go
```

Then try running:
```bash
./create-deployment --help
```

**Note**: This may still fail on some macOS versions, especially with Go 1.22+ on Apple Silicon.

---

### ✅ Solution 4: Code Sign the Binary

Sign the binary with your Apple Developer certificate (if available):

```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go

# Build the binary
go build -o create-deployment create-deployment.go

# Sign it (requires Apple Developer certificate)
codesign -s "Your Developer ID" create-deployment

# Or use ad-hoc signing (no certificate required, but limited)
codesign -s - create-deployment

# Verify signature
codesign -v create-deployment
```

**Pros**:
- ✅ Creates a proper signed binary

**Cons**:
- Requires Apple Developer certificate (or limited ad-hoc signing)
- More complex setup

---

### ✅ Solution 5: Use Python Implementation Instead

Since the Python implementation has the same features:

```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/python

python3 create-deployment.py --help
python3 create-deployment.py --total-namespaces 5
```

**Pros**:
- ✅ No binary execution issues
- ✅ Same functionality
- ✅ No compilation needed

**Cons**:
- Requires Python 3 and kubernetes Python client

---

## Recommended Workflow

### For Development/Testing (Local):

**Use `go run` or the wrapper script**:
```bash
# Option 1: Direct go run
go run create-deployment.go --total-namespaces 5

# Option 2: Wrapper script
./run-create-deployment.sh --total-namespaces 5
```

### For Production/CI (Linux):

**Build normally** (no macOS issues on Linux):
```bash
# On Linux, this works fine
go build -o create-deployment create-deployment.go
./create-deployment --total-namespaces 10
```

### For Distribution:

**Use Python version** or provide wrapper script with instructions to use `go run`.

---

## Why This Happens

### Technical Background

1. **Go 1.22+ Changes**: Go 1.22 introduced changes to binary generation on macOS
2. **Apple Silicon (ARM64)**: More strict code signing requirements
3. **LC_UUID Missing**: The binary lacks a required load command that macOS dyld expects
4. **Security Feature**: macOS requires proper code signing for binaries to execute

### Related Issues

- [golang/go#65334](https://github.com/golang/go/issues/65334)
- [golang/go#64875](https://github.com/golang/go/issues/64875)

This is a known issue in the Go community, especially on macOS 14+ (Sonoma) with Apple Silicon.

---

## Quick Reference

### Best Commands for Each Scenario

**Testing locally**:
```bash
go run create-deployment.go --total-namespaces 2
```

**Quick execution**:
```bash
./run-create-deployment.sh --total-namespaces 5
```

**With environment variables**:
```bash
BUILD_PARALLELISM=5 TOTAL_NAMESPACES=3 go run create-deployment.go
```

**Production deployment (Linux)**:
```bash
go build -o create-deployment create-deployment.go
./create-deployment --total-namespaces 100
```

---

## Testing the Solution

### Test 1: Verify go run works

```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go
go run create-deployment.go --help
```

**Expected**: Help text displays without errors.

### Test 2: Verify wrapper script works

```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go
./run-create-deployment.sh --help
```

**Expected**: Help text displays without errors.

### Test 3: Small test run

```bash
# Using go run
go run create-deployment.go --total-namespaces 1

# Or using wrapper
./run-create-deployment.sh --total-namespaces 1
```

**Expected**: Creates 1 namespace with deployments and build jobs.

---

## Summary

**Problem**: macOS code signing issue with Go binaries (`missing LC_UUID load command`)

**Root Cause**: Go 1.22+ on Apple Silicon + macOS security requirements

**Best Solutions**:
1. ✅ **Use `go run create-deployment.go`** (recommended for local testing)
2. ✅ **Use `./run-create-deployment.sh`** (wrapper script provided)
3. ✅ **Use Python version** (`python3 create-deployment.py`)

**For Production/Linux**: Regular `go build` works fine (no macOS issues)

**Status**: Not a bug in the code - it's a macOS platform limitation that affects all Go binaries on recent macOS versions with Apple Silicon.

---

## Additional Resources

- **Go Issue Tracker**: Search for "macOS LC_UUID" issues
- **Workaround**: Always use `go run` on macOS for development
- **Alternative**: Python implementation has identical functionality
- **Future**: This may be fixed in future Go versions

---

## Files Location

- **Go Source**: `go/create-deployment.go`
- **Wrapper Script**: `go/run-create-deployment.sh` (executable)
- **Python Alternative**: `python/create-deployment.py`
- **This Guide**: `go/MACOS_BINARY_FIX.md`
