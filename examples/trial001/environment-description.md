# KASBench Environment Description

**Run ID:** trial001

---

## Metadata

| Field | Value |
|-------|-------|
| Run ID | trial001 |
| Environment Profile | small |
| AWS Region | us-east-1 |
| Availability Zone | us-east-1a |
| Created At | 2026-05-31T15:03:30Z |
| OpenTofu Version | detected-at-apply-time |
| AWS Provider Version | detected-at-apply-time |
| Git Commit Hash | not available |

---

## Network

### VPC

| Field | Value |
|-------|-------|
| VPC ID | vpc-0a9060c64b5cc3bdf |
| VPC CIDR | 10.0.0.0/16 |

### Subnets

| Subnet | ID | CIDR |
|--------|----|------|
| Public | subnet-0ea11172485b39b3a | 10.0.1.0/24 |
| Private | subnet-0846820aa42aa3232 | 10.0.2.0/24 |

### Gateways

| Gateway | ID |
|---------|----|
| Internet Gateway | igw-00ed76cca0df0ebce |
| NAT Gateway | nat-0ea258198eb5992c4 |

### Route Tables

| Route Table | ID |
|-------------|----|
| private | rtb-07b0b3227baea9d63 |
| public | rtb-03beabaa1c9f6b296 |

---

## Security Groups

| Name | ID | Rules |
|------|----|-------|
| kasbench-runner-20260531150053370200000010 | sg-0529205bbb3573d09 | SSH from bastion only; all egress |
| kasbench-cp-2026053115005316540000000d | sg-0c677b76ed4cb8e1f | 6443 from workers/runner; 2379-2380 self; 10250 from workers; SSH from bastion |
| kasbench-nlb-2026053115005320590000000e | sg-0420f4a7aeb6ead5e | TCP from benchmark-runner only |
| kasbench-worker-2026053115005320700000000f | sg-06e99f515b6febfef | 10250 from CP; 30000-32767 self; 9090/9100 self; SSH from bastion; all egress |

---

## IAM Roles

| Component | Role Name | Instance Profile |
|-----------|-----------|-----------------|
| benchmark_runner | kasbench-runner-20260531150050572300000002 | kasbench-runner-20260531150050907300000008 |
| control_plane | kasbench-cp-20260531150050572400000004 | kasbench-cp-2026053115005093000000000a |
| worker_node | kasbench-worker-20260531150050572500000005 | kasbench-worker-20260531150050916600000009 |

---

## Compute

### Control Plane

| Field | Value |
|-------|-------|
| Instance ID | i-0ba502902d8407d74 |
| Private IP | 10.0.2.206 |
| Instance Type | t3.small |
| AMI ID | ami-0e9bb5aa03403fb04 |
| Architecture | amd64 |
| Subnet ID | subnet-0846820aa42aa3232 |
| Root Volume ID | vol-0e86e25bfc75c465f |

### Workers — amd64

| Index | Instance ID | Private IP | Instance Type | AMI ID | Subnet ID | Root Volume ID |
|-------|-------------|------------|---------------|--------|-----------|----------------|
| 0 | i-0ea9111b2ccadc0d4 | 10.0.2.116 | t3.small | ami-0e9bb5aa03403fb04 | subnet-0846820aa42aa3232 | vol-0dfbed596376cb3a6 |

### Workers — arm64

| Index | Instance ID | Private IP | Instance Type | AMI ID | Subnet ID | Root Volume ID |
|-------|-------------|------------|---------------|--------|-----------|----------------|
| 0 | i-0054645812ae7f5a9 | 10.0.2.4 | t4g.small | ami-03647711f14b625b8 | subnet-0846820aa42aa3232 | vol-0f1343cc9b3f284f8 |

### Benchmark Runner

| Field | Value |
|-------|-------|
| Instance ID | i-06350b695b043ead2 |
| Public IP | 174.129.166.9 |
| Private IP | 10.0.1.156 |
| Instance Type | t3.small |
| AMI ID | ami-0e9bb5aa03403fb04 |
| Architecture | amd64 |
| Subnet ID | subnet-0ea11172485b39b3a |
| Root Volume ID | vol-0ce3692555e86e8d9 |

---

## Load Balancing

### Network Load Balancer

| Field | Value |
|-------|-------|
| DNS Name | kasb-20260531150058220800000013-b35bd70de6ea7606.elb.us-east-1.amazonaws.com |
| ARN | arn:aws:elasticloadbalancing:us-east-1:377288663341:loadbalancer/net/kasb-20260531150058220800000013/b35bd70de6ea7606 |
| Scheme | internal |

### Listeners

| Name | Port | Protocol |
|------|------|----------|
| http | 80 | TCP |

### Target Groups

| Name | ARN | Port |
|------|-----|------|
| http | arn:aws:elasticloadbalancing:us-east-1:377288663341:targetgroup/kasb-20260531150053372000000011/8b4cb5908623b58f | 30080 |

---

## Storage

### EBS Volumes

| Name | Volume ID | Size (GiB) | Type | IOPS | Throughput | AZ | Workload |
|------|-----------|------------|------|------|------------|-----|----------|
| etcd | vol-03618374286b44b3c | 20 | gp3 | 3000 | 125 | us-east-1a | etcd |

### Storage Class Metadata

```json
{"default_workload_storage":{"description":"Default StorageClass for PostgreSQL, Kafka, Prometheus PVCs","encrypted":true,"fs_type":"ext4","volume_type":"gp3"},"high_iops_storage":{"description":"High-IOPS StorageClass for Kafka and PostgreSQL","encrypted":true,"fs_type":"ext4","iops":6000,"throughput":250,"volume_type":"gp3"}}
```

---

## External Dependencies

### Bastion Host

| Field | Value |
|-------|-------|
| Instance ID | N/A |
| Private IP | N/A |
| Name | N/A |

### S3 Run Bucket

| Field | Value |
|-------|-------|
| Bucket Name | kasbench-test-20260528-377288663341-us-east-1-an  |
| Environment Prefix | environment/ |
| Reports Prefix | reports/ |
| Trials Prefix | trials/ |

---
