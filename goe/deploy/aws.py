"""AWS API boundary for identity, SSM provisioning, and live status."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Any

import boto3


@dataclass(frozen=True)
class CommandResult:
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


class AwsDeploymentClient:
    def __init__(
        self,
        *,
        region: str,
        profile: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        poll_interval: float = 10.0,
    ) -> None:
        session_args: dict[str, Any] = {"region_name": region}
        if profile:
            session_args["profile_name"] = profile
        elif access_key_id and secret_access_key:
            session_args["aws_access_key_id"] = access_key_id
            session_args["aws_secret_access_key"] = secret_access_key
            if session_token:
                session_args["aws_session_token"] = session_token
        self.session = boto3.Session(**session_args)
        self.region = region
        self.ec2 = self.session.client("ec2")
        self.ssm = self.session.client("ssm")
        self.sts = self.session.client("sts")
        self.poll_interval = poll_interval

    def account_id(self) -> str:
        return str(self.sts.get_caller_identity()["Account"])

    def wait_ready(self, instances: dict[str, str], timeout: int = 600) -> set[str]:
        if not instances:
            return set()
        waiter = self.ec2.get_waiter("instance_running")
        waiter.wait(
            InstanceIds=list(instances.values()),
            WaiterConfig={"Delay": 10, "MaxAttempts": max(1, timeout // 10)},
        )
        deadline = time.monotonic() + timeout
        ready: set[str] = set()
        while time.monotonic() < deadline:
            response = self.ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": list(instances.values())}]
            )
            online_ids = {
                item["InstanceId"]
                for item in response.get("InstanceInformationList", [])
                if item.get("PingStatus") == "Online"
            }
            ready = {system_id for system_id, iid in instances.items() if iid in online_ids}
            if len(ready) == len(instances):
                return ready
            time.sleep(self.poll_interval)
        return ready

    def run_command(
        self,
        instance_id: str,
        commands: list[str],
        *,
        comment: str,
        timeout: int = 900,
    ) -> CommandResult:
        response = self.ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={
                "commands": commands,
                "executionTimeout": [str(timeout)],
            },
            Comment=comment[:100],
            TimeoutSeconds=timeout + 60,
        )
        command_id = response["Command"]["CommandId"]
        deadline = time.monotonic() + timeout + 120
        while time.monotonic() < deadline:
            try:
                invocation = self.ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except self.ssm.exceptions.InvocationDoesNotExist:
                time.sleep(self.poll_interval)
                continue
            status = invocation["Status"]
            if status == "Success":
                return CommandResult(
                    status="success",
                    exit_code=invocation.get("ResponseCode", 0),
                    stdout=invocation.get("StandardOutputContent", ""),
                    stderr=invocation.get("StandardErrorContent", ""),
                )
            if status in {"Failed", "Cancelled", "TimedOut", "Cancelling"}:
                return CommandResult(
                    status="timeout" if status == "TimedOut" else "failed",
                    exit_code=invocation.get("ResponseCode"),
                    stdout=invocation.get("StandardOutputContent", ""),
                    stderr=invocation.get("StandardErrorContent", ""),
                )
            time.sleep(self.poll_interval)
        return CommandResult(status="timeout", stderr=f"SSM command {command_id} timed out")

    def provision(
        self,
        instance_id: str,
        bucket: str,
        object_key: str,
        system_id: str,
        host_map: dict[str, str],
    ) -> CommandResult:
        destination = f"/opt/goe/{system_id}_deploy.sh"
        hosts = "\n".join(f"{address} {hostname}" for hostname, address in host_map.items())
        configure_hosts = (
            "sed -i '/# BEGIN GOE HOSTS/,/# END GOE HOSTS/d' /etc/hosts\n"
            "cat >> /etc/hosts <<'GOE_HOSTS_EOF'\n"
            "# BEGIN GOE HOSTS\n"
            f"{hosts}\n"
            "# END GOE HOSTS\n"
            "GOE_HOSTS_EOF"
        )
        commands = [
            # AWS-RunShellScript executes its command list with /bin/sh. Keep
            # the bootstrap POSIX-compatible; the generated scenario itself is
            # invoked explicitly with bash below.
            "set -eu",
            # The SSM agent can become available before cloud-init has finished
            # installing awscli and the other instance prerequisites.
            "cloud-init status --wait >/dev/null",
            "command -v aws >/dev/null || { echo 'aws CLI was not installed by cloud-init' >&2; exit 1; }",
            "install -d -m 700 /opt/goe",
            configure_hosts,
            f"aws s3 cp 's3://{bucket}/{object_key}' '{destination}' --only-show-errors",
            # Packages can be generated on Windows. Normalize before bash so
            # CRLF (or legacy CR) line endings cannot corrupt shell tokens.
            f"sed -i 's/\\r$//' '{destination}'",
            f"chmod 700 '{destination}'",
            f"bash '{destination}'",
        ]
        return self.run_command(instance_id, commands, comment=f"GoE deploy {system_id}")

    def check_listening_ports(self, instance_id: str, ports: list[int], system_id: str) -> CommandResult:
        if not ports:
            return CommandResult(status="success", exit_code=0)
        port_words = " ".join(str(port) for port in ports)
        script = (
            "set -eu\n"
            f"for port in {port_words}; do\n"
            "  if ! ss -H -lnt | awk '{print $4}' | grep -Eq \"[:.]${port}$\"; then\n"
            "    echo \"port ${port} is not listening\" >&2\n"
            "    exit 1\n"
            "  fi\n"
            "done"
        )
        return self.run_command(instance_id, [script], comment=f"GoE verify {system_id}", timeout=120)

    def live_status(self, instance_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not instance_ids:
            return {}
        response = self.ec2.describe_instances(InstanceIds=instance_ids)
        result: dict[str, dict[str, Any]] = {}
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                result[instance["InstanceId"]] = {
                    "instance_state": instance["State"]["Name"],
                    "public_ip": instance.get("PublicIpAddress"),
                    "private_ip": instance.get("PrivateIpAddress"),
                    "ssm_ready": False,
                }
        ssm = self.ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": instance_ids}]
        )
        for item in ssm.get("InstanceInformationList", []):
            if item["InstanceId"] in result:
                result[item["InstanceId"]]["ssm_ready"] = item.get("PingStatus") == "Online"
        return result


def public_port_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
