"""
Check that fintel/surrealdb-creds in Secrets Manager is valid JSON
and contains the expected user/pass keys.

Usage:
    python infra/check_surrealdb_secret.py [--region us-east-1]
"""
import json
import subprocess
import sys

REGION = "us-east-1"
SECRET_ID = "fintel/surrealdb-creds"

if "--region" in sys.argv:
    REGION = sys.argv[sys.argv.index("--region") + 1]

print(f"Fetching secret: {SECRET_ID} (region={REGION})")

result = subprocess.run(
    [
        "aws", "secretsmanager", "get-secret-value",
        "--secret-id", SECRET_ID,
        "--query", "SecretString",
        "--output", "text",
        "--region", REGION,
    ],
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    print(f"[FAIL] aws cli error:\n{result.stderr.strip()}")
    sys.exit(1)

raw = result.stdout.strip()
print(f"Raw value: {raw!r}")

try:
    data = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"[FAIL] JSON parse error: {e}")
    print("       Fix: aws secretsmanager put-secret-value --secret-id fintel/surrealdb-creds --secret-string '{{\"user\":\"root\",\"pass\":\"root\"}}'")
    sys.exit(1)

missing = [k for k in ("user", "pass") if not data.get(k)]
if missing:
    print(f"[FAIL] Missing or empty keys: {missing}")
    sys.exit(1)

print(f"[OK]   user={data['user']!r}  pass={'*' * len(data['pass'])}")
