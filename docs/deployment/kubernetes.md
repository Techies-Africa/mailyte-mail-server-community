# Kubernetes Deployment

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


Running Mailyte on Kubernetes for high availability, auto-scaling, and multi-region deployments.

## When to Use Kubernetes

Use K8s when you need:

- High availability (no single point of failure)
- Horizontal scaling across multiple nodes
- Multi-region deployment
- Integration with existing K8s infrastructure
- Automated rolling updates

If you're running a single mail server for one domain, Docker Compose is simpler and works fine. Kubernetes is for when you've outgrown that.

## Architecture

```mermaid
graph TB
    LB[Load Balancer] --> ING[Ingress Controller]
    ING --> API[API Deployment]
    ING --> SMTP[Postfix StatefulSet]
    ING --> IMAP[Dovecot StatefulSet]

    API --> DB[(MySQL StatefulSet)]
    API --> RD[(Redis StatefulSet)]
    API --> WK[Worker Deployment]
    WK --> SMTP

    SMTP --> RS[Rspamd Deployment]
    RS --> RD

    subgraph Monitoring
        PR[Prometheus]
        GR[Grafana]
        HM[Health Monitor]
    end
```

## Namespace

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: mailyte
  labels:
    app: mailyte
```

```bash
kubectl apply -f namespace.yaml
```

## Secrets

Store sensitive values in Kubernetes Secrets:

```yaml
# secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: mailyte-secrets
  namespace: mailyte
type: Opaque
stringData:
  mysql-root-password: "your-root-password"
  mysql-password: "your-db-password"
  redis-password: "your-redis-password"
  api-secret-key: "your-api-secret"
---
apiVersion: v1
kind: Secret
metadata:
  name: mailyte-tls
  namespace: mailyte
type: kubernetes.io/tls
data:
  tls.crt: <base64-encoded-cert>
  tls.key: <base64-encoded-key>
```

> **Warning:** Don't commit secrets to version control. Use `kubectl create secret` or a secrets manager like Vault, Sealed Secrets, or External Secrets Operator.

## MySQL StatefulSet

MySQL uses a StatefulSet because it needs stable storage and a consistent network identity.

```yaml
# mysql.yaml
apiVersion: v1
kind: Service
metadata:
  name: mysql
  namespace: mailyte
spec:
  ports:
    - port: 3306
  selector:
    app: mysql
  clusterIP: None  # Headless service for StatefulSet
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mysql
  namespace: mailyte
spec:
  serviceName: mysql
  replicas: 1
  selector:
    matchLabels:
      app: mysql
  template:
    metadata:
      labels:
        app: mysql
    spec:
      containers:
        - name: mysql
          image: mysql:8.0
          ports:
            - containerPort: 3306
          env:
            - name: MYSQL_ROOT_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mailyte-secrets
                  key: mysql-root-password
            - name: MYSQL_DATABASE
              value: mailyte
            - name: MYSQL_USER
              value: mailyte
            - name: MYSQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mailyte-secrets
                  key: mysql-password
          volumeMounts:
            - name: mysql-data
              mountPath: /var/lib/mysql
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
          livenessProbe:
            exec:
              command: ["mysqladmin", "ping", "-h", "localhost"]
            initialDelaySeconds: 60
            periodSeconds: 30
          readinessProbe:
            exec:
              command: ["mysql", "-h", "localhost", "-e", "SELECT 1"]
            initialDelaySeconds: 30
            periodSeconds: 10
  volumeClaimTemplates:
    - metadata:
        name: mysql-data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: fast-ssd
        resources:
          requests:
            storage: 50Gi
```

## Redis StatefulSet

```yaml
# redis.yaml
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: mailyte
spec:
  ports:
    - port: 6379
  selector:
    app: redis
  clusterIP: None
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
  namespace: mailyte
spec:
  serviceName: redis
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          command: ["redis-server", "--requirepass", "$(REDIS_PASSWORD)", "--maxmemory", "512mb"]
          ports:
            - containerPort: 6379
          env:
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mailyte-secrets
                  key: redis-password
          volumeMounts:
            - name: redis-data
              mountPath: /data
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
  volumeClaimTemplates:
    - metadata:
        name: redis-data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 5Gi
```

## Postfix StatefulSet

Postfix needs a StatefulSet because mail queues and state should persist.

```yaml
# postfix.yaml
apiVersion: v1
kind: Service
metadata:
  name: postfix
  namespace: mailyte
spec:
  type: LoadBalancer
  ports:
    - name: smtp
      port: 25
      targetPort: 25
    - name: submission
      port: 587
      targetPort: 587
  selector:
    app: postfix
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postfix
  namespace: mailyte
spec:
  serviceName: postfix
  replicas: 1
  selector:
    matchLabels:
      app: postfix
  template:
    metadata:
      labels:
        app: postfix
    spec:
      containers:
        - name: postfix
          image: mailyte/postfix:latest
          ports:
            - containerPort: 25
            - containerPort: 587
          env:
            - name: DOMAIN
              value: yourdomain.com
            - name: HOSTNAME
              value: mail.yourdomain.com
            - name: MYSQL_HOST
              value: mysql
            - name: MYSQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mailyte-secrets
                  key: mysql-password
          volumeMounts:
            - name: mail-data
              mountPath: /var/mail
            - name: mail-state
              mountPath: /var/mail-state
            - name: tls
              mountPath: /etc/ssl/mail
              readOnly: true
          resources:
            requests:
              cpu: "500m"
              memory: "256Mi"
            limits:
              cpu: "2000m"
              memory: "1Gi"
      volumes:
        - name: tls
          secret:
            secretName: mailyte-tls
  volumeClaimTemplates:
    - metadata:
        name: mail-data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: fast-ssd
        resources:
          requests:
            storage: 100Gi
    - metadata:
        name: mail-state
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 10Gi
```

## Dovecot StatefulSet

```yaml
# dovecot.yaml
apiVersion: v1
kind: Service
metadata:
  name: dovecot
  namespace: mailyte
spec:
  type: LoadBalancer
  ports:
    - name: imaps
      port: 993
      targetPort: 993
  selector:
    app: dovecot
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: dovecot
  namespace: mailyte
spec:
  serviceName: dovecot
  replicas: 1
  selector:
    matchLabels:
      app: dovecot
  template:
    metadata:
      labels:
        app: dovecot
    spec:
      containers:
        - name: dovecot
          image: mailyte/dovecot:latest
          ports:
            - containerPort: 993
          volumeMounts:
            - name: mail-data
              mountPath: /var/mail
            - name: tls
              mountPath: /etc/ssl/mail
              readOnly: true
          resources:
            requests:
              cpu: "250m"
              memory: "256Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
      volumes:
        - name: tls
          secret:
            secretName: mailyte-tls
  volumeClaimTemplates:
    - metadata:
        name: mail-data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: fast-ssd
        resources:
          requests:
            storage: 100Gi
```

> **Note:** Postfix and Dovecot need access to the same mail data. In K8s, consider using a shared filesystem (NFS, CephFS, EFS) via a `ReadWriteMany` PVC, or co-locate them in the same pod.

## API Deployment

The API is stateless, so it uses a Deployment (not StatefulSet) and can scale horizontally.

```yaml
# api.yaml
apiVersion: v1
kind: Service
metadata:
  name: api
  namespace: mailyte
spec:
  ports:
    - port: 5000
      targetPort: 5000
  selector:
    app: api
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: mailyte
spec:
  replicas: 2
  selector:
    matchLabels:
      app: api
  template:
    metadata:
      labels:
        app: api
    spec:
      containers:
        - name: api
          image: mailyte/api:latest
          ports:
            - containerPort: 5000
          env:
            - name: DATABASE_URL
              value: "mysql+aiomysql://mailyte:$(MYSQL_PASSWORD)@mysql:3306/mailyte"
            - name: REDIS_URL
              value: "redis://:$(REDIS_PASSWORD)@redis:6379/0"
            - name: MYSQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mailyte-secrets
                  key: mysql-password
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mailyte-secrets
                  key: redis-password
            - name: SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: mailyte-secrets
                  key: api-secret-key
          resources:
            requests:
              cpu: "250m"
              memory: "128Mi"
            limits:
              cpu: "1000m"
              memory: "512Mi"
          livenessProbe:
            httpGet:
              path: /health
              port: 5000
            initialDelaySeconds: 30
            periodSeconds: 15
          readinessProbe:
            httpGet:
              path: /health
              port: 5000
            initialDelaySeconds: 10
            periodSeconds: 5
```

## Ingress

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mailyte-ingress
  namespace: mailyte
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - api.mail.yourdomain.com
      secretName: mailyte-api-tls
  rules:
    - host: api.mail.yourdomain.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: api
                port:
                  number: 5000
```

## Deploying

```bash
# Apply everything
kubectl apply -f namespace.yaml
kubectl apply -f secrets.yaml
kubectl apply -f mysql.yaml
kubectl apply -f redis.yaml
kubectl apply -f postfix.yaml
kubectl apply -f dovecot.yaml
kubectl apply -f api.yaml
kubectl apply -f ingress.yaml

# Check status
kubectl -n mailyte get pods
kubectl -n mailyte get services
kubectl -n mailyte get pvc

# View logs
kubectl -n mailyte logs -f deployment/api
kubectl -n mailyte logs -f statefulset/postfix
```

## Scaling

```bash
# Scale API replicas
kubectl -n mailyte scale deployment/api --replicas=4

# Scale workers
kubectl -n mailyte scale deployment/worker --replicas=3

# Check HPA (if configured)
kubectl -n mailyte get hpa
```

### Horizontal Pod Autoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-hpa
  namespace: mailyte
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```
