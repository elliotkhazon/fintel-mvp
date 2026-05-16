output "instance_id" {
  description = "EC2 instance ID — used by deploy.ps1 for SSM commands"
  value       = aws_instance.k3s.id
}

output "private_ip" {
  description = "EC2 private IP — SurrealDB reachable at this IP on port 8000 (hostPort)"
  value       = aws_instance.k3s.private_ip
}

output "surrealdb_host" {
  description = "SurrealDB endpoint for AgentCore and FastAPI (EC2 private IP + port 8000)"
  value       = "${aws_instance.k3s.private_ip}:8000"
}

output "kubeconfig_secret_name" {
  description = "Secrets Manager secret name holding the base64-encoded kubeconfig"
  value       = aws_secretsmanager_secret.kubeconfig.name
}
