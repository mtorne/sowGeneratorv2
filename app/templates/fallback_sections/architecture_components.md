| OCI Component | Purpose & Key Configuration |
|---|---|
| Virtual Cloud Network (VCN) | The solution uses a VCN segmented into an edge tier, an application/container tier, and a data tier. This segmentation supports controlled traffic and functional isolation across public-facing, workload, and data services. |
| Internet Gateway | The Internet Gateway provides inbound access for public-facing traffic. It is used with edge-tier routing to expose required entry points. |
| NAT Gateway | The NAT Gateway provides outbound connectivity for private subnets. It enables controlled internet egress without exposing private resources directly. |
| Service Gateway | The Service Gateway enables private access to OCI platform services. It is used for services such as OCI Container Registry without traversing the public internet. |
| Dynamic Routing Gateway (DRG) | The DRG is included to provide the foundation for future hybrid connectivity. It supports architecture extension toward on-premises or external network integration. |
| Oracle Kubernetes Engine (OKE) | OKE is the primary compute platform for containerized application workloads. Node pools are sized for standard and GPU-accelerated workloads as required. |
| Bastion Service | OCI Bastion is used to provide secure administrative access to private resources. It avoids exposing administrative endpoints publicly. |
| Jump Host | A jump host is deployed to support controlled administrator access paths. It works with Bastion for secure access into private network tiers. |
| OCI MySQL Database System | OCI MySQL Database System manages persistent relational data in the private data tier. High-availability options are enabled to improve database resilience. |
| OCI Cache with Redis | OCI Cache with Redis provides in-memory caching to reduce database load. It improves application response times for cacheable workloads. |
| OCI Container Registry (OCIR) | OCIR stores and manages container images for deployment workflows. It is integrated with the CI/CD pipeline and OKE deployment process. |
| OCI File Storage | OCI File Storage provides shared network file system access. It supports workloads requiring persistent shared storage. |
| Web Application Firewall (WAF) | WAF is positioned at the public load balancer to inspect and filter inbound web traffic. It is configured to enforce web-layer protection controls. |
| Network Security Groups (NSGs) | NSGs enforce a zero-trust perimeter between architecture tiers. Explicit ingress rules control inter-tier communication paths. |
| Identity and Access Management (IAM) | IAM policies enforce least-privilege access for service identities and administrators. Access scope is governed through OCI policy controls. |
| CI/CD Pipeline | The CI/CD pipeline automates build, test, and deployment for containerized applications. It deploys images from OCIR into OKE through automated stages. |
| Horizontal Pod Autoscaler (HPA) | HPA provides Kubernetes-native autoscaling based on workload signals. It supports efficient scaling for variable demand patterns. |
| KEDA | KEDA provides event-driven scaling where required by workload behavior. It complements HPA for responsive scaling in event-based scenarios. |
