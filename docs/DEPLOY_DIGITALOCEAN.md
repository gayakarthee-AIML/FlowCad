# Deploying FlowCAD on DigitalOcean

FlowCAD runs as two containers on one Droplet (nginx + frontend, FastAPI +
CadQuery backend). Gemma 4 runs on DigitalOcean Serverless Inference, called
only from the backend.

> **Status:** this guide has not been run end to end by the FlowCAD authors.
> The Docker images could not be built in the environment where FlowCAD was
> written (Docker Hub was blocked there). Follow the verification steps at
> the end before you rely on a deployment.

## 1. Costs and credits

Serverless Inference and Droplets are billed separately. Inference is
prepaid from your inference balance or credits; a Droplet bills from your
account balance. Do not assume hackathon inference credits cover the
Droplet. Check current prices before the event:

- Serverless Inference pricing: https://docs.digitalocean.com/products/inference/details/pricing/
- Droplet pricing: https://www.digitalocean.com/pricing/droplets

At the time of writing, DigitalOcean listed Gemma 4 at $0.18 per 1M input
tokens and $0.50 per 1M output tokens. One FlowCAD request is roughly 1,000
input and under 200 output tokens.

## 2. Create a model access key and confirm the model ID

1. In the DigitalOcean control panel, open **Inference** (Serverless
   Inference) and create a **model access key**.
2. On your laptop, put it in `.env` (repo root) as `DO_INFERENCE_API_KEY`.
3. Confirm the Gemma 4 model ID your key can use:

   ```bash
   cd backend
   python scripts/check_inference.py
   ```

   The script lists models from `GET /v1/models`, highlights Gemma ones,
   warns if `DO_INFERENCE_MODEL` is not among them, and sends one test
   request through FlowCAD's real parsing and validation path. Set
   `DO_INFERENCE_MODEL` to the ID it shows.

## 3. Create the Droplet

- Image: **Ubuntu 24.04 LTS**
- Size: 2 vCPU / 4 GB RAM or larger (CadQuery and its wheels are large;
  1 GB Droplets run out of memory during the image build)
- Authentication: SSH key
- Add a **Cloud Firewall**: inbound TCP 22 (your IP only) and 80
  (and 443 if you add TLS). Do not open 8000; the backend is only reachable
  through nginx.

## 4. Install Docker and copy the project

```bash
ssh root@<droplet-ip>
curl -fsSL https://get.docker.com | sh
git clone https://github.com/<you>/flowcad.git
cd flowcad
cp .env.example .env
nano .env        # set DO_INFERENCE_API_KEY and DO_INFERENCE_MODEL; FLOWCAD_PORT=80
chmod 600 .env
```

The key lives only in `.env` on the Droplet. `.env` is in `.gitignore` and
both `.dockerignore` files, so it is never committed or baked into an image;
Compose passes it to the backend container at runtime.

## 5. Build and start

```bash
docker compose up --build -d
docker compose ps          # backend should become "healthy"
docker compose logs -f backend
```

## 6. Verify the deployment actually builds CAD

Run these on the Droplet; each must succeed.

```bash
# API health through nginx (CAD engine available, AI configured)
curl -s http://localhost/health

# CAD kernel inside the container: builds, validates and exports a bracket
docker compose exec backend python -m app.services.cad_engine | head -40

# Full request through nginx: prompt -> Gemma -> validation -> CAD -> files
curl -s -X POST http://localhost/api/design/edit \
  -H 'content-type: application/json' \
  -d '{"session_id":"deploycheck01","text":"Base 120 mm long, 6 mm thick"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'], d['interpretation']['source'], d['revision']['files'])"

# Download the STL it reported (replace the file name)
curl -s -o /tmp/check.stl http://localhost/api/files/<filename>.stl && ls -l /tmp/check.stl
```

Expect `committed gemma [...]`. If the source is `rules`, the response's
`interpretation.ai_error` says why Gemma was not used (bad key, wrong model
ID, rate limit, timeout); the app keeps working with the offline parser.

Then open `http://<droplet-ip>/` in a browser, click **Use example →
Generate**, and confirm the model renders and both downloads work.

## 7. Optional: HTTPS

Point a domain at the Droplet and put Caddy or certbot in front of port 80,
or use a DigitalOcean Load Balancer with a managed certificate.

## Updating

```bash
git pull && docker compose up --build -d
```

Generated files live in the `flowcad-output` volume. Revision history is in
memory and resets when the backend restarts.
