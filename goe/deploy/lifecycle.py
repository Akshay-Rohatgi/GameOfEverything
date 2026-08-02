"""High-level AWS deploy, status, and destroy operations."""

from __future__ import annotations

import ipaddress
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from goe.config import GoEConfig
from goe.deploy.aws import AwsDeploymentClient, CommandResult, public_port_reachable
from goe.deploy.models import (
    DeploymentManifest,
    DeploymentState,
    ProvisionStatus,
    SystemDeployment,
    load_manifest,
    manifest_path,
    utc_now,
    write_manifest,
)
from goe.deploy.spec import AwsDeploymentSpec, load_deployment_spec
from goe.deploy.terraform import TerraformRunner


class DeploymentError(RuntimeError):
    """The requested deployment lifecycle operation could not complete."""


class DeploymentCancelled(DeploymentError):
    """The operator declined a planned state change."""


ConfirmPlan = Callable[[str], bool]
Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _validate_package(out_dir: Path, spec: AwsDeploymentSpec) -> None:
    if not spec.entry_system_ids:
        raise DeploymentError("AWS deployment requires at least one public entry-point system")
    supported = {"ubuntu_22_04"}
    for system in spec.systems:
        if system.public and not system.exposed_ports:
            raise DeploymentError(
                f"public entry-point system {system.id} must declare at least one exposed port"
            )
        if system.os not in supported:
            raise DeploymentError(
                f"system {system.id} uses unsupported AWS OS {system.os!r}; "
                "only ubuntu_22_04 is currently supported"
            )
        script = out_dir / system.script
        if not script.is_file():
            raise DeploymentError(f"deployment script missing for system {system.id}: {script}")


def _validate_cidr(value: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise DeploymentError(f"invalid attacker CIDR {value!r}; use an address such as 203.0.113.5/32") from exc
    if network.version != 4:
        raise DeploymentError("only IPv4 attacker CIDRs are currently supported")
    if network.prefixlen == 0:
        raise DeploymentError("attacker CIDR may not expose the scenario to the entire internet")
    return str(network)


def _terraform_environment(
    config: GoEConfig, region: str, profile: str | None
) -> dict[str, str | None]:
    environment: dict[str, str | None] = {
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
    }
    if profile:
        environment["AWS_PROFILE"] = profile
        # An explicit profile must win consistently for boto3 and Terraform.
        environment["AWS_ACCESS_KEY_ID"] = None
        environment["AWS_SECRET_ACCESS_KEY"] = None
        environment["AWS_SESSION_TOKEN"] = None
    elif config.aws_access_key_id and config.aws_secret_access_key:
        environment["AWS_ACCESS_KEY_ID"] = config.aws_access_key_id
        environment["AWS_SECRET_ACCESS_KEY"] = config.aws_secret_access_key
        if config.aws_session_token:
            environment["AWS_SESSION_TOKEN"] = config.aws_session_token
    return environment


def _client(config: GoEConfig, region: str, profile: str | None) -> AwsDeploymentClient:
    return AwsDeploymentClient(
        region=region,
        profile=profile,
        access_key_id=config.aws_access_key_id or None,
        secret_access_key=config.aws_secret_access_key or None,
        session_token=config.aws_session_token or None,
    )


@contextmanager
def _operation_lock(out_dir: Path) -> Iterator[None]:
    lock = out_dir / ".aws" / "operation.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise DeploymentError(
            f"another deployment operation may be running ({lock}); "
            "remove the lock only after confirming no goe process is active"
        ) from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _existing_deployment_blocks(out_dir: Path) -> None:
    path = manifest_path(out_dir)
    if not path.exists():
        return
    manifest = load_manifest(out_dir)
    retryable_plan = (
        manifest.state == DeploymentState.FAILED
        and not manifest.infrastructure_started
        and manifest.vpc_id is None
    )
    if manifest.state != DeploymentState.DESTROYED and not retryable_plan:
        raise DeploymentError(
            f"output already has an AWS deployment in state {manifest.state.value!r}; "
            f"run 'goe status {out_dir}' or 'goe destroy {out_dir}'"
        )


def _manifest_from_outputs(
    manifest: DeploymentManifest,
    outputs: dict,
    spec: AwsDeploymentSpec,
) -> None:
    try:
        manifest.vpc_id = str(outputs["vpc_id"])
        manifest.artifact_bucket = str(outputs["artifact_bucket"])
        systems = outputs["systems"]
        expected = {system.id for system in spec.systems}
        if set(systems) != expected:
            raise DeploymentError(
                "Terraform system outputs do not match the package specification"
            )
        manifest.systems = {
            system_id: SystemDeployment(
                system_id=system_id,
                instance_id=str(info["instance_id"]),
                public_ip=str(info["public_ip"]) if info.get("public_ip") else None,
                private_ip=str(info["private_ip"]),
                security_group_id=str(info["security_group_id"]),
            )
            for system_id, info in systems.items()
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise DeploymentError("Terraform outputs did not contain the expected AWS resources") from exc


def _provision_all(
    aws: AwsDeploymentClient,
    spec: AwsDeploymentSpec,
    manifest: DeploymentManifest,
) -> dict[str, CommandResult]:
    if manifest.artifact_bucket is None:
        raise DeploymentError("Terraform did not return an artifact bucket")
    results: dict[str, CommandResult] = {}
    specs = {system.id: system for system in spec.systems}
    host_map = {
        specs[system_id].hostname: deployed.private_ip
        for system_id, deployed in manifest.systems.items()
    }

    def provision(system_id: str) -> CommandResult:
        deployed = manifest.systems[system_id]
        return aws.provision(
            deployed.instance_id,
            manifest.artifact_bucket or "",
            f"scripts/{system_id}.sh",
            system_id,
            host_map,
        )

    with ThreadPoolExecutor(max_workers=min(4, len(spec.systems))) as pool:
        futures = {pool.submit(provision, system.id): system.id for system in spec.systems}
        for future in as_completed(futures):
            system_id = futures[future]
            try:
                results[system_id] = future.result()
            except Exception as exc:  # AWS SDK exceptions are recorded per system
                results[system_id] = CommandResult(status="failed", stderr=str(exc))
    return results


def _wait_public_port(host: str, port: int, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if public_port_reachable(host, port):
            return True
        time.sleep(3)
    return False


def _verify_all(
    aws: AwsDeploymentClient,
    spec: AwsDeploymentSpec,
    manifest: DeploymentManifest,
) -> list[str]:
    errors: list[str] = []
    for system in spec.systems:
        deployed = manifest.systems[system.id]
        ports = sorted(set(system.exposed_ports + system.internal_ports))
        result = aws.check_listening_ports(deployed.instance_id, ports, system.id)
        for port in ports:
            deployed.readiness[f"listening:{port}"] = result.status == "success"
        if result.status != "success":
            message = result.stderr.strip() or f"declared ports are not listening on {system.id}"
            deployed.error = message
            errors.append(f"{system.id}: {message}")
            continue
        if system.public and deployed.public_ip:
            for port in system.exposed_ports:
                reachable = _wait_public_port(deployed.public_ip, port)
                deployed.readiness[f"public:{port}"] = reachable
                if not reachable:
                    errors.append(f"{system.id}: public port {port} is not reachable")
        elif system.public:
            errors.append(f"{system.id}: public entry point has no public IP")
    return errors


def _provision_and_verify(
    aws: AwsDeploymentClient,
    spec: AwsDeploymentSpec,
    manifest: DeploymentManifest,
    out_dir: Path,
    progress: Progress,
) -> DeploymentManifest:
    """Run the repeatable portion of deployment against existing instances."""
    manifest.state = DeploymentState.PROVISIONING
    manifest.error = None
    for deployed in manifest.systems.values():
        deployed.ssm_ready = False
        deployed.provision_status = ProvisionStatus.PENDING
        deployed.provision_exit_code = None
        deployed.readiness = {}
        deployed.error = None
    write_manifest(out_dir, manifest)

    progress("Waiting for EC2 and SSM readiness")
    instance_map = {
        system_id: deployed.instance_id
        for system_id, deployed in manifest.systems.items()
    }
    ready = aws.wait_ready(instance_map)
    for system_id, deployed in manifest.systems.items():
        deployed.ssm_ready = system_id in ready
        if system_id not in ready:
            deployed.error = "SSM did not become ready before timeout"
    write_manifest(out_dir, manifest)
    if len(ready) != len(instance_map):
        missing = sorted(set(instance_map) - ready)
        raise DeploymentError(f"SSM did not become ready for: {', '.join(missing)}")

    progress("Provisioning scenario systems through SSM")
    provision_results = _provision_all(aws, spec, manifest)
    failures: list[str] = []
    for system_id, result in provision_results.items():
        deployed = manifest.systems[system_id]
        deployed.provision_status = ProvisionStatus(result.status)
        deployed.provision_exit_code = result.exit_code
        if result.status != "success":
            deployed.error = result.stderr.strip() or f"SSM provisioning {result.status}"
            failures.append(f"{system_id}: {deployed.error}")
    write_manifest(out_dir, manifest)
    if failures:
        raise DeploymentError("provisioning failed: " + "; ".join(failures))

    manifest.state = DeploymentState.VERIFYING
    write_manifest(out_dir, manifest)
    progress("Verifying declared services")
    verification_errors = _verify_all(aws, spec, manifest)
    if verification_errors:
        raise DeploymentError("verification failed: " + "; ".join(verification_errors))

    manifest.state = DeploymentState.READY
    manifest.error = None
    write_manifest(out_dir, manifest)
    return manifest


def deploy(
    out_dir: Path,
    *,
    region: str,
    instance_type: str,
    attacker_cidr: str,
    profile: str | None = None,
    rollback_on_failure: bool = False,
    confirm_plan: ConfirmPlan | None = None,
    progress: Progress = _noop,
    config: GoEConfig | None = None,
    terraform_factory=TerraformRunner,
    aws_factory=None,
) -> DeploymentManifest:
    """Provision and configure a packaged scenario in AWS."""
    out_dir = Path(out_dir).resolve()
    if not out_dir.is_dir():
        raise DeploymentError(f"output directory does not exist: {out_dir}")
    spec = load_deployment_spec(out_dir)
    _validate_package(out_dir, spec)
    attacker_cidr = _validate_cidr(attacker_cidr)
    config = config or GoEConfig.get()

    with _operation_lock(out_dir):
        _existing_deployment_blocks(out_dir)
        environment = _terraform_environment(config, region, profile)
        terraform = terraform_factory(out_dir, environment=environment)
        terraform.ensure_available()
        try:
            aws = aws_factory(config, region, profile) if aws_factory else _client(config, region, profile)
            progress("Checking AWS identity")
            account_id = aws.account_id()
        except Exception as exc:
            raise DeploymentError(f"AWS identity check failed: {exc}") from exc
        now = utc_now()
        manifest = DeploymentManifest(
            run_id=spec.run_id,
            output_dir=str(out_dir),
            region=region,
            profile=profile,
            account_id=account_id,
            instance_type=instance_type,
            attacker_cidr=attacker_cidr,
            state=DeploymentState.PLANNING,
            created_at=now,
            updated_at=now,
        )
        write_manifest(out_dir, manifest)

        try:
            progress("Preparing Terraform")
            terraform.prepare(
                spec,
                region=region,
                instance_type=instance_type,
                attacker_cidr=attacker_cidr,
            )
            terraform.init()
            plan = terraform.plan()
            if confirm_plan is not None and not confirm_plan(plan):
                raise DeploymentCancelled("deployment cancelled before Terraform apply")

            manifest.state = DeploymentState.APPLYING
            manifest.infrastructure_started = True
            write_manifest(out_dir, manifest)
            progress("Applying Terraform infrastructure")
            _manifest_from_outputs(manifest, terraform.apply(), spec)
            return _provision_and_verify(aws, spec, manifest, out_dir, progress)
        except Exception as exc:
            manifest.state = DeploymentState.FAILED
            manifest.error = str(exc)
            write_manifest(out_dir, manifest)
            if rollback_on_failure and manifest.infrastructure_started:
                progress("Rolling back failed deployment")
                try:
                    terraform.destroy()
                    manifest.state = DeploymentState.DESTROYED
                    manifest.error = f"deployment failed and was rolled back: {exc}"
                    write_manifest(out_dir, manifest)
                except Exception as rollback_exc:
                    manifest.error = f"{exc}; rollback also failed: {rollback_exc}"
                    write_manifest(out_dir, manifest)
            if isinstance(exc, DeploymentError):
                raise
            raise DeploymentError(str(exc)) from exc


def retry_provisioning(
    out_dir: Path,
    *,
    profile: str | None = None,
    rollback_on_failure: bool = False,
    progress: Progress = _noop,
    config: GoEConfig | None = None,
    terraform_factory=TerraformRunner,
    aws_factory=None,
) -> DeploymentManifest:
    """Retry SSM provisioning and verification without recreating infrastructure."""
    out_dir = Path(out_dir).resolve()
    spec = load_deployment_spec(out_dir)
    _validate_package(out_dir, spec)
    config = config or GoEConfig.get()

    with _operation_lock(out_dir):
        manifest = load_manifest(out_dir)
        if manifest.state != DeploymentState.FAILED:
            raise DeploymentError(
                f"provisioning can only be retried from a failed deployment; "
                f"current state is {manifest.state.value!r}"
            )
        if (
            not manifest.infrastructure_started
            or not manifest.vpc_id
            or not manifest.artifact_bucket
            or not manifest.systems
        ):
            raise DeploymentError(
                "failed deployment has no reusable AWS infrastructure; run a normal deploy"
            )
        expected = {system.id for system in spec.systems}
        if set(manifest.systems) != expected:
            raise DeploymentError("deployment inventory does not match the packaged system topology")

        effective_profile = profile or manifest.profile
        try:
            aws = (
                aws_factory(config, manifest.region, effective_profile)
                if aws_factory
                else _client(config, manifest.region, effective_profile)
            )
            progress("Checking AWS identity")
            account_id = aws.account_id()
        except Exception as exc:
            raise DeploymentError(f"AWS identity check failed: {exc}") from exc
        if account_id != manifest.account_id:
            raise DeploymentError(
                f"AWS account mismatch: deployment belongs to {manifest.account_id}, "
                f"but current credentials use {account_id}"
            )

        try:
            return _provision_and_verify(aws, spec, manifest, out_dir, progress)
        except Exception as exc:
            manifest.state = DeploymentState.FAILED
            manifest.error = str(exc)
            write_manifest(out_dir, manifest)
            if rollback_on_failure:
                progress("Rolling back failed deployment")
                environment = _terraform_environment(config, manifest.region, effective_profile)
                terraform = terraform_factory(out_dir, environment=environment)
                try:
                    terraform.ensure_available()
                    terraform.destroy()
                    manifest.state = DeploymentState.DESTROYED
                    manifest.error = f"provisioning retry failed and was rolled back: {exc}"
                    write_manifest(out_dir, manifest)
                except Exception as rollback_exc:
                    manifest.error = f"{exc}; rollback also failed: {rollback_exc}"
                    write_manifest(out_dir, manifest)
            if isinstance(exc, DeploymentError):
                raise
            raise DeploymentError(str(exc)) from exc


def status(
    out_dir: Path,
    *,
    profile: str | None = None,
    config: GoEConfig | None = None,
    aws_factory=None,
) -> tuple[DeploymentManifest, dict[str, dict]]:
    out_dir = Path(out_dir).resolve()
    manifest = load_manifest(out_dir)
    if manifest.state == DeploymentState.DESTROYED or not manifest.systems:
        return manifest, {}
    config = config or GoEConfig.get()
    effective_profile = profile or manifest.profile
    try:
        aws = (
            aws_factory(config, manifest.region, effective_profile)
            if aws_factory
            else _client(config, manifest.region, effective_profile)
        )
        live = aws.live_status([system.instance_id for system in manifest.systems.values()])
    except Exception as exc:
        raise DeploymentError(f"unable to query live AWS status: {exc}") from exc
    return manifest, live


def destroy(
    out_dir: Path,
    *,
    profile: str | None = None,
    progress: Progress = _noop,
    config: GoEConfig | None = None,
    terraform_factory=TerraformRunner,
) -> DeploymentManifest:
    out_dir = Path(out_dir).resolve()
    config = config or GoEConfig.get()
    with _operation_lock(out_dir):
        manifest = load_manifest(out_dir)
        if manifest.state == DeploymentState.DESTROYED:
            return manifest
        effective_profile = profile or manifest.profile
        environment = _terraform_environment(config, manifest.region, effective_profile)
        terraform = terraform_factory(out_dir, environment=environment)
        terraform.ensure_available()
        manifest.state = DeploymentState.DESTROYING
        manifest.error = None
        write_manifest(out_dir, manifest)
        progress("Destroying AWS resources with Terraform")
        try:
            terraform.destroy()
        except Exception as exc:
            manifest.state = DeploymentState.FAILED
            manifest.error = f"destroy failed: {exc}"
            write_manifest(out_dir, manifest)
            if isinstance(exc, DeploymentError):
                raise
            raise DeploymentError(str(exc)) from exc
        manifest.state = DeploymentState.DESTROYED
        manifest.error = None
        write_manifest(out_dir, manifest)
        return manifest
