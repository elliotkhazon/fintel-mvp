#!/usr/bin/env bash
# k3s bootstrap — Phase 2: SurrealDB on k3s
# Runs once on first boot. Logs written to /var/log/user_data.log and CloudWatch.
set -euo pipefail
exec > >(tee /var/log/user_data.log | logger -t user_data -s) 2>&1

ENV="${env}"
AWS_REGION="${aws_region}"
ECR_REPO_URL="${ecr_repo_url}"
ECR_REGISTRY=$(echo "$ECR_REPO_URL" | cut -d'/' -f1)
KUBECTL="/usr/local/bin/kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml"

# ── Step 0a: Install AWS CLI ──────────────────────────────────────────────────
# Ubuntu 22.04 does not ship the AWS CLI — install v2 before any aws commands.

echo "==> [0a/5] Installing AWS CLI v2"
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
python3 -c "
import zipfile, os
with zipfile.ZipFile('/tmp/awscliv2.zip') as z:
    for info in z.infolist():
        z.extract(info, '/tmp/')
        perm = info.external_attr >> 16
        if perm:
            os.chmod('/tmp/' + info.filename, perm)
"
/tmp/aws/install
rm -rf /tmp/awscliv2.zip /tmp/aws
echo "  [ok] AWS CLI $(aws --version)"

# ── Step 0b: Ensure SSM agent is running ──────────────────────────────────────
# Ubuntu 22.04 ships SSM agent as a snap. Start it early so the instance
# registers with SSM before the rest of bootstrap runs.

echo "==> [0b/5] Starting SSM agent"
if systemctl list-units --type=service | grep -q "snap.amazon-ssm-agent"; then
  systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent || true
elif systemctl list-units --type=service | grep -q "amazon-ssm-agent"; then
  systemctl enable --now amazon-ssm-agent || true
else
  # Not pre-installed — install via snap
  snap install amazon-ssm-agent --classic
  systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent
fi
echo "  [ok] SSM agent started"

# ── Step 1: Install k3s ───────────────────────────────────────────────────────

K3S_VERSION="v1.29.4+k3s1"
echo "==> [1/5] Installing k3s $K3S_VERSION"
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION="$K3S_VERSION" \
  K3S_KUBECONFIG_MODE="644" \
  sh -s - \
  --disable=traefik \
  --disable=servicelb \
  --disable=metrics-server

until $KUBECTL get nodes 2>/dev/null | grep -q " Ready"; do
  echo "  waiting for k3s node..."
  sleep 5
done
echo "  [ok] k3s ready"

# k3s/flannel rewrites nftables rules. Re-open SSH after flannel has settled
# so the port stays reachable via EC2 Instance Connect.
cat > /etc/systemd/system/fintel-ssh-allow.service <<'EOF'
[Unit]
Description=Re-open SSH port after k3s/flannel nftables setup
After=k3s.service
Wants=k3s.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 30
ExecStart=/bin/bash -c 'nft insert rule inet filter input tcp dport 22 accept 2>/dev/null || iptables -I INPUT -p tcp --dport 22 -j ACCEPT'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now fintel-ssh-allow.service
echo "  [ok] fintel-ssh-allow.service installed"

# Also re-open SSM agent ports after flannel settles (outbound HTTPS via NAT).
# k3s nftables rules can block host-level outbound; restart the agent after flannel.
cat > /etc/systemd/system/fintel-ssm-restart.service <<'EOF'
[Unit]
Description=Restart SSM agent after k3s/flannel nftables setup
After=k3s.service fintel-ssh-allow.service
Wants=k3s.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 35
ExecStart=/bin/bash -c 'systemctl restart snap.amazon-ssm-agent.amazon-ssm-agent 2>/dev/null || systemctl restart amazon-ssm-agent 2>/dev/null || true'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now fintel-ssm-restart.service
echo "  [ok] fintel-ssm-restart.service installed"

# Create ECR pull secret so containerd (via Kubernetes imagePullSecrets) can
# authenticate against the private ECR registry. Token is valid 12 hours.
ECR_TOKEN=$(aws ecr get-login-password --region "$AWS_REGION")
$KUBECTL create secret docker-registry ecr-creds \
  --docker-server="$ECR_REGISTRY" \
  --docker-username=AWS \
  --docker-password="$ECR_TOKEN" \
  --dry-run=client -o yaml | $KUBECTL apply -f -
echo "  [ok] ECR pull secret created"

# ── Step 2: Format and mount EBS data volume ──────────────────────────────────

echo "==> [2/5] Mounting EBS data volume"
DEVICE=/dev/xvdf
if [ ! -b "$DEVICE" ]; then DEVICE=/dev/nvme1n1; fi

until test -b "$DEVICE"; do
  echo "  waiting for $DEVICE..."
  sleep 3
done

if ! blkid "$DEVICE" &>/dev/null; then
  echo "  formatting $DEVICE as ext4"
  mkfs.ext4 -L surrealdb-data "$DEVICE"
fi

mkdir -p /mnt/surrealdb-data
mount "$DEVICE" /mnt/surrealdb-data
chmod 755 /mnt/surrealdb-data
echo "$DEVICE /mnt/surrealdb-data ext4 defaults,nofail 0 2" >> /etc/fstab
echo "  [ok] $DEVICE mounted at /mnt/surrealdb-data"

# ── Step 3: Fetch SurrealDB credentials from Secrets Manager ──────────────────

echo "==> [3/5] Fetching SurrealDB credentials"
SURREALDB_CREDS=$(aws secretsmanager get-secret-value \
  --secret-id fintel/surrealdb-creds \
  --query SecretString \
  --output text \
  --region "$AWS_REGION")
SURREAL_USER=$(echo "$SURREALDB_CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['user'])")
SURREAL_PASS=$(echo "$SURREALDB_CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['pass'])")
echo "  [ok] credentials retrieved"

# ── Step 4: Deploy SurrealDB via k8s manifests ────────────────────────────────

echo "==> [4/5] Deploying SurrealDB manifests"

$KUBECTL create secret generic surrealdb-creds \
  --from-literal=user="$SURREAL_USER" \
  --from-literal=pass="$SURREAL_PASS" \
  --dry-run=client -o yaml | $KUBECTL apply -f -

$KUBECTL apply -f - <<'MANIFESTS'
apiVersion: v1
kind: PersistentVolume
metadata:
  name: surrealdb-pv
spec:
  capacity:
    storage: 18Gi
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  hostPath:
    path: /mnt/surrealdb-data
    type: DirectoryOrCreate
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: surrealdb-pvc
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 18Gi
  storageClassName: ""
  volumeName: surrealdb-pv
MANIFESTS

$KUBECTL apply -f - <<MANIFESTS
apiVersion: apps/v1
kind: Deployment
metadata:
  name: surrealdb
  labels:
    app: surrealdb
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: surrealdb
  template:
    metadata:
      labels:
        app: surrealdb
    spec:
      imagePullSecrets:
      - name: ecr-creds
      containers:
      - name: surrealdb
        image: ${ecr_repo_url}:surrealdb
        securityContext:
          runAsUser: 0
        args:
          - start
          - --log=info
          - -b
          - 0.0.0.0:8000
          - surrealkv:///data/fintel.db
        ports:
        - name: surreal
          containerPort: 8000
          hostPort: 8000
        env:
        - name: SURREAL_USER
          valueFrom:
            secretKeyRef:
              name: surrealdb-creds
              key: user
        - name: SURREAL_PASS
          valueFrom:
            secretKeyRef:
              name: surrealdb-creds
              key: pass
        resources:
          requests:
            cpu: "500m"
            memory: "1Gi"
          limits:
            cpu: "1000m"
            memory: "1536Mi"
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
        volumeMounts:
        - name: data
          mountPath: /data
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: surrealdb-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: surrealdb
spec:
  selector:
    app: surrealdb
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
MANIFESTS

echo "  waiting for SurrealDB rollout..."
$KUBECTL rollout status deployment/surrealdb --timeout=300s
echo "  [ok] SurrealDB pod running"

# ── Step 5: Export kubeconfig to Secrets Manager ──────────────────────────────

echo "==> [5/5] Exporting kubeconfig to Secrets Manager"
KUBECONFIG_B64=$(base64 -w0 /etc/rancher/k3s/k3s.yaml)
aws secretsmanager put-secret-value \
  --secret-id "fintel/kubeconfig-$ENV" \
  --secret-string "$KUBECONFIG_B64" \
  --region "$AWS_REGION"
echo "  [ok] kubeconfig written to fintel/kubeconfig-$ENV"

echo "==> user-data complete — SurrealDB is running; schema is applied by FastAPI on Phase 3 deploy"
