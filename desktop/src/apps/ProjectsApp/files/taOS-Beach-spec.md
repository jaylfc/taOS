# taOS Beach - SPEC

## Overview

target: 'taOS Beach' - a sandbox system where agents can request, share, spin up and create LXCs/containers for projects, giving taOS a Coolify-like environment provisioning capability.

## 1. Provisioning Control Plane

### 1.1 Request Flow
- Agent submits sandbox request with specifications:
  - Container type (LXC/Docker)
  - Resource quota (CPU, memory, disk)
  - Network configuration (ports, DNS)
  - Project/agent association

### 1.2 Policy/Consent Layer
- Reuses existing `scope-requests` and `Decisions` apps for authorization
- Separation of concerns:
  - Request validation and policy checking
  - Independent approval workflow
  - Action execution control

### 1.3 Provisioning Lifecycle
1. **Create**: Request approved -> provisioning initiated
2. **Share**: Per-project grants for resource access
3. **Lifecycle Management**: Monitoring and garbage collection
4. **Destroy**: Clean teardown when no longer needed

### 1.4 Integration Points
- Agent audit layer for tracking all requests
- Existing project system for authorization and grants
- Decisions app for policy enforcement

## 2. Backends

### 2.1 Container Runtimes
- **LXC**: Existing LXC implementation
- **Docker**: Pre-existing container runtime
- Both run in parallel across cluster nodes

### 2.2 Scheduling & Capacity
- Node-level capacity awareness
- Hosts with 4GB RAM = core-only workloads
- Resource allocation based on host capabilities
- Load balancing across available nodes

### 2.3 Cluster Architecture
- Multi-node deployment support
- Resource pooling and sharing
- Fallback mechanisms for single-node operation

## 3. Quotas & Resource Hygiene

### 3.1 Per-Project Quotas
- Resource limits per project:
  - Maximum containers
  - CPU allocation
  - Memory allocation
  - Disk space allocation

### 3.2 Per-Agent Quotas
- Individual agent resource limits
- Fair sharing across agents
- Dynamic adjustment based on workload

### 3.3 Network Hygiene
- **No core-port binds**: Avoid conflicts with system ports
- **Magic DNS names**: Automatically generated for spun-up environments
- Port allocation automation
- Network isolation between sandboxes

### 3.4 Resource Monitoring
- Real-time resource usage tracking
- Quota enforcement and alerting
- Automatic cleanup of exceeded quotas

## 4. Beach App (OS Integration)

### 4.1 Human Interface
- Live surfaces design law: auto-refresh interfaces
- Running sandboxes management view
- Resource dashboard for all beach environments

### 4.2 Management Features
- Create new sandbox environments
- Configure existing sandboxes
- View resource usage and status
- Lifecycle control (start/stop/restart/destroy)

### 4.3 Integration with taOS
- Single-click access from OS level
- Auto-discovery of provisioned environments
- Unified management interface

## 5. Multi-User Isolation

### 5.1 Boundary Enforcement
- Strict isolation between sandboxes
- Resource boundary controls
- Access control per project/agent

### 5.2 Security Considerations
- Multi-level access controls
- Network segmentation
- Resource quota enforcement
- Auditing of all operations

### 5.3 Audit Trail
- Comprehensive logging of all operations
- Integration with existing agent audit layer
- Compliance tracking and reporting

## 6. Phase 1 Cut (Minimum Viable Slice)

### 6.1 Core Requirements
- Single-node LXC + Docker deployment
- Request approval workflow
- Basic create/destroy lifecycle
- Quota enforcement
- Simple project list view

### 6.2 Timeline
- Initial deployment focus on core functionality
- Incremental enhancement based on user feedback
- Scalability improvements in subsequent phases

## Implementation Notes

### Dependencies
- Existing `scope-requests` system
- `Decisions` app for policy enforcement
- Agent audit infrastructure
- Current LXC and Docker container runtimes

### Architecture Principles
- Authorization separated from action execution
- Resource hygiene and security by default
- Live surfaces with auto-refresh
- Progressive enhancement approach

## References

- Jay's vision: 2026-08-09, DIRECT
- Existing taOS Coolify-like provisioning concepts
- Current container runtime implementations
- Agent scope system and decisions framework