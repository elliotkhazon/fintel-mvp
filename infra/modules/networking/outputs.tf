output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "sg_k3s_id" {
  value = aws_security_group.k3s.id
}

output "sg_endpoints_id" {
  value = aws_security_group.endpoints.id
}

output "private_route_table_id" {
  value = aws_route_table.private.id
}
