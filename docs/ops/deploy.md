# 极快AI操作系统 (JKOS) 部署运维指南

## 目录

1. [环境准备](#环境准备)
2. [Docker 部署](#docker-部署)
3. [Kubernetes 部署](#kubernetes-部署)
4. [配置管理](#配置管理)
5. [数据库配置](#数据库配置)
6. [缓存配置](#缓存配置)
7. [监控告警](#监控告警)
8. [备份恢复](#备份恢复)
9. [安全加固](#安全加固)
10. [故障排查](#故障排查)
11. [性能调优](#性能调优)

---

## 环境准备

### 系统要求

| 组件 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 2 核 | 4+ 核 |
| 内存 | 4 GB | 8+ GB |
| 磁盘 | 20 GB | 100+ GB |
| 网络 | 10 Mbps | 100+ Mbps |

### 依赖软件

| 软件 | 版本 | 说明 |
|------|------|------|
| Docker | 24.0+ | 容器运行时 |
| Docker Compose | 2.20+ | 容器编排 |
| kubectl | 1.28+ | Kubernetes CLI (可选) |
| Helm | 3.12+ | Kubernetes 包管理 (可选) |

### 端口规划

| 端口 | 服务 | 说明 |
|------|------|------|
| 8000 | dsh-api | API 服务 |
| 6379 | redis | 缓存服务 |
| 5432 | postgres | 数据库 (生产) |
| 4113 | nats | 消息总线 |
| 9090 | prometheus | 指标收集 |
| 3000 | grafana | 可视化 (可选) |

---

## Docker 部署

### docker-compose.yml

```yaml
version: "3.8"

services:
  dsh-api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DSH_JWT_SECRET=${DSH_JWT_SECRET}
      - DSH_LLM_API_KEY=${DSH_LLM_API_KEY}
      - DSH_QWEN_API_KEY=${DSH_QWEN_API_KEY}
      - DSH_CLAUDE_API_KEY=${DSH_CLAUDE_API_KEY}
      - DSH_DATABASE_URL=${DSH_DATABASE_URL}
      - DSH_REDIS_URL=${DSH_REDIS_URL}
      - DSH_CORS_ORIGINS=${DSH_CORS_ORIGINS}
      - DSH_LOG_LEVEL=${DSH_LOG_LEVEL:-INFO}
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  postgres:
    image: postgres:15-alpine
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_USER=${POSTGRES_USER:-dsh}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB:-dsh}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-dsh}"]
      interval: 10s
      timeout: 5s
      retries: 3

  nats:
    image: nats:2-alpine
    ports:
      - "4113:4113"
      - "4222:4222"
    restart: unless-stopped

volumes:
  redis_data:
  postgres_data:
```

### 启动

```bash
# 创建环境变量文件
cp .env.example .env
# 编辑 .env 文件，配置必要的密钥

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f dsh-api

# 检查服务状态
docker-compose ps
```

### 停止

```bash
docker-compose down

# 保留数据
docker-compose down --volumes
```

---

## Kubernetes 部署

### 命名空间

```bash
kubectl create namespace dsh
```

### ConfigMap

```yaml
# k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dsh-config
  namespace: dsh
data:
  DSH_LOG_LEVEL: "INFO"
  DSH_CORS_ORIGINS: "https://yourdomain.com"
```

### Secret

```yaml
# k8s/secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: dsh-secret
  namespace: dsh
type: Opaque
stringData:
  DSH_JWT_SECRET: "your-secret-key"
  DSH_LLM_API_KEY: "sk-..."
  DSH_DATABASE_URL: "postgresql://user:pass@postgres:5432/dsh"
  DSH_REDIS_URL: "redis://redis:6379/0"
```

### Deployment

```yaml
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dsh-api
  namespace: dsh
spec:
  replicas: 2
  selector:
    matchLabels:
      app: dsh-api
  template:
    metadata:
      labels:
        app: dsh-api
    spec:
      containers:
        - name: dsh-api
          image: dsh-ai-platform:latest
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef:
                name: dsh-config
            - secretRef:
                name: dsh-secret
          resources:
            requests:
              cpu: 200m
              memory: 512Mi
            limits:
              cpu: 1000m
              memory: 2Gi
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /readyz
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
```

### Service

```yaml
# k8s/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: dsh-api
  namespace: dsh
spec:
  selector:
    app: dsh-api
  ports:
    - port: 8000
      targetPort: 8000
  type: ClusterIP
```

### Ingress

```yaml
# k8s/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: dsh-api
  namespace: dsh
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  tls:
    - hosts:
        - api.yourdomain.com
      secretName: dsh-tls
  rules:
    - host: api.yourdomain.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: dsh-api
                port:
                  number: 8000
```

### 部署命令

```bash
# 应用所有配置
kubectl apply -f k8s/

# 检查部署状态
kubectl get pods -n dsh
kubectl get svc -n dsh
kubectl get ingress -n dsh

# 查看日志
kubectl logs -n dsh -l app=dsh-api -f
```

---

## 配置管理

### 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `DSH_JWT_SECRET` | JWT 签名密钥 | `your-secret-key` |
| `DSH_LLM_API_KEY` | LLM API 密钥 | `sk-...` |
| `DSH_DATABASE_URL` | 数据库连接字符串 | `postgresql://...` |
| `DSH_REDIS_URL` | Redis 连接字符串 | `redis://...` |
| `DSH_ENCRYPTION_KEY` | 数据加密密钥 | `your-32-byte-key` |
| `DSH_CORS_ORIGINS` | CORS 白名单 | `https://example.com` |
| `DSH_LOG_LEVEL` | 日志级别 | `INFO` |

### 配置文件

```yaml
# config.yaml
server:
  host: 0.0.0.0
  port: 8000
  workers: 4

database:
  url: postgresql://user:pass@localhost:5432/dsh
  pool_size: 10
  max_overflow: 20

cache:
  type: redis
  url: redis://localhost:6379/0
  ttl: 3600

llm:
  default_provider: deepseek
  fallback_chain:
    - deepseek
    - openai
    - qwen
    - claude
    - simulated

rate_limit:
  enabled: true
  default_rps: 100
```

---

## 数据库配置

### SQLite (开发环境)

```bash
# 默认配置
DSH_DATABASE_URL=sqlite:///./data/dsh.db
```

### PostgreSQL (生产环境)

```bash
# 生产环境配置
DSH_DATABASE_URL=postgresql://user:password@localhost:5432/dsh

# 连接池配置
DSH_DB_POOL_SIZE=10
DSH_DB_MAX_OVERFLOW=20
DSH_DB_POOL_TIMEOUT=30
```

### 数据库迁移

```bash
# 初始化数据库
dsh-server init

# 手动迁移
dsh-server migrate

# 创建迁移脚本
dsh-server makemigrations
```

### 数据库备份

```bash
# SQLite 备份
cp data/dsh.db data/dsh.db.bak

# PostgreSQL 备份
pg_dump -h localhost -U dsh dsh > backup.sql

# PostgreSQL 恢复
psql -h localhost -U dsh dsh < backup.sql
```

---

## 缓存配置

### 内存缓存 (开发环境)

```bash
# 默认使用内存缓存
DSH_CACHE_TYPE=memory
```

### Redis (生产环境)

```bash
# Redis 配置
DSH_CACHE_TYPE=redis
DSH_REDIS_URL=redis://localhost:6379/0

# 缓存 TTL (秒)
DSH_CACHE_TTL=3600

# 多级缓存配置
DSH_CACHE_L1_TTL=300
DSH_CACHE_L2_TTL=3600
```

### Redis 集群

```bash
# Redis 集群配置
DSH_REDIS_URL=redis://redis1:6379,redis2:6379,redis3:6379
```

---

## 监控告警

### Prometheus 配置

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'dsh-api'
    static_configs:
      - targets: ['dsh-api:8000']
    metrics_path: '/metrics'

  - job_name: 'redis'
    static_configs:
      - targets: ['redis:9121']

  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres-exporter:9187']
```

### Grafana 仪表盘

导入 `docs/ops/grafana-dashboard.json` 或使用以下 JSON：

```json
{
  "dashboard": {
    "title": "极快AI操作系统 API",
    "panels": [
      {
        "title": "请求速率",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(dsh_requests_total[5m])"
          }
        ]
      },
      {
        "title": "请求延迟",
        "type": "heatmap",
        "targets": [
          {
            "expr": "rate(dsh_request_duration_seconds_bucket[5m])"
          }
        ]
      },
      {
        "title": "LLM 调用量",
        "type": "stat",
        "targets": [
          {
            "expr": "dsh_llm_calls_total"
          }
        ]
      }
    ]
  }
}
```

### 告警规则

```yaml
# alerting.yml
groups:
  - name: dsh-api
    rules:
      - alert: HighErrorRate
        expr: rate(dsh_requests_total{status=~"5.."}[5m]) / rate(dsh_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "错误率过高"

      - alert: ServiceDown
        expr: up{job="dsh-api"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "服务不可用"

      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(dsh_request_duration_seconds_bucket[5m])) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "请求延迟过高"
```

---

## 备份恢复

### SQLite 备份

```bash
# 备份
cp data/dsh.db data/dsh.db.$(date +%Y%m%d).bak

# 恢复
cp data/dsh.db.$(date +%Y%m%d).bak data/dsh.db
```

### PostgreSQL 备份

```bash
# 全量备份
pg_dump -h localhost -U dsh -d dsh -F c -f backup.dump

# 增量备份 (需要 WAL 归档)
pg_basebackup -h localhost -U dsh -D backup -X stream

# 恢复
pg_restore -h localhost -U dsh -d dsh backup.dump
```

### Redis 备份

```bash
# 手动备份
redis-cli BGSAVE

# 自动备份 (配置 redis.conf)
# save 900 1
# save 300 10
# save 60 10000
```

### 配置文件备份

```bash
# 备份所有配置
tar czf config-backup-$(date +%Y%m%d).tar.gz .env docker-compose.yml
```

---

## 安全加固

### HTTPS 配置

```yaml
# nginx.conf
server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate /etc/ssl/certs/fullchain.pem;
    ssl_certificate_key /etc/ssl/private/privkey.pem;

    location / {
        proxy_pass http://dsh-api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 安全头

```python
# 在 API 服务中配置安全头
# dsh_core/api/security.py

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Content-Security-Policy": "default-src 'self'",
    "Referrer-Policy": "strict-origin-when-cross-origin"
}
```

### 速率限制

```bash
# 全局速率限制
DSH_RATE_LIMIT_ENABLED=true
DSH_RATE_LIMIT_RPS=100

# 按端点限制
DSH_RATE_LIMIT_LLM=60
DSH_RATE_LIMIT_TEXT=100
```

### 防火墙规则

```bash
# 仅允许必要端口
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw enable
```

---

## 故障排查

### 服务无法启动

**症状**: 服务启动失败或立即退出

**排查步骤**:

1. 检查日志
   ```bash
   docker-compose logs dsh-api
   kubectl logs -n dsh -l app=dsh-api
   ```

2. 检查端口
   ```bash
   netstat -tlnp | grep 8000
   ss -tlnp | grep 8000
   ```

3. 检查环境变量
   ```bash
   docker-compose config
   printenv | grep DSH
   ```

4. 检查依赖服务
   ```bash
   docker-compose ps
   kubectl get pods -n dsh
   ```

### 数据库连接失败

**症状**: 数据库连接错误

**排查步骤**:

1. 检查数据库服务状态
   ```bash
   docker-compose ps postgres
   pg_isready -h localhost -U dsh
   ```

2. 检查连接字符串
   ```bash
   echo $DSH_DATABASE_URL
   ```

3. 检查文件权限 (SQLite)
   ```bash
   ls -la data/dsh.db
   chmod 644 data/dsh.db
   ```

4. 检查磁盘空间
   ```bash
   df -h
   ```

### 缓存连接失败

**症状**: 缓存操作失败

**排查步骤**:

1. 检查 Redis 服务
   ```bash
   redis-cli ping
   # 返回 PONG 表示正常
   ```

2. 检查网络连通性
   ```bash
   telnet redis 6379
   ```

3. 检查 Redis 配置
   ```bash
   redis-cli config get requirepass
   ```

### LLM API 调用失败

**症状**: LLM 调用返回错误

**排查步骤**:

1. 检查 API 密钥
   ```bash
   echo $DSH_LLM_API_KEY
   ```

2. 检查 API 配额
   - 登录供应商控制台查看配额

3. 检查网络连通性
   ```bash
   curl -I https://api.deepseek.com
   ```

4. 查看降级日志
   ```bash
   grep "fallback" logs/dsh.log
   ```

### 内存泄漏

**症状**: 内存使用持续增长

**排查步骤**:

1. 检查内存使用
   ```bash
   docker stats
   ```

2. 检查连接池泄漏
   ```bash
   grep "connection leak" logs/dsh.log
   ```

3. 检查缓存大小
   ```bash
   redis-cli dbsize
   redis-cli memory stats
   ```

---

## 性能调优

### 数据库调优

```yaml
# PostgreSQL 配置 (postgresql.conf)
shared_buffers: 256MB
work_mem: 4MB
maintenance_work_mem: 64MB
max_connections: 100
```

### 缓存调优

```yaml
# 缓存配置
DSH_CACHE_L1_TTL: 300      # L1 内存缓存 5 分钟
DSH_CACHE_L2_TTL: 3600     # L2 Redis 缓存 1 小时
DSH_CACHE_BLOOM_CAPACITY: 10000
DSH_CACHE_BLOOM_ERROR_RATE: 0.01
```

### 连接池调优

```yaml
# 数据库连接池
DSH_DB_POOL_SIZE: 10
DSH_DB_MAX_OVERFLOW: 20
DSH_DB_POOL_TIMEOUT: 30

# 连接池健康检查
DSH_DB_HEALTH_CHECK_INTERVAL: 60
```

### 异步任务队列调优

```yaml
# 异步任务队列
DSH_ASYNCQ_MAX_WORKERS: 10
DSH_ASYNCQ_DEFAULT_TIMEOUT: 300
DSH_ASYNCQ_MAX_RETRIES: 3
```

### LLM 调用调优

```yaml
# LLM 调用配置
DSH_LLM_MAX_CONCURRENT: 10
DSH_LLM_REQUEST_TIMEOUT: 60
DSH_LLM_CACHE_TTL: 3600
```

---

## 常见问题

### Q: 如何重置数据库？

```bash
# SQLite
rm data/dsh.db
dsh-server init

# PostgreSQL
dropdb -U dsh dsh
createdb -U dsh dsh
dsh-server init
```

### Q: 如何切换 LLM 供应商？

```bash
# 设置默认供应商
DSH_LLM_PROVIDER=openai

# 或设置降级链
DSH_LLM_FALLBACK_CHAIN=deepseek,openai,qwen,claude
```

### Q: 如何启用调试模式？

```bash
DSH_LOG_LEVEL=DEBUG
```

### Q: 如何查看 API 文档？

```bash
# 启动服务后访问
# Swagger UI: http://localhost:8000/docs
# ReDoc: http://localhost:8000/redoc
```

---

## 相关文档

- [开发指南](../dev/guide.md)
- [API 文档](../api/openapi.md)
- [架构设计](../architecture/)
- [技术设计白皮书](../../多模态AI-Agent中台-技术设计白皮书.md)
