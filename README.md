# Razorpay Revenue Recovery Agent

AI agent that recovers revenue from failed Razorpay subscription auto-debits by classifying failure reasons, executing a decision engine, and simulating recovery outreach channels.

## Setup Instructions

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Generate the synthetic subscriptions dataset:
   ```bash
   python generate_data.py
   ```

3. Start the FastAPI recovery service:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

4. In a separate terminal, replay the failed payment webhooks to test the agent:
   ```bash
   python replay_batch.py
   ```

5. Open the demo dashboard in your browser:
   ```
   http://localhost:8000/dashboard
   ```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhook/razorpay` | Main webhook handler — classifies, decides, logs, creates payment links |
| `GET`  | `/summary` | Aggregate recovery stats (powers the dashboard) |
| `GET`  | `/audit/{subscription_id}` | Full decision trail for one subscription |
| `GET`  | `/audit/all` | All audit log rows, newest-first (used by dashboard) |
| `GET`  | `/dashboard` | Interactive demo dashboard (HTML) |
| `GET`  | `/docs` | Auto-generated Swagger UI |
