import boto3, sys, subprocess

ec2 = boto3.client('ec2', region_name='us-east-1')
instance_id = subprocess.check_output(
    "terraform -chdir=infra output -raw k3s_instance_id", shell=True
).decode().strip()
r = ec2.get_console_output(InstanceId=instance_id, Latest=True)
output = r.get('Output', '(no output yet)')
lines = output.splitlines()
sys.stdout.buffer.write(('\n'.join(lines[-60:]) + '\n').encode('utf-8', 'replace'))
