# AWS deployment

GoE can deploy a successfully packaged scenario to an isolated AWS VPC. Each
`System` in the entity graph becomes one Ubuntu 22.04 EC2 instance. Deployment
is explicit and separate from scenario generation.

## Prerequisites

- Terraform 1.5 or later on `PATH`
- AWS credentials available through the normal AWS credential chain, or an AWS
  shared-credentials profile
- AWS CLI on `PATH` (`aws sts get-caller-identity` should succeed)
- Permission to manage EC2/VPC resources, IAM roles and instance profiles, S3
  buckets and objects, and SSM Run Command
- An IPv4 CIDR identifying the operator, normally the operator's public IP with
  a `/32` suffix

Configuration defaults can be set in `goe.toml`:

```toml
[deploy.aws]
region = "us-east-1"
instance_type = "t3.small"
attacker_cidr = "203.0.113.5/32"
# profile = "sandbox"
```

Flags take precedence over configuration. `GOE_AWS_REGION`,
`GOE_AWS_PROFILE`, `GOE_AWS_INSTANCE_TYPE`, and `GOE_ATTACKER_CIDR` are also
supported.

## Deploy

```bash
goe deploy aws output/<run_id> --attacker-cidr 203.0.113.5/32
```

The command validates the package and AWS identity, shows the Terraform plan,
and asks before applying it. Use `--yes` for non-interactive execution. Use
`--rollback-on-failure` when a failed provisioning or readiness check should
immediately trigger Terraform destroy; otherwise infrastructure is preserved
for diagnosis.

After correcting a provisioning problem, reuse the existing instances and
Terraform state without another plan/apply:

```bash
goe deploy aws output/<run_id> --retry-provisioning
```

The packager writes `aws_spec.json`. It records the deployment topology and
script filenames without copying entity-graph secrets. Systems referenced by an
operator-originated network-reach edge, plus requirement-free initial systems,
are public entry points. At least one public entry point and one exposed port on
each public system are required.

## Network and provisioning

- Declared `exposed_ports` accept TCP connections only from the attacker CIDR
  and from inside the scenario VPC.
- Declared `internal_ports` accept TCP connections only from inside the VPC.
- Systems without public entry points use a private subnet and NAT gateway.
- Instances have encrypted GP3 storage, require IMDSv2, and do not require an
  SSH key pair.
- Generated scripts are stored in a private, encrypted deployment-specific S3
  bucket. A narrowly scoped instance role downloads them, and SSM Run Command
  executes them as root.
- Before provisioning, every instance receives the graph hostname-to-private-IP
  map so cross-system references resolve the same way they did in local tests.
- GoE verifies SSM provisioning, declared listening ports, and externally
  reachable public ports. It does not automatically execute `solve.sh`.

## Local state and inventory

State is intentionally local to the output package:

```text
output/<run_id>/
  aws_spec.json
  aws_inventory.json
  .aws/
    manifest.json
    terraform/
      terraform.tfstate
      terraform.tfvars.json
```

Treat this directory as sensitive and do not delete it while resources exist.
Losing the Terraform state makes complete teardown substantially harder.
Credentials are passed through the process environment and are not written to
the variables file or inventory.

Inspect current EC2 and SSM state with:

```bash
goe status output/<run_id>
```

If the stored shared-credentials profile is no longer available, pass
`--profile <name>` to status or destroy to override it without changing state.

## Teardown

```bash
goe destroy output/<run_id>
```

The command identifies the AWS account and region and asks for confirmation.
It destroys the EC2 instances, VPC resources, IAM resources, S3 objects, and S3
bucket. The final inventory remains as a destroyed-deployment record.

Automatic TTL destruction is not implemented. Private systems create a NAT
gateway, which incurs hourly and data-processing charges until teardown. EC2,
EBS, public IPv4 addresses, and S3 can also incur charges. Always run destroy
when the environment is no longer needed.
