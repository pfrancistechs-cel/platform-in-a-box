# Diagram prompt (paste into an AI image model)

Create a clean, wide infrastructure and flow diagram, white background, flat icons, thin labelled arrows, a legend at the bottom. Title: "Platform in a box on one EC2 instance: tests, policy, build, sign, gate, canary, SLO alert".

Outer box: "EC2 instance m7i-flex.large (Docker, network platform-net)". Inside, a left-to-right pipeline of six numbered stages in a box "ship.sh (the golden path)":
1. "Unit tests". 2. "Policy (conftest): Dockerfile rules DKR-001..005". 3. "Build and push" to "registry:2 (127.0.0.1:5000)". 4. "SBOM (syft) and sign (cosign)": padlock with "release.key", writing ".sig" and ".att" next to the image in the registry. 5. "Release gate": verify signature, verify attestation, SBOM deny policy. 6. "Canary release (deploy.py)".

Red stop signs under stages 2, 5 and 6: "sloppy Dockerfile stopped before build", "vulnerable library (urllib3 1.26.4) denied", "canary errors: automatic rollback".

Right of the pipeline: "router :8080" with two slots "plat-blue (v1)" and "plat-green (v2)" and a weight arrow "10% / 50% / 100%". "plat-traffic" sends 20 req/s into the router. The router exports "/metrics" to "Prometheus 2.54.1 (127.0.0.1:9090)" with a rule "error ratio > 5% for 10 s: ErrorRatioHigh" drawn as a bell. A dotted arrow from the router's stats back to deploy.py labelled "canary judged on errors and p95".

On the far right, "console :8081": browser window with panels "Live service", "SLO 99%", "Pipeline runs" and "Registry (signed, SBOM)", fed by dotted arrows from router, Prometheus, registry and a "results/" folder. A small wrench labelled "platform.sh heal" restarts the live container.

Outside the box: "Your laptop (My IP only)" with arrows to ":8080" and ":8081".

Security group note: "Inbound: 22, 8080, 8081 from My IP only. Ports 5000 and 9090 are localhost only". Small note: "No S3, no IAM role, no Alertmanager: local keys and simulated pieces (limit)".
