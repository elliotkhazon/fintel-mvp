output "ec2_instance_profile_name" {
  value = aws_iam_instance_profile.ec2.name
}

output "agentcore_role_arn" {
  value = aws_iam_role.agentcore.arn
}

output "glue_role_arn" {
  value = aws_iam_role.glue.arn
}

output "ci_role_arns" {
  value = { for k, v in aws_iam_role.ci : k => v.arn }
}
